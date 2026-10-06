"""Admin API (docs/tasks/09, 11): login, sources added by URL (robots.txt, kind, category by rules, merge into a
domain), the one-call list, jobs, ratings — no database, no network (in-memory store, fake city hall sites)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import admin, llm_settings, main
from app.admin import Duplicate

LOGIN = {"login": "admin", "password": "correct horse battery"}
TOKEN: dict[str, str] = {}
ROBOTS = {
    "acc.md": "User-agent: *\nAllow: /\n",
    "chisinau.md": "User-agent: *\nDisallow: /\n",
}
PAGES = {  # <title> of fake home pages
    "acc.md": "Apă-Canal Chișinău",
    "scoala-noua.md": "Liceul Teoretic nr. 5",
    "exemplu.md": "Buna ziua",
    "spital-nou.md": "Spitalul Municipal",
}
FETCHED: list[str] = []


def fake_site(request: httpx.Request) -> httpx.Response:
    FETCHED.append(str(request.url))
    host = request.url.host.removeprefix("www.")
    path = request.url.path
    if host == "mort.md":
        raise httpx.ConnectError("no route")
    if path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS[host]) if host in ROBOTS else httpx.Response(404)
    if path.endswith(".pdf") or path == "/download/act":
        return httpx.Response(200, headers={"content-type": "application/pdf"})
    if path.endswith(".docx"):  # a document served with a wrong content type
        return httpx.Response(200, headers={"content-type": "application/octet-stream"})
    title = PAGES.get(host, "Pagina")
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                          text=f"<html><head><title>{title}</title></head><body>…</body></html>")


class MemoryAdmin:
    def __init__(self):
        self.sources: dict[int, dict] = {}
        self.jobs: dict[int, dict] = {}
        self.ratings: list[dict] = []

    def list_sources(self):
        return [self.get_source(i) for i in sorted(self.sources)]

    def get_source(self, source_id):
        s = self.sources.get(source_id)
        if s is None:
            return None
        return s | {"chunks": s.get("chunks", 0), "lines": 0, "pages": 0, "documents_found": 0,
                    "documents_downloaded": 0, "last_crawled": None, "last_job": self.jobs.get(s.get("last_job_id"))}

    def find_by_site(self, site_id):
        return next((self.get_source(i) for i, s in self.sources.items() if s["site_id"] == site_id), None)

    def add_source(self, row):
        if self.find_by_site(row["site_id"]):
            raise Duplicate
        sid = len(self.sources) + 1
        self.sources[sid] = row | {"id": sid, "enabled": True, "created_at": "2026-09-26T17:00:00+03:00",
                                   "last_job_id": None}
        return self.get_source(sid)

    def merge_url(self, source_id, url):
        urls = self.sources[source_id]["start_urls"]
        if url not in urls:
            urls.append(url)
        return self.get_source(source_id)

    def totals(self):
        return {"sites_total": len(self.sources), "sites_indexed": 0, "pages": 0, "documents_found": 0,
                "documents_downloaded": 0, "chunks": 0, "lines": 0, "documents_replaced": 0, "documents_removed": 0}

    def patch_source(self, source_id, fields):
        if source_id not in self.sources:
            return None
        self.sources[source_id] |= fields
        return self.get_source(source_id)

    def delete_source(self, source_id, purge):
        return self.sources.pop(source_id, None) is not None

    def create_job(self, source_id, kind, url=None):
        jid = len(self.jobs) + 1
        self.jobs[jid] = {"id": jid, "source_id": source_id, "kind": kind, "url": url, "status": "queued",
                          "stage": None, "stage_done": 0, "stage_total": 0, "percent": 0.0, "eta_s": None,
                          "started_at": None, "finished_at": None, "stats": {}, "log_tail": [], "error": None}
        if source_id is not None:
            self.sources[source_id]["last_job_id"] = jid
        return self.jobs[jid]

    def list_jobs(self, status):
        return [j for j in self.jobs.values() if status in (None, j["status"])]

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def cancel_job(self, job_id):
        job = self.jobs.get(job_id)
        if job and job["status"] == "queued":
            job["status"] = "cancelled"
        return job

    def retry_job(self, job_id):
        job = self.jobs.get(job_id)
        return self.create_job(job["source_id"], job["kind"], job["url"]) if job else None

    def delete_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job["status"] in ("queued", "running"):
            return False
        del self.jobs[job_id]
        return True

    def clear_jobs(self):
        done = [i for i, j in self.jobs.items() if j["status"] not in ("queued", "running")]
        for i in done:
            del self.jobs[i]
        return len(done)

    def feedback(self, max_rating, limit):
        return [r for r in self.ratings if r["rating"] <= max_rating][:limit]

    def feedback_stats(self):
        return {"count": len(self.ratings), "average": None, "per_star": {str(n): 0 for n in range(1, 6)},
                "top_tags": [], "by_day": []}


def no_llm(*a, **kw):
    raise AssertionError("adding a source must not create an LLM client")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_LOGIN", LOGIN["login"])
    monkeypatch.setenv("ADMIN_PASSWORD", LOGIN["password"])
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    admin.login_limiter.hits.clear()
    main.app.state.admin = MemoryAdmin()
    FETCHED.clear()
    monkeypatch.setattr(llm_settings, "RoutedLLM", no_llm)
    main.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(fake_site))
    c = TestClient(main.app)
    TOKEN["Authorization"] = "Bearer " + c.post("/api/admin/login", json=LOGIN).json()["token"]
    yield c
    main.app.state.admin = None


def test_login_gives_a_session_token(client):
    r = client.post("/api/admin/login", json=LOGIN)
    session = r.json()
    assert r.status_code == 200 and session["login"] == "admin"
    assert datetime.fromisoformat(session["expires_at"]) > datetime.now(UTC) + timedelta(hours=11)
    assert client.get("/api/admin/me", headers={"Authorization": f"Bearer {session['token']}"}).json() == {"login": "admin"}


def test_admin_needs_a_valid_session(client, monkeypatch):
    assert client.post("/api/admin/login", json=LOGIN | {"password": "wrong"}).status_code == 401
    token = TOKEN["Authorization"].removeprefix("Bearer ")
    payload, signature = token.split(".")
    expired = admin.make_token("admin", datetime.now(UTC) - timedelta(minutes=1))
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Bearer {payload}.{signature[:-2]}xx"},
                    {"Authorization": f"Bearer {expired}"}):
        r = client.get("/api/admin/sources", headers=headers)
        assert r.status_code == 401 and r.json()["error"] == "unauthorized"
    monkeypatch.setenv("ADMIN_PASSWORD", "changed")  # a new password ends every session
    assert client.get("/api/admin/me", headers=TOKEN).status_code == 401
    monkeypatch.delenv("ADMIN_LOGIN")  # no credentials on the server: the admin is off
    assert client.post("/api/admin/login", json=LOGIN).status_code == 401


def test_login_attempts_are_limited(client):
    codes = [client.post("/api/admin/login", json=LOGIN | {"password": "guess"}).status_code for _ in range(6)]
    assert codes[-1] == 429  # the fixture's login + 4 wrong ones use up 5 a minute


def add(client, url, **extra):
    return client.post("/api/admin/sources", headers=TOKEN, json={"url": url} | extra)


def test_site_root_gets_a_category_and_a_crawl(client):
    r = add(client, "www.acc.md/")
    body = r.json()
    assert r.status_code == 201 and body["merged_into"] is None
    assert (body["kind"], body["site_id"], body["url"], body["title"]) == ("site", "acc.md", "https://www.acc.md/",
                                                                           "Apă-Canal Chișinău")
    # No domain rule matches acc.md; "Apă" in its title does.
    assert (body["category"], body["category_source"], body["max_depth"]) == ("urban_utilities", "keywords", 4)
    assert body["status"] == "queued" and body["progress"]["percent"] == 0
    assert body["detected"]["reason"] == ("Detected a website (urban_utilities, by title keywords); "
                                          "crawling up to depth 4.")
    assert add(client, "https://acc.md").status_code == 409  # the same site root again


def test_category_by_title_keywords_and_default(client):
    body = add(client, "https://scoala-noua.md/").json()
    assert (body["category"], body["category_source"]) == ("education", "rule")  # "scoal" in the domain
    body = add(client, "https://spital-nou.md/").json()
    assert (body["category"], body["category_source"]) == ("healthcare", "rule")
    PAGES["exemplu2.md"] = "Liceul Teoretic nr. 5"
    body = add(client, "https://exemplu2.md/").json()
    assert (body["category"], body["category_source"]) == ("education", "keywords")
    body = add(client, "https://exemplu.md/").json()
    assert (body["category"], body["category_source"]) == ("other", "default")


def test_documents_by_content_type_or_extension(client):
    by_type = add(client, "https://docs-a.md/download/act").json()
    assert (by_type["kind"], by_type["start_urls"], by_type["max_depth"]) == ("document", [], None)
    assert by_type["detected"]["reason"].endswith("downloading, parsing and indexing it.")
    by_extension = add(client, "https://docs-b.md/files/regulament.docx").json()
    assert by_extension["kind"] == "document"  # served as octet-stream, the extension decides
    assert add(client, "https://docs-a.md/download/act").status_code == 409  # the same document again


def test_deeper_path_and_documents_go_into_the_domains_source(client):
    root = add(client, "https://acc.md/").json()
    r = add(client, "https://acc.md/ro/servicii/")
    body = r.json()
    assert r.status_code == 200 and body["merged_into"] == root["id"]
    assert body["start_urls"] == ["https://acc.md/", "https://acc.md/ro/servicii"]
    assert body["detected"]["crawl_depth"] == 2 and "/ro/servicii" in body["detected"]["reason"]
    job = main.app.state.admin.jobs[body["last_job"]["id"]]
    assert job["url"] == "https://acc.md/ro/servicii"  # only that path is crawled
    doc = add(client, "https://acc.md/files/tarife.pdf")
    assert doc.status_code == 200 and doc.json()["merged_into"] == root["id"]
    assert len(main.app.state.admin.sources) == 1  # never a second row for a domain
    assert add(client, "https://acc.md/ro/servicii").status_code == 409


def test_robots_blocked_site_is_saved_without_fetching_or_crawling(client):
    r = add(client, "https://www.chisinau.md/")
    body = r.json()
    assert r.status_code == 201 and (body["robots"], body["status"], body["last_job"]) == ("blocked", "blocked", None)
    assert "forbids crawling" in body["detected"]["reason"]
    assert FETCHED == []  # chisinau.md is on the excluded list: not even robots.txt is fetched
    ROBOTS["private.md"] = "User-agent: *\nDisallow: /\n"
    body = add(client, "https://private.md/").json()
    assert body["robots"] == "blocked" and FETCHED == ["https://private.md/robots.txt"]  # the page itself never
    deeper = add(client, "https://private.md/ro/acte")
    assert deeper.status_code == 200 and deeper.json()["last_job"] is None  # saved into it, still no crawl
    r = client.post(f"/api/admin/sources/{body['id']}/jobs", headers=TOKEN, json={"kind": "crawl"})
    assert r.status_code == 409 and "robots.txt" in r.json()["message"]


def test_unreachable_or_bad_links_are_422(client):
    assert add(client, "https://mort.md/").status_code == 422
    assert add(client, "ftp://acc.md/").status_code == 422
    assert add(client, "not a link").status_code == 422


def test_one_list_call_has_status_progress_and_totals(client):
    body = add(client, "https://acc.md/").json()
    job = main.app.state.admin.jobs[body["last_job"]["id"]]
    job |= {"status": "running", "stage": "parse", "percent": 63.0, "eta_s": 120.0,
            "log_tail": ["[7/12] parsed raw/ab/regulament.pdf 1.2s", "[8/12] parsed (ocr) raw/cd/tarife.pdf 9.0s"]}
    listed = client.get("/api/admin/sources", headers=TOKEN).json()
    row = listed["sources"][0]
    # `current`: the file the stage is on right now, so the row says more than "parse 63%"
    assert row["status"] == "running" and row["progress"] == {"job_id": job["id"], "stage": "parse",
                                                              "percent": 63.0, "eta_s": 120.0,
                                                              "current": "parsed tarife.pdf"}
    assert listed["totals"]["sites_total"] == 1
    job |= {"status": "failed", "error": "downloader exited with code 1"}
    row = client.get("/api/admin/sources", headers=TOKEN).json()["sources"][0]
    assert (row["status"], row["progress"], row["last_error"]) == ("failed", None, "downloader exited with code 1")


def test_status_precedence():
    base = {"enabled": True, "robots": "allowed", "chunks": 5, "last_job": None}
    assert admin.status_of(base) == "indexed"
    assert admin.status_of(base | {"chunks": 0}) == "pending"
    assert admin.status_of(base | {"last_job": {"status": "failed"}}) == "failed"
    assert admin.status_of(base | {"last_job": {"status": "queued"}}) == "queued"
    assert admin.status_of(base | {"robots": "blocked", "last_job": {"status": "running"}}) == "blocked"
    assert admin.status_of(base | {"enabled": False, "robots": "blocked"}) == "disabled"


def test_patch_and_delete(client):
    source = add(client, "https://acc.md/", start=False).json()
    assert source["status"] == "pending"
    r = client.patch(f"/api/admin/sources/{source['id']}", headers=TOKEN, json={"enabled": False, "max_pages": 100})
    assert (r.json()["enabled"], r.json()["max_pages"], r.json()["status"]) == (False, 100, "disabled")
    assert client.post(f"/api/admin/sources/{source['id']}/jobs", headers=TOKEN, json={"kind": "crawl"}).status_code == 409
    assert client.delete(f"/api/admin/sources/{source['id']}", headers=TOKEN, params={"purge": True}).json() == {"ok": True}
    assert client.delete(f"/api/admin/sources/{source['id']}", headers=TOKEN).status_code == 404


def test_jobs_can_be_retried_deleted_and_cleared(client):
    body = add(client, "https://acc.md/").json()
    jid = body["last_job"]["id"]
    assert client.post(f"/api/admin/jobs/{jid}/retry", headers=TOKEN).status_code == 409  # still queued
    assert client.delete(f"/api/admin/jobs/{jid}", headers=TOKEN).status_code == 409  # stop it first
    main.app.state.admin.jobs[jid]["status"] = "failed"
    r = client.post(f"/api/admin/jobs/{jid}/retry", headers=TOKEN)
    assert r.status_code == 201 and r.json()["id"] != jid and r.json()["kind"] == "crawl"
    main.app.state.admin.jobs[r.json()["id"]]["status"] = "done"
    assert client.delete(f"/api/admin/jobs/{jid}", headers=TOKEN).json() == {"ok": True}
    assert client.delete(f"/api/admin/jobs/{jid}", headers=TOKEN).status_code == 404
    assert client.delete("/api/admin/jobs", headers=TOKEN).json() == {"deleted": 1}
    assert client.get("/api/admin/jobs", headers=TOKEN).json()["jobs"] == []
