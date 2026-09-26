"""Admin jobs (docs/tasks/09): stage weights, progress 0 → 100 %, cancel; sources import from sites.toml."""

import json
import time
from pathlib import Path

import pytest

from tools.sources import SITES_TOML, import_sites
from worker import core
from worker.core import JobRunner, plan

SITE = {"id": 7, "kind": "site", "site_id": "acc.md", "url": "https://acc.md/", "max_depth": 2, "max_pages": 50}
DOCUMENT = {"id": 8, "kind": "document", "site_id": "dgaurf.md", "url": "https://dgaurf.md/storage/a.pdf"}


class FakeJobs:
    def __init__(self):
        self.updates: list[dict] = []
        self.cancel = False

    def update_job(self, job_id, **fields):
        self.updates.append(fields)

    def cancel_requested(self, job_id):
        return self.cancel

    def site_stats(self, site_id):
        return {"pages": 12, "chunks": 30}


def stub_stages(ran: list[str], items: int = 4, on_item=None):
    """A stage that works through `items` items, reporting like common.progress does and stopping on cancel."""

    def run(step, env, on_line):
        ran.append(step.args[1])
        progress = Path(env["PROGRESS_FILE"])
        for done in range(1, items + 1):
            if Path(env["CANCEL_FILE"]).exists():
                return 0
            time.sleep(0.01)
            progress.write_text(json.dumps({"done": done, "total": items, "errors": 0}), encoding="utf-8")
            on_line(f"{step.args[1]} item {done}")
            if on_item:
                on_item(step, done)
        return 0

    return run


@pytest.fixture(autouse=True)
def fast_reports(monkeypatch):
    monkeypatch.setattr(core, "REPORT_EVERY_S", 0.005)


@pytest.mark.parametrize("job,source", [({"kind": "crawl"}, SITE), ({"kind": "refresh"}, SITE),
                                        ({"kind": "crawl"}, DOCUMENT), ({"kind": "crawl"}, None)])
def test_stage_weights_add_up(job, source):
    steps = plan(job, source, ["a.md", "b.md"])
    assert sum(s.weight for s in steps) == 100
    assert {s.stage for s in steps} == {"crawl", "download", "parse", "index"}


def test_plans_for_site_document_and_refresh():
    crawl = plan({"kind": "crawl"}, SITE)
    assert crawl[0].args == ["-m", "crawler", "--sites", "acc.md", "--max-depth", "2", "--max-pages", "50"]
    assert [s.args[1] for s in crawl] == ["crawler", "downloader", "parsing", "pages_parsing", "indexing"]
    assert "--refresh" in plan({"kind": "refresh"}, SITE)[1].args and "--resume" in plan({"kind": "refresh"}, SITE)[0].args
    doc = plan({"kind": "crawl"}, DOCUMENT)
    assert [s.args[1] for s in doc] == ["worker.register", "downloader", "parsing", "indexing"]


def test_a_link_merged_into_a_source_is_the_only_thing_its_job_does():
    deeper = plan({"kind": "crawl", "url": "https://acc.md/ro/servicii"}, SITE)
    assert deeper[0].args == ["-m", "crawler", "--sites", "acc.md", "--start-urls", "https://acc.md/ro/servicii",
                              "--path-prefix", "/ro/servicii", "--max-depth", "2"]
    doc = plan({"kind": "crawl", "url": "https://acc.md/files/tarife.pdf"}, SITE)
    assert doc[0].args == ["-m", "worker.register", "--url", "https://acc.md/files/tarife.pdf", "--site", "acc.md"]
    assert sum(s.weight for s in doc) == 100 and "pages_parsing" not in [s.args[1] for s in doc]


def test_percent_goes_from_0_to_100():
    jobs, ran = FakeJobs(), []
    status = JobRunner(jobs, run_step=stub_stages(ran)).run({"id": 1, "kind": "crawl"}, SITE)

    assert status == "done"
    assert ran == ["crawler", "downloader", "parsing", "pages_parsing", "indexing"]
    percents = [u["percent"] for u in jobs.updates if "percent" in u]
    assert percents[0] == 0 and percents[-1] == 100 and percents == sorted(percents)
    stages = [u["stage"] for u in jobs.updates if "stage" in u]
    assert list(dict.fromkeys(stages)) == ["crawl", "download", "parse", "index"]
    final = jobs.updates[-1]
    assert final["status"] == "done" and final["stats"] == {"pages": 12, "chunks": 30, "errors": 0}
    assert final["log_tail"][-1] == "indexing item 4"
    assert all(u["eta_s"] is None or u["eta_s"] >= 0 for u in jobs.updates if "eta_s" in u)


def test_cancel_stops_after_the_current_item():
    jobs, ran = FakeJobs(), []

    def cancel_during_download(step, done):
        if step.stage == "download" and done == 2:
            jobs.cancel = True
            time.sleep(0.05)  # the reporter notices and drops the cancel file

    status = JobRunner(jobs, run_step=stub_stages(ran, items=6, on_item=cancel_during_download)).run(
        {"id": 2, "kind": "crawl"}, SITE)

    assert status == "cancelled"
    assert ran == ["crawler", "downloader"]  # nothing after the stage that was running
    download = [u for u in jobs.updates if u.get("stage") == "download"]
    assert download[-1]["stage_done"] < 6  # it stopped part-way
    assert jobs.updates[-1]["status"] == "cancelled" and "percent" not in jobs.updates[-1]


def test_failed_stage_fails_the_job():
    jobs = FakeJobs()
    status = JobRunner(jobs, run_step=lambda step, env, on_line: 1 if step.stage == "parse" else 0).run(
        {"id": 3, "kind": "crawl"}, SITE)
    assert status == "failed" and jobs.updates[-1]["error"] == "parsing exited with code 1"


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.rowcount = rows, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if params is not None:
            self.rows.append(params)
        self.rowcount = 1

    def fetchone(self):
        return (None,)  # to_regclass('public.chunks'): no index in this fake database


class FakeConn:
    def __init__(self):
        self.rows = []

    def cursor(self):
        return FakeCursor(self.rows)


def test_import_from_sites_toml_gives_40_sources():
    conn = FakeConn()
    assert import_sites(conn, SITES_TOML) == 40
    assert {row[1] for row in conn.rows if row[-1] == "blocked"} == {"chisinau.md"}  # robots.txt: Disallow: /


def test_eta_counts_down_while_progress_stands_still():
    now = [0.0]
    runner, state = JobRunner(FakeJobs(), clock=lambda: now[0]), {}
    now[0] = 10.0
    first = runner._eta(10.0, 0.0, state)  # 10 % in 10 s: ~90 s left
    assert first == pytest.approx(90.0)
    etas = []
    for _ in range(5):  # no new progress for 5 reports
        now[0] += 2.0
        etas.append(runner._eta(10.0, 0.0, state))
    assert etas == sorted(etas, reverse=True) and etas[-1] == pytest.approx(80.0)
    now[0] += 60.0
    assert runner._eta(10.0, 0.0, state) == 20.0  # still counting down, not climbing
