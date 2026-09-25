# Chișinău Municipal Assistant

AI-ассистент Примэрии Кишинэу (челлендж DeepTech GigaHack 2026): отвечает на вопросы граждан и сотрудников на румынском и русском **только по корпусу публичных документов**, цитирует документ и фрагмент, явно сообщает, если информации нет или документы противоречат друг другу, и направляет на нужную страницу сайта.

## Структура

Три независимых проекта:

| Папка | Стек | Назначение |
|---|---|---|
| [`offline_indexation/`](offline_indexation) | Python, uv | Сбор корпуса: обход сайтов из Annex 1, поиск документов, дальше — оцифровка (OCR) и индексация |
| [`backend/`](backend) | Python, uv, FastAPI | API ассистента: поиск по корпусу, ответ с цитатами |
| [`frontend/`](frontend) | Node, Next.js | Веб-чат (RO / RU) |

## Запуск

**Offline indexation** — этапы пишут в общий реестр `offline_indexation/data/registry.sqlite`:
```bash
cd offline_indexation
uv run python -m crawler --list                          # сайты из Annex 1
uv run python -m crawler --max-depth 2 --max-pages 200   # 1. обход сайтов → страницы и ссылки на документы
uv run python -m crawler --resume                        #    продолжить прерванный обход
uv run python -m downloader                              # 2. скачать новые документы → data/raw/<sha>.<ext>
uv run python -m downloader --refresh                    #    проверить скачанные на обновления (304 = не изменился)
uv run python -m parsing                                 # 3. разобрать файлы (Docling + OCR) → data/parsed/<sha>.json и .md
uv run python -m parsing --rebuild                       #    пересобрать JSON/MD из кэша Docling, без повторного OCR
```

`data/parsed/<sha>.json` — текстовое представление документа: метаданные (тип акта, номер, дата, язык), источники (URL и страница сайта, где найден), страницы (текстовый слой или OCR) и блоки (`heading` / `paragraph` / `list_item` / `table`) с номером страницы и путём разделов — из них дальше режутся фрагменты для поиска и цитирования.
Список сайтов и их настройки — [`offline_indexation/data/sources/sites.toml`](offline_indexation/data/sources/sites.toml).

**Backend** (http://localhost:8000, документация API — `/docs`):
```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

**Frontend** (http://localhost:3000):
```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

## Контракт API

`POST /api/ask` → `{ status: "answered" | "not_found" | "conflict", lang, answer, citations[], nav_links[] }`.
Описан в [`backend/app/schemas.py`](backend/app/schemas.py), зеркально — в [`frontend/src/lib/api.ts`](frontend/src/lib/api.ts).
