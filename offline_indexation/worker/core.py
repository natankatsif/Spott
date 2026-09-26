"""One admin job: the pipeline stages for one source, with progress in % (docs/tasks/09).

Stages and their share of the job: crawl 20%, download 20%, parse 40% (files, then pages), index 20%.
Each stage is a child process of this interpreter; it reports its counters through PROGRESS_FILE and stops
after its current item when CANCEL_FILE appears (common/progress.py). The runner turns them into the job row:
stage, stage_done / stage_total, percent, ETA from the stage's rate, the last 50 log lines.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.progress import read_progress

OI_DIR = Path(__file__).resolve().parents[1]
WEIGHTS = {"crawl": 20.0, "download": 20.0, "parse": 40.0, "index": 20.0}
REPORT_EVERY_S = 1.0  # the job row is written at least this often while a stage runs
LOG_TAIL = 50


@dataclass
class Step:
    stage: str  # crawl | download | parse | index
    args: list[str]  # python arguments, run in offline_indexation/
    weight: float  # percent of the whole job


class JobStore(Protocol):
    def update_job(self, job_id: int, **fields) -> None: ...
    def cancel_requested(self, job_id: int) -> bool: ...
    def site_stats(self, site_id: str) -> dict[str, int]: ...


def plan(job: dict, source: dict | None, all_sites: list[str] | None = None) -> list[Step]:
    """The stages of a job. A site source is crawled; a document source is registered and fetched as a document;
    `refresh` re-checks what was crawled before (tools.pipeline update)."""
    refresh = job["kind"] == "refresh"
    sites = [source["site_id"]] if source is not None else list(all_sites or [])
    if source is not None and source["kind"] == "document":
        crawl = ["-m", "worker.register", "--url", source["url"], "--site", source["site_id"]]
    else:
        crawl = ["-m", "crawler", "--sites", *sites] + (["--resume"] if refresh else [])
        if source is not None and source.get("max_depth") is not None:
            crawl += ["--max-depth", str(source["max_depth"])]
        if source is not None and source.get("max_pages") is not None:
            crawl += ["--max-pages", str(source["max_pages"])]
    download = ["-m", "downloader", "--sites", *sites] + (["--refresh"] if refresh else [])
    steps = [
        Step("crawl", crawl, WEIGHTS["crawl"]),
        Step("download", download, WEIGHTS["download"]),
        Step("parse", ["-m", "parsing"], WEIGHTS["parse"] / 2),
        Step("parse", ["-m", "pages_parsing", "--sites", *sites], WEIGHTS["parse"] / 2),
        Step("index", ["-m", "indexing", "--sites", *sites], WEIGHTS["index"]),
    ]
    if source is not None and source["kind"] == "document":
        steps = [s for s in steps if s.args[1] != "pages_parsing"]
        steps[2].weight = WEIGHTS["parse"]  # files only
    return steps


def run_process(step: Step, env: dict[str, str], on_line: Callable[[str], None]) -> int:
    """The stage as a child process of this interpreter; its output goes line by line to on_line."""
    proc = subprocess.Popen([sys.executable, *step.args], cwd=OI_DIR, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\n"))
    return proc.wait()


INDEX_NUMBERS = {
    "embeddings_reused": re.compile(r"Chunk emb reused:\s+(\d+)"),
    "embeddings_computed": re.compile(r"Chunk emb computed:\s+(\d+)"),
    "line_embeddings_reused": re.compile(r"Line emb reused:\s+(\d+)"),
    "line_embeddings_computed": re.compile(r"Line emb computed:\s+(\d+)"),
}


class JobRunner:
    def __init__(self, store: JobStore, run_step: Callable = run_process, clock: Callable[[], float] = time.monotonic):
        self.store, self.run_step, self.clock = store, run_step, clock

    def run(self, job: dict, source: dict | None, all_sites: list[str] | None = None) -> str:
        """Runs the job to the end; returns its final status (done | failed | cancelled)."""
        job_id = job["id"]
        steps = plan(job, source, all_sites)
        assert abs(sum(s.weight for s in steps) - 100) < 1e-9
        tail: deque[str] = deque(maxlen=LOG_TAIL)
        errors, finished_weight, status, error = 0, 0.0, "done", None
        numbers: dict[str, int] = {}
        started = self.clock()
        with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmp:
            progress_file, cancel_file = Path(tmp) / "progress.json", Path(tmp) / "cancel"
            for step in steps:
                if self.store.cancel_requested(job_id):
                    status = "cancelled"
                    break
                progress_file.unlink(missing_ok=True)
                env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                       "PROGRESS_FILE": str(progress_file), "CANCEL_FILE": str(cancel_file)}
                code = self._run_step(job_id, step, env, tail, progress_file, cancel_file, finished_weight, started,
                                      numbers)
                errors += (read_progress(progress_file) or {}).get("errors", 0)
                if cancel_file.exists():
                    status = "cancelled"
                    break
                if code != 0:
                    status, error = "failed", f"{step.args[1]} exited with code {code}"
                    break
                finished_weight += step.weight
        site = source["site_id"] if source else None
        stats = (self.store.site_stats(site) if site else {}) | numbers | {"errors": errors}
        final = {"status": status, "stats": stats, "log_tail": list(tail), "error": error, "eta_s": None,
                 "finished": True}
        if status == "done":
            final |= {"percent": 100.0, "stage_done": 0, "stage_total": 0}
        self.store.update_job(job_id, **final)
        return status

    def _run_step(self, job_id: int, step: Step, env: dict, tail: deque, progress_file: Path, cancel_file: Path,
                  finished_weight: float, job_started: float, numbers: dict) -> int:
        stage_started = self.clock()
        stop = threading.Event()
        state = {"percent": finished_weight}

        last: dict = {}

        def report() -> None:
            p = read_progress(progress_file) or last  # a file being rewritten: keep the last counters
            last.update(p)
            done, total = p.get("done", 0), p.get("total", 0)
            fraction = min(1.0, done / total) if total else 0.0
            percent = finished_weight + step.weight * fraction
            state["percent"] = percent
            elapsed = self.clock() - stage_started
            # ETA from the stage's rate: the time the rest of this stage and every later stage would take at it.
            rate = step.weight * fraction / elapsed if elapsed > 0 else 0.0
            eta = (100.0 - percent) / rate if rate > 0 else None
            self.store.update_job(job_id, stage=step.stage, stage_done=done, stage_total=total,
                                  percent=round(percent, 1), eta_s=round(eta, 1) if eta is not None else None,
                                  log_tail=list(tail))
            if not cancel_file.exists() and self.store.cancel_requested(job_id):
                cancel_file.touch()  # the stage stops after its current item

        def loop() -> None:
            while not stop.wait(REPORT_EVERY_S):
                report()

        def on_line(line: str) -> None:
            tail.append(line)
            for key, pattern in INDEX_NUMBERS.items():
                if m := pattern.search(line):
                    numbers[key] = int(m[1])

        report()
        reporter = threading.Thread(target=loop, daemon=True)
        reporter.start()
        try:
            return self.run_step(step, env, on_line)
        finally:
            stop.set()
            reporter.join()
            report()
