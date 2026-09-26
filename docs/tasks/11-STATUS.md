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

## Left
1. **A — "changed page" e2e case**: candidates found (live page lacks the indexed line), e.g. `page:proiecte.chisinau.md/ro/newprojects` line `09ec2dbe23cef6a4b562d9b3ce0e3031dbc4fae6`. Set it in `CASES["changed-page"]`, run, check the banner. Note: many mobilitatechisinau.md lines are site-wide boilerplate ("Aceasta implică reparația…") not shown on the live page — worth a look.
2. **A — mobile e2e** (`test_mobile_full_screen`) not run yet.
3. **A — unit tests (pytest, no browser)**: sanitizer (no scripts but ours, no on*, no javascript:, `<base>`), `shown_text_has`, PDF view embeds page + boxes, text view (DOCX), headers (CSP nonce, frame-ancestors, no XFO), 404 unknown doc. The JS matcher tiers are covered by e2e; a Python twin of the matcher isn't written.
4. **Mocks** (contract tests fail until done):
   - `backend/scripts/export_previews.py`: render previews for citations of `frontend/src/lib/mocks/ask/*.json` into `frontend/public/mocks/preview/<id>.html` (PDF inlined as `file_data`, pdf.js copied next to them), set `preview_url` = `/mocks/preview/<id>.html`, `preview_kind` in every ask mock.
   - `mocks/admin/sources.json` = real `GET /api/admin/sources` after seed (40 rows; edit 1 running 63 % parse, 1 failed, 1 document row); `mocks/admin/add-source-{site,document,merged,blocked,unreachable}.json`; `mocks/admin/gaps.json` (6 groups) + recheck/hide responses.
   - `frontend/src/lib/api.ts`: `preview_url`/`preview_kind` in Citation, new `SourceRow`/`SourceList.totals`/`SourceAdded`, gaps types + admin client (mock mode reads the files).
5. **Docs**: `docs/API.md` (preview endpoint + postMessage, Citation fields, admin sources by URL, list fields, gaps); `docs/FRONTEND-11.md` (WebPreview snippet + `resolvePreviewUrl`, focus/switch/ready handling, iframe attrs — WebPreviewBody uses `sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"`: add `allow-popups-to-escape-sandbox` so "open original" isn't sandboxed; mobile full screen `embed=0`; admin one-page flow; gotchas: frame-ancestors = CORS_ORIGINS incl. dev ports, scans need wasm + `'wasm-unsafe-eval'`, JS-built pages → text view, parent URL containing `/api/preview/` when matching frames).
6. **Report** (REPORT.md "Задача 11"): preview table (doc/kind/found/ready ms/screenshot), live-fetch failure banner, sizes (preview HTML p50, pdf.js load); B seed output (40 / indexed 5) and 5 real `POST {url}` responses (acc.md with `start:false`, a deeper dgaurf path, a dgaurf PDF, chisinau.md blocked, a dead URL); D gaps on the current DB (answers table is empty here) + one manual recheck (a single real call is OK — ask first).
7. CI: `browser` tests stay out of CI (need DB + API); keep `-m "not db"` green on ubuntu + windows.
8. Leftover local WIP from the old viewer attempt: `frontend/public/pdf.worker.min.mjs`, `frontend/scripts/copy-pdf-worker.mjs` (not committed; not needed now — the preview is backend-side).
