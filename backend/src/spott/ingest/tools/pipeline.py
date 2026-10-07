"""Corpus pipeline without bash: works the same on Mac, Windows and Linux.

    uv run python -m spott.ingest.tools.pipeline update                 # refresh already crawled sites, re-check files, index changes
    uv run python -m spott.ingest.tools.pipeline full                   # full crawl of all allowed sites + parse + index
    uv run python -m spott.ingest.tools.pipeline full --only crawler downloader    # crawl + download only (no OCR, no models)
    uv run python -m spott.ingest.tools.pipeline update --dry-run       # print what would run

Sites whose robots.txt forbids crawling (chisinau.md) are never crawled here.
Logs: data/logs/<mode>-<date>/<stage>.log
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

import psycopg

from spott.core.db import get_connection
from spott.core.paths import BACKEND_DIR, DATA_DIR, SITES_TOML
from spott.core.sources import EXCLUDED_SITES
from spott.ingest.common.console import child_env, utf8_console

STAGES = ("crawler", "downloader", "parsing", "pages_parsing", "indexing")


def allowed_sites(config: Path = SITES_TOML) -> list[str]:
    """The admin panel's crawlable sites; sites.toml while the database has none."""
    from spott.ingest.crawler.config import load_sites_from_db

    if from_db := load_sites_from_db():
        return [s.id for s in from_db if s.id not in EXCLUDED_SITES]
    with config.open("rb") as f:
        sites = tomllib.load(f).get("site", [])
    return [s["id"] for s in sites if s["id"] not in EXCLUDED_SITES]


def crawled_sites() -> list[str]:
    """Sites that already have pages in the registry — what `update` refreshes by default; [] without a database."""
    try:
        with get_connection(register=False) as conn:
            rows = conn.execute("SELECT DISTINCT site FROM registry_pages").fetchall()
    except psycopg.Error:
        return []
    return sorted(site for (site,) in rows if site not in EXCLUDED_SITES)


def plan(mode: str, sites: list[str], max_depth: int | None) -> list[tuple[str, list[str]]]:
    """(stage, module args) in run order."""
    depth = ["--max-depth", str(max_depth)] if max_depth else []
    if mode == "update":
        return [
            ("crawler", ["-m", "spott.ingest.crawler", "--resume", "--sites", *sites, *depth]),
            ("downloader", ["-m", "spott.ingest.downloader", "--refresh"]),
            ("parsing", ["-m", "spott.ingest.parsing"]),
            ("pages_parsing", ["-m", "spott.ingest.pages_parsing"]),
            ("indexing", ["-m", "spott.ingest.indexing"]),
        ]
    return [
        ("crawler", ["-m", "spott.ingest.crawler", "--sites", *sites, *depth]),
        ("downloader", ["-m", "spott.ingest.downloader"]),
        ("parsing", ["-m", "spott.ingest.parsing"]),
        ("pages_parsing", ["-m", "spott.ingest.pages_parsing"]),
        ("indexing", ["-m", "spott.ingest.indexing"]),  # it chunks the parsed documents itself
    ]


def run_stage(name: str, args: list[str], log_file: Path) -> bool:
    """Runs one stage with this interpreter, streaming output to the console and the log file."""
    started = time.monotonic()
    print(f"\n━━━ {datetime.now():%H:%M:%S} ▸ {name} ━━━", flush=True)
    with log_file.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, *args], cwd=BACKEND_DIR, env=child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line)
        code = proc.wait()
    status = "ok" if code == 0 else f"ОШИБКА (код {code})"
    print(f"  ⏱ {name}: {time.monotonic() - started:.0f} с — {status}", flush=True)
    return code == 0


def registry_summary() -> list[str]:
    from spott.ingest.common.registry import Registry

    try:
        registry = Registry.open()
    except psycopg.Error as e:
        return [f"реестр недоступен: {e}"]
    try:
        counts = registry.status_counts()
    finally:
        registry.close()
    titles = {"documents": "Документы (файлы)", "pages": "Страницы (разбор)", "files": "Файлы (разбор)"}
    return [f"{title}: " + ", ".join(f"{k or '—'} {v}" for k, v in counts[key].items())
            for key, title in titles.items()]


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(description="Пайплайн корпуса (кроссплатформенный)")
    p.add_argument("mode", choices=["update", "full"])
    p.add_argument("--only", nargs="+", choices=STAGES, help="запустить только эти этапы")
    p.add_argument("--sites", nargs="+", metavar="ID", help="только эти сайты (по умолчанию все разрешённые)")
    p.add_argument("--max-depth", type=int, help="глубина обхода (по умолчанию из sites.toml)")
    p.add_argument("--dry-run", action="store_true", help="показать план и выйти")
    args = p.parse_args(argv)

    sites = allowed_sites() if args.mode == "full" else (crawled_sites() or allowed_sites())
    if args.sites:
        blocked = set(args.sites) & EXCLUDED_SITES
        if blocked:
            p.error(f"эти сайты запрещены robots.txt и без разрешения не обходятся: {', '.join(sorted(blocked))}")
        sites = args.sites
    steps = [(n, a) for n, a in plan(args.mode, sites, args.max_depth) if not args.only or n in args.only]

    print(f"Режим: {args.mode} · сайтов: {len(sites)} · этапы: {', '.join(n for n, _ in steps)}")
    if args.dry_run:
        for name, a in steps:
            print(f"  {name}: python {' '.join(a)}")
        return

    log_dir = DATA_DIR / "logs" / f"{args.mode}-{datetime.now():%Y-%m-%d_%H%M%S}"
    log_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for name, a in steps:
        if not run_stage(name, a, log_dir / f"{name}.log"):
            failed.append(name)

    print("\n══════ Итог ══════")
    for line in registry_summary():
        print("  " + line)
    idx_log = log_dir / "indexing.log"
    if idx_log.exists():
        stats = [ln.strip() for ln in idx_log.read_text(encoding="utf-8").splitlines() if "emb" in ln or "removed" in ln]
        for ln in stats:
            print("  " + ln)
    print(f"  Логи: {log_dir}")
    if failed:
        print(f"  ❌ Упали этапы: {', '.join(failed)} — смотри их .log")
        sys.exit(1)
    print("  ✅ Все этапы прошли")


if __name__ == "__main__":
    main()
