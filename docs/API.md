# API contract (frontend ↔ backend)

**This document is the contract.** The backend implements it (Pydantic models in `backend/app/schemas.py`). The frontend mirrors it in `frontend/src/lib/api.ts` and `stream.ts`. Example payloads: `frontend/src/lib/mocks/`, one per UI state, built from **real corpus data** (real doc_ids, line_ids, quotes, bboxes). Wording of the mock answers is illustrative.

**To change the contract**, change this file, `api.ts` and the mocks in one PR, and tell the other side.

**For the backend:** add a test that validates every `frontend/src/lib/mocks/**/*.json` against your models. Then drift shows up in CI, not in the demo.

| Endpoint | State |
|---|---|
| `POST /api/search`, `/api/tools/*`, `GET /health` | work |
| `POST /api/ask` | works: fast path (one retrieval + answer); `mode=deep` also runs the fast path for now |
| `POST /api/ask/stream` | works: the answer is generated whole, then streamed sentence by sentence (word deltas) |
| `POST /api/feedback` | works: stored in `data/feedback/<date>.jsonl` |
| `GET /api/documents/{doc_id}/file` | works where the PDFs are on disk (`OFFLINE_DATA_DIR/raw`); otherwise 404 and `file_url` is `null` |
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
| `lang` | the UI language (RO/RU switch). **The answer is in the language the question is written in**; `lang` only decides ambiguous cases ("PUG 2021?"). If omitted, detect |
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
| `meta` | `{model, path: fast\|agent\|none, latency_ms, verified}`. `verified` = quotes re-read from the DB and claims checked against them |

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

### States the UI must handle
1. **answered**: text with [n] markers; citation cards (quote, label, "open source").
2. **Cross-lingual** (RU question → RO document): quote in RO plus `translation` below it.
3. **not_found**: honest "not in documents", `nav_links` where to ask, no citations.
4. **conflict**: both sources side by side with dates, `preferred_citation_id` highlighted, `explanation`.
5. **checklist**: numbered steps, each with its source; documents to bring, fee, deadline.
6. **refused**: short polite reply, no sources.
7. **Loading / error**: backend down → error message; timeout 30 s.

## `POST /api/ask/stream` (SSE)

Same request as `/api/ask`. Response: `text/event-stream`. Each event looks like:
```
event: delta
data: {"type":"delta","index":0,"text":"Elaborarea "}

```

Order: `start → trace* → (citation* → delta* → sentence)* → done`. `error` can come at any point.

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

Backend rules for task 09:
- The LLM writes citation markers inline (`… achiziții publice [c1].`). The backend:
  - cuts each sentence at `.`/`!`/`?`;
  - strips the markers from the text and emits `delta` without them;
  - emits `sentence` with the collected `cites`.
- A citation's `quote` is filled from the DB by `line_id` **before** it is emitted, never from model output.
- Verification of the sentence against its quotes happens after the sentence is complete (`sentence.verified`). The final `done.meta.verified` covers the whole answer.
- The concatenated `delta` texts (trimmed) must equal `done.response.answer`.

## `POST /api/feedback`
```json
{ "answer_id": "…", "vote": "down", "comment": "sursa e veche", "citation_id": "c2" }
```
→ `{ "ok": true }`

## `GET /api/documents/{doc_id}/file`
- Returns the stored original PDF (`application/pdf`) for the viewer: pdf.js + `bboxes` highlight. City hall sites don't send CORS headers, so the viewer can't load the originals directly.
- `doc_id` is URL-encoded; use `citation.file_url` as is.

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
| `not_found` | 404 | unknown doc_id in viewer: open `deep_link` instead |
| `rate_limited` | 429 | "too many questions, wait N s" (QR-wall protection: suggested 10 questions/min per IP) |
| `unavailable` | 503 | "service warming up / DB down, try again" |
| `not_implemented` | 501 | feature not ready: hide it |
| `internal` | 500 | generic error |

Before the stream starts (bad request, rate limit), `/api/ask/stream` answers with HTTP status + `ApiError`. After it starts, errors come as the `error` event. Client: `ApiRequestError` in `api.ts`.

## CORS
- Local frontend: `http://localhost:3000`, `http://127.0.0.1:3000`.
- The widget embedded on other sites needs `*` for `/api/ask*`, `/api/feedback`, `/api/documents/*`. Configure via env `CORS_ORIGINS`.
- Streaming behind a proxy needs `X-Accel-Buffering: no` and `Cache-Control: no-cache`.

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
