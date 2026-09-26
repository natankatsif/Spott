"""Demo data for the admin page in mock mode (docs/tasks/11 B4, D): frontend/src/lib/mocks/admin/
sources.json, add-source-*.json, gaps.json, gap-recheck.json, gap-hide.json.

Built offline, no database: the 40 sources from sites.toml (same seed and category rules as the backend), their
counters from the corpus-stats mock (a snapshot of the real index), lines spread by chunks. Then a few rows edited
for the demo: one running (63 %, parse), one failed, one document added by URL. Gaps are hand-written realistic
questions, shaped like app/gaps.py builds them (a group re-checked as answered is not listed). To refresh sources.json from a real DB instead, save `GET /api/admin/sources` over it and re-apply the
three edits (see edit_for_demo). Deterministic: same input → same files.

    uv run python backend/scripts/make_admin_mocks.py          (from the repo root)
"""

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages" / "retrieval" / "src")]

from retrieval.sources import DEFAULTS, EXCLUDED_SITES  # noqa: E402

MOCKS = ROOT / "frontend" / "src" / "lib" / "mocks"
OUT = MOCKS / "admin"
TOML = ROOT / "offline_indexation" / "data" / "sources" / "sites.toml"
T0 = "2026-09-26T{}+03:00"


def ts(hm: str) -> str:
    return T0.format(hm + ":00")


def job(job_id: int, source_id: int, status: str, stage: str | None, percent: float, stats: dict, *,
        started: str, finished: str | None, eta: float | None = None, error: str | None = None,
        log: list[str] | None = None, done: int = 0, total: int = 0) -> dict:
    return {"id": job_id, "source_id": source_id, "kind": "crawl", "status": status, "stage": stage,
            "stage_done": done, "stage_total": total, "percent": percent, "eta_s": eta, "started_at": started,
            "finished_at": finished, "stats": stats, "log_tail": log or [], "error": error}


def rows() -> tuple[list[dict], dict]:
    data = tomllib.loads(TOML.read_text(encoding="utf-8"))
    defaults = DEFAULTS | data.get("defaults", {})
    stats = json.loads((MOCKS / "corpus-stats.json").read_text(encoding="utf-8"))
    by_site = {s["site"]: s for s in stats["sites"]}
    totals = stats["totals"]
    per_chunk = totals["lines"] / max(totals["chunks"], 1)
    out = []
    for i, s in enumerate(data["site"], start=1):
        s = defaults | s
        c = by_site.get(s["id"], {})
        blocked = s["id"] in EXCLUDED_SITES
        chunks = c.get("chunks", 0)
        indexed = chunks > 0
        last_job = None
        if indexed:
            last_job = job(100 + i, i, "done", "index", 100.0, {
                "pages": c["pages"], "documents_found": c["documents_found"],
                "documents_downloaded": c["documents_downloaded"], "files_parsed": c["documents_downloaded"],
                "chunks": chunks, "lines": round(chunks * per_chunk), "errors": 0},
                started=ts("09:%02d" % (i % 60)), finished=ts("10:%02d" % (i % 60)),
                log=["Indexing finished:", f"  Chunks: {chunks}"])
        out.append({
            "id": i, "kind": "site", "url": s["start_urls"][0], "site_id": s["id"], "title": None,
            "category": s.get("category"), "category_source": "toml", "start_urls": s["start_urls"],
            "max_depth": s["max_depth"], "max_pages": s["max_pages"], "enabled": True,
            "robots": "blocked" if blocked else "allowed",
            "status": "blocked" if blocked else "indexed" if indexed else "pending",
            "pages": c.get("pages", 0), "documents_found": c.get("documents_found", 0),
            "documents_downloaded": c.get("documents_downloaded", 0), "chunks": chunks,
            "lines": round(chunks * per_chunk), "last_crawled": c.get("last_crawled"), "progress": None,
            "last_error": None, "created_at": ts("08:00"), "last_job": last_job})
    return out, totals


def edit_for_demo(sources: list[dict]) -> None:
    """One running crawl, one failed crawl, one document added by URL."""
    by = {s["site_id"]: s for s in sources}
    run = by["mobilitatechisinau.md"]
    run["status"] = "running"
    run["progress"] = {"job_id": 201, "stage": "parse", "percent": 63.0, "eta_s": 95.0}
    run["last_job"] = job(201, run["id"], "running", "parse", 63.0, {
        "pages": 212, "documents_found": 41, "documents_downloaded": 41, "files_parsed": 17, "chunks": 0,
        "lines": 0, "errors": 0}, started=ts("11:40"), finished=None, eta=95.0, done=17, total=41,
        log=["parse 17/41: regulament-parcare-2024.pdf", "parse 16/41: harta-traseelor.pdf"])
    run["pages"], run["documents_found"], run["documents_downloaded"] = 212, 41, 41

    bad = by["liftservice.md"]
    bad["status"] = "failed"
    bad["last_error"] = "crawl: https://liftservice.md/ timed out 3 times (ConnectTimeout)"
    bad["last_job"] = job(202, bad["id"], "failed", "crawl", 4.0, {"pages": 0, "errors": 3},
                          started=ts("11:12"), finished=ts("11:13"), error=bad["last_error"],
                          log=["GET https://liftservice.md/ → ConnectTimeout (3/3)"])

    doc_id = len(sources) + 1
    sources.append({
        "id": doc_id, "kind": "document",
        "url": "https://ansp.gov.md/sites/default/files/ghid-demo-autorizare.pdf", "site_id": "ansp.gov.md",
        "title": "Ghid privind autorizarea sanitară", "category": "healthcare", "category_source": "keywords",
        "start_urls": [], "max_depth": 0, "max_pages": 1, "enabled": True, "robots": "allowed",
        "status": "indexed", "pages": 0, "documents_found": 1, "documents_downloaded": 1, "chunks": 14,
        "lines": 61, "last_crawled": ts("11:31"), "progress": None, "last_error": None, "created_at": ts("11:30"),
        "last_job": job(203, doc_id, "done", "index", 100.0, {
            "pages": 0, "documents_found": 1, "documents_downloaded": 1, "files_parsed": 1, "chunks": 14,
            "lines": 61, "errors": 0}, started=ts("11:30"), finished=ts("11:31"))})


def added(row: dict, detected: dict, merged_into: int | None = None) -> dict:
    return row | {"detected": detected, "merged_into": merged_into}


def add_source_mocks(sources: list[dict]) -> dict[str, dict]:
    by = {s["site_id"]: s for s in sources}
    n = len(sources)
    new_site = {
        "id": n + 1, "kind": "site", "url": "https://www.anre.md/", "site_id": "anre.md",
        "title": "Agenția Națională pentru Reglementare în Energetică", "category": "other",
        "category_source": "default", "start_urls": ["https://www.anre.md/"], "max_depth": 4, "max_pages": 2000,
        "enabled": True, "robots": "allowed", "status": "queued", "pages": 0, "documents_found": 0,
        "documents_downloaded": 0, "chunks": 0, "lines": 0, "last_crawled": None,
        "progress": {"job_id": 301, "stage": None, "percent": 0.0, "eta_s": None}, "last_error": None,
        "created_at": ts("12:00"),
        "last_job": job(301, n + 1, "queued", None, 0.0, {}, started=None, finished=None)}
    site = added(new_site, {"kind": "site", "category": "other", "category_source": "default",
                            "title": new_site["title"], "crawl_depth": 4, "max_pages": 2000,
                            "reason": "Detected a website (other, no rule matched); crawling up to depth 4."})

    new_doc = dict(new_site, id=n + 2, kind="document", site_id="cnas.md",
                   url="https://cnas.md/files/ghid-asigurare-2026.pdf", title="Ghid asigurare medicală 2026",
                   category="healthcare", category_source="keywords", start_urls=[], max_depth=0, max_pages=1,
                   progress={"job_id": 302, "stage": None, "percent": 0.0, "eta_s": None},
                   last_job=job(302, n + 2, "queued", None, 0.0, {}, started=None, finished=None))
    document = added(new_doc, {"kind": "document", "category": "healthcare", "category_source": "keywords",
                               "title": new_doc["title"], "crawl_depth": 0, "max_pages": 1,
                               "reason": "Detected a document (healthcare, by title keywords); downloading, parsing "
                                         "and indexing it."})

    base = by["dgaurf.md"]
    path = "https://dgaurf.md/ro/transparenta/decizii"
    merged_row = dict(base, start_urls=[*base["start_urls"], path], status="queued",
                      progress={"job_id": 303, "stage": None, "percent": 0.0, "eta_s": None},
                      last_job=job(303, base["id"], "queued", None, 0.0, {}, started=None, finished=None))
    merged = added(merged_row, {"kind": "site", "category": base["category"], "category_source": "existing",
                                "title": "Decizii — DGAURF", "crawl_depth": 2, "max_pages": 500,
                                "reason": "Added the path /ro/transparenta/decizii to dgaurf.md; crawling it up to "
                                          "depth 2."}, merged_into=base["id"])

    blocked_row = dict(new_site, id=n + 3, url="https://actelocale.gov.md/", site_id="actelocale.gov.md",
                       title=None, category="transparency", category_source="rule",
                       start_urls=["https://actelocale.gov.md/"], robots="blocked", status="blocked",
                       progress=None, last_job=None)
    blocked = added(blocked_row, {"kind": "site", "category": "transparency", "category_source": "rule",
                                  "title": None, "crawl_depth": 4, "max_pages": 2000,
                                  "reason": "Detected a website (transparency, by domain rule); robots.txt of "
                                            "actelocale.gov.md forbids crawling: saved, no crawl."})
    unreachable = {"error": "validation_error", "message": "The site doesn't answer (ConnectTimeout)"}
    return {"add-source-site.json": site, "add-source-document.json": document,
            "add-source-merged.json": merged, "add-source-blocked.json": blocked,
            "add-source-unreachable.json": unreachable}


def group(gid: str, asked: list[tuple[str, str, str, str]], missing: list[str], hints: list[tuple[str, int]],
          rechecked: dict | None = None) -> dict:
    """A group as the backend builds it (app/gaps.py gap_item): questions oldest first, the first one is the
    example and the id, status = the worst, count = how many questions."""
    questions = [{"answer_id": f"{gid}_{i}" if i else gid, "question": text, "lang": lang, "status": status,
                  "ts": ts(hm)} for i, (text, lang, status, hm) in enumerate(asked)]
    worst = "not_found" if any(x["status"] == "not_found" for x in questions) else "partial"
    return {"id": gid, "example": questions[0]["question"], "questions": questions, "count": len(questions),
            "last_asked": max(x["ts"] for x in questions), "langs": sorted({x["lang"] for x in questions}),
            "status": worst, "missing": missing, "hint_sites": [{"site": s, "hits": h} for s, h in hints],
            "rechecked": rechecked, "hidden": False}


def gaps() -> dict:
    items = [
        group("ans_g1", [
            ("Cât costă o autorizație de construire pentru un garaj?", "ro", "partial", "09:05"),
            ("autorizatie construire garaj pret", "ro", "not_found", "09:48"),
            ("Сколько стоит разрешение на строительство гаража?", "ru", "partial", "10:20"),
            ("Care este taxa pentru autorizația de construire?", "ro", "partial", "11:02"),
            ("Разрешение на строительство: сколько платить и сколько ждать?", "ru", "partial", "11:52")],
            ["taxa pentru eliberarea autorizației", "termenul de eliberare"],
            [("dgaurf.md", 5), ("help.chisinau.md", 2)]),
        group("ans_g2", [
            ("Cum plătesc parcarea prin SMS?", "ro", "not_found", "09:40"),
            ("Где оплатить парковку в центре через SMS?", "ru", "not_found", "10:31"),
            ("Номер для оплаты парковки SMS", "ru", "not_found", "11:47")],
            [], [("mobilitatechisinau.md", 3)]),
        group("ans_g3", [
            ("Program de lucru al Direcției educație în weekend", "ro", "partial", "10:12"),
            ("Lucrează Direcția generală educație sâmbăta?", "ro", "partial", "11:30")],
            ["programul de sâmbătă"], [("chisinauedu.dgets.md", 2), ("detscentru.md", 1)]),
        group("ans_g4", [
            ("Как записать ребёнка в детский сад онлайн?", "ru", "partial", "10:10"),
            ("Какие документы нужны для записи в детский сад?", "ru", "partial", "10:58")],
            ["lista documentelor necesare"], [("egradinita.md", 2)],
            rechecked={"status": "partial", "verified": True, "answer_id": "ans_g4_re", "ts": ts("11:05")}),
        group("ans_g5", [
            ("Unde depun o plângere despre gunoiul neridicat?", "ro", "not_found", "10:41"),
            ("Куда жаловаться, если не вывозят мусор?", "ru", "not_found", "11:15")],
            [], [("autosalubritate.md", 2)]),
        group("ans_g6", [("Cine repară drumul pe strada mea? Tel •••", "ro", "not_found", "09:15")],
              [], [("proiecte.chisinau.md", 1), ("dglca.md", 1)]),
    ]
    rows = [x for g in items for x in g["questions"]]
    return {"items": items, "totals": {"not_found": sum(x["status"] == "not_found" for x in rows),
                                       "partial": sum(x["status"] == "partial" for x in rows),
                                       "groups": len(items)}}


def write(name: str, data: dict) -> None:
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", (OUT / name).relative_to(ROOT))


def main() -> None:
    sources, totals = rows()
    edit_for_demo(sources)
    doc = sources[-1]  # the document added by URL is a new domain in the index
    totals = totals | {"sites_total": totals["sites_total"] + 1, "sites_indexed": totals["sites_indexed"] + 1,
                       "documents_found": totals["documents_found"] + 1,
                       "documents_downloaded": totals["documents_downloaded"] + 1,
                       "chunks": totals["chunks"] + doc["chunks"], "lines": totals["lines"] + doc["lines"]}
    write("sources.json", {"sources": sources, "totals": totals})
    for name, data in add_source_mocks(sources).items():
        write(name, data)
    write("gaps.json", gaps())
    write("gap-recheck.json", {"status": "answered", "verified": True, "answer_id": "ans_g1_re", "ts": ts("12:03")})
    write("gap-hide.json", {"ok": True})


if __name__ == "__main__":
    main()
