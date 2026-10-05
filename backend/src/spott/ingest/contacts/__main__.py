"""Contacts table from the index (docs/history/tasks/09 §4): no re-crawl, no OCR.

    uv run python -m spott.ingest.contacts            # rebuild `contacts` from the indexed web pages, print the report
    uv run python -m spott.ingest.contacts --dry-run  # report only

Reads the chunks with contacts (chunks.has_contacts) of web pages and their lines; one card per chunk with a
phone or an e-mail, the same card repeated on many pages of a site kept once; embedding of "name. area. about" (bge-m3)
(plus the page's first sentence) for the answer to find the nearest card.
"""

import argparse
import logging
from collections import Counter, defaultdict

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from spott.core.db import get_connection, init_app_db
from spott.core.embeddings import get_device, get_embedding_model

from .extract import GENERAL, contact_from_chunk, dedupe

log = logging.getLogger("contacts")

COLUMNS = ("contact_id", "name", "area", "phone", "email", "address", "hours", "url", "site", "category", "doc_id",
           "line_ids", "is_general", "embedding")
JSON = {"phone", "email", "line_ids"}


def embed_text(card: dict) -> str:
    """What the card is found by. "Primăria municipiului Chișinău" (the page title of a whole site, so also the name or
    area of many cards) would pull in every question about the city, so it is cut out."""
    parts = (GENERAL.sub("", x or "").strip(" .:|-") for x in (card["name"], card["area"], card.get("about")))
    return ". ".join(x for x in parts if x)


def load(conn) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT chunk_id, doc_id, site, url, title, category FROM chunks "
                    "WHERE has_contacts AND kind = 'page' ORDER BY doc_id, (block_ids->>0)::int")
        chunks = cur.fetchall()
        cur.execute("SELECT line_id, chunk_id, doc_id, idx, text FROM lines WHERE doc_id = ANY(%s) ORDER BY chunk_id, idx",
                    (list({c["doc_id"] for c in chunks}),))
        lines = cur.fetchall()
    by_chunk, by_doc = defaultdict(list), defaultdict(list)
    for line in lines:
        by_chunk[line["chunk_id"]].append(line)
        by_doc[line["doc_id"]].append(line)
    cards = [card for c in chunks if (card := contact_from_chunk(c, by_chunk[c["chunk_id"]], by_doc[c["doc_id"]]))]
    return dedupe(cards)


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.contacts", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="print the report, don't write the table")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    with get_connection(autocommit=True) as conn:
        init_app_db(conn)
        cards = load(conn)
        texts = {line_id: text for line_id, text in conn.execute(
            "SELECT line_id, text FROM lines WHERE line_id = ANY(%s)",
            ([lid for c in cards for lid in c["line_ids"]],)).fetchall()}
        if not args.dry_run:
            model = get_embedding_model(get_device())
            vectors = model.encode([embed_text(c) for c in cards], normalize_embeddings=True, show_progress_bar=False)
            with conn.transaction():
                conn.execute("DELETE FROM contacts")
                with conn.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO contacts ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})",
                        [[Jsonb(c[k]) if k in JSON else c[k] for k in COLUMNS[:-1]] + [vec]
                         for c, vec in zip(cards, vectors, strict=True)])

    per_site = Counter(c["site"] for c in cards)
    print(f"contacts: {len(cards)} ({sum(c['is_general'] for c in cards)} general City Hall)"
          + (" — dry run, table not written" if args.dry_run else ""))
    for site, n in per_site.most_common():
        print(f"  {site:28} {n}")
    print("\nexamples:")
    for c in sorted(cards, key=lambda c: (-len(c["phone"]) - len(c["email"]), c["site"]))[:5]:
        print(f"- {c['name']} ({c['site']}) phone={c['phone']} email={c['email']} address={c['address']}")
        print(f"  area: {c['area']}")
        for lid in c["line_ids"]:
            print(f"  line {lid[:10]}: {texts.get(lid, '')[:150]}")


if __name__ == "__main__":
    main()
