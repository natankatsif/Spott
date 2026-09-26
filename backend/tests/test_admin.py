"""Admin API (docs/tasks/09): login, sources with robots.txt and document checks, jobs, ratings — no database,
no network (in-memory store, fake city hall sites)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import admin, main
from app.admin import Duplicate

LOGIN = {"login": "admin", "password": "correct horse battery"}
TOKEN: dict[str, str] = {}
ROBOTS = {
    "acc.md": "User-agent: *\nAllow: /\n",
    "chisinau.md": "User-agent: *\nDisallow: /\n",
}


def fake_site(request: httpx.Request) -> httpx.Response:
    host = request.url.host.removeprefix("www.")
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS[host]) if host in ROBOTS else httpx.Response(404)
    if request.url.path.endswith(".pdf"):
        return httpx.Response(200, headers={"content-type": "application/pdf"})
    return httpx.Response(200, headers={"content-type": "text/html"})


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
        return s | {"chunks": 0, "last_job": self.jobs.get(s.get("last_job_id"))}

    def add_source(self, row):
        key = (row["kind"], row["site_id"] if row["kind"] == "site" else row["url"])
        if any((s["kind"], s["site_id"] if s["kind"] == "site" else s["url"]) == key for s in self.sources.values()):
            raise Duplicate
        sid = len(self.sources) + 1
        self.sources[sid] = row | {"id": sid, "enabled": True, "created_at": "2026-09-26T17:00:00+03:00",
                                   "last_job_id": None}
        return self.get_source(sid)

    def patch_source(self, source_id, fields):
        if source_id not in self.sources:
            return None
        self.sources[source_id] |= fields
        return self.get_source(source_id)

    def delete_source(self, source_id, purge):
        return self.sources.pop(source_id, None) is not None

    def create_job(self, source_id, kind):
        jid = len(self.jobs) + 1
        self.jobs[jid] = {"id": jid, "source_id": source_id, "kind": kind, "status": "queued", "stage": None,
                          "stage_done": 0, "stage_total": 0, "percent": 0.0, "eta_s": None, "started_at": None,
                          "finished_at": None, "stats": {}, "log_tail": [], "error": None}
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

    def feedback(self, max_rating, limit):
        return [r for r in self.ratings if r["rating"] <= max_rating][:limit]

    def feedback_stats(self):
        return {"count": len(self.ratings), "average": None, "per_star": {str(n): 0 for n in range(1, 6)},
                "top_tags": [], "by_day": []}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_LOGIN", LOGIN["login"])
    monkeypatch.setenv("ADMIN_PASSWORD", LOGIN["password"])
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    admin.login_limiter.hits.clear()
    main.app.state.admin = MemoryAdmin()
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


def test_add_a_site_and_start_its_crawl(client):
    r = client.post("/api/admin/sources", headers=TOKEN,
                    json={"kind": "site", "url": "https://www.acc.md/ro/", "category": "agency", "start": True})
    assert r.status_code == 201
    source = r.json()
    assert (source["site_id"], source["robots"], source["start_urls"]) == ("acc.md", "allowed", ["https://www.acc.md/ro/"])
    assert source["last_job"]["status"] == "queued"

    r = client.post(f"/api/admin/sources/{source['id']}/jobs", headers=TOKEN, json={"kind": "refresh"})
    assert r.status_code == 409  # the first job is still queued

    job_id = source["last_job"]["id"]
    assert client.post(f"/api/admin/jobs/{job_id}/cancel", headers=TOKEN).json()["status"] == "cancelled"
    assert client.get("/api/admin/jobs", headers=TOKEN, params={"status": "cancelled"}).json()["jobs"][0]["id"] == job_id


def test_duplicate_domain_is_rejected(client):
    client.post("/api/admin/sources", headers=TOKEN, json={"kind": "site", "url": "https://acc.md/"})
    r = client.post("/api/admin/sources", headers=TOKEN, json={"kind": "site", "url": "https://www.acc.md/despre"})
    assert r.status_code == 409 and r.json()["error"] == "conflict"


def test_robots_blocked_site_is_kept_but_never_crawled(client):
    r = client.post("/api/admin/sources", headers=TOKEN, json={"kind": "site", "url": "https://chisinau.md/", "start": True})
    source = r.json()
    assert (r.status_code, source["robots"], source["last_job"]) == (201, "blocked", None)
    r = client.post(f"/api/admin/sources/{source['id']}/jobs", headers=TOKEN, json={"kind": "crawl"})
    assert r.status_code == 409 and "robots.txt" in r.json()["message"]


def test_document_source_must_be_a_document(client):
    ok = client.post("/api/admin/sources", headers=TOKEN, json={"kind": "document", "url": "https://acc.md/files/a.pdf"})
    assert ok.status_code == 201 and ok.json()["start_urls"] == []
    page = client.post("/api/admin/sources", headers=TOKEN, json={"kind": "document", "url": "https://acc.md/despre"})
    assert page.status_code == 422
    ftp = client.post("/api/admin/sources", headers=TOKEN, json={"kind": "site", "url": "ftp://acc.md/"})
    assert ftp.status_code == 422


def test_patch_and_delete(client):
    source = client.post("/api/admin/sources", headers=TOKEN, json={"kind": "site", "url": "https://acc.md/"}).json()
    r = client.patch(f"/api/admin/sources/{source['id']}", headers=TOKEN, json={"enabled": False, "max_pages": 100})
    assert (r.json()["enabled"], r.json()["max_pages"]) == (False, 100)
    assert client.post(f"/api/admin/sources/{source['id']}/jobs", headers=TOKEN, json={"kind": "crawl"}).status_code == 409
    assert client.delete(f"/api/admin/sources/{source['id']}", headers=TOKEN, params={"purge": True}).json() == {"ok": True}
    assert client.delete(f"/api/admin/sources/{source['id']}", headers=TOKEN).status_code == 404
