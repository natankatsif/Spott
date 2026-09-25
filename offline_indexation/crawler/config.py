"""Loads the site list (data/sources/sites.toml)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


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
    defaults = data.get("defaults", {})
    return [Site(**(defaults | entry)) for entry in data["site"]]
