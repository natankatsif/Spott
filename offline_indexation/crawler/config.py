"""The site list: the admin panel's `sources` table (Postgres), or data/sources/sites.toml before it is imported."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {"max_depth": 4, "max_pages": 2000, "delay": 0.5, "ignore_robots": False}


@dataclass
class Site:
    id: str
    category: str
    start_urls: list[str]
    max_depth: int
    max_pages: int
    delay: float
    ignore_robots: bool


def load_sites(path: Path) -> list[Site]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = DEFAULTS | data.get("defaults", {})
    return [Site(**(defaults | entry)) for entry in data["site"]]


def load_sites_from_db() -> list[Site]:
    """Enabled site sources that robots.txt lets us crawl; [] when there are none or no database is reachable."""
    import psycopg
    from retrieval.db import get_connection

    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT site_id, category, start_urls, max_depth, max_pages, delay FROM sources "
                "WHERE kind = 'site' AND enabled AND robots = 'allowed' ORDER BY id").fetchall()
    except psycopg.Error:
        return []
    return [Site(id=sid, category=cat or "", start_urls=list(urls or []) or [f"https://{sid}/"],
                 max_depth=depth if depth is not None else DEFAULTS["max_depth"],
                 max_pages=pages if pages is not None else DEFAULTS["max_pages"],
                 delay=delay if delay is not None else DEFAULTS["delay"], ignore_robots=False)
            for sid, cat, urls, depth, pages, delay in rows]
