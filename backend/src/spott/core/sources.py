"""The admin panel's sources (Postgres `sources`): what may be crawled and how the crawler introduces itself, what a
document source is, category rules, the seed from sites.toml. Shared by the API (seeds at startup, adds sources by
URL, checks robots.txt) and the ingest stages and tools.

No LLM anywhere here: the category of a new source comes from its domain, else from keywords in its <title> and
meta description, else "other".
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

# robots.txt "Disallow: /" (checked by hand): never crawled without the mentor's permission.
EXCLUDED_SITES = {"chisinau.md", "actelocale.gov.md"}
DEFAULTS = {"max_depth": 4, "max_pages": 2000, "delay": 0.5}
# Every request to a city hall site, robots.txt checks included.
USER_AGENT = "ChisinauAssistantBot/0.1 (+GigaHack 2026; municipal RAG research crawler)"
# A source that is a single document (anything else is a website): by its content type or its link's extension.
DOCUMENT_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def is_document_link(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(tuple(DOCUMENT_TYPES.values()))

# The categories sites.toml uses. First match wins, against the full host.
DOMAIN_RULES: list[tuple[str, re.Pattern]] = [
    ("education", re.compile(r"dets|educ|scoal|gradinit|extrascolar|liceu|gimnazi")),
    ("healthcare", re.compile(r"amt|sanat|spital|dgams|medic")),
    ("district", re.compile(r"pretura|botanica|ciocana|rascani|riscani|buiucani|centru")),
    ("mobility", re.compile(r"mobil|transport|autourban|rtec|exdrupo")),
    ("urban_utilities", re.compile(r"salubr|apa|lift|termo|dgaurf|dglca|agsv")),
    ("transparency", re.compile(r"(^|\.)chisinau\.md$|proiecte|suburbii|transparen")),
]
KEYWORDS: list[tuple[str, re.Pattern]] = [
    ("education", re.compile(r"școal|scoal|liceu|grădiniț|gradinit|educați|educati|elev|школ|лице|детск\w* сад|"
                             r"образовани", re.I)),
    ("healthcare", re.compile(r"spital|sănăt|sanat|medic|clinic|policlinic|больниц|здоров|медицин|поликлиник",
                              re.I)),
    ("district", re.compile(r"pretur|sector|претур|сектор", re.I)),
    ("mobility", re.compile(r"transport|parcar|trafic|troleibuz|autobuz|транспорт|парковк|троллейбус|автобус", re.I)),
    ("urban_utilities", re.compile(r"urbanism|arhitect|salubr|deșeu|deseu|apă|apa-canal|termic|lift|"
                                   r"градострои|архитект|отход|водоснаб|отоплен", re.I)),
    ("services", re.compile(r"servici|turism|comerț|comert|invest|tineret|услуг|туризм|торгов|инвест|молод",
                            re.I)),
    ("transparency", re.compile(r"primări|primari|consiliul municipal|decizi|dispoziți|transparen|примэри|мэри|"
                                r"решени|распоряжени", re.I)),
]


def categorize(host: str, text: str = "") -> tuple[str, str]:
    """(category, how it was decided: rule | keywords | default)."""
    for category, pattern in DOMAIN_RULES:
        if pattern.search(host):
            return category, "rule"
    for category, pattern in KEYWORDS:
        if text and pattern.search(text):
            return category, "keywords"
    return "other", "default"


INSERT_SITE = """
INSERT INTO sources (kind, url, site_id, category, category_source, start_urls, max_depth, max_pages, delay, robots)
VALUES ('site', %s, %s, %s, 'toml', %s, %s, %s, %s, %s)
ON CONFLICT (site_id) WHERE kind = 'site' DO NOTHING
"""


def seed_sources(conn, toml_path: Path) -> int:
    """sites.toml into `sources` (only the sites not there yet), then every site that has chunks in the index but
    no source (an index imported from another machine), categorized by its domain. Returns how many rows were added.
    Idempotent."""
    added = 0
    if toml_path.is_file():
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        defaults = DEFAULTS | data.get("defaults", {})
        with conn.cursor() as cur:
            for s in data.get("site", []):
                s = defaults | s
                cur.execute(INSERT_SITE, (s["start_urls"][0], s["id"], s.get("category"), Jsonb(s["start_urls"]),
                                          s["max_depth"], s["max_pages"], s["delay"],
                                          "blocked" if s["id"] in EXCLUDED_SITES else "allowed"))
                added += cur.rowcount
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.chunks')")
        if cur.fetchone()[0] is None:  # no index on this database yet
            return added
        cur.execute("""
            SELECT DISTINCT c.site FROM chunks c
            WHERE c.site IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sources s WHERE s.site_id = c.site)""")
        for (site,) in cur.fetchall():
            cur.execute(INSERT_SITE.replace("'toml'", "'index'"),
                        (f"https://{site}/", site, categorize(site)[0], Jsonb([f"https://{site}/"]),
                         DEFAULTS["max_depth"], DEFAULTS["max_pages"], DEFAULTS["delay"],
                         "blocked" if site in EXCLUDED_SITES else "allowed"))
            added += cur.rowcount
    return added
