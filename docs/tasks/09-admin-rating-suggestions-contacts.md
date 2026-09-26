# Task 09: admin panel for sources, star ratings, quick questions, contact when there's no answer

Read `docs/API.md`, `backend/app/*`, `offline_indexation/tools/pipeline.py`, `offline_indexation/data/sources/sites.toml`.

Four parts. Each part changes the API, so each one changes **`docs/API.md` + `frontend/src/lib/mocks/` in the same PR**, and tell the frontend: they mirror it in `api.ts`. Order: 1 → 2 → 4 → 3.

---

## 1. Admin panel: sources in the DB, crawl from the UI, progress in %

Today the list of sites is hard-coded in `sites.toml` and the pipeline is started by hand. Needed: an admin adds a site or document URL → the server crawls, parses and indexes it → the admin sees progress in % and the result.

### Data (Postgres)
- `sources`:
  - `id`, `kind` (`site` | `document`), `url`, `site_id` (domain), `category`;
  - settings: `start_urls[]`, `max_depth`, `max_pages`, `delay`;
  - state: `enabled`, `robots` (`allowed` | `blocked`), `created_at`, `last_job_id`.
  - One-time import from `sites.toml` (`uv run python -m tools.sources import`). After that the DB is the source of truth. The crawler and pipeline read the DB, and `sites.toml` stays only as the seed.
- `jobs`:
  - `id`, `source_id` (NULL = all), `kind` (`crawl` | `refresh`), `status` (`queued` | `running` | `done` | `failed` | `cancelled`);
  - progress: `stage`, `stage_done`, `stage_total`, `percent`;
  - timing: `started_at`, `finished_at`, `eta_s`;
  - result: `stats` (json: pages, documents found / downloaded / parsed, chunks, lines, embeddings reused/computed, errors), `log_tail` (last 50 lines).

### Worker
- A separate process: `uv run python -m worker` (Mac / Windows / Linux, next to uvicorn).
  - Takes one `queued` job at a time; heavy stages never run in parallel.
  - Runs the stages **for that source only**: crawler `--sites X` → downloader `--sites X` → parsing (new files) → pages_parsing `--sites X` → indexing `--sites X`.
- Progress: every stage reports counters to `jobs` at least every 2 s. The ETA comes from the stage rate.

  | Stage | Weight | `stage_total` |
  |---|---|---|
  | crawl | 20% | `max_pages` (or discovered pages when known) |
  | download | 20% | documents found |
  | parse | 40% | files to parse |
  | index | 20% | documents to index |

- Cancel: the job stops after the current item, status `cancelled`, partial results stay.
- Refresh: `refresh` = `tools.pipeline update` for that source. It replaces changed documents and drops vanished ones (task 06 logic).

### Rules
- `robots.txt` is checked when a source is added. If it forbids crawling, the source gets `robots=blocked` and a crawl can't be started from the UI (chisinau.md stays blocked).
- Only `http(s)`; duplicate domain → 409; a `document` URL must answer with a PDF/DOC/DOCX content type.
- Admin endpoints need header `Authorization: Bearer $ADMIN_TOKEN` (env). Without it → 401 `ApiError`.
- `/api/corpus/stats` must read from `sources`, not from the TOML.

### API (add to docs/API.md)
```
GET    /api/admin/sources                    → { sources: SourceRow[] }  (row = source + last job summary + chunks count)
POST   /api/admin/sources                    { kind, url, category?, max_depth?, max_pages? } → SourceRow (+ job queued if start=true)
PATCH  /api/admin/sources/{id}               { enabled?, max_depth?, max_pages?, category? }
DELETE /api/admin/sources/{id}?purge=true    purge=true also removes its docs from the index
POST   /api/admin/sources/{id}/jobs          { kind: "crawl" | "refresh" } → Job
GET    /api/admin/jobs?status=running        → { jobs: Job[] }
GET    /api/admin/jobs/{id}                  → Job  (the frontend polls every 1–2 s)
POST   /api/admin/jobs/{id}/cancel
```
`Job` = `{id, source_id, kind, status, stage, stage_done, stage_total, percent, eta_s, started_at, finished_at, stats, log_tail, error}`.

### Tests
- Adding a duplicate is rejected; robots-blocked is rejected.
- A job's `percent` goes 0 → 100, and stage weights add up. Use a fake pipeline with stub stages.
- Cancel works.
- Import from `sites.toml` gives 40 sources.

---

## 2. Star ratings for answers

Under each answer: 1–5 stars, plus optional reason chips and a comment.

- `FeedbackRequest` becomes `{ answer_id, rating: 1..5, tags?: string[], comment?, citation_id? }`.
  - Tags: `wrong`, `outdated`, `incomplete`, `wrong_source`, `not_understood`, `helpful`.
  - Keep accepting the old `vote` (`up` = 5, `down` = 1) for compatibility.
  - Re-rating the same `answer_id` from the same `session_id` overwrites the previous rating.
- Storage: Postgres table `feedback`. Each row stores the question, answer status, cited doc_ids and `meta.path`, so a rating can be analysed without the logs.
- `GET /api/admin/feedback?max_rating=2&limit=50` → low-rated answers with question, answer and sources, for review.
- `GET /api/admin/feedback/stats` → average rating, count per star, top tags, trend by day.
- Export: `uv run python -m tools.feedback export` → `eval/from_feedback.yaml`, candidates for the eval set (rating ≤ 2).

---

## 3. Quick questions from recent real questions, checked by their answers

The chips on the empty screen and after an answer should be real popular questions that **we know we answer well**, not hard-coded ones.

- `GET /api/suggestions?lang=ro&limit=6` → `{ items: [{ question, lang, answer_id, asked_count, rating_avg }] }`.
- Candidates come from the query log (`data/query_log`) plus `feedback`. A question qualifies when all of these hold:
  - status `answered`, `meta.verified = true`, ≥ 1 citation;
  - no rating ≤ 2, and `rating_avg ≥ 4` if it was rated;
  - asked at least 2 times (or pinned by the admin);
  - no masked personal data (reuse `wall.mask`; if anything was masked, drop the question);
  - 10–120 characters, the same language as requested, not `refused`.
- Dedupe near-identical questions: cosine similarity > 0.9 on bge-m3 embeddings. Keep the most frequent wording.
- **Re-check the answer**: when the index changes (after a job finishes) and at least once a day, re-ask every candidate through `answer_question` without streaming. Drop it if it's no longer `answered` + verified.
- Instant answer (optional, nice for the demo): cache the last verified `AskResponse` per suggestion, keyed by index version. When a suggestion is clicked, `/api/ask/stream` replays the cached answer as a stream (same events), `meta.path = "cache"`. Add `cache` to the contract.
- Admin: `POST /api/admin/suggestions` `{question, lang, pinned: true}`, `DELETE /api/admin/suggestions/{id}` (hide).
- Empty log: fall back to a curated seed list per language (file in the repo), passed through the same answer check.

---

## 4. No answer → "we can't answer, but this contact can"

When the status is `not_found` or `partial`, the answer ends with 1–2 **real contacts from the corpus** that can help, with the source line.

### Offline: contacts table
New module `offline_indexation/contacts` (`uv run python -m contacts`) over the indexed chunks and lines. No re-crawl, no OCR.
- Take chunks with `has_contacts = true` and the contact/footer pages of every site (help.chisinau.md, dgaurf.md, directions, district preturi).
- Extract `{name (institution / department), area (what they handle, 1 sentence from the page), phone[], email[], address, hours, url, site, category, line_ids[]}`.
  - Phones and emails come **only from lines**, via the regex already in `common/text.check_contacts`.
  - If `area` isn't stated, use the category from the site and the page title.
- Table `contacts` with an embedding of `name + area`.
- Report: how many contacts per site, and 5 examples with their source lines.

### Answering
- When the status is `not_found` or `partial`: vector-search `contacts` with the question. Boost contacts whose `site`/`category` matches the sites of the chunks that were retrieved but not used. Take the top 2 above a threshold.
- If nothing passes, use the general City Hall contact, also from the corpus.
- Never invent contact data: every phone, email and address in the card must be in `line_ids`.
- The answer text ends with "Din păcate nu putem răspunde la această întrebare din documentele disponibile. Credem că vă poate ajuta: …" (RU: «К сожалению, мы не можем ответить на этот вопрос по имеющимся документам. Думаем, вам поможет: …»).

### Contract (add to AskResponse)
```
contacts: ContactCard[] = []
ContactCard = { name, area | null, phone[]: string[], email[]: string[], address | null, hours | null,
                url, site, reason (why this contact, 1 sentence in lang), line_ids[], deep_link }
```
Filled for `not_found` / `partial`, empty otherwise. Add mocks: `not-found-ru.json` and `partial-ro.json` with contacts.

### Tests
- A question outside the corpus returns ≥ 1 contact whose phone/email appears verbatim in its lines.
- The general contact is used when nothing is similar enough.
- No contacts for `answered`.

---

## Report
- **Admin:** a real job on one small site that isn't indexed yet (e.g. `acc.md`), with the job's `stats` and a screenshot or JSON of progress snapshots over time.
- **Ratings:** the stats output after 10 fake ratings.
- **Suggestions:** the list for RO and RU, and how many candidates were dropped by the answer re-check.
- **Contacts:** the count per site; 3 `not_found` questions with the contacts returned.

Numbers only from script output. Tests and ruff green.

## Don't
- No crawling of robots-blocked sites, no bypass flag in the UI.
- No contact data that isn't in a corpus line.
- No public endpoint that returns raw user questions without masking.
