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

**Краулер** (результат в `offline_indexation/data/crawl/`):
```bash
cd offline_indexation
uv run python -m crawler --list                          # сайты из Annex 1
uv run python -m crawler --max-depth 2 --max-pages 200   # быстрая разведка
uv run python -m crawler --resume                        # продолжить прерванный обход
```
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
