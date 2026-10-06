"""CLI: build act_relations (which act amends / repeals / refers to which) from the lines in Postgres.

    uv run python -m lineage

Runs over the index in seconds; no re-parse. Re-run after `indexing`: the table is rebuilt from scratch.
"""

import argparse
import logging
from collections import Counter, defaultdict

from psycopg.rows import dict_row
from retrieval.db import get_connection, init_db

from .extract import find_references, normalize_number

log = logging.getLogger("lineage")


def load_documents(cur) -> dict[tuple[str, str], list[dict]]:
    """Acts in the corpus by (type, number): the targets references resolve to."""
    cur.execute("SELECT doc_id, doc_type, number, date, title FROM documents WHERE number IS NOT NULL")
    acts: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        acts[(row["doc_type"], normalize_number(row["number"]))].append(row)
    return acts


def resolve(acts: dict, doc_type: str, number: str, date: str | None) -> str | None:
    candidates = acts.get((doc_type, number), [])
    if date:  # a dated reference must match the act's date when the act has one
        candidates = [a for a in candidates if a["date"] in (None, date)]
    return candidates[0]["doc_id"] if len(candidates) == 1 else None


def document_lines(cur):
    """Lines of each document in reading order: (doc_id, [(line_id, text), …])."""
    cur.execute(
        "SELECT l.doc_id, l.line_id, l.text FROM lines l JOIN chunks c ON c.chunk_id = l.chunk_id "
        "ORDER BY l.doc_id, (c.block_ids->>0)::int NULLS FIRST, c.chunk_id, l.idx"
    )
    doc_id, lines = None, []
    for row in cur.fetchall():
        if row["doc_id"] != doc_id and lines:
            yield doc_id, lines
            lines = []
        doc_id = row["doc_id"]
        lines.append((row["line_id"], row["text"]))
    if lines:
        yield doc_id, lines


def relations(doc_id: str, lines: list[tuple[str, str]], acts: dict) -> list[tuple]:
    """References found in the document's text, each tied to the line where its number starts."""
    text, starts = "", []
    for line_id, line in lines:
        starts.append((len(text), line_id))
        text += line + "\n"  # references often break after "nr." onto the next line
    rows = {}
    for ref in find_references(text):
        line_id = next(lid for pos, lid in reversed(starts) if pos <= ref.end - 1)
        target = resolve(acts, ref.doc_type, ref.number, ref.date)
        if target == doc_id:
            continue  # the act's own title
        rows[(doc_id, line_id, ref.text)] = (doc_id, target, ref.text, ref.doc_type, ref.number, ref.date,
                                             ref.relation, line_id)
    return list(rows.values())


def main() -> None:
    argparse.ArgumentParser(prog="python -m lineage", description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    init_db()
    with get_connection(autocommit=False) as conn, conn.cursor(row_factory=dict_row) as cur:
        acts = load_documents(cur)
        rows = [r for doc_id, lines in document_lines(cur) for r in relations(doc_id, lines, acts)]
        cur.execute("DELETE FROM act_relations")
        cur.executemany(
            "INSERT INTO act_relations (from_doc_id, to_doc_id, to_ref_text, to_doc_type, to_number, to_date, "
            "relation, line_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()

        by_relation = Counter(r[6] for r in rows)
        resolved = sum(1 for r in rows if r[1])
        print(f"act_relations: {len(rows)} rows, resolved to a corpus act: {resolved}")
        print("  " + ", ".join(f"{k}={v}" for k, v in sorted(by_relation.items())))
        cur.execute(
            """
            SELECT f.doc_type AS from_type, f.number AS from_number, r.relation, r.to_ref_text,
                   t.number AS to_number, r.to_doc_id IS NOT NULL AS in_corpus
            FROM act_relations r JOIN documents f ON f.doc_id = r.from_doc_id
            LEFT JOIN documents t ON t.doc_id = r.to_doc_id
            WHERE r.relation IN ('amends', 'repeals') OR f.number IN ('79', '12/14', '251-d')
            ORDER BY f.number, r.relation
            """
        )
        print("\nfrom            relation  to (text in the act)                                      in corpus")
        for r in cur.fetchall():
            print(f"{r['from_type'] or '?':>10} {r['from_number'] or '?':<8} {r['relation']:<8}  "
                  f"{r['to_ref_text'][:58]:<58} {'yes ' + (r['to_number'] or '') if r['in_corpus'] else 'no'}")


if __name__ == "__main__":
    main()
