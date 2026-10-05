# Development

How to run Spott on your own machine, stage by stage. The project overview is in the [README](../README.md); testers on Mac / Windows: [TESTER.md](TESTER.md) (in Russian); the API contract: [API.md](API.md).

## Getting started

Testers on Mac / Windows: [TESTER.md](TESTER.md) (in Russian) installs everything and loads the ready-made index dump. Technical notes on every pipeline stage, eval and benchmarks: [README.ru.md](README.ru.md).

Quick start with a ready index dump (Docker Desktop running):

```bash
cp .env.example .env                  # Windows: copy .env.example .env
cd backend
uv sync --all-extras
uv run python -m spott.ingest.tools.index_io import <path/to/index-YYYY-MM-DD.dump>   # starts the DB container, loads the index
uv run python -m spott.ingest.tools.doctor                                           # environment check
uv run qsearch                                                                       # console search (RO / RU)
```

Cross-platform maintenance commands (run in `backend/`; `scripts/*.sh` are thin wrappers for Mac/Linux):

| Command | What it does |
|---|---|
| `uv run python -m spott.ingest.tools.pipeline update` | refresh already crawled sites, replace changed documents, drop removed ones, re-index |
| `uv run python -m spott.ingest.tools.pipeline full [--only crawler downloader]` | full crawl of all allowed sites (never `chisinau.md`) |
| `uv run python -m spott.ingest.tools.index_io export` | dump the index to `data/export/` (not committed) |

### Running stages by hand

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Node.js 20+. Parsing uses Apple Vision OCR on macOS. On Linux it falls back to Tesseract, which needs the `ron` and `rus` language packs installed. Old binary Office files (`.doc`, `.rtf`, `.xls`, `.ppt`) also need [LibreOffice](https://www.libreoffice.org/) (`soffice` on `PATH`); without it they are marked failed and the rest of the corpus parses as usual.

### Corpus building (spott.ingest)

```bash
cd backend
uv sync --all-extras

uv run python -m spott.ingest.crawler --list                          # configured sites
uv run python -m spott.ingest.crawler --max-depth 2 --max-pages 200   # 1. crawl (quick pass); --resume continues an interrupted crawl
uv run python -m spott.ingest.downloader                              # 2. download new documents; --refresh re-checks known ones
uv run python -m spott.ingest.parsing                                 # 3. parse into data/parsed/; --rebuild re-derives output without OCR
uv run python -m spott.ingest.pages_parsing                           # 4. text of crawled HTML pages
docker compose up -d                                     #    (from the repo root) Postgres + pgvector
uv run python -m spott.ingest.indexing                                # 5. chunk, embed and index (incremental)
```

Every command accepts `--help`. All generated data stays in `data/` and is git-ignored.

The first parsing run downloads Docling's layout and table models, which takes a few minutes. After that, parsing takes about 1–3 s per page, including OCR, on an Apple M4.

### Backend

```bash
cd backend
uv run uvicorn spott.api.main:app --reload --port 8000   # API docs at http://localhost:8000/docs
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev                  # http://localhost:3000
```

## Parsed document format

Each document becomes `data/parsed/<sha256>.json`, shortened here:

```json
{
  "metadata": {
    "title": "Cu privire la aprobarea Planului de acțiuni pentru elaborarea ...",
    "doc_type": "decizie", "number": "12/14", "date": "2020-07-28", "lang": "ro"
  },
  "sources": [{
    "url": "https://dgaurf.md/storage/decizie-1214-din-28.07.2020-(1).pdf",
    "found_on": "https://dgaurf.md/ro/documentatii-de-urbanism",
    "found_on_title": "Documentații de urbanism",
    "anchor_text": "Decizia CMC privind elaborarea PUG"
  }],
  "pages":  [{ "n": 1, "text_layer": false }, "..."],
  "blocks": [
    { "id": 7, "type": "list_item", "marker": "1.", "page": 1, "section": ["DECIZIE"], "lang": "ro",
      "text": "1. Se aprobă Planul de acțiuni pentru elaborarea Planului de amenajare a teritoriului ..." },
    { "id": 12, "type": "table", "page": 1, "header": ["Nr. crt.", "Nume și prenume", "Funcția", "Rol în grup"],
      "rows": [["1", "...", "...", "Președinte"]] }
  ]
}
```

The same content is also written as Markdown (`<sha256>.md`) for reading and debugging. The raw Docling output (`<sha256>.docling.json`) is cached so the corpus can be rebuilt without running OCR again.
