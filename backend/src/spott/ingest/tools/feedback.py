"""Low-rated answers as eval candidates (docs/history/tasks/09 §2).

    uv run python -m spott.ingest.tools.feedback export            # rating ≤ 2 → eval/from_feedback.yaml
    uv run python -m spott.ingest.tools.feedback export --max-rating 3

Each item: the question, its language, the answer given, the rating, reason tags and comment, the cited
documents. A person turns a candidate into a real eval case by adding what the right answer cites.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from psycopg.rows import dict_row

from spott.core.db import get_connection, init_app_db
from spott.core.paths import EVAL_DIR
from spott.ingest.common.console import utf8_console

OUT = EVAL_DIR / "from_feedback.yaml"


def export(max_rating: int, out: Path) -> int:
    with get_connection() as conn:
        init_app_db(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM feedback WHERE rating <= %s ORDER BY rating, updated_at DESC", (max_rating,))
            rows = cur.fetchall()
    items = [{"id": f"fb-{r['answer_id']}-{r['session_id'] or 'anon'}", "question": r["question"], "lang": r["lang"],
              "rating": r["rating"], "tags": r["tags"], "comment": r["comment"], "status": r["status"],
              "path": r["path"], "answer": r["answer"], "cited_doc_ids": r["doc_ids"],
              "rated_at": r["updated_at"].isoformat(timespec="seconds")} for r in rows]
    header = (f"# Candidates from answers rated <= {max_rating} (tools.feedback export). "
              "Not an eval set yet: add what the right answer cites.\n")
    out.write_text(header + yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return len(items)


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(prog="python -m spott.ingest.tools.feedback", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["export"])
    p.add_argument("--max-rating", type=int, default=2)
    p.add_argument("--out", type=Path, default=OUT)
    args = p.parse_args(argv)
    n = export(args.max_rating, args.out)
    print(f"{n} low-rated answers → {args.out}")


if __name__ == "__main__":
    main()
