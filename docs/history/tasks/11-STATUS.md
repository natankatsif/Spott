# Task 11 — status (WIP, branch `task-11-wip`)

## Done
**Part B (admin sources by URL)**
- `retrieval/sources.py`: seed from sites.toml + indexed sites (idempotent), category rules (domain → title/meta keywords → other), no LLM. Seed runs at backend startup, in `tools.sources import`, after `tools.index_io import`.
- Schema: `sources.title`, `sources.category_source`, `jobs.url`.
- `POST /api/admin/sources {url}`: normalize → robots.txt first (EXCLUDED_SITES never fetched) → HEAD/GET 8 s → kind (content-type/extension) → one row per domain (deeper path / document of a known domain → 200 `merged_into`, job with `url`) → category → crawl settings → job queued → `SourceAdded` with `detected.reason`.
- `GET /api/admin/sources`: `status` (precedence), counters (index + registry.sqlite), `progress`, `last_error`, `totals`.
- Crawler `--start-urls/--path-prefix`, worker plans a job `url` (deeper path crawl depth 2 under prefix, or one document).
- Tests: `backend/tests/test_admin.py` (12), `packages/retrieval/tests/test_sources.py` (seed 40 → 0 on restart, categories), worker plan test.

**Part D (gaps)**
- `answers.missing/retrieved_sites/gap_hidden/recheck`; `answer_events` passes `on_done(req, resp, info)`; `PgAnswers.record` stores them.
- `backend/app/gaps.py` (grouping by bge-m3 cosine ≥ 0.85, masking, hidden, solved), endpoints `GET /api/admin/gaps`, `POST /gaps/{id}/recheck|hide|unhide`; recheck = one model call (`app.state.ask_once`: no rewrite, no second pass).
- Tests: `backend/tests/test_gaps.py` (5).

**Part A (preview)**
- `GET /api/preview/{doc_id}?line=&lang=&embed=` (`backend/app/preview.py`): page = crawled copy or live fetch (5 s, 1 h cache), sanitized (selectolax), `<base>`, our nonce script; PDF = pdf.js viewer (vendored `backend/app/static/pdfjs`: pdf.js 6.3.289 + wasm decoders for scans + standard fonts), boxes + text layer; DOCX / unreachable page / page rendered by site JS → our text view. CSP with nonce, `frame-ancestors` = CORS_ORIGINS, no XFO. Static at `/api/preview-static/`.
- `static/preview.js` (normalize, exact / ≥8 words / first 12 words, CSS Highlight API or `<mark>`, banner, postMessage ready/highlight), `static/pdf-viewer.js`.
- `Citation.preview_url`, `Citation.preview_kind` set in answering.
- Browser e2e (`backend/tests/test_preview_e2e.py`, marker `browser`, needs a running API on 8011 with `CORS_ORIGINS=http://127.0.0.1:8012`): passing on 3 pages (dgaurf services → text view because the page is built by JS; proiecte; help RU) and 3 PDFs (2 text, 1 scan). Timings in `backend/tests/artifacts/preview/results.json` (gitignored): ready 0.1–2.4 s.

**Finished after the WIP commit**
- Unit tests without a browser: `backend/tests/test_preview.py` (16: sanitizer, data block can't close its script, shown_text_has incl. ş/ș, JS page → text view, PDF/text views, CSP nonce + frame-ancestors + no XFO per kind, 404, line selection ≤ 5, unreachable page → text view, legacy pdf.js).
- Browser tests without API/DB: `backend/tests/test_preview_browser.py` (7, marker `browser`): tiers, "page changed" banner, re-highlight over postMessage without reload, foreign-origin messages ignored, mobile full-screen text view with back link and no horizontal scroll. Passing on Chromium 141.
- **pdf.js switched to the legacy build** (same 6.3.289): the modern build calls `Map.prototype.getOrInsertComputed`, missing in Chromium 141 / older Safari & Firefox → PDF previews never sent `ready`. Found by the static mock previews; legacy verified on Chromium 141.
- Mocks: `backend/scripts/export_previews.py` (no DB: reads data/chunks, crawl copies, data/raw) → `frontend/public/mocks/preview/` (10 previews, PDFs + pdf.js copied, all relative); every ask mock has `preview_url`/`preview_kind`. `backend/scripts/make_admin_mocks.py` → `mocks/admin/sources.json` (40 sites from sites.toml + counters from the corpus-stats mock + running 63 % parse, failed, blocked, a document), `add-source-{site,document,merged,blocked,unreachable}.json`, `gaps.json` (6 groups shaped like app/gaps.py), `gap-recheck.json`, `gap-hide.json`. Contract tests cover all of them.
- Docs: `docs/API.md` (preview endpoint + postMessage, Citation fields, admin sources by URL, list fields, gaps), `docs/FRONTEND-11.md`.
- Finding: preview.js's `start` tier can't win over `words` (a 12-word head is an 8-word run). Kept for the contract; docs say to treat both alike.

## Left (needs the DB + API on Natan's machine)
1. **"Changed page" e2e.** With a crawled copy present the preview shows *that* copy, which always contains the indexed line, so `found: none` only happens when the crawl copy is missing and the page is fetched live (e.g. a server that has the index but not `data/crawl`). The banner itself is covered by `test_preview_browser.py`. For the e2e: point the API at a data dir without `registry.sqlite` (`OFFLINE_DATA_DIR=/tmp/empty-data`) and set `PREVIEW_CHANGED_DOC` to a page whose live version lost the line (candidate: `page:proiecte.chisinau.md/ro/newprojects`, line `09ec2dbe…`). **Deploy note:** ship `data/crawl` + `registry.sqlite` with the backend, else every page preview is a live fetch.
2. **Real-corpus e2e + mobile e2e:**
   ```
   cd backend && CORS_ORIGINS=http://127.0.0.1:8012 uv run uvicorn app.main:app --port 8011
   PREVIEW_API=http://127.0.0.1:8011 uv run pytest -m browser backend/tests/test_preview_e2e.py backend/tests/test_preview_browser.py
   ```
3. **Report** (REPORT.md "Задача 11"): the preview table from `backend/tests/artifacts/preview/results.json` + 3 screenshots; B seed output (40 / indexed 5); 5 real `POST {url}` (with `start:false`): a new domain (acc.md is already in sites.toml → 409, use e.g. `https://www.anre.md/`), a deeper dgaurf path, a dgaurf PDF, chisinau.md (blocked), a dead URL; D gaps on the current DB + one manual recheck (one real call — ask first).
4. Optionally refresh `mocks/admin/sources.json` from the real `GET /api/admin/sources` (see make_admin_mocks.py docstring).
5. Leftover local WIP from the old viewer attempt (not committed, not needed): `frontend/public/pdf.worker.min.mjs`, `frontend/scripts/copy-pdf-worker.mjs`.
6. Frontend (ours): `lib/api.ts` types for `preview_url`/`preview_kind`, `SourceRow`/`SourceAdded`/gaps + mock-mode admin client; WebPreview + mobile chips; one-page admin with URL add + gaps. Guide: `docs/FRONTEND-11.md`.
