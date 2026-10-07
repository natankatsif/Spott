"""The "crawl" stage of a document source: registers its URL in the registry, so the downloader fetches it.

    uv run python -m spott.ingest.worker.register --url https://site.md/storage/act.pdf --site site.md
"""

import argparse

from spott.ingest.common.progress import Progress
from spott.ingest.common.registry import Registry
from spott.ingest.common.urls import extension, url_key


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m spott.ingest.worker.register", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--site", required=True)
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    progress = Progress(total=1)
    registry = Registry.open()
    try:
        new = registry.add_document(key=url_key(args.url), url=args.url, site=args.site,
                                    extension=extension(args.url), source={"found_on": ""})
    finally:
        registry.close()
    progress.advance()
    progress.finish()
    print(f"{'registered' if new else 'already registered'}: {args.url}")


if __name__ == "__main__":
    main()
