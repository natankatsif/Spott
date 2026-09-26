# API contract (frontend ↔ backend)

**This document is the contract.** The backend implements it (Pydantic models in `backend/app/schemas.py`). The frontend mirrors it in `frontend/src/lib/api.ts` and `stream.ts`. Example payloads: `frontend/src/lib/mocks/`, one per UI state, built from **real corpus data** (real doc_ids, line_ids, quotes, bboxes). Wording of the mock answers is illustrative.

**To change the contract**, change this file, `api.ts` and the mocks in one PR, and tell the other side.

**For the backend:** add a test that validates every `frontend/src/lib/mocks/**/*.json` against your models. Then drift shows up in CI, not in the demo.

| Endpoint | State |
|---|---|
| `POST /api/search`, `/api/tools/*`, `GET /health` | work |
| `POST /api/ask` | works: fast path (one retrieval + answer); `mode=deep` also runs the fast path for now |
| `POST /api/ask/stream` | works: real token streaming; each sentence is checked when the model closes it |
| `POST /api/feedback` | works: 1–5 stars + reason tags, stored in Postgres `feedback` with the answer (without a DB: `data/feedback/<date>.jsonl`) |
| `GET /api/suggestions` | works: quick questions from real questions, re-checked by their answers (seed list while the log is empty) |
| `/api/admin/*` | works: login (credentials from env) → session token; sources (add by URL, one-call list), crawl jobs with progress (worker `python -m worker`), questions without an answer (gaps), low ratings, quick questions |
| `GET /api/preview/{doc_id}` | works: the cited page/PDF scrolled to the quote and highlighted, for an iframe (`citation.preview_url`) |
| `GET /api/documents/{doc_id}/file` | works: fetched from the city hall site by the document's URL (or a stored copy), passed through |
| `GET /api/wall` | works (in memory) |
| `GET /api/corpus/stats` | works; without `registry.sqlite` on the machine, pages/documents come from the index |
| Error body `ApiError`, CORS | works: `CORS_ORIGINS`, rate limit `ASK_RATE_LIMIT` per minute per client |

Frontend without backend: `NEXT_PUBLIC_API_MOCK=1` in `frontend/.env.local`. Then `ask()` picks a mock by keywords in the question:

| Question contains | Mock shown |
|---|---|
| «крокод» | not_found |
| «ignor» / «игнорир» | refused |
| «conflict» / «противореч» | conflict |
| «formular» / «școal» / «школ» | checklist |
| any Cyrillic | answer in RU with a quote in RO + translation |
| «când» / «termen» / «когда» / «срок» | partial |
| anything else | answered (RO) |

`wall()` and `corpusStats()` return `mocks/wall.json` and `mocks/corpus-stats.json`. The corpus stats mock is built from the real registry: 40 Annex-1 sites, 5 indexed.

---

## `POST /api/ask`

### Request
```json
{
  "question": "Cine elaborează Planul urbanistic general?",
  "lang": "ro",
  "history": [{ "role": "user", "text": "..." }, { "role": "assistant", "text": "..." }],
  "page_context": { "url": "https://dgaurf.md/ro/documentatii-de-urbanism", "title": "Documentații de urbanism" },
  "mode": "auto",
  "session_id": "anon-7f3a"
}
```

| Field | Rule |
|---|---|
| `question` | required, 1–2000 characters |
| `lang` | the UI language (RO/RU/EN switch). **The answer is in the language the question is written in**; `lang` only decides ambiguous cases ("PUG 2021?"). If omitted, detect. The English UI omits it (answers are RO/RU only) |
| `history` | optional, max 10 turns, oldest first; for follow-ups like "а сколько это стоит?" |
| `page_context` | the embeddable widget sends the page the user is on. Used to bias navigation and search |
| `mode` | `fast` = one retrieval + answer; `deep` = agent walks the corpus (`search/grep/toc/open`); `auto` = backend decides |
| `session_id` | anonymous id: live wall, analytics |

### Response: `AskResponse`

| Field | Meaning |
|---|---|
| `id` | answer id, for `/api/feedback` |
| `status` | `answered` · `partial` (some parts not in corpus; the answer says which) · `not_found` · `conflict` · `refused` (off-topic, prompt injection, abuse) |
| `lang` | answer language (= question language) |
| `answer` | `sentences[].text` joined by a space: fallback for plain rendering. Plain text, **no Markdown**. Lists come as `checklist` |
| `sentences[]` | `{text, cites[]}`. **Every factual sentence cites ≥1 citation.** Sentences with empty `cites` are meta ("Verificați decizia mai nouă…"). Render markers [1][2] from `cites` |
| `citations[]` | see below. Every citation is referenced by some sentence or checklist step. Ordered by first mention: **display number [n] = index in this array + 1** |
| `conflict` | when `status=conflict`: `kind` (`outdated` = newer act replaces older, `contradiction` = unresolved), `explanation`, `citation_ids` (all sides), `preferred_citation_id` (the one the answer relies on) |
| `checklist` | for "how do I…" procedures: `title`, `steps[] {text, cites}`, `documents_needed[]`, `fee`, `deadline` |
| `nav_links[]` | `{title, url, kind: page\|service\|contact\|document, selector}`: where to go on the city hall sites. `selector` (widget only): CSS selector of the element to highlight when `url` is the page the user is on (`page_context.url`); otherwise `null` |
| `followups[]` | suggested next questions, in `lang` |
| `trace[]` | agent steps `{tool, input, summary, ms}`: for the "how I searched" panel. `summary` is in `lang` |
| `meta` | `{model, path: fast\|agent\|none\|cache, latency_ms, verified}`. `verified` = quotes re-read from the DB and claims checked against them. `cache` = a quick question's checked answer, replayed |
| `focus_citation_id` | citation to open right away in the source viewer, when the question asks where exactly something is written ("unde anume scrie…", "где именно написано…"). Prefers a PDF citation. `null` = open only on click |
| `contacts[]` | for `not_found` / `partial` only (else `[]`): 1–2 real contacts from the corpus that can help, `{name, area, phone[], email[], address, hours, url, site, reason, line_ids[], deep_link}`. Every phone, e-mail and address is in `line_ids` (never invented). The answer text then ends with "Din păcate nu putem răspunde… Credem că vă poate ajuta: …" / "К сожалению… Думаем, вам поможет: …" (partial: "Pentru ce lipsește din documente…") |

### Citation

| Field | Meaning |
|---|---|
| `id` | `"c1"`, unique within the answer |
| `doc_id`, `chunk_id`, `line_ids[]` | ids in our index (`doc_id` = `file:<url_key>` / `page:<url_key>`) |
| `kind` | `file` (PDF/DOCX) or `page` (web page) |
| `document_title`, `doc_type`, `act_number`, `published`, `location`, `page` | label: *Decizia nr. 79 din 27.07.2021, pct. 2, p. 1* |
| `quote` | **verbatim from the DB, never written by the LLM** |
| `quote_lang` | language of the quote |
| `translation` | required when `quote_lang ≠ lang`. Show under the quote, marked "перевод ИИ / traducere AI" |
| `url` | original on the city hall site |
| `deep_link` | `url#page=N` for PDF, `url#:~:text=…` for web pages; always starts with `url` |
| `found_on`, `site` | page where the document is published |
| `file_url` | our copy of the PDF for the viewer (`/api/documents/{doc_id}/file`, relative to API_URL). `null` for web pages |
| `bboxes[]` | highlight rectangles: `{page, l, t, r, b, page_width, page_height}` in **PDF points, origin top-left** (Docling gives bottom-left: the backend converts). Scale factor = rendered page width / `page_width`. Often the rectangle is the whole paragraph or table cell, not the exact line: draw a soft highlight, not an underline. Empty for web pages and some old documents: then open `deep_link` |
| `preview_url` | the source preview: the page or PDF **already scrolled to the quote, highlighted** (see [`GET /api/preview`](#get-apipreviewdoc_id-the-source-preview)). Put it in an iframe (desktop) or open it full screen (mobile). From the backend it starts with `/api/` → prefix API_URL; in mocks it is a frontend path (`/mocks/preview/…`) → use as is |
| `preview_kind` | `page` (our copy of the web page) · `pdf` (pdf.js viewer) · `text` (our text view: DOCX and other files) |

### States the UI must handle
1. **answered**: text with [n] markers; citation cards (quote, label, "open source").
2. **Cross-lingual** (RU question → RO document): quote in RO plus `translation` below it.
3. **not_found**: honest "not in documents", `nav_links` where to ask, no citations.
4. **conflict**: both sources side by side with dates, `preferred_citation_id` highlighted, `explanation`.
5. **checklist**: numbered steps, each with its source; documents to bring, fee, deadline.
6. **refused**: short polite reply, no sources.
7. **Loading / error**: backend down → error message; timeout 30 s.
8. **Where exactly** (`focus_citation_id` set): open that citation in the source viewer inside the answer: PDF page rendered, `bboxes` highlighted, scrolled to them. Any citation with `file_url` can be opened the same way on click.

## `POST /api/ask/stream` (SSE)

Same request as `/api/ask`. Response: `text/event-stream`. Each event looks like:
```
event: delta
data: {"type":"delta","index":0,"text":"Elaborarea "}

```

Order: `start → trace* → (citation* → delta* → sentence)* → citation* → done`. The trailing `citation*` are sources cited only by checklist steps or the conflict. `error` can come at any point.

| Event | Payload | UI |
|---|---|---|
| `start` | `id, lang` | show the answer bubble |
| `trace` | `step: TraceStep` | "Caut în documente… / Deschid Decizia nr. 79…" while the agent works (before any text) |
| `citation` | `citation: Citation` | sent **before** the first sentence that cites it, so markers [n] and the source card render at once |
| `delta` | `index, text` | append text to sentence `index`; no markers inside the text |
| `sentence` | `index, sentence {text, cites}, verified` | sentence complete: final `cites` → show [n] markers; `verified=false` → mark as unconfirmed |
| `done` | `response: AskResponse` | **authoritative**: replace the assembled state with it (status, conflict, checklist, nav_links, followups) |
| `error` | `code: ErrorCode, message` | show the error, keep what was already streamed |

Client: `frontend/src/lib/stream.ts`:
- `askStream(req, onUpdate, signal)` reads the stream with fetch (EventSource can't POST);
- `applyStreamEvent` is a pure reducer;
- with `NEXT_PUBLIC_API_MOCK=1`, `mockStream` replays a mock with realistic delays.

How the backend streams (task 10):
- The model writes JSON; each sentence is `{refs, text}` with the line ids **before** the text. A sentence's `delta`s start only once its refs point at real source lines, so an unbacked sentence is never shown; the `sentence` event comes when the model closes the sentence, with `verified` checked against its quotes right then.
- `citation` events are built from the DB (`quote` by `line_id`), never from model output. Their `translation` may be `null` in the stream: the model writes translations after the sentences, so they come in `done`.
- The `delta`s of sentence `i`, concatenated and trimmed, equal `sentences[i].text`; `answer` is the sentences joined with spaces.
- Sentences the backend adds after the model's (a quoted repeal note, the `missing` parts of a `partial` answer, the `not_found` / `refused` texts) come after the model's sentences, same event order.
- `trace` events only come before the first text. Steps after it (a second search, `verify`) are only in `done.response.trace`.
- A second answer (≤ 20% of questions: the first one is `partial` or has a conflict, **and** newer acts turned up) is not streamed again: `done` replaces what was shown. `done` is always authoritative.

## `POST /api/feedback`
```json
{ "answer_id": "a_…", "rating": 2, "tags": ["outdated", "wrong_source"], "comment": "sursa e veche",
  "citation_id": "c2", "session_id": "anon-7f3a" }
```
→ `{ "ok": true }`
- `rating` 1–5 (or the older `vote`: `up` = 5, `down` = 1; one of them is required).
- `tags` (optional): `wrong`, `outdated`, `incomplete`, `wrong_source`, `not_understood`, `helpful`.
- Rating the same `answer_id` again from the same `session_id` overwrites the previous rating.
- Unknown `answer_id` → 404. Stored with the question, status, cited doc_ids and `meta.path` of the answer.

## `GET /api/suggestions?lang=ro&limit=6`
→ `{ "items": [{ "id", "question", "lang", "answer_id", "asked_count", "rating_avg", "pinned" }] }` (mock: `mocks/suggestions.json`)
- Chips on the empty screen and after an answer: real questions asked ≥ 2 times (or pinned by the admin), answered + verified with a citation, no rating ≤ 2 and average ≥ 4 if rated, nothing personal in them, 10–120 characters. Near-identical wordings are one question.
- Every one is asked again when the index changes and at least daily; one that is no longer answered and verified disappears. Empty log → a seed list, through the same check.
- Clicking one: send it to `/api/ask/stream` as usual. If it was checked against the current index, the answer is replayed at once (same events, `meta.path = "cache"`, a new answer id).

## Admin: `/api/admin/*`
**Login.** The login and password live in the server's env (`ADMIN_LOGIN`, `ADMIN_PASSWORD`). The UI shows a login form and sends:
```
POST /api/admin/login   { login, password } → { token, login, expires_at }   (mock: mocks/admin/session.json)
GET  /api/admin/me      → { login }   (is the stored token still valid)
```
Every other admin call needs `Authorization: Bearer <token>`. The token is signed by the server and lasts 12 h (`ADMIN_SESSION_HOURS`); changing the password ends every session. Wrong login/password, a missing, expired or forged token → `401 unauthorized` (show the login form). More than 5 login attempts a minute from one IP → `429 rate_limited`. Without `ADMIN_LOGIN`/`ADMIN_PASSWORD` on the server the admin is off (always 401). Client: `adminLogin()` and the `admin*()` functions in `frontend/src/lib/api.ts`. Mocks: `mocks/admin/*.json`.
```
GET    /api/admin/sources                    → { sources: SourceRow[], totals }   (one call for the whole admin page)
POST   /api/admin/sources                    { url } → 201 SourceAdded (new) · 200 SourceAdded (merged into an existing source)
PATCH  /api/admin/sources/{id}               { enabled?, max_depth?, max_pages?, category? } → SourceRow
DELETE /api/admin/sources/{id}?purge=true    purge also removes its documents from the index → { ok: true }
POST   /api/admin/sources/{id}/jobs          { kind: "crawl"|"refresh" } → 201 Job
GET    /api/admin/jobs?status=running        → { jobs: Job[] }
GET    /api/admin/jobs/{id}                  → Job   (poll every 1–2 s while running)
POST   /api/admin/jobs/{id}/cancel           → Job   (queued: cancelled at once; running: stops after the current item)
GET    /api/admin/gaps?status=not_found,partial&lang=&days=30&limit=50&hidden=0  → GapList
POST   /api/admin/gaps/{id}/recheck          → GapRecheck   (one model call, only on click)
POST   /api/admin/gaps/{id}/hide | /unhide   → { ok: true }
GET    /api/admin/feedback?max_rating=2&limit=50  → { items: FeedbackItem[] }  lowest first
GET    /api/admin/feedback/stats             → { count, average, per_star{"1".."5"}, top_tags[{tag,count}], by_day[{day,count,average}] }
POST   /api/admin/suggestions                { question, lang, pinned: true } → 201 Suggestion
DELETE /api/admin/suggestions/{id}           hide it → { ok: true }
```
**Sources (one admin page, no sub-pages).**
- `SourceRow` = `{id, kind: site|document, url, site_id, title, category, category_source, start_urls[], max_depth, max_pages, enabled, robots: allowed|blocked, status, pages, documents_found, documents_downloaded, chunks, lines, last_crawled, progress, last_error, created_at, last_job: Job|null}`.
  - `status`: `disabled` > `blocked` > `running` > `queued` > `failed` (the last job failed) > `indexed` (has chunks) > `pending` (never indexed, no job): the first that applies.
  - `progress` = `{job_id, stage, percent, eta_s}` while a job is queued/running, else `null`. **Poll `GET /sources` every 2 s while any row is `running`/`queued`**; stop when none is.
  - `category_source`: `toml` (sites.toml) · `index` (found in the index) · `rule` (domain) · `keywords` (page title/description) · `default` (`other`) · `manual`.
  - `totals` = the same object as `/api/corpus/stats` totals (header numbers, no second call).
  - The list is never empty on a machine with an index: the backend seeds it from sites.toml and the index at startup.
- **Add = paste one link.** `POST {url}`: a website, a page of one, or a link to a PDF/DOC/DOCX. No file uploads. The server decides the rest (no LLM): robots.txt first (chisinau.md, actelocale.gov.md are never fetched), then HEAD/GET (8 s), kind by content type/extension, category by domain rule → title keywords → `other`, crawl settings (domain root: depth 4 / 2000 pages; a deeper path: depth 2 under that path; a document: download + parse), and queues the job at once.
  - `SourceAdded` = `SourceRow` + `detected: {kind, category, category_source, title, crawl_depth, max_pages, reason}` + `merged_into: id|null`. Show `detected.reason` (one English sentence) in the toast, or your own RO/RU text built from the fields.
  - One row per domain: a deeper path or a document of a domain that is already a source → **200** with `merged_into` = that source's id (the path joins its `start_urls`, a crawl of it is queued).
  - Responses to map in the UI: 201 new · 200 merged · 201/200 with `robots: "blocked"` (saved, no crawl) · `409 conflict` (the same site root or document again) · `422 validation_error` (not http(s), the site doesn't answer, it answered 4xx/5xx).
  - The old optional fields (`kind`, `category`, `max_depth`, `max_pages`, `start`) are still accepted; the UI doesn't send them.
- `Job` = `{id, source_id, kind, status: queued|running|done|failed|cancelled, stage: crawl|download|parse|index, stage_done, stage_total, percent, eta_s, started_at, finished_at, stats{pages, documents_found, documents_downloaded, files_parsed, chunks, lines, embeddings_reused, embeddings_computed, errors}, log_tail[], error}`. `percent` over all stages: crawl 20, download 20, parse 40, index 20.
- A job can't start on a `blocked` or disabled source, or while another one of it is queued/running (`409`).

**Gaps: questions the bot couldn't (fully) answer.** Similar questions are grouped (local embeddings, no LLM), sorted by how often they were asked.
- `GapList` = `{items: Gap[], totals: {not_found, partial, groups}}`.
- `Gap` = `{id, example, questions[{answer_id, question, lang, status, ts}] (oldest first, the latest 20), count, last_asked, langs[], status: not_found|partial (the worst in the group), missing[] (what partial answers said is missing), hint_sites[{site, hits}] (sites that were found but not used: whom to ask), rechecked: {status, verified, answer_id, ts}|null, hidden}`. Questions are masked like the wall.
- Loop for the demo: a gap → add the missing document by URL → when its job is done, **Recheck** → answered → the group leaves the list. A recheck costs one model call: only on a click, never automatically.
- Mocks: `mocks/admin/gaps.json`, `gap-recheck.json`, `gap-hide.json`, `sources.json`, `add-source-{site,document,merged,blocked,unreachable}.json` (regenerate: `backend/scripts/make_admin_mocks.py`).
- The jobs run in a separate process next to uvicorn: `cd offline_indexation && uv run python -m worker`.

## `GET /api/documents/{doc_id}/file`
- Returns the PDF (`application/pdf`) for the viewer: pdf.js + `bboxes` highlight. City hall sites don't send CORS headers, so the viewer can't load the originals directly: the backend fetches the document from its original `url` on request and passes it through (in-memory cache, nothing stored; a stored copy is used if the machine has one). Only documents in our index, only PDFs.
- `doc_id` is URL-encoded; use `citation.file_url` as is. `file_url` is set for every PDF citation; `null` for web pages and non-PDF files (DOCX).
- Errors: `404 not_found` (unknown document, not a PDF, the site answered 404) → open `deep_link` instead; `503 unavailable` (the city hall site didn't answer) → same fallback.

## `GET /api/preview/{doc_id}`: the source preview
One URL per citation (`citation.preview_url`) that shows the cited source **scrolled to the quote and highlighted**, and that works inside the chat's iframe whatever the city hall site sends. Text fragments (`#:~:text=`) don't work in iframes and city hall pages can't be framed, so the backend serves its own page:
- `page`: our copy of the web page (crawled copy, else a live fetch cached 1 h), sanitized (no site scripts, frames, forms, handlers; CSS and images still load from the original), our script finds and highlights the quote. A page built by JavaScript, or one that can't be loaded → `text`.
- `pdf`: a pdf.js viewer (vendored, legacy build) that renders the cited page first, draws `bboxes` and highlights the line in the text layer; scans work (the box is the highlight).
- `text`: our view of the indexed lines (DOCX, other files, fallbacks), the quote highlighted.

Query: `line` = line_id (repeatable, max 5; unknown ids ignored), `lang` = ro|ru (banner language), `embed` = 1 (inline) | 0 (full screen: adds a "← back" link that closes the tab or goes back).
- Always `200 text/html` with the best view there is; a banner on top says where the copy is from ("Copie din 25.09.2026 · Deschide originalul ↗") and, if the quote isn't on the page any more, says so and shows the quote. Unknown `doc_id` → `404 not_found` (JSON).
- Headers: CSP with a nonce, `frame-ancestors 'self' <CORS_ORIGINS>` (so **every frontend origin, dev ports included, must be in `CORS_ORIGINS`**), no `X-Frame-Options`, `Cache-Control: private, max-age=600`. Static assets under `/api/preview-static/`.

**`postMessage` protocol** (accepted only from `CORS_ORIGINS`):
- preview → parent (and `window.opener` on mobile), after load and after every re-highlight: `{type: "src-preview:ready", doc_id, found: "exact"|"words"|"start"|"none", kind: "page"|"pdf"|"text"}`. `words` = a run of ≥ 8 words matched (the page changed a little), `none` = not found (the banner explains). `start` (the first 12 words) is in the contract but in practice `words` always wins over it: treat both alike.
- parent → preview: `{type: "src-preview:highlight", line_ids: [...]}` re-highlights and scrolls **without reloading** (another citation of the same `doc_id`), then `ready` again. Every line of the document is embedded in the page, so any `line_id` of it works.

Wiring guide for the frontend (WebPreview snippet, mobile, gotchas): `docs/FRONTEND-11.md`.

## `POST /api/search` (works now)
```json
{ "query": "autobuze 2020", "lang": "ro", "k": 5 }
```
→ `{ results: [{chunk_id, doc_id, citation_label, text, url, site, lang, page, matched_lines: [{line_id, idx, text, score}]}], timings_ms, not_found }`.
Useful for a "what search found" debug panel and for the demo before `/api/ask` is ready.

## Errors: every non-2xx response

```json
{ "error": "rate_limited", "message": "Too many questions, retry in 20 s", "retry_after_s": 20 }
```

| `error` | HTTP | UI |
|---|---|---|
| `validation_error` | 422 | "question too long / empty" (FastAPI's default `{detail:[…]}` must be replaced with this shape) |
| `unauthorized` | 401 | admin: wrong login/password or the session is over: show the login form |
| `not_found` | 404 | unknown doc_id in viewer: open `deep_link` instead |
| `conflict` | 409 | admin: duplicate source, robots.txt forbids crawling, a job already running |
| `rate_limited` | 429 | "too many questions, wait N s" (QR-wall protection: suggested 10 questions/min per IP) |
| `unavailable` | 503 | "service warming up / DB down, try again" |
| `not_implemented` | 501 | feature not ready: hide it |
| `internal` | 500 | generic error |

Before the stream starts (bad request, rate limit), `/api/ask/stream` answers with HTTP status + `ApiError`. After it starts, errors come as the `error` event. Client: `ApiRequestError` in `api.ts`.

## CORS
- Local frontend: `http://localhost:3000`, `http://127.0.0.1:3000`.
- The widget embedded on other sites needs `*` for `/api/ask*`, `/api/feedback`, `/api/documents/*`. Configure via env `CORS_ORIGINS`.
- Streaming behind a proxy needs `X-Accel-Buffering: no` and `Cache-Control: no-cache`.

## `POST /api/visits`
`{ "visitor_id": "anon-…" }` → `{ "visitors": 1284 }` (mock: a fixed number)
- The unique-visitor counter in the header. The frontend sends its anonymous per-browser id (`lib/session.ts`, the same as `AskRequest.session_id`) once per page load; each id is counted once, nothing else about the visitor is stored.
- Without a database → 503 `unavailable`; the header then shows no counter.

## `GET /health`
`{status, device, models_loaded, chunk_count}`. While `models_loaded=false` (first ~20 s after start), show "warming up".

## `GET /api/wall?after=<id>&limit=50`: live "break the bot" wall
Poll every 2–3 s. `after` = newest id you already have, so the response only has newer items.

```json
{ "items": [{ "id": "…", "ts": "2026-09-26T10:17:00+00:00", "question": "… мой тел •••", "lang": "ru",
              "status": "not_found", "verified": true, "latency_ms": 1810, "top_source": null }],
  "total_questions": 7, "by_status": { "answered": 3, "not_found": 1, "conflict": 1, "refused": 1, "partial": 1 } }
```
- Items are newest first.
- The backend masks personal data before storing: phones, IDNP and other long digit runs, e-mails → `•••`. `question` is at most 200 chars.
- In-memory is fine (resets on restart).
- Every `/api/ask` and `/api/ask/stream` call adds an item.

## `GET /api/corpus/stats`: corpus health dashboard
```json
{ "updated_at": "…",
  "totals": { "sites_total": 40, "sites_indexed": 5, "pages": 484, "documents_found": 372, "documents_downloaded": 79,
              "chunks": 2284, "lines": 9906, "documents_replaced": 0, "documents_removed": 0 },
  "sites": [{ "site": "dgaurf.md", "category": "urban", "status": "indexed", "pages": 44, "documents_found": 252,
              "documents_downloaded": 15, "chunks": 260, "last_crawled": "…" }] }
```
- `status` is one of:
  - `indexed`: has chunks in the index;
  - `pending`: an Annex-1 site we haven't crawled yet;
  - `blocked`: robots.txt forbids crawling (chisinau.md, actelocale.gov.md).
- Sources for the backend:
  - `offline_indexation/data/sources/sites.toml`: all Annex-1 sites + category;
  - `registry.sqlite`: pages / documents per site, `version > 1` = replaced, `status = 'removed'`;
  - Postgres: chunks per site, lines total.

## Not in the contract (frontend owns)
- Example questions for the empty state.
- UI texts in RO/RU.
- The budget page (static).
