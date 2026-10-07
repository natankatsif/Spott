# Task 11 on the frontend: source preview, admin sources, gaps

How to wire what task 11 added. The contract is in `docs/API.md` (Citation `preview_url`/`preview_kind`, `GET /api/preview`, admin sources, gaps). This file is the research: what works, what breaks, and why.

## 1. Inline preview on desktop (AI Elements `WebPreview`)

The component isn't in the repo yet. Add it with `npx ai-elements@latest add web-preview`, or copy `web-preview.tsx` from github.com/vercel/ai-elements if the registry is unreachable.

```tsx
import { API_URL } from "@/lib/api";
import { WebPreview, WebPreviewNavigation, WebPreviewUrl, WebPreviewBody } from "@/components/ai-elements/web-preview";

// /api/… comes from the backend (prefix API_URL); /mocks/preview/… is a static file of the frontend (as is).
export const resolvePreviewUrl = (u: string) => (u.startsWith("/api/") ? `${API_URL}${u}` : u);

export function SourcePreview({ citation }: { citation: Citation }) {
  const src = resolvePreviewUrl(citation.preview_url);
  return (
    <WebPreview defaultUrl={src} className="h-[520px]">
      <WebPreviewNavigation>
        <WebPreviewUrl readOnly value={citation.url} /> {/* show the city hall URL, not ours */}
      </WebPreviewNavigation>
      <WebPreviewBody src={src} ref={frameRef}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-presentation"
        referrerPolicy="no-referrer" loading="eager" />
    </WebPreview>
  );
}
```
- **Which citation opens first:** `answer.focus_citation_id`, else the first citation. The preview is part of the answer; don't wait for a click on desktop.
- **Switching citations [n]:**
  - Same `doc_id` as the one shown → no reload:
    ```ts
    frame.contentWindow?.postMessage({ type: "src-preview:highlight", line_ids: c.line_ids }, new URL(src).origin)
    ```
  - Another document → change `src`.
- **Listening:**
  ```ts
  window.addEventListener("message", (e) => {
    if (e.source !== frameRef.current?.contentWindow) return; // this iframe only
    if (e.data?.type !== "src-preview:ready") return;
    setFound(e.data.found); // "exact" | "words" | "start" | "none"
  });
  ```
  - Check `e.source`: several previews can be on the page, and in mock mode the origin is your own.
  - `found === "none"`: show a small badge "pagina s-a schimbat / страница изменилась". The preview's own banner already shows the quote.
- **Sandbox:** keep `allow-same-origin` (pdf.js needs it) and `allow-scripts`. Add **`allow-popups-to-escape-sandbox`**: without it, "Deschide originalul ↗" opens the city hall site sandboxed, and some pages break there.
- **Height:** 480–560 px on desktop. The banner is ~32 px and sticky, and the highlighted line is scrolled to the centre.
- **Loading / timeout:**
  - Measured time to `ready`, on the e2e run of the branch's first commit (backend agent): 0.1–2.4 s. Pages come fast; the slowest is a scanned PDF (pdf.js + wasm).
  - Show a skeleton until `ready`.
  - After **8 s** with no `ready`: show "Deschide originalul" with `citation.deep_link` next to the frame. Leave the frame in place: it may still finish.
- **Dark mode:** a `page` preview is the city hall site's own look (light); `pdf` is the grey pdf.js backdrop; `text` follows `prefers-color-scheme`. Don't try to theme the iframe.

## 2. Mobile (< 768 px)
No inline window. Each citation is a chip:
```
{site} · {document_title}{location ? ", " + location : ""} ↗
```
On tap, open the preview full screen. Two ways:
1. **A full-screen `Sheet` with the same iframe, `embed=1` (recommended).** Your own "← Înapoi / ← Назад" closes the sheet. Chat state stays, there are no popup blockers, and `postMessage` works the same way.
2. **A new tab with `embed=0`.** Add `&embed=0`: the preview then shows its own "← back" link, which does `window.close()` when it has an `opener`, else `history.back()`.
   - Open with `window.open(url, "_blank")` **without** `noopener` (the page is ours), otherwise the back link has nothing to close.
   - Not verified on a real iPhone yet.

Before the demo, check both on iOS Safari and Android Chrome. Nobody has, yet.

## 3. Admin: sources, one page
**Header:** `totals` from the same response: sites indexed / total, pages, documents, chunks.

**Add a source:** one input + one button.
```ts
const r = await adminFetch("/api/admin/sources", { method: "POST", body: JSON.stringify({ url }) });
```

| Response | UI |
|---|---|
| 201 | toast "Sursă adăugată" + `detected` (kind, category, depth); the row appears with `status: queued` |
| 200, `merged_into` | toast "Adăugat la {site_id}"; highlight row `merged_into` |
| 201/200 with `robots: "blocked"` | warning toast "robots.txt interzice indexarea — salvat, fără indexare" |
| 409 `conflict` | "Deja există" (the message names the site) |
| 422 `validation_error` | "Site-ul nu răspunde / link invalid" + `message` |

`detected.reason` is English; build the RO/RU toast text from `detected.kind`, `category`, `crawl_depth`.

**Polling:** `GET /api/admin/sources` every **2 s** while any row is `running` or `queued`. Stop when none is, and start again after an add or a refresh. One call has everything: no per-row requests.

**Table:**
- Columns: site / document (`title ?? site_id`, `url`) · category (+ `category_source` as a hint) · status badge · progress · pages · documents (`documents_downloaded`/`documents_found`) · chunks · last crawled · actions.
- `progress`: a bar with `percent`, `stage` (crawl / download / parse / index → RO/RU labels) and `eta_s` ("~2 min").

| status | badge | RO | RU |
|---|---|---|---|
| indexed | green | Indexat | Проиндексирован |
| running | blue + spinner | Se indexează | Индексируется |
| queued | blue outline | În coadă | В очереди |
| pending | grey | Neindexat | Не проиндексирован |
| failed | red + `last_error` tooltip | Eroare | Ошибка |
| blocked | amber | Blocat de robots.txt | Запрещён robots.txt |
| disabled | grey outline | Dezactivat | Выключен |

**Row actions:**
- Refresh: `POST /sources/{id}/jobs {kind:"refresh"}`. Disabled when `blocked` or `disabled`, or while a job runs.
- Enable / disable: `PATCH {enabled}`.
- Category: `PATCH {category}`.
- Delete: `DELETE ?purge=true`. Confirm it: it removes the documents from the index.
- Cancel: `POST /jobs/{progress.job_id}/cancel`.

## 4. Admin: gaps block (same page)
`GET /api/admin/gaps` → cards or rows, biggest first:
- the example question;
- `count` × asked, last time;
- language chips;
- status (`not_found` red / `partial` amber);
- `missing` as "Lipsește: …";
- `hint_sites` as "Caută la: dgaurf.md (5)".

Actions:
- "Adaugă sursă" focuses the add-URL input.
- "Verifică din nou" → `POST /gaps/{id}/recheck`. This is **one GPT call**: disable the button while it runs and never loop over it. When it comes back `answered`, remove the card with a small success toast.
- "Ascunde" → `/hide`; a "hidden" filter uses `?hidden=1` + `/unhide`.

The demo loop: a gap → paste the missing document's link → wait until its row is `indexed` → Recheck → the card disappears.

## 5. Mock mode
- Ask mocks already carry `preview_url` = `/mocks/preview/<mock>-<citation>.html`. Those are real static previews in `frontend/public/mocks/preview/`: pdf.js, the PDFs, highlights and `postMessage` all work, with no backend.
- Regenerate them after the mocks change: `cd backend && uv run python scripts/export_previews.py ../frontend` (reads the local index files, no DB, no LLM).
- Admin mocks are in `src/lib/mocks/admin/`:
  - `sources.json`: 40 sites + a document; one running at 63 %, one failed, one blocked;
  - `add-source-{site,document,merged,blocked,unreachable}.json`;
  - `gaps.json`, `gap-recheck.json`, `gap-hide.json`.

  Regenerate: `cd backend && uv run python scripts/make_admin_mocks.py ../frontend`. Validated by `npm run check:mocks` (frontend) against `backend/openapi.json`.
- In mock mode, the polling can simply stop after the first response: the mock never finishes.

## 6. Gotchas we hit
- **`frame-ancestors` = `CORS_ORIGINS`.** If the frontend origin isn't in the backend's `CORS_ORIGINS` (a new dev port, `127.0.0.1` vs `localhost`, a Vercel preview URL), the iframe stays blank. The only trace is a CSP error in the console of the *parent* page. Add every origin you serve from.
- **pdf.js modern build = blank PDF on older browsers.** pdf.js 6.x's modern build calls `Map.prototype.getOrInsertComputed`, which Chromium 141 doesn't have. The preview then never sends `ready`. The backend now vendors the **legacy** build, which polyfills it; checked in Chromium 141. Don't "upgrade" it back to `build/`.
- **Scanned PDFs** need WebAssembly (`'wasm-unsafe-eval'` is in the preview's CSP). If you proxy or re-host the preview, keep its CSP header.
- **Pages built by JavaScript** (e.g. dgaurf.md services) show an empty shell without the site's scripts, so the backend returns the `text` view for them. That is expected, not a bug.
- **The city hall site is down:** CSS and images of a `page` preview don't load (they come from the original through `<base>`). The text and the highlight still work.
- **Safari < 17.2 / old Firefox** have no CSS Custom Highlight API. The preview falls back to `<mark>` automatically; nothing to do.
- When matching iframes by URL (e.g. several previews), the parent URL contains `/api/preview/`: compare `e.source`, not URLs.

## 7. Screenshots and test page
- `backend/tests/preview_harness/index.html` is the iframe setup the e2e test uses: same attributes, other origin. Copy it.
- Screenshots of the e2e run are in `backend/tests/artifacts/preview/` (gitignored; run `test_preview_e2e.py` to get them).
