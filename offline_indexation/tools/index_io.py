"""Export / import the search index (Postgres tables documents, chunks, lines) via Docker.

    uv run python -m tools.index_io export                 # -> <repo>/data/export/index-<date>.dump
    uv run python -m tools.index_io import path/to.dump    # starts DB, creates schema, restores, prints counts

pg_dump / pg_restore run inside the container, so no local Postgres client is needed (Windows-friendly).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from .common import CONTAINER, ROOT, db_env, utf8_console

TABLES = ("documents", "chunks", "lines", "act_relations")
# Dumps made before the indexer filled chunks.ord have 0 everywhere; the position is the first block.
ORD_BACKFILL = (
    "UPDATE chunks SET ord = (block_ids->>0)::int "
    "WHERE ord = 0 AND jsonb_typeof(block_ids) = 'array' AND jsonb_array_length(block_ids) > 0 "
    "AND (block_ids->>0)::int > 0"
)


def docker(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=True, **kw)


def wait_ready(user: str, db: str, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(["docker", "exec", CONTAINER, "pg_isready", "-U", user, "-d", db], capture_output=True)
        if r.returncode == 0:
            return
        time.sleep(1)
    sys.exit(f"Postgres в контейнере {CONTAINER} не поднялся за {timeout} с. Открыт ли Docker Desktop?")


def export(out: Path | None) -> Path:
    env = db_env()
    out = out or ROOT / "data" / "export" / f"index-{date.today()}.dump"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = docker("exec", CONTAINER, "psql", "-U", env["user"], "-d", env["db"], "-At", "-c",
                      "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
                      capture_output=True, text=True, encoding="utf-8").stdout.split()
    tables = [a for t in TABLES if t in existing for a in ("-t", t)]  # act_relations: only after lineage ran
    with out.open("wb") as f:
        docker("exec", CONTAINER, "pg_dump", "-U", env["user"], "-d", env["db"], "-Fc", *tables, stdout=f)
    print(f"Готово: {out} ({out.stat().st_size / 1024**2:.1f} МБ)")
    return out


def counts(user: str, db: str) -> str:
    sql = ("SELECT (SELECT count(*) FROM documents) AS documents, (SELECT count(*) FROM chunks) AS chunks, "
           "(SELECT count(*) FROM chunks WHERE embedding IS NULL) AS chunks_no_emb, "
           "(SELECT count(*) FROM lines) AS lines, (SELECT count(*) FROM lines WHERE embedding IS NULL) AS lines_no_emb")
    r = docker("exec", CONTAINER, "psql", "-U", user, "-d", db, "-c", sql, capture_output=True, text=True,
               encoding="utf-8")
    return r.stdout


def import_dump(dump: Path) -> None:
    if not dump.is_file():
        sys.exit(f"Файл не найден: {dump}")
    env = db_env()
    print("1/5 docker compose up -d")
    docker("compose", "up", "-d", cwd=ROOT)
    print("2/5 жду Postgres…")
    wait_ready(env["user"], env["db"])
    print("3/5 схема (расширения, таблицы, индексы)")
    from retrieval.db import init_db

    init_db()
    print(f"4/5 восстанавливаю {dump.name}")
    with dump.open("rb") as f:
        r = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "pg_restore", "-U", env["user"], "-d", env["db"],
             "--clean", "--if-exists", "--no-owner", "--no-privileges"],
            stdin=f, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    if r.returncode != 0:  # pg_restore warns about objects that init_db already created — show, don't fail
        print("pg_restore предупреждения:\n" + "\n".join(r.stderr.splitlines()[-10:]))
    print("5/5 схема поверх дампа (колонки старых дампов) и порядок фрагментов (chunks.ord)")
    init_db()  # pg_restore --clean recreated the tables as they were in the dump
    r = docker("exec", CONTAINER, "psql", "-U", env["user"], "-d", env["db"], "-c", ORD_BACKFILL,
               capture_output=True, text=True, encoding="utf-8")
    print("   " + r.stdout.strip())
    print(counts(env["user"], env["db"]))


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(description="Экспорт / импорт индекса")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="выгрузить индекс в .dump")
    e.add_argument("--out", type=Path)
    i = sub.add_parser("import", help="загрузить индекс из .dump")
    i.add_argument("dump", type=Path)
    args = p.parse_args(argv)
    try:
        if args.cmd == "export":
            export(args.out)
        else:
            import_dump(args.dump)
    except FileNotFoundError:
        sys.exit("Команда docker не найдена. Установи и запусти Docker Desktop.")
    except subprocess.CalledProcessError as err:
        sys.exit(f"Ошибка: {' '.join(map(str, err.cmd[:4]))}… (код {err.returncode}). Запущен ли Docker Desktop?")


if __name__ == "__main__":
    main()
