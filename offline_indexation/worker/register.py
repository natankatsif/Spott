"""The "crawl" stage of a document source: registers its URL in the registry, so the downloader fetches it.

    uv run python -m worker.register --url https://site.md/storage/act.pdf --site site.md
"""

import argparse
from pathlib import Path

from common.progress import Progress
from common.registry import Registry
from common.urls import extension, url_key


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m worker.register", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--category", default="document")
    p.add_argument("--db", type=Path, default=Path("data/registry.sqlite"))
    args = p.parse_args()
    progress = Progress(total=1)
    registry = Registry(args.db)
    try:
        new = registry.add_document(key=url_key(args.url), url=args.url, site=args.site, category=args.category,
                                    extension=extension(args.url), external=False,
                                    source={"found_on": "", "via": "admin"})
    finally:
        registry.close()
    progress.advance()
    progress.finish()
    print(f"{'registered' if new else 'already registered'}: {args.url}")


if __name__ == "__main__":
    main()
