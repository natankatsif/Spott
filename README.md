# Spott: Chișinău Municipal Assistant

An AI assistant for the Chișinău City Hall (Primăria Municipiului Chișinău) that answers questions from citizens and municipal employees in **Romanian and Russian**, using **only** the City Hall's public documents, and shows the exact document and passage behind every answer.

Built for the Primăria Chișinău challenge at DeepTech GigaHack 2026. **Live demo: [spott-sepia.vercel.app](https://spott-sepia.vercel.app)**

![An answer with its sources: the scanned council decision opens next to the chat, the quoted point highlighted](docs/images/chat.png)

| A Russian question answered from Romanian documents | Admin: sources, processing, unanswered questions, spending |
|---|---|
| <img src="docs/images/chat-mobile-ru.png" width="280" alt="Mobile chat in Russian with a cited Romanian source"> | <img src="docs/images/admin-sources.png" alt="Admin panel, sources page"> |

<sub>Screenshots use the demo answers in `frontend/src/lib/mocks/`, which come from the real index.</sub>

---

## The problem

The information people need is spread across many municipal websites: council decisions, mayor's dispositions, regulations, procedures, contacts, schedules. Much of it is published as PDFs, and many of those are scanned paper documents.

A generic chatbot can't be trusted here. An answer that is incomplete, outdated or invented can mislead someone about their rights, obligations or the procedure they need to follow.

## What the assistant does

Mapped one-to-one to the challenge brief.

### Must-have

| Requirement | How we meet it | Status |
|---|---|---|
| **Answer questions from a defined corpus** | The corpus is built only from the Annex 1 websites. Answers are generated only from passages retrieved from it, never from the model's general knowledge. | ✅ |
| **Romanian and Russian** | Questions in either language, answer in the same language. Retrieval works across languages, so a Russian question can be answered from a Romanian-only document. | ✅ |
| **Cite the exact document and passage** | Every answer cites the act (type, number, date), the passage quoted verbatim, the page and point (e.g. *Decizia nr. 12/14 din 28.07.2020, pct. 5, p. 2*), and links to the original on the City Hall website. Quotes come from the index, with PDF page boxes for highlighting. | ✅ |
| **Flag missing information** | If retrieval finds nothing relevant enough, the answer is `not_found`: the assistant says the corpus doesn't cover the question instead of guessing; `partial` says which part is missing. | ✅ |
| **Flag contradictions** | If sources disagree, the answer is `conflict` and cites all of them with their dates. Example: an older decision amended by a newer one. `outdated` (a newer act replaces the older) is told apart from a real `contradiction`. | ✅ in answers<br>⏳ corpus-wide scan |
| **Website navigation** | Answers include links to the relevant page: a department's contacts, a service portal, a procedure page. Every crawled page (URL, title, language versions) is in the registry. An embeddable widget puts the assistant on any City Hall site. | ✅ links in answers, site widget<br>⏳ highlighting the element on the page |
| **Monthly model-maintenance budget** | External API vs self-hosted model, deployment location, estimated monthly cost. See [Budget](#budget). | ✅ |

### Bonus

| Requirement | How | Status |
|---|---|---|
| **Feedback on answers** | 👍 / 👎 with an optional comment on each answer, stored together with the question, the answer and its sources, so weak spots of the corpus or the retrieval become visible. | ✅ in the chat, reviewed in the admin panel |
| **Innovative solution** | See [What's innovative](#whats-innovative). | partly ✅ |

## What's innovative

- **Scanned acts become searchable and citable.** Most official acts on the sites, such as council decisions and mayor's dispositions, are published as scans with no text at all. The pipeline OCRs them and recovers their structure (points, tables), so they can be cited down to the point. A plain text extractor would find nothing in them. ✅
- **Every quote is traceable.** Each passage carries its full provenance: the file, the page of the PDF, the point of the act, the website page where the document was published, and the link text used there. ✅
- **Newer acts first.** The registry keeps every version of a document, and act numbers and dates are extracted. Alongside the question's own search, the assistant looks for later acts that mention the found acts by number and for newer acts on the same sites. The model sees the newest first, and a partial or conflicting answer gets a second pass over what turned up, so an outdated rule isn't presented as current. ✅
- **Publication quality report for the City Hall.** Cross-checking the site against the documents reveals inconsistencies. We already found a link labelled "Dispoziția nr. 23/1" whose document is actually a *Decizie*. Collected into a report, these checks help the City Hall fix its own publications. ⏳
- **Anti-hallucination guard.** The model never writes quotes. It only points at numbered lines of the retrieved passages; the quotes are taken from the index, a sentence without a backing line is dropped, and a sentence whose numbers don't appear in its quotes is marked unverified. ✅

## How it works

The system has two halves. **Offline indexation** turns the City Hall websites into a searchable, citable corpus. The **online** part answers questions against that corpus.

```
                        OFFLINE INDEXATION                                        ONLINE
┌───────────┐   ┌─────────┐   ┌────────────┐   ┌─────────┐   ┌────────────┐
│ 42 public │──►│ crawler │──►│ downloader │──►│ parsing │──►│ chunking + │──► index ◄── backend ◄── frontend
│ websites  │   └─────────┘   └────────────┘   └─────────┘   │  indexing  │             (retrieval,   (chat,
└───────────┘   pages, doc    files, dedup     text, OCR,    └────────────┘              LLM answer    RO / RU)
                links         by SHA-256       structure     Postgres+pgvector           with citations)
                     └──────────────┴────────────────┴─ Postgres registry ─┘
```

### Offline indexation

Each stage is a separate command. The stages share one registry, the `registry_*` tables in Postgres, so every stage is incremental and can be re-run on its own.

1. **Crawler.** Breadth-first crawl of the sites listed in Annex 1. It records every page and every link to a document (PDF, DOC/DOCX, XLS/XLSX, ODT, …), including where the link was found and its anchor text. This provenance is later used for citations. On WordPress sites it also reads the media library API, which finds files that no page links to. It respects `robots.txt` and rate limits, and can resume after an interruption.
2. **Downloader.** Stores files by content hash (`data/raw/<sha256>.<ext>`). Deduplication happens at three levels:
   - URLs already downloaded are skipped;
   - re-checks use conditional HTTP requests, so unchanged files return `304 Not Modified` without a body;
   - identical content found under different URLs is stored and parsed once.

   A changed file keeps its previous version in the history.
3. **Parsing.** Converts each file into a structured text representation using [Docling](https://github.com/docling-project/docling):
   - layout analysis recovers headings, numbered points and tables;
   - OCR (Apple Vision on macOS, Tesseract elsewhere) handles scanned pages. Most official acts on the sites are scans;
   - text normalization fixes Romanian diacritics (ş/ţ → ș/ț) and mixed Latin/Cyrillic look-alike characters;
   - metadata extraction pulls out act type, number, date and language (ro / ru / uk / en).
4. **Chunking and indexing.** Splits documents into citable passages along their structure (article / point `legal_path`, headings attached) and into single **lines**, so a fact that lives in one line can be found and quoted on its own. Everything goes into Postgres 17 + pgvector:
   - vector search with the multilingual `bge-m3` model (one space for Romanian and Russian), over passages and over lines;
   - full-text search with Romanian and Russian stemming (unaccented), plus trigram `grep` for exact act numbers and street names;
   - results are fused with weighted RRF; each hit carries a deep link to the PDF page (`#page=N`) or the exact text on the web page (`#:~:text=`).

   Documents are keyed by their source URL: a changed file **replaces** the old version in the index (history stays in the registry), and a document missing from the site on two crawls in a row is removed. Unchanged text reuses its embeddings.
5. **Corpus tools.** `search`, `grep`, `toc` and `open` walk the corpus like a file tree and quote lines by their id; testers use them in the `qsearch` console (`:grep`, `:toc`, `:open`).

### Online

- **Backend** (FastAPI, contract in [docs/API.md](docs/API.md)). Retrieves the most relevant passages and gives the LLM their lines, numbered. The model answers only from them and marks which lines back each sentence; the quotes are then taken from the index (never written by the model), sentences without a backing line are dropped, and a sentence whose numbers don't appear in its quotes is marked unverified. Answers stream over SSE (`/api/ask/stream`).
- **Frontend** (Next.js). Chat interface with a Romanian / Russian switch. It shows each answer with its sources and navigation links.

## Data sources

The corpus is built from the websites listed in the challenge's Annex 1: 42 domains across transparency, urban mobility, architecture and utilities, education, healthcare, district administrations, and public services. The list, with per-site crawl settings, is in [`data/sources/sites.toml`](data/sources/sites.toml).

> `chisinau.md` disallows all crawling in its `robots.txt`, so it is skipped by default. It can be enabled per site with `ignore_robots = true` once crawling has been agreed with the City Hall.

## Repository layout

Two independent projects that talk only over HTTP: the Python backend and the Next.js frontend. The contract between them is [`backend/openapi.json`](backend/openapi.json), generated from the API's models; the frontend's types are generated from it.

| Directory | Stack | Purpose |
|---|---|---|
| [`backend/`](backend) | Python 3.14, uv | One package, `spott` (one `pyproject.toml`, one `uv.lock`), in three layers: |
| [`backend/src/spott/core/`](backend/src/spott/core) | pgvector, bge-m3 | the database schema, embeddings, hybrid search, corpus tools, the `qsearch` console |
| [`backend/src/spott/ingest/`](backend/src/spott/ingest) | Docling | corpus building: `crawler`, `downloader`, `parsing`, `pages_parsing`, `chunking`, `indexing`, the admin `worker`, `tools` (doctor, pipeline, index export/import) |
| [`backend/src/spott/api/`](backend/src/spott/api) | FastAPI | the question answering and admin API |
| [`backend/eval/`](backend/eval), [`backend/scripts/`](backend/scripts) | | eval sets, benchmarks and one-off scripts |
| [`frontend/`](frontend) | Node, Next.js 16 | Chat UI, admin panel, site widget |
| [`data/`](data) | | everything generated (crawl, files, dumps, logs; `SPOTT_DATA_DIR`), only `data/sources/sites.toml` is versioned |

`api` and `ingest` both build on `core` and never import each other; CI checks it (`uv run lint-imports`, rules in `backend/pyproject.toml`). The API needs only the `api` extra, the pipeline the `ingest` extra (Docling, OCR); `uv sync --all-extras` installs both.

## Run the backend in Docker (one command)

Needs only Docker. Put the index dump (`index-YYYY-MM-DD.dump`, made with `python -m spott.ingest.tools.index_io export` on the machine that has the index) into `data/export/`, then:

```bash
./start.sh            # database + API + admin worker → http://localhost:8000
./start.sh --public   # also a public https://….trycloudflare.com address (for the Vercel frontend), no server needed
./start.sh --https    # on a server with its own domain: Caddy + Let's Encrypt for DOMAIN from .env
./start.sh --stop
```

The first run asks for the OpenAI key and writes `.env` (random database and admin passwords, printed once); the first build downloads ~3 GB (CPU-only torch, the bge-m3 model baked into the image). On start the API container creates the schema, restores the dump into an empty database, fills the sources and builds the contact cards. A server that should update itself on every push to `main`: set the `DEPLOY_*` secrets described in `.github/workflows/ci.yml`.

## Getting started

**New here or testing on Mac / Windows: follow [docs/TESTER.md](docs/TESTER.md)** (in Russian). Running every part locally, stage by stage, and the format of parsed documents: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Technical notes on every pipeline stage, eval and benchmarks: [docs/README.ru.md](docs/README.ru.md).

## API

`POST /api/ask`

```json
{ "question": "Cum obțin certificatul de urbanism?", "lang": "ro" }
```

```json
{
  "status": "answered | partial | not_found | conflict | refused",
  "lang": "ro",
  "answer": "...",
  "sentences": [{ "text": "...", "cites": ["c1"] }],
  "citations": [{ "id": "c1", "document_title": "Decizia nr. 79 din 27.07.2021 ...", "location": "pct. 2", "page": 1,
                  "quote": "...", "translation": null, "url": "https://dgaurf.md/storage/...pdf", "preview_url": "..." }],
  "nav_links": [{ "title": "...", "url": "...", "kind": "page" }],
  "followups": ["..."]
}
```

Shortened; the full contract, including the streaming events of `/api/ask/stream`, is in [docs/API.md](docs/API.md). It is defined in [`backend/src/spott/api/schemas.py`](backend/src/spott/api/schemas.py) and mirrored in [`frontend/src/lib/api.ts`](frontend/src/lib/api.ts).

## Budget

Measured on this code unless marked as an estimate. Admin → Spending tracks the real spend: tokens of every model call, cost per model and per day, a monthly budget and the month's forecast.

**What runs where**

| Part | Where | What it costs |
|---|---|---|
| Answer model, query rewrite for Russian questions | external API: OpenAI by default; Claude, Gemini or an OpenAI-compatible server of your own can be switched in Admin → Models | tokens per question |
| Crawling, OCR (Tesseract), embeddings (bge-m3), hybrid search, Postgres + pgvector, the background worker | one server, CPU only (`./start.sh`) | the server |
| Chat UI | Vercel, or `next start` on the same server | nothing extra on the same server |

**Indexing** makes no paid API calls. OCR, structure recovery and embeddings run on the server's CPU, so the initial corpus costs only CPU hours, and the background worker works through it batch by batch. Nightly updates re-process only what changed.

**Answering, per question.** The prompt is ~3,650 tokens (p50). A second model call happens for ~25% of questions, those about later amendments to an act. Measured with `backend/scripts/eval_speed.py` over all model calls of a question (REPORT.md, "Задача 10"):

| Model | $ per 100 questions | Quality on our eval set |
|---|---|---|
| `gpt-6-luna` | 0.067 | within the noise of gpt-4o; 100% verified sentences, 0% false contradictions |
| `gpt-4o` (the team's default) | 1.22–1.45 | reference |

**Monthly estimate by traffic**, model calls only:

| Questions per month | `gpt-6-luna` | `gpt-4o` |
|---|---|---|
| 3,000 (~100 a day) | ~$2 | $36–44 |
| 30,000 (~1,000 a day) | ~$20 | $365–435 |
| 300,000 (~10,000 a day) | ~$200 | $3,650–4,350 |

The daily re-check of quick questions adds at most 20 calls a day: under $0.50 a month on `gpt-6-luna`, under $9 on `gpt-4o`. Rate limits matter as much as price: our OpenAI organisation has 30,000 tokens per minute on `gpt-4o`, about 7 questions a minute for everyone, against 200,000 on `gpt-6-luna`.

**Server.** 8 GB of RAM is the floor: Postgres with the index, the API with bge-m3 in memory, and parsing of long scanned PDFs, which once reached ~6 GB before pages were rendered one at a time. Example: Hetzner Cloud CX33 (4 vCPU, 8 GB, 80 GB SSD), €6.49 a month after its April 2026 price change ([price check](https://costgoat.com/pricing/hetzner), verify before ordering).

**A self-hosted model instead of the API.** Any OpenAI-compatible server (Ollama, vLLM) plugs in through Admin → Models. A GPU server that can run a mid-size open model starts at about €184–234 a month (Hetzner GEX44, RTX 4000 Ada 20 GB; [prices vary by source](https://bex.co/blog/2026/07/13/hetzner-gex44-gpu-pricing-break-even)). That equals the API cost of roughly 300,000+ questions a month on `gpt-6-luna`, or 15,000–20,000 on `gpt-4o`. We have not measured an open model's quality on our eval set (Romanian and Russian, strict JSON with line references). So self-hosting pays off only when questions must not leave the city's infrastructure, or at gpt-4o-class quality and high volume.

**Recommendation:** `gpt-6-luna` through the API plus one 8 GB server. That is about €7 a month for the server and $2–20 a month for the model at 3,000–30,000 questions, under €30 a month in total.

## Status

| Part | State |
|---|---|
| Crawler, downloader, parsing (incl. OCR) | ✅ working, tested on a subset of the sites |
| Chunking, line index, hybrid search (Postgres + pgvector) | ✅ ~0.1–0.2 s per query; the background worker indexes the remaining sites batch by batch |
| Document updates (replace, not duplicate; removal of vanished documents) | ✅ |
| Corpus tools `search / grep / toc / open` in the `qsearch` console for testers | ✅ |
| Cited answers `/api/ask` + `/api/ask/stream` (docs/API.md): answered / partial / not_found / conflict / refused, checklists, translations of quotes | ✅ fast path; agent path (`mode=deep`) ⏳ |
| Mac / Windows setup, CI on Linux + Windows (backend) and frontend lint + build | ✅ |
| Later acts that amend or repeal a cited act, found by number and put first | ✅ |
| Corpus-wide contradiction scan, highlighting the element on the page from the widget | ⏳ planned |
| Chat UI | ✅ streaming answers with inline citations, the source opened next to the answer with the quote highlighted (PDF and web pages), RO / RU / EN, light / dark, mobile; embeddable site widget |
| Admin panel | ✅ sources added by URL, processing jobs with progress, unanswered questions grouped by topic, ratings, quick questions, models and spending |
| Automatic updates | ✅ nightly check of what changed, weekly full refresh, earlier on users' signals |
| Feedback, corpus totals in the admin | ✅ |
| Legacy and OpenDocument files (`.doc`, `.rtf`, `.xls`, `.ppt`, `.odt`, `.ods`, `.odp`) | ✅ the binary formats through LibreOffice (in the Docker image) |
| Monthly maintenance budget | ✅ [Budget](#budget) |

## Team

Built at DeepTech GigaHack 2026 by [Natan Katsif](https://github.com/natankatsif), [Karnavski S.](https://github.com/rlwq), [TheMorkovkaBest](https://github.com/TheMorkovkaBest) and [Pooromens](https://github.com/Pooromens).

- **Natan Katsif**: chunking and the line index, hybrid search, the chat and admin UI, source preview.
- **Karnavski S.**: crawler, downloader, parsing and OCR; the answering API with verified citations, streaming, the search for later acts; admin backend and the background worker; Docker deploy.
