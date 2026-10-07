# Инструкция тестировщика

Как поставить проект на **Mac или Windows**, загрузить готовый поисковый индекс и проверять поиск из консоли. Вся установка занимает 20–30 минут, большая часть из них — скачивание.

---

## 1. Что уже реализовано

Мы делаем ассистента Примэрии Кишинэу. Он отвечает на вопросы на румынском и русском **только по публичным документам**, цитирует конкретную строку документа и честно говорит, если ответа в документах нет.

| Часть | Статус | Что делает |
|---|---|---|
| Обход сайтов (`crawler`) | ✅ | Обходит сайты из списка Annex 1 (`data/sources/sites.toml`), соблюдает robots.txt. `chisinau.md` не обходим: robots.txt это запрещает |
| Скачивание (`downloader`) | ✅ | Качает PDF/DOCX. Изменившийся файл **заменяет** старую версию, а не лежит рядом. Документ, пропавший с сайта два обхода подряд, удаляется из поиска |
| Разбор (`parsing`, `pages_parsing`) | ✅ | Достаёт текст из PDF и страниц, распознаёт сканы (OCR: на Mac — Apple Vision, на Windows — Tesseract) |
| Нарезка (`chunking`) | ✅ | Режет документы на куски по статьям/пунктам и на отдельные строки |
| Индекс (`indexing`) | ✅ | Кладёт куски и строки в Postgres с векторами (модель `bge-m3`, понимает RO и RU) |
| Поиск | ✅ | Гибридный: векторный + полнотекстовый. Находит нужную строку и даёт ссылку прямо на страницу PDF или место на сайте. ~0,1–0,2 с на вопрос |
| **`qsearch` — консольный поиск** | ✅ | То, что ты тестируешь: вопрос → топ-5 строк с источниками + твои отметки «правильно / неправильно» |
| API (`backend`, FastAPI) | ✅ | Ответы с цитатами (`/api/ask`), админка, предпросмотр источников |
| ИИ-ответы (GPT) | ⏳ | Следующий этап |
| Сайт/виджет (`frontend`) | ⏳ | Позже |

Сейчас в индексе **5 сайтов из 40**: autosalubritate.md, mobilitatechisinau.md, help.chisinau.md, dgaurf.md, proiecte.chisinau.md. Про остальные поиск честно ничего не найдёт — это не баг.

### Как это устроено и при чём тут Docker

```
твой компьютер
├── Docker Desktop
│   └── контейнер qwerty-pgvector  ← база Postgres 17 + pgvector: тут лежит индекс
│                                    (порт 5432, данные в томе pgdata — переживают перезапуск)
└── папка проекта (Python, uv)
    ├── qsearch                    ← консольный поиск: модель bge-m3 превращает вопрос в вектор,
    │                                 дальше запрос в базу
    └── backend/src/spott/ingest/tools   ← doctor (проверка), pipeline (обход сайтов), index_io (дамп)
```

- **Docker** нужен только для базы. Сам Python-код работает прямо на компьютере.
- База описана в `docker-compose.yml`, пароль и порт берутся из `.env`.
- Индекс мы передаём **дампом** — одним файлом `index-<дата>.dump` (~55 МБ). Его нет в git: пришлём в Telegram / Drive.
- Модель `bge-m3` (~2,3 ГБ) скачается сама при первом запуске поиска в `~/.cache/huggingface`.

---

## 2. Установка

### Что понадобится
- 10 ГБ свободного места, 8 ГБ RAM (лучше 16);
- интернет для скачивания (~6 ГБ);
- файл дампа индекса от нас.

### Mac

Открой **Терминал**:

```bash
# 1. Инструменты (если нет Homebrew: https://brew.sh)
brew install git uv
brew install --cask docker          # затем открой Docker Desktop из Программ и дождись "Running"

# 2. Проект
git clone https://github.com/rlwq/DocumentParsing.git qwerty
cd qwerty
cp .env.example .env                # ключ OpenAI для тестирования не нужен

# 3. Зависимости (нужен Python ≥ 3.12, нет подходящего — uv скачает сам; первый раз 5–10 минут)
cd backend
uv sync --all-extras

# 4. Индекс (путь к присланному файлу)
uv run python -m spott.ingest.tools.index_io import ~/Downloads/index-2026-09-26.dump

# 5. Проверка — все пункты должны быть ✅ или ⚠️
uv run python -m spott.ingest.tools.doctor
```

### Windows

Открой **PowerShell** (лучше в Windows Terminal — там правильно отображаются ș, ț, ă, ы):

```powershell
# 1. Инструменты
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id Docker.DockerDesktop -e
# Перезапусти PowerShell (чтобы появились команды git и uv).
# Открой Docker Desktop, прими условия и дождись статуса "Engine running".
# Если Docker просит WSL 2 — согласись и перезагрузи компьютер.

# 2. Проект (путь покороче: у Windows ограничение на длину путей)
cd C:\
git clone https://github.com/rlwq/DocumentParsing.git qwerty
cd qwerty
copy .env.example .env

# 3. Зависимости (первый раз 5–10 минут)
cd backend
uv sync --all-extras

# 4. Индекс
uv run python -m spott.ingest.tools.index_io import $HOME\Downloads\index-2026-09-26.dump

# 5. Проверка
uv run python -m spott.ingest.tools.doctor
```

**Tesseract (OCR сканов) для тестирования поиска не нужен.** Понадобится, только если попросим тебя разбирать документы: поставь с https://github.com/UB-Mannheim/tesseract/wiki, в установщике отметь языки **Romanian** и **Russian**. Если `doctor` его не видит, пропиши путь в `.env`:
```
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

---

## 3. Тестирование поиска: `qsearch`

Из папки **`backend`** (`qwerty/backend`):

```bash
uv run qsearch
```

Первый запуск грузит модель (до минуты, первый раз ещё скачивает ~2,3 ГБ). Потом появится `❯` — пиши вопрос на румынском или русском и жми Enter.

Пример выдачи:
```
[1] 0.0328  RO  proiecte.chisinau.md  Transport public — achiziții 2020  p.3
     предыдущая строка (серым)
   ▸ Propunerea grupului de lucru, acceptată de CMC: achiziționarea în 2020 a 100 autobuze…
     следующая строка (серым)
     https://proiecte.chisinau.md/...pdf#page=3

⏱ 140 мс · отметь: :ok N / :bad / :none
```

Что где:
- `[1]` — номер результата;
- число — оценка поиска (сравнивать только внутри одной выдачи);
- `RO`/`RU` — язык документа;
- `▸` — найденная строка, слова из вопроса подсвечены жёлтым;
- ссылка открывает PDF сразу на нужной странице.

### Главное — ставь отметку после каждого вопроса

| Команда | Когда |
|---|---|
| `:ok 2` | правильный ответ есть, в результате №2 |
| `:bad` | правильного ответа нет в выдаче (а в документах он, по-твоему, есть) |
| `:none` | ответа в документах нет, и это правильно (вопрос вне корпуса) |
| `:note текст` | комментарий: «ответ есть, но на 7-м месте», «ссылка битая», «строка — мусор OCR» |

Отметки и вопросы сами пишутся в `data/test_logs/<дата>.jsonl`. Из них мы собираем честную оценку качества.

### Полезные команды

| Команда | Что делает |
|---|---|
| `:open 2` | показать текст куска из результата 2 целиком |
| `:toc 2` | оглавление документа из результата 2 |
| `:grep 100 autobuze` | найти точную фразу во всех документах |
| `:lang ro` / `:lang ru` / `:lang all` | искать только в документах на одном языке |
| `:k 10` | показывать 10 результатов вместо 5 |
| `:help`, `:q` | помощь, выход |

Разовый поиск без интерактива: `uv run qsearch "вопрос"`, для скриптов — `uv run qsearch "вопрос" --json`.

### Что спрашивать

1. **Вопросы с конкретным ответом** в одной строке (число, дата, телефон, адрес, кто отвечает):
   - `Ce tip de transport public va fi achiziționat în Chișinău în anul 2020?`
   - `Care este numărul de telefon al Liceului Teoretic „M. Kotiubinski” din Chișinău?`
   - `Cine deține terenul destinat gestionării deșeurilor solide în Chișinău?`
2. **Вопрос на русском, документ на румынском** — это важно, так будут спрашивать жители:
   - `Что планируется установить рядом со станцией ожидания пассажиров?`
   - `Какие занятия предлагаются в летнем лагере Green Gate для детей?`
3. **Вопросы, ответа на которые точно нет** — правильное поведение: ничего похожего в топе, отметка `:none`:
   - `Как записаться на курс дрессировки домашних крокодилов в муниципальном приюте?`
4. Формулируй **как обычный житель**: с опечатками, разговорно, без диакритики (`deseuri` вместо `deșeuri`).

Цель на сессию — **30–50 вопросов с отметками**. Посмотреть свою сводку:
```bash
uv run qsearch --report
```

### Как отправить нам результаты
Пришли файлы из папки `data/test_logs/` (в корне проекта). В git они не попадают.

---

## 4. Если что-то сломалось

Сначала запусти `uv run python -m spott.ingest.tools.doctor` из папки `backend`: он скажет, что не так, и подскажет, как исправить.

| Симптом | Что делать |
|---|---|
| `Нет соединения с базой` | Открой Docker Desktop, дождись Running, затем в корне проекта `docker compose up -d` |
| `База пустая` | Не импортирован дамп — шаг 4 установки |
| `port 5432 is already allocated` | На компьютере уже есть Postgres. Поставь в `.env` `POSTGRES_PORT=5433`, затем `docker compose up -d` |
| Кракозябры вместо ș / ы | Windows: запускай в Windows Terminal (PowerShell 7) |
| `uv: command not found` | Перезапусти терминал после установки uv |
| Поиск первый раз очень долгий | Скачивается модель ~2,3 ГБ, один раз |
| Поиск медленный (секунды) | Нормально без видеокарты: модель считает на процессоре |
| `Filename too long` при `git clone` (Windows) | `git config --global core.longpaths true` и клонируй в `C:\qwerty` |

Не получилось — пришли вывод `doctor` и текст ошибки.

---

## 5. Для команды: другие команды

Всё запускается из `backend/` и работает одинаково на Mac и Windows:

```bash
uv run python -m spott.ingest.tools.doctor                         # проверка окружения
uv run python -m spott.ingest.tools.index_io export                # выгрузить индекс в data/export/index-<дата>.dump
uv run python -m spott.ingest.tools.index_io import <файл.dump>    # загрузить индекс
uv run python -m spott.ingest.tools.pipeline update                # обновить уже обойдённые сайты и переиндексировать изменения
uv run python -m spott.ingest.tools.pipeline full --only crawler downloader   # обойти все разрешённые сайты и скачать документы
uv run python -m spott.ingest.tools.pipeline full --dry-run        # показать план, ничего не запуская
```

Логи этапов пишутся в `data/logs/`. На Mac/Linux то же самое есть обёртками в `scripts/*.sh`.
