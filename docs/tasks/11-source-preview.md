# Task 11: source preview inside the answer: the page or PDF opens scrolled to the quote, highlighted

Read `docs/API.md` (Citation, `focus_citation_id`, `/api/documents/{doc_id}/file`), `backend/app/files.py`, `offline_indexation/common/registry.py` (`pages.html_file`), and the AI Elements component https://elements.ai-sdk.dev/components/web-preview (`frontend/src/components/ai-elements` after `npx ai-elements add web-preview`).

## Goal (what the user sees)
- **Desktop**: inside the answer there is a small browser window (AI Elements `WebPreview`: toolbar + URL + iframe). It shows the city hall page, or the PDF, of the cited source, **already scrolled to the quoted line, with the line highlighted**. Clicking another citation `[n]` switches the preview to that source and line.
- **Mobile**: no inline window. Each citation is a chip in the message ("dgaurf.md · Decizia nr. 79, pct. 2 ↗"). Tapping it opens the same preview full-screen, scrolled and highlighted, with a back button.
- It must **always** work: sites that forbid iframes, sites without CORS, scanned PDFs, a page that changed since we crawled it. When exact highlighting is impossible, the user still gets the document and an honest note, never a blank/404 frame (see the screenshot in the chat: the empty `WebPreview` shows "404").

## Why the backend must do it (don't re-research, this is settled)
- Text fragments (`#:~:text=`) are **not applied inside iframes** (MDN: only the main frame, only user-initiated navigations). Chrome's exception is same-origin only. So `deep_link` can't do the job inside `WebPreview`.
- A city hall page in an iframe is cross-origin. We can't run our highlight code in it, and many sites send `X-Frame-Options` / CSP `frame-ancestors`.
- So the **backend serves its own preview page** for every citation: a snapshot of the web page, or a PDF viewer, **with the highlight done inside that page by our script**. The frontend only puts one URL in an iframe (desktop) or opens it (mobile). Nothing depends on the city hall site's headers.

## What to build

### 1. `GET /api/preview/{doc_id}` → `text/html` (one URL for pages and PDFs)
Query:
- `line` = line_id (repeatable, max 5);
- `lang` = ro | ru (UI language of the banner);
- `embed` = 1 | 0 (inline or full-screen: full-screen shows a back link).

**Web page (`kind=page`)**
- HTML source, in order:
  1. the crawled copy (`pages.html_file` under `data/crawl/<site>/`);
  2. a live fetch of `url` (httpx, 5 s timeout, same UA as the crawler), cached in memory for 1 h.
- Sanitize (allow-list, e.g. `nh3`/`bleach` or selectolax):
  - drop `<script>`, `<noscript>`, `<iframe>`, `<object>`, `<embed>`, `<form>` actions, `on*=` attributes, `javascript:` URLs, `<meta http-equiv=refresh>`, `<base>` from the site;
  - keep the layout: CSS `<link>`/`<style>` and images stay, but load from the original site through an added `<base href="<original url>">`;
  - all links get `target="_blank" rel="noopener"`.
- Inject **one** script of ours (inline, with a CSP nonce) and a small style block:
  - **Find the quote.** Normalize both sides: NFKC, collapse whitespace, ş/ţ ↔ ș/ț, typographic quotes/dashes, soft hyphens. Walk text nodes (TreeWalker) and match across element boundaries. Match order:
    - exact normalized match;
    - the longest run of ≥ 8 consecutive words;
    - the first 12 words.

    Report the tier that matched.
  - **Highlight** with the CSS Custom Highlight API (`CSS.highlights`, `::highlight(src-quote)`); if unsupported, wrap in `<mark data-src-quote>`. Several lines = several ranges. Then `scrollIntoView({block: "center"})`, and scroll again after images load.
  - **Banner** on top (sticky, not covering the quote):
    - "Copie din 25.09.2026 · Deschide originalul ↗" / «Копия от 25.09.2026 · Открыть оригинал ↗»;
    - when not found: "Fragmentul nu a fost găsit exact pe pagină — pagina s-a schimbat. Citatul: «…»" (RU equivalent);
    - the "original" link = `deep_link` (text fragment) opened in a new tab.
  - **`postMessage` protocol** (see 4).

**PDF (`kind=file`, PDF)**
- Return a small viewer page: pdf.js pinned version from cdnjs/jsDelivr, or vendored into `backend/static/`, not the browser's built-in viewer.
  - It loads `/api/documents/{doc_id}/file` (same origin as the preview, so no CORS).
  - It renders the cited page first, then the neighbours lazily.
  - It draws the citation `bboxes` (already top-left, PDF points) as semi-transparent rectangles scaled to the canvas, and scrolls to the first one.
- Also render pdf.js's **text layer**, and highlight the line text there when it is found. Scanned pages have no text layer; the bbox is then the highlight.
- Same banner, with the page number and "Deschide PDF-ul original ↗" (`deep_link` = `url#page=N`).

**DOCX / other files**: render the lines we have for that document (our own text view: title, the lines ±15 around the quote, quote highlighted), plus a download link to the original. Same banner.

**Unknown doc_id** → 404 `ApiError`. Everything else must return 200 with the best possible view, never an empty frame.

**Headers**
- `Content-Security-Policy`: `default-src 'none'; img-src * data:; style-src * 'unsafe-inline'; font-src *; script-src 'nonce-…' <pdf.js origin>; connect-src 'self'; frame-ancestors <CORS_ORIGINS>`.
- No `X-Frame-Options`.
- `Cache-Control: private, max-age=600`.

### 2. Contract (`docs/API.md` + `frontend/src/lib/api.ts` + all mocks, one PR)
`Citation` gains:
- `preview_url: string`: always set, relative to API_URL, e.g. `/api/preview/<doc_id>?line=<line_id>&lang=ro`;
- `preview_kind: "page" | "pdf" | "text"`.

Nothing else changes. `focus_citation_id` (exists) = which citation the inline preview opens first. If it is null, the preview opens on the first citation when there is one, and only on click on mobile.

### 3. Mocks that really work in mock mode
Generate static preview HTMLs **with your endpoint** for the citations used in `frontend/src/lib/mocks/ask/*.json`, and save them to `frontend/public/mocks/preview/<id>.html` with their assets inlined or absolute. In the mocks, set `preview_url` to `/mocks/preview/<id>.html`. The frontend then shows a real highlighted preview without the backend. Add `tools/export_previews.py` to regenerate them.

### 4. `postMessage` protocol (parent ↔ preview)
- Preview → parent, after load: `{type: "src-preview:ready", doc_id, found: "exact" | "words" | "start" | "none", kind}`. Send it to `window.parent` and to `window.opener` (mobile).
- Parent → preview: `{type: "src-preview:highlight", line_ids: [...]}`. The preview re-highlights and scrolls **without reloading**, then answers with `ready` again. The line texts come from a JSON block embedded in the page for every line of that document, or from `GET /api/preview/{doc_id}/lines?ids=`.
- Accept messages only from `CORS_ORIGINS`. Ignore everything else.

## Tests (required, CI-green on ubuntu + windows)
1. **Unit (pytest)**:
   - sanitizer: no `<script>` except ours (nonce), no `on*`, no `javascript:`, `<base>` present;
   - matcher: normalization cases (ş/ș, quotes, soft hyphen, line broken across `<span>`s, text split by `<br>`), each tier;
   - PDF viewer HTML embeds the correct page and bboxes;
   - DOCX text view;
   - headers (CSP with nonce, `frame-ancestors`, no XFO);
   - 404 for an unknown doc.
2. **Contract**: every mock validates; every citation has `preview_url` and `preview_kind`; `/api/ask` responses include them.
3. **Browser end-to-end (Playwright, headless Chromium)**. A test page embeds `/api/preview/...` in an `<iframe>` exactly like `WebPreview` does, from another origin (a second local port) to mimic the frontend. Then it asserts:
   - the iframe loads without CSP/XFO errors (console clean);
   - `ready` arrives with `found != "none"`;
   - the highlighted range / bbox is inside the iframe viewport (`getBoundingClientRect`);
   - after `src-preview:highlight` with another line, the new range is highlighted and in view, without reload.

   Run it on **real corpus documents**: 3 web pages (dgaurf.md news, proiecte.chisinau.md project page, help.chisinau.md), 2 text PDFs, 1 scanned PDF, 1 page whose live version differs from the crawl. Save screenshots to `backend/tests/artifacts/preview/*.png` (gitignored) and put 3 of them in the report.
4. **Mobile**: the same assertions with a 390×844 viewport and `embed=0` (back link present).

## Report
- Table:

  | doc | kind | found tier | time to ready (ms) | screenshot |
  |---|---|---|---|---|

- What happens on a site whose live fetch fails (show the banner).
- Sizes: preview HTML p50, pdf.js load time.

## Don't
- Don't iframe the city hall site directly, and don't rely on `#:~:text=` inside an iframe.
- Don't execute the site's own JS.
- Don't store new copies of PDFs; reuse `/api/documents/{doc_id}/file`.
- Don't change existing contract fields; only add the two `Citation` fields.

---

# Part B (same task): admin sources: just paste a link

Problems reported by the team after task 09:
1. **Admin "Surse" is empty.** `sources` is filled only by `tools.sources import`, which nobody ran on a fresh DB. The admin must never be empty on a machine that has an index.
2. **Adding a source is too manual.** The form asks for site/document, category, depth, max pages. Needed: **one field, a URL**. The server decides everything else and starts indexing by itself. No file uploads, only links (a site/page link or a link to a PDF/DOC/DOCX).
3. **The admin is one page, no sub-pages**, so one API call must give everything the table needs.

## B1. Seed automatically
- On backend startup (and in `tools.index_io import`): if `sources` is empty, import `sites.toml` (same code as `tools.sources import`, idempotent).
- Then link what is already indexed. For every site with chunks in the index, fill the counters from the index + `registry.sqlite` when present: pages, documents found/downloaded, last crawl.
- Result on the current data: 40 rows, 5 of them `indexed` with real numbers, chisinau.md `blocked`.

## B2. `POST /api/admin/sources` = `{ "url": "…" }`
The body is only `url`. Keep the old optional fields accepted for compatibility, but the UI won't send them. The server:
1. Normalizes the URL (scheme, `www.`, trailing slash) and fetches it: HEAD, then GET if needed, 8 s timeout, follow redirects.
   - Unreachable / not http(s) → 422 with a clear message ("Сайт не отвечает" / "Site-ul nu răspunde" is on the UI side; the backend sends the English message + code).
2. **Kind**: content-type or extension PDF/DOC/DOCX → `document`, otherwise `site`.
3. **Same domain already a source:**
   - a site URL with a deeper path → add it to that source's `start_urls`, queue a crawl, return **200** with `merged_into: <id>`;
   - the same document again → 409 "already indexed";
   - never create a second row for the same domain.
4. **Category**, automatically, in this order:
   - domain rules (`dets|educ|scoal|gradinit|extrascolar` → education, `amt|sanat|spital` → healthcare, `pretura|botanica|ciocana|rascani|buiucani|centru` → district, `mobil|transport|autourban|rtec` → mobility, `salubr|apa|lift|termo` → urban_utilities, `chisinau.md` → city_hall);
   - else one small-model call on `<title>` + meta description + domain, answer limited to the category list;
   - else `other`.

   Store `category_source: "rule" | "llm" | "default"`.
5. **Crawl settings**, automatically:
   - domain root → depth 4, max 2000 pages (sites.toml defaults);
   - a deeper path → depth 2, restricted to that path prefix;
   - document → just download + parse it.
6. **robots.txt** forbids crawling → the row is saved with `robots=blocked`, no job, and the response says so.
7. Otherwise **queue the job immediately**: crawl for a site, download → parse → index for a document.
8. Response: the full `SourceRow` + `detected: {kind, category, category_source, title, crawl_depth, max_pages, reason}`. `reason` is one short English sentence for the toast, e.g. "Detected a website (education, by domain rule); crawling up to depth 4.".

## B3. One list for the single admin page
`GET /api/admin/sources` → every row has everything the table shows:
- identity and settings: `id, kind, url, site_id, title, category, category_source, enabled, robots`;
- `status`: `indexed | pending | running | queued | failed | blocked | disabled`;
- counters: `pages, documents_found, documents_downloaded, chunks, lines, last_crawled`;
- `progress`: `{job_id, stage, percent, eta_s}` or null. The UI polls every 2 s while any row is `running`/`queued`;
- `last_error`: the last job's error, short.

Plus `totals` (same as `/api/corpus/stats` totals), so the page header needs no second call.

Actions stay as they are: refresh (`POST /sources/{id}/jobs {kind:"refresh"}`), enable/disable (PATCH), delete (`?purge=true`), cancel job.

## B4. Demo data for the admin UI
- `frontend/src/lib/mocks/admin/sources.json` — a real `GET /api/admin/sources` response from the current DB after B1:
  - 40 rows;
  - 1 row edited to `running` at 63% with `stage: "parse"`;
  - 1 row `failed` with a real-looking error;
  - 1 `document` row (a PDF link added through B2).
- `frontend/src/lib/mocks/admin/add-source-*.json` — B2 responses: a site, a document, a merged path, a blocked site, and an unreachable 422.
- `api.ts` admin client: mock mode reads these.

## B tests
- Empty DB + startup → 40 sources; restart → still 40 (idempotent).
- B2 with a mocked HTTP layer:
  - PDF by content-type; PDF by extension with wrong content-type;
  - site root; deeper path merged into an existing domain (200 + `merged_into`);
  - duplicate document 409; robots blocked; unreachable 422;
  - category by rule and by LLM (fake LLM) and default.
- B3 list contains `status` and `progress` for a running job (fake worker progress).
- Contract/mocks validate.

## B report
- Output of the seed on the current DB (40 / indexed 5).
- 5 real `POST {url}` responses:
  - `https://acc.md/`;
  - a deeper dgaurf.md page (merged);
  - a PDF link from dgaurf.md;
  - `https://www.chisinau.md/` (blocked);
  - a dead URL.
