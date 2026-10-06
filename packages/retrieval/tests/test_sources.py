"""Admin sources seeding and category rules (docs/tasks/11 B): idempotent seed, no LLM."""

from pathlib import Path

from retrieval.sources import categorize, seed_sources

SITES_TOML = Path(__file__).resolve().parents[3] / "offline_indexation" / "data" / "sources" / "sites.toml"


class SeedDb:
    """What seed_sources needs of Postgres: INSERT … ON CONFLICT (site_id) DO NOTHING, the chunks check."""

    def __init__(self, indexed_sites=()):
        self.sites: dict[str, tuple] = {}
        self.indexed = list(indexed_sites)
        self.rowcount = 0
        self._result = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("INSERT"):
            site = params[1]
            self.rowcount = 0 if site in self.sites else 1
            self.sites.setdefault(site, params)
        elif "to_regclass" in sql:
            self._result = [("chunks",)] if self.indexed else [(None,)]
        else:  # indexed sites without a source
            self._result = [(s, "other") for s in self.indexed if s not in self.sites]

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result


def test_empty_database_gets_40_sources_and_a_restart_adds_none():
    db = SeedDb()
    assert seed_sources(db, SITES_TOML) == 40
    assert seed_sources(db, SITES_TOML) == 0
    assert len(db.sites) == 40
    assert db.sites["chisinau.md"][-1] == "blocked"


def test_indexed_site_missing_from_sites_toml_gets_a_source():
    db = SeedDb(indexed_sites=["dgaurf.md", "new-site.md"])
    assert seed_sources(db, SITES_TOML) == 41
    assert db.sites["new-site.md"][0] == "https://new-site.md/"


def test_category_rules():
    assert categorize("detsbotanica.md") == ("education", "rule")
    assert categorize("amt-centru.md") == ("healthcare", "rule")
    assert categorize("preturabuiucani.md") == ("district", "rule")
    assert categorize("rtec.md") == ("mobility", "rule")
    assert categorize("exemplu.md", "Liceul Teoretic nr. 5 — orarul lecțiilor") == ("education", "keywords")
    assert categorize("exemplu.md", "Городская больница") == ("healthcare", "keywords")
    assert categorize("exemplu.md", "Bine ați venit") == ("other", "default")
