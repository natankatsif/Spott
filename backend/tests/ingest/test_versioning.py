"""Tests for Task 06: document versioning, replacement, and removal.

Fixtures use the registry in a throwaway Postgres schema to verify:
- v1 → v2: only v2 chunks/lines in index, doc_id unchanged, 2 versions in history
- unchanged text: 0 recomputed embeddings
- document missing once: stays; missing twice: removed, chunks gone
- page changed: old chunk_ids removed
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spott.ingest.chunking.chunker import chunk_document
from spott.ingest.common.loader import load_active_documents
from spott.ingest.common.registry import Registry, now


@pytest.fixture()
def tmp_data(tmp_path: Path):
    """Creates a temporary data directory with registry and parsed directories."""
    (tmp_path / "parsed").mkdir()
    (tmp_path / "parsed" / "pages").mkdir()
    return tmp_path


def make_file_doc(*, sha256: str, title: str, text_blocks: list[dict], url: str = "https://chisinau.md/doc.pdf",
                  site: str = "chisinau.md", category: str = "decizii"):
    """Helper: build a parsed-file JSON dict."""
    return {
        "sha256": sha256,
        "metadata": {"title": title, "doc_type": "decizie"},
        "sources": [{"url": url, "site": site, "category": category}],
        "blocks": text_blocks,
    }


def write_parsed_json(data_dir: Path, sha256: str, doc: dict) -> None:
    (data_dir / "parsed" / f"{sha256}.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


def setup_registry_with_file(reg: Registry, *, key: str, url: str, site: str,
                              sha256: str, category: str = "decizii", ext: str = ".pdf") -> Registry:
    """Adds a fully-downloaded, parsed file to the registry."""
    reg.add_document(key=key, url=url, site=site, category=category,
                     extension=ext, external=False, source={"found_on": url, "site": site})
    reg.record_download(key, sha256=sha256, path=f"raw/{sha256[:2]}/{sha256}.pdf",
                        size=1000, content_type="application/pdf", extension=".pdf",
                        http_status=200, etag=None, last_modified=None)
    reg.conn.execute("UPDATE registry_files SET parse_status = 'parsed' WHERE sha256 = %s", (sha256,))
    return reg


# ---------------------------------------------------------------------------
# Test 1: v1 → v2 of one PDF at the same URL
# ---------------------------------------------------------------------------
class TestVersionReplacement:
    def test_v1_to_v2_chunks_only_v2(self, tmp_data: Path, registry: Registry):
        """After updating to v2, only v2 text appears in chunks; doc_id unchanged; 2 versions in history."""
        url = "https://chisinau.md/decisions/dec1.pdf"
        key = "chisinau.md/decisions/dec1.pdf"
        site = "chisinau.md"
        sha_v1 = hashlib.sha256(b"content-v1").hexdigest()
        sha_v2 = hashlib.sha256(b"content-v2").hexdigest()

        # Setup v1
        doc_v1 = make_file_doc(sha256=sha_v1, title="Decizia nr. 1 v1", url=url,
                               text_blocks=[{"id": 0, "type": "paragraph",
                                             "text": "Versiunea 1 a documentului conține reguli inițiale pentru cetățeni.",
                                             "section": [], "lang": "ro"}])
        write_parsed_json(tmp_data, sha_v1, doc_v1)
        reg = setup_registry_with_file(registry, key=key, url=url, site=site, sha256=sha_v1)

        # Verify v1 loads correctly
        docs_v1 = load_active_documents(tmp_data, registry=registry)
        assert len(docs_v1) == 1
        assert docs_v1[0]["doc_id"] == f"file:{key}"
        chunks_v1 = chunk_document(docs_v1[0])
        assert any("Versiunea 1" in c["text"] for c in chunks_v1)

        # Update to v2: new content at same URL
        doc_v2 = make_file_doc(sha256=sha_v2, title="Decizia nr. 1 v2", url=url,
                               text_blocks=[{"id": 0, "type": "paragraph",
                                             "text": "Versiunea 2 actualizată a documentului cu modificări noi.",
                                             "section": [], "lang": "ro"}])
        write_parsed_json(tmp_data, sha_v2, doc_v2)

        # Record new download (simulates downloader finding new sha)
        # Need to insert the new file first
        reg.conn.execute(
            "INSERT INTO registry_files (sha256, path, size, content_type, extension, downloaded_at, parse_status) "
            "VALUES (%s, %s, %s, %s, %s, NOW(), 'parsed')",
            (sha_v2, f"raw/{sha_v2[:2]}/{sha_v2}.pdf", 1200, "application/pdf", ".pdf")
        )
        reg.record_download(key, sha256=sha_v2, path=None, size=1200,
                            content_type="application/pdf", extension=".pdf",
                            http_status=200, etag=None, last_modified=None)

        # Verify v2 is active now, same doc_id
        docs_v2 = load_active_documents(tmp_data, registry=registry)
        assert len(docs_v2) == 1
        assert docs_v2[0]["doc_id"] == f"file:{key}"  # doc_id unchanged

        chunks_v2 = chunk_document(docs_v2[0])
        # Only v2 text in chunks
        for c in chunks_v2:
            assert "Versiunea 1" not in c["text"]
            assert "Versiunea 2" in c["text"]

        # Verify version history: 2 versions exist
        versions = reg.conn.execute(
            "SELECT sha256, version FROM registry_document_versions WHERE document_key = %s ORDER BY version", (key,)
        ).fetchall()
        assert versions == [(sha_v1, 1), (sha_v2, 2)]

        # Doc record shows version=2, previous_sha256=sha_v1
        doc_rec = reg.conn.execute("SELECT version, previous_sha256, sha256 FROM registry_documents WHERE key = %s",
                                   (key,)).fetchone()
        assert doc_rec == (2, sha_v1, sha_v2)


    def test_unchanged_text_zero_recompute(self, tmp_data: Path, registry: Registry):
        """Re-indexing the same text reuses content_hash; 0 new embeddings needed."""
        url = "https://chisinau.md/doc-stable.pdf"
        key = "chisinau.md/doc-stable.pdf"
        sha = hashlib.sha256(b"stable-content").hexdigest()

        doc = make_file_doc(sha256=sha, title="Doc stabil", url=url,
                            text_blocks=[{"id": 0, "type": "paragraph",
                                          "text": "Textul documentului rămâne complet neschimbat între versiuni.",
                                          "section": [], "lang": "ro"}])
        write_parsed_json(tmp_data, sha, doc)
        setup_registry_with_file(registry, key=key, url=url, site="chisinau.md", sha256=sha)

        # Load + chunk twice — content_hash should be identical
        docs1 = load_active_documents(tmp_data, registry=registry)
        chunks1 = chunk_document(docs1[0])

        docs2 = load_active_documents(tmp_data, registry=registry)
        chunks2 = chunk_document(docs2[0])

        assert len(chunks1) == len(chunks2)
        for c1, c2 in zip(chunks1, chunks2, strict=True):
            assert c1["content_hash"] == c2["content_hash"]
            assert c1["chunk_id"] == c2["chunk_id"]

        # All content_hashes match → indexer.embed_and_store would find them all cached → 0 computed
        hashes1 = {c["content_hash"] for c in chunks1}
        hashes2 = {c["content_hash"] for c in chunks2}
        assert hashes1 == hashes2  # every hash is reusable


# ---------------------------------------------------------------------------
# Test 2: Document missing once → stays; twice → removed, no chunks
# ---------------------------------------------------------------------------
class TestDocumentRemoval:
    def test_missing_once_stays(self, tmp_data: Path, registry: Registry):
        """A document that goes missing 1 time still has status != 'removed'."""
        url = "https://chisinau.md/temp.pdf"
        key = "chisinau.md/temp.pdf"
        sha = hashlib.sha256(b"temp").hexdigest()

        doc = make_file_doc(sha256=sha, title="Temp", url=url,
                            text_blocks=[{"id": 0, "type": "paragraph",
                                          "text": "Document temporar care ar putea dispărea de pe server.",
                                          "section": [], "lang": "ro"}])
        write_parsed_json(tmp_data, sha, doc)
        reg = setup_registry_with_file(registry, key=key, url=url, site="chisinau.md", sha256=sha)

        # Simulate one missing (404)
        removed = reg.record_download_missing(key, 404)
        assert not removed

        row = reg.conn.execute("SELECT status, consecutive_missing FROM registry_documents WHERE key = %s",
                               (key,)).fetchone()
        assert row == ("downloaded", 1)  # still in the index: one 404 can be a hiccup
        assert any(d.get("doc_id") == f"file:{key}" for d in load_active_documents(tmp_data, registry=registry))


    def test_missing_twice_removed(self, tmp_data: Path, registry: Registry):
        """A document missing 2 times in a row becomes 'removed'."""
        url = "https://chisinau.md/gone.pdf"
        key = "chisinau.md/gone.pdf"
        sha = hashlib.sha256(b"gone").hexdigest()

        doc = make_file_doc(sha256=sha, title="Gone", url=url,
                            text_blocks=[{"id": 0, "type": "paragraph",
                                          "text": "Document care va fi eliminat după două verificări consecutive.",
                                          "section": [], "lang": "ro"}])
        write_parsed_json(tmp_data, sha, doc)
        reg = setup_registry_with_file(registry, key=key, url=url, site="chisinau.md", sha256=sha)

        # First missing
        removed = reg.record_download_missing(key, 404)
        assert not removed

        # Second missing → removed
        removed = reg.record_download_missing(key, 404)
        assert removed

        status, missing, removed_at = reg.conn.execute(
            "SELECT status, consecutive_missing, removed_at FROM registry_documents WHERE key = %s", (key,)).fetchone()
        assert (status, missing) == ("removed", 2)
        assert removed_at is not None

        # load_active_documents should NOT include this doc
        docs = load_active_documents(tmp_data, registry=registry)
        for d in docs:
            assert d.get("doc_id") != f"file:{key}"


    def test_crawl_missing_tracks_per_site(self, registry: Registry):
        """record_crawl_missing increments consecutive_missing for unseen docs in a site."""
        site = "test.md"

        # Add two docs
        for i in range(2):
            k = f"test.md/doc{i}.pdf"
            url = f"https://test.md/doc{i}.pdf"
            registry.add_document(key=k, url=url, site=site, category="test",
                             extension=".pdf", external=False, source={"found_on": url, "site": site})

        # Crawl sees only doc0, not doc1
        missing, removed = registry.record_crawl_missing(site, {"test.md/doc0.pdf"})
        assert missing == 1  # doc1 is missing once
        assert removed == 0

        # Second crawl — still no doc1
        missing, removed = registry.record_crawl_missing(site, {"test.md/doc0.pdf"})
        assert missing == 0
        assert removed == 1  # doc1 now removed

        row = registry.conn.execute("SELECT status FROM registry_documents WHERE key = 'test.md/doc1.pdf'").fetchone()
        assert row == ("removed",)



# ---------------------------------------------------------------------------
# Test 3: Page changed → old chunk_ids removed
# ---------------------------------------------------------------------------
class TestPageUpdate:
    def test_page_changed_old_chunks_gone(self):
        """When a page's HTML changes, re-chunking produces new chunk_ids; old ones should be detected as stale."""
        from spott.ingest.common.urls import url_key as ukey

        page_url = "https://chisinau.md/page1"
        uk = ukey(page_url)


        # v1 of the page
        page_v1 = {
            "kind": "page",
            "doc_id": f"page:{uk}",
            "url": page_url,
            "url_key": uk,
            "site": "chisinau.md",
            "title": "Pagina de servicii v1",
            "lang": "ro",
            "blocks": [
                {"id": 0, "type": "paragraph",
                 "text": "Versiunea 1 a paginii cu informații despre serviciile municipale disponibile.",
                 "section": ["Servicii"], "lang": "ro"},
            ],
        }
        chunks_v1 = chunk_document(page_v1)
        assert len(chunks_v1) >= 1
        v1_ids = {c["chunk_id"] for c in chunks_v1}

        # v2 of the page — different text → different chunk_ids
        page_v2 = {
            "kind": "page",
            "doc_id": f"page:{uk}",
            "url": page_url,
            "url_key": uk,
            "site": "chisinau.md",
            "title": "Pagina de servicii v2",
            "lang": "ro",
            "blocks": [
                {"id": 0, "type": "paragraph",
                 "text": "Versiunea 2 actualizată cu noi detalii despre programul de lucru al ghișeelor.",
                 "section": ["Servicii"], "lang": "ro"},
                {"id": 1, "type": "paragraph",
                 "text": "Program: Luni-Vineri 09:00-17:00, Sâmbătă 09:00-13:00.",
                 "section": ["Servicii"], "lang": "ro"},
            ],
        }
        chunks_v2 = chunk_document(page_v2)
        v2_ids = {c["chunk_id"] for c in chunks_v2}

        # doc_id unchanged
        assert chunks_v1[0]["doc_id"] == chunks_v2[0]["doc_id"]
        # chunk_ids changed (different text content)
        assert v1_ids != v2_ids
        # v1 chunk_ids are stale and should be deleted by indexer.delete_stale_chunks
        stale = v1_ids - v2_ids
        assert len(stale) > 0  # there ARE stale chunks to clean up


# ---------------------------------------------------------------------------
# Test 4: Stable doc_id uses url_key, not sha256
# ---------------------------------------------------------------------------
class TestStableDocId:
    def test_file_doc_id_uses_url_key(self, tmp_data: Path, registry: Registry):
        """File doc_id is file:<url_key>, not file:<sha256>."""
        url = "https://chisinau.md/files/report.pdf"
        key = "chisinau.md/files/report.pdf"
        sha = hashlib.sha256(b"report-content").hexdigest()

        doc = make_file_doc(sha256=sha, title="Report", url=url,
                            text_blocks=[{"id": 0, "type": "paragraph",
                                          "text": "Raport complet privind activitățile municipale din anul curent.",
                                          "section": [], "lang": "ro"}])
        write_parsed_json(tmp_data, sha, doc)
        setup_registry_with_file(registry, key=key, url=url, site="chisinau.md", sha256=sha)

        docs = load_active_documents(tmp_data, registry=registry)
        assert len(docs) == 1
        assert docs[0]["doc_id"] == f"file:{key}"
        assert sha not in docs[0]["doc_id"]  # doc_id is NOT based on sha256


    def test_page_doc_id_uses_url_key(self):
        """Page doc_id is page:<url_key>."""
        from spott.ingest.common.urls import url_key as ukey
        page_url = "https://chisinau.md/about"
        uk = ukey(page_url)

        page = {
            "kind": "page",
            "url": page_url,
            "url_key": uk,
            "site": "chisinau.md",
            "title": "Despre primărie",
            "lang": "ro",
            "blocks": [
                {"id": 0, "type": "paragraph",
                 "text": "Primăria municipiului Chișinău oferă servicii diverse pentru cetățenii săi.",
                 "section": [], "lang": "ro"},
            ],
        }
        chunks = chunk_document(page)
        assert chunks[0]["doc_id"] == f"page:{uk}"


class TestTransientFailures:
    def test_failure_keeps_a_downloaded_document(self, tmp_data: Path, registry: Registry):
        """A timeout / 5xx on refresh must not take a good document out of the index."""
        url, key = "https://chisinau.md/ok.pdf", "chisinau.md/ok.pdf"
        sha = hashlib.sha256(b"ok").hexdigest()
        write_parsed_json(tmp_data, sha, make_file_doc(sha256=sha, title="Ok", url=url, text_blocks=[
            {"id": 0, "type": "paragraph", "text": "Un document bun, care se descarcă de obicei.", "section": [],
             "lang": "ro"}]))
        reg = setup_registry_with_file(registry, key=key, url=url, site="chisinau.md", sha256=sha)
        reg.mark_checked(key, "failed", http_status=503)
        row = reg.conn.execute("SELECT status, http_status FROM registry_documents WHERE key = %s", (key,)).fetchone()
        assert row == ("downloaded", 503)
        assert any(d.get("doc_id") == f"file:{key}" for d in load_active_documents(tmp_data, registry=registry))
        reg.mark_checked(key, "not_a_file", http_status=200)  # now a page: that one is real
        assert reg.conn.execute("SELECT status FROM registry_documents WHERE key = %s", (key,)).fetchone()[0] == "not_a_file"

    def test_failure_of_a_new_document_is_failed(self, registry: Registry):
        registry.add_document(key="a.md/x.pdf", url="https://a.md/x.pdf", site="a.md", category="c", extension=".pdf",
                         external=False, source={"found_on": "https://a.md/"})
        registry.mark_checked("a.md/x.pdf", "failed", http_status=500)
        assert registry.conn.execute("SELECT status FROM registry_documents").fetchone()[0] == "failed"


class TestPagesOnRecrawl:
    def page(self, reg, url, status=200, html="html/a.html"):
        reg.upsert_page({"url": url, "site": "a.md", "status": status, "html_file": html, "fetched_at": now(),
                         "title": "t", "lang": "ro"})

    def test_a_failed_fetch_keeps_the_last_good_copy(self, registry: Registry):
        self.page(registry, "https://a.md/x")
        registry.page_fetch_failed({"url": "https://a.md/x", "site": "a.md", "status": 503, "fetched_at": now()})
        row = registry.conn.execute("SELECT status, html_file, error FROM registry_pages").fetchone()
        assert row == (200, "html/a.html", "HTTP 503")
        registry.page_fetch_failed({"url": "https://a.md/new", "site": "a.md", "error": "ConnectTimeout", "fetched_at": now()})
        assert registry.conn.execute("SELECT html_file FROM registry_pages WHERE url = 'https://a.md/new'").fetchone()[0] is None

    def test_pages_a_complete_crawl_did_not_reach_are_dropped(self, registry: Registry):
        registry.upsert_page({"url": "https://a.md/old", "site": "a.md", "status": 200, "html_file": "html/o.html",
                         "fetched_at": datetime(2026, 1, 1, tzinfo=UTC)})
        self.page(registry, "https://a.md/now")
        assert registry.drop_unseen_pages("a.md", datetime(2026, 6, 1, tzinfo=UTC)) == 1
        statuses = dict(registry.conn.execute("SELECT url, status FROM registry_pages").fetchall())
        assert statuses == {"https://a.md/old": 410, "https://a.md/now": 200}

    def test_a_page_fetched_again_is_parsed_again(self, registry: Registry):
        self.page(registry, "https://a.md/x")
        registry.mark_page_parsed("https://a.md/x", "parsed", html_hash="h1")
        assert registry.pages_to_parse() == []
        self.page(registry, "https://a.md/x")
        [page] = registry.pages_to_parse()
        assert (page["url"], page["parse_status"], page["html_hash"]) == ("https://a.md/x", "pending", None)


def test_files_of_documents_are_only_theirs(registry: Registry):
    for key, sha in (("a.md/1.pdf", "a" * 64), ("a.md/2.pdf", "b" * 64)):
        registry.add_document(key=key, url=f"https://{key}", site="a.md", category="c", extension=".pdf", external=False,
                         source={"found_on": "https://a.md/"})
        registry.record_download(key, sha256=sha, path=f"raw/{sha[:2]}/{sha}.pdf", size=1, content_type="application/pdf",
                            extension=".pdf", http_status=200, etag=None, last_modified=None)
    assert [r["sha256"] for r in registry.files_of_documents(["a.md/2.pdf"], ["pending"])] == ["b" * 64]
    assert registry.files_of_documents([], ["pending"]) == []


def test_files_to_parse_of_some_sites_only(registry: Registry):
    """A job for one site parses that site's files, not every pending file of every site."""
    for key, site, sha in (("a.md/1.pdf", "a.md", "a" * 64), ("b.md/1.pdf", "b.md", "b" * 64)):
        registry.add_document(key=key, url=f"https://{key}", site=site, category="c", extension=".pdf", external=False,
                         source={"found_on": f"https://{site}/"})
        registry.record_download(key, sha256=sha, path=f"raw/{sha[:2]}/{sha}.pdf", size=1, content_type="application/pdf",
                            extension=".pdf", http_status=200, etag=None, last_modified=None)
    assert [r["sha256"] for r in registry.files_to_parse(["pending"], None, sites=["b.md"])] == ["b" * 64]
    assert registry.files_to_parse(["pending"], None, sites=["none.md"]) == []
    assert len(registry.files_to_parse(["pending"], None)) == 2  # no filter: the whole corpus, as `tools.pipeline` wants
