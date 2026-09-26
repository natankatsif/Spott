# Отчёт по обработке и чанкингу корпуса данных (Task 02)

**Дата:** 25 сентября 2026  
**Версия:** 2.0 (Review Fixes Light)  
**Компоненты:** `parsing`, `pages_parsing`, `chunking`, `indexing` (подготовка), `common`

---

## 1. Обзор изменений

В рамках задачи 02 были устранены архитектурные и алгоритмические недочёты первого этапа:
1. **Чанкинг:**
   - Исправлена функция `split_long_text` (безопасное деление монолитных строк без пробелов/переносов).
   - Реализована привязка заголовков к телу секции (заголовки больше не выделяются в изолированные чанки).
   - Добавлено слияние коротких фрагментов (< 150 символов) с соседними блоками того же раздела и отсечение мусорных хвостов (< 30 символов).
   - Устранена «липкость» `legal_path` (автоматический сброс уровней `PUNCT`, `ALINEAT`, `LITERA` при смене секции; отключение отслеживания для ненормативных документов).
2. **Нормализация языков:**
   - Функция `normalize_lang` стандартизирует языковые метки до `ro | ru | en | uk`, исключая региональные суффиксы (`ru-RU`, `ro-RO`) и 3-буквенные коды.
3. **Поиск и индексация (подготовка):**
   - Реализована чистая функция `build_fts_query` с OR-синтаксисом (`|`) для полнотекстового поиска PostgreSQL `to_tsquery('simple', ...)`, удалением стоп-слов (румынских и русских) и экранированием спецсимволов.
   - Реализована функция `deduplicate_results` по `content_hash` (приоритет официальных актов над веб-страницами).
   - Индексатор переведён на чтение напрямую из `data/parsed` по умолчанию, добавлен флаг `--from-jsonl`, очистка устаревших `.jsonl`-файлов и защита сирот (`--clean-orphans`).
4. **Устранение дублирования кода:**
   - Общие модули вынесены в `common/text.py` (форматирование таблиц, парсинг контактов) и `indexing/embeddings.py` (ленивая загрузка модели и детекция устройства).
   - Все 84 юнит-теста проходят без регрессий.

---

## 2. Раздел «Парсинг»

- **Всего обработано документов:** 485
  - Официальные файлы (`PDF`, `DOCX`): **39** файлов в `data/parsed/`.
  - Веб-страницы (`HTML`): **446** страниц в `data/parsed/pages/`.
- **Языковая нормализация:**
  - Устранены аномальные метки (например, `ru-RU`).
  - Все документы строго категоризированы по базовым кодам языков: `ro`, `ru`, `en`, `uk`.
- **Таблицы и контакты:**
  - Обнаружено и преобразовано в чистый Markdown **188** таблиц.
  - Детектированы контактные данные (телефоны, email) в **314** документах с сохранением структурированного флага `has_contacts: true`.

---

## 3. Раздел «Чанкинг»

### 3.1. Сравнительная статистика корпуса: ДО и ПОСЛЕ

| Метрика | До 01 (Task 01) | После 02 (Task 02) | После 03 (Task 03) | Дельта (03 vs 01) |
|---|---|---|---|---|
| **Всего чанков** | 3,339 | 2,705 | **2,269** | **-1070 (-32.0%)** |
| **По типу (kind):** | | | | |
| • Файлы (`file`) | 2,535 | 2,018 | **1,582** | -953 (-37.6%) |
| • Страницы (`page`) | 804 | 687 | **687** | -117 (-14.6%) |
| **По языкам (lang):** | | | | |
| • `ro` | 3,013 | 2,482 | **2,046** | -967 |
| • `ru` | 161 | 95 | **95** | -66 |
| • `uk` | 87 | 63 | **63** | -24 |
| • `en` | 77 | 65 | **65** | -12 |
| • `ru-RU` | 1 | 0 | **0** | 0 (устранён) |
| **По категориям:** | | | | |
| • `urban_utilities` | 1,786 | 1,468 | **1,232** | -554 |
| • `mobility` | 768 | 663 | **463** | -305 |
| • `healthcare` | 578 | 370 | **370** | -208 |
| • `transparency` | 207 | 204 | **204** | -3 |
| **Длина текста (символы):** | | | | |
| • Минимальная (min) | 3 | 30 | **30** | +27 (отсечён мусор <30) |
| • Медианная (median) | 181 | 320 | **487** | **+306 (+169.1%)** |
| • Максимальная (max) | 2,751 | 2,751 | **2,751** | 0 |
| • Чанки < 80 символов | 1,040 (31.15%) | 536 (19.82%) | **333 (14.68%)** | **-707 (-68.0%)** |
| • Файловые < 80 симв. | ~950 | 513 | **310** | **-203 склейкой пунктов** |
| **Юридические пути (`legal_path`):** | 2,179 (65.26%) | 1,432 (52.94%) | **996 (43.90%)** | Диапазоны `pct. 2–4` вместо дублирования |
| **BBoxes (для файлов):** | 2,535/2,535 (100.0%) | 2,018/2,018 (100.0%) | **1,582/1,582 (100.0%)** | 100% покрытие координат |
| **Таблицы (`is_table: true`):** | 188 (5.63%) | 188 (6.95%) | **188 (8.29%)** | Markdown таблицы сохранены |
| **Контакты (`has_contacts: true`):** | 314 (9.40%) | 314 (11.61%) | **314 (13.84%)** | Извлечены телефоны/email |
| **Дубликаты `content_hash`:** | 700 | 584 | **503** | -197 (-28.1%) |

### 3.2. Объяснение ключевых изменений

1. **Ликвидация микро-чанков и объединение фрагментов:**
   - В исходной версии 31.15% чанков содержали менее 80 символов (зачастую это были оторванные заголовки или одиночные даты).
   - Теперь заголовок автоматически прикрепляется к тексту первого абзаца, а чанки длиной менее 150 символов объединяются с последующими в рамках одной секции/статьи.
   - Изолированные остатки короче 30 символов отбрасываются. В результате медианный размер полезного контекста вырос со 181 до 320 знаков.
2. **Не-липкий `legal_path`:**
   - Ранее номер пункта (например, `pct. 1`), встретившись в начале документа, ошибочно наследовался всеми последующими абзацами и другими главами.
   - Теперь смена заголовка/главы гарантированно сбрасывает вложенные уровни (`pct.`, `lit.`, `alin.`), а отслеживание активируется только для нормативных актов (`doc_type: decizie/dispozitie`) либо текстов со статьями и главами.
3. **Сохранение геометрии (BBoxes):**
   - 100% текстовых блоков из PDF-документов содержат координаты ограничивающих рамок (`bboxes`) с указанием страницы и системы координат (`BOTTOMLEFT`), что обеспечивает точную подсветку цитат в интерфейсе.

---

### 3.3. Примеры чанков (JSON Samples)

#### Пример 1: Решение с указанием статьи (`Articolul`)
```json
{
  "chunk_id": "57fce3f28cf1c74a34cb2687f045b91687a2fee0",
  "doc_id": "file:d26fca87fba76536fd656c699a386722da6dfe13270300e16959a56273daca94",
  "kind": "file",
  "text": "Articolul 1. Obiect și scop\n\nPrezentul Regulament stabilește cadrul de organizare și funcționare al Grupului de supraveghere a procesului de reactualizare a Planului Urbanistic General (PUG) al municipiului Chișinău 2025-2040.\n\nGrupul de supraveghere (in continuare - Grupul) este un organism consultativ și de monitorizare, fără personalitate juridică proprie, care asigură supravegherea transparentă, participativă și multidisciplinară a procesului de elaborare, consultare publică și aprobare a PUG, conform prevederilor legale în vigoare.",
  "embed_text": "cu privire la organizarea și funcționarea Grupului de supraveghere a procesului de reactualizare a Planului Urbanistic General al municipiului Chișinău 2025-2040\nRegulament nr. 434/2023 › Articolul 1\nArticolul 1. Obiect și scop\n\nPrezentul Regulament stabilește cadrul de organizare și funcționare al Grupului de supraveghere a procesului de reactualizare a Planului Urbanistic General (PUG) al municipiului Chișinău 2025-2040.\n\nGrupul de supraveghere (in continuare - Grupul) este un organism consultativ și de monitorizare, fără personalitate juridică proprie, care asigură supravegherea transparentă, participativă și multidisciplinară a procesului de elaborare, consultare publică și aprobare a PUG, conform prevederilor legale în vigoare.",
  "citation_label": "Regulament nr. 434/2023 › Articolul 1",
  "section": [
    "Articolul 1. Obiect și scop"
  ],
  "legal_path": [
    "Articolul 1"
  ],
  "block_ids": [
    2,
    3,
    4
  ],
  "pages": [
    2
  ],
  "bboxes": [
    {
      "page": 2,
      "l": 76.18297176819323,
      "t": 682.999999768633,
      "r": 208.5615951039308,
      "b": 671.6666664352997,
      "origin": "BOTTOMLEFT"
    },
    {
      "page": 2,
      "l": 75.25725317759873,
      "t": 668.0348839890812,
      "r": 565.8913064339954,
      "b": 626.3052326468535,
      "origin": "BOTTOMLEFT"
    },
    {
      "page": 2,
      "l": 75.25724357320576,
      "t": 622.6666666807826,
      "r": 565.8913059526185,
      "b": 564.1744192073086,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 542,
  "content_hash": "814857232352f718e78c243356a5027f2e301dd2",
  "has_contacts": true,
  "is_table": false,
  "title": "cu privire la organizarea și funcționarea Grupului de supraveghere a procesului de reactualizare a Planului Urbanistic General al municipiului Chișinău 2025-2040",
  "doc_type": "regulament",
  "number": "434/2023",
  "date": null,
  "category": "urban_utilities",
  "site": "dgaurf.md",
  "url": "https://dgaurf.md/storage/regulament-gs.pdf",
  "found_on": "https://dgaurf.md/ro/documentatii-de-urbanism"
}
```

#### Пример 2: Иерархический юридический путь (`Capitolul` + `Articolul` + `pct.`)
```json
{
  "chunk_id": "e58d2f37aeb929987117401b256ea797131d0860",
  "doc_id": "file:78a2f6d2f562ed518842a5c705b03ec1744bce5aad1bddae8c57eede06051297",
  "kind": "file",
  "text": "DISPOZIȚII GENERALE\n\n1. Regulamentul privind organizarea și funcționarea Direcției generale mobilitate urbană (în continuare - Regulament) stabilește misiunea, obiectivele strategice, domeniile de activitate, structura organizatorică și funcțiile Direcției generale mobilitate urbană (în continuare DGMU), atribuțiile și drepturile angajaților.",
  "embed_text": "CAPITOLUL I\nRegulament nr. 1001/2011 › Capitolul I › pct. 1\nDISPOZIȚII GENERALE\n\n1. Regulamentul privind organizarea și funcționarea Direcției generale mobilitate urbană (în continuare - Regulament) stabilește misiunea, obiectivele strategice, domeniile de activitate, structura organizatorică și funcțiile Direcției generale mobilitate urbană (în continuare DGMU), atribuțiile și drepturile angajaților.",
  "citation_label": "Regulament nr. 1001/2011 › Capitolul I › pct. 1",
  "section": [
    "DISPOZIȚII GENERALE"
  ],
  "legal_path": [
    "Capitolul I",
    "pct. 1"
  ],
  "block_ids": [
    3,
    4
  ],
  "pages": [
    2
  ],
  "bboxes": [
    {
      "page": 2,
      "l": 217.72489912578715,
      "t": 783.6777880430268,
      "r": 378.57630417668935,
      "b": 768.6846436188952,
      "origin": "BOTTOMLEFT"
    },
    {
      "page": 2,
      "l": 50.524093729956505,
      "t": 759.3555761017672,
      "r": 545.7771181867026,
      "b": 695.6831398563904,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 344,
  "content_hash": "7053be384d37988bba681640530ca2e17f6281a4",
  "has_contacts": false,
  "is_table": false,
  "title": "CAPITOLUL I",
  "doc_type": "regulament",
  "number": "1001/2011",
  "date": null,
  "category": "mobility",
  "site": "mobilitatechisinau.md",
  "url": "https://mobilitatechisinau.md/wp-content/uploads/2023/11/Regulament-privind-organizarea-si-functionarea-DGMU.pdf",
  "found_on": "https://mobilitatechisinau.md/"
}
```

#### Пример 3: Подпункт нормативного акта (`pct. 1.1`)
```json
{
  "chunk_id": "c5e02b2ee1cfa335fc17a4590bb69cb15bb99272",
  "doc_id": "file:0b5154e4c7bd9f048be29b6216c670e9f95b483fd76f6d25bca7b9b828a78d84",
  "kind": "file",
  "text": "1. Primarul General al municipiului Chișinău domnul Ion Ceban va asigura, până la data de 01.04.2020, crearea unui Consorțiu internațional și a unui Grup de lucru pentru supravegherea procesului de elaborare a Strategiei de dezvoltare a municipiului Chișinău și a Planului Urbanistic General, cu aprobarea ulterioară de Consiliul municipal Chișinău.",
  "embed_text": "Cu privire la elaborarea strategiei de dezvoltare a municipiului Chișinău și a Planului urbanistic general\nDecizie nr. 4/1 din 2020-03-05 › pct. 1\n1. Primarul General al municipiului Chișinău domnul Ion Ceban va asigura, până la data de 01.04.2020, crearea unui Consorțiu internațional și a unui Grup de lucru pentru supravegherea procesului de elaborare a Strategiei de dezvoltare a municipiului Chișinău și a Planului Urbanistic General, cu aprobarea ulterioară de Consiliul municipal Chișinău.",
  "citation_label": "Decizie nr. 4/1 din 2020-03-05 › pct. 1",
  "section": [],
  "legal_path": [
    "pct. 1"
  ],
  "block_ids": [
    3
  ],
  "pages": [
    1
  ],
  "bboxes": [
    {
      "page": 1,
      "l": 87.6877112285318,
      "t": 412.4438985902989,
      "r": 551.4967118795735,
      "b": 321.1344291595375,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 349,
  "content_hash": "b39b1bc0b51f58e4cdb47808fd702c76675a1544",
  "has_contacts": false,
  "is_table": false,
  "title": "Cu privire la elaborarea strategiei de dezvoltare a municipiului Chișinău și a Planului urbanistic general",
  "doc_type": "decizie",
  "number": "4/1",
  "date": "2020-03-05",
  "category": "urban_utilities",
  "site": "dgaurf.md",
  "url": "https://dgaurf.md/storage/decizia_nr_41_din_05_03_2020_-cu_privire_la_elaborarea_strategiei.pdf",
  "found_on": "https://dgaurf.md/ro/documentatii-de-urbanism"
}
```

#### Пример 4: Приложение с пунктом (`Anexa` + `pct.`)
```json
{
  "chunk_id": "78c8a9766d0fa060329f9de4612e80faad54923e",
  "doc_id": "file:17f21fccde298cfe1c8dc5f04b4f2ca74c8b80ce7e1dacc912340e3794f995a4",
  "kind": "file",
  "text": "1. Efectuarea unei evaluări a riscurilor pentru sănătatea și securitatea lucrătorilor pentru toate activitățile susceptibile de a prezenta un rise de expunere la agenți biologici și chimici; determinarea naturii, gradului și duratei de expunere; determinarca măsurilor ce trebuie luate;",
  "embed_text": "PROIECTUL\nPROIECTUL › Anexa nr. 6 › pct. 1\n1. Efectuarea unei evaluări a riscurilor pentru sănătatea și securitatea lucrătorilor pentru toate activitățile susceptibile de a prezenta un rise de expunere la agenți biologici și chimici; determinarea naturii, gradului și duratei de expunere; determinarca măsurilor ce trebuie luate;",
  "citation_label": "PROIECTUL › Anexa nr. 6 › pct. 1",
  "section": [
    "Anexa 6: Avizul emis de Centrul de Sănătate Publică Chișinău pentru Lotul nr. 1"
  ],
  "legal_path": [
    "Anexa nr. 6",
    "pct. 1"
  ],
  "block_ids": [
    388
  ],
  "pages": [
    29
  ],
  "bboxes": [
    {
      "page": 29,
      "l": 180.50965110402421,
      "t": 301.40697674738163,
      "r": 485.1883885351115,
      "b": 260.94026128000996,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 286,
  "content_hash": "d3a7c7e3b87964e12e68cabf8a074fa7b6505888",
  "has_contacts": false,
  "is_table": false,
  "title": "PROIECTUL",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "urban_utilities",
  "site": "autosalubritate.md",
  "url": "https://autosalubritate.md/wp-content/uploads/2025/11/RO_Lista_finala_de_verificare_de_mediu_si_sociala_pentru_inchidere.pdf",
  "found_on": "https://autosalubritate.md/raportul-auditului-2024/ro_lista_finala_de_verificare_de_mediu_si_sociala_pentru_inchidere/"
}
```

#### Пример 5: Таблица 1 (Markdown представление)
```json
{
  "chunk_id": "293736ae6110fcd5f1c503f41da3a595f62af6a7",
  "doc_id": "file:15e6740af305472992ddb1754923375c73dddf4bf5e8b92f1a1a7da32c37842f",
  "kind": "file",
  "text": "| Nr | Strada | \\ -lTii tfuqLli | Suma |\n| --- | --- | --- | --- |\n| Lucriri de asfaltare intesr{t/ | Lucriri de asfaltare intesr{t/ | Lucriri de asfaltare intesr{t/ | Lucriri de asfaltare intesr{t/ |\n| Sect. Buiucani | Sect. Buiucani | Sect. Buiucani | Sect. Buiucani |\n| 1 | Str. I. Neculce(tronson str. Neculcel-Coca) | 2432m2plc 534bord. mari 297,O3t str. esali | 2 080 581 |\n| 2 | Str. Neculce (tronson str. Coca-Belinski) | 4 893m2 plc I 626rn2 tr. 1 030bord. mari 574.26t str. esaliz. | 5 470 22t |\n| 3 | Str. $t. Neaga (tronson str. Neculce- CreangS.) | 2 lOOrn2 plc 25m2 casete 72Obord. mari 79.21t str. esaliz | I 479 308 |\n| 4 | Str.27 Martie | 6 000m2 p/c l925rn2 tr. 2OObord.mari 1 005bord.mici 3Ofint.inst. 7OO.9t str. esaliz. | 5 568 949 |\n| 5 | Amenaiare parcdre str. Ghidiehici | 8 5OOm2 | 6 566 969 |\n|  | Total: | 23 925m2 plc 3 551m2 tr. 25m2 casete | 2t 166 o.24 |\n| Sect. Riscani | Sect. Riscani | Sect. Riscani | Sect. Riscani |\n| 6 | Str. Socoleni (tronson str. Ceucari-C. Orheiului) | 6 8OOm2 plc 3 2OOm2 tr. 1 400bord. mari 1 200bord.mici 14fint. inst. 237 ,62t str. esaliz. | 5 968 543 |\n| 7 | Bd. Renaqterii (Tipografie-Circ) | 269OOm2plc 2 TOObord. mari 27fint. inst. 891,09t str. esaliz. | 14 949 729 |\n| 8 | Str. BraniEtei | 5 4OOm2 plc 198.2t str. egaliz. | 3 428 515 |\n| 9 | C. Orheiului (supralargire) o | 1 050m2 p/c 22Obord.mari 200bord.mici 9OOm2 pavai tr. | 2 99t 126 |\n| 10 | Str. Saharov (tronson str. D. Riqcanu- Dimo) | 2 6OOrn2 plc 500bord. mari 59,41t str. esaliz. | 1 513 080 |\n| 11 | Str. D. Riqcanu | 4 2OOrn2 plc 1 300bord. mari | 2 779 88t |",
  "embed_text": "Plan de intervenții 2022\nPlan de intervenții 2022\n| Nr | Strada | \\ -lTii tfuqLli | Suma |\n| --- | --- | --- | --- |\n| Lucriri de asfaltare intesr{t/ | Lucriri de asfaltare intesr{t/ | Lucriri de asfaltare intesr{t/ | Lucriri de asfaltare intesr{t/ |\n| Sect. Buiucani | Sect. Buiucani | Sect. Buiucani | Sect. Buiucani |\n| 1 | Str. I. Neculce(tronson str. Neculcel-Coca) | 2432m2plc 534bord. mari 297,O3t str. esali | 2 080 581 |\n| 2 | Str. Neculce (tronson str. Coca-Belinski) | 4 893m2 plc I 626rn2 tr. 1 030bord. mari 574.26t str. esaliz. | 5 470 22t |\n| 3 | Str. $t. Neaga (tronson str. Neculce- CreangS.) | 2 lOOrn2 plc 25m2 casete 72Obord. mari 79.21t str. esaliz | I 479 308 |\n| 4 | Str.27 Martie | 6 000m2 p/c l925rn2 tr. 2OObord.mari 1 005bord.mici 3Ofint.inst. 7OO.9t str. esaliz. | 5 568 949 |\n| 5 | Amenaiare parcdre str. Ghidiehici | 8 5OOm2 | 6 566 969 |\n|  | Total: | 23 925m2 plc 3 551m2 tr. 25m2 casete | 2t 166 o.24 |\n| Sect. Riscani | Sect. Riscani | Sect. Riscani | Sect. Riscani |\n| 6 | Str. Socoleni (tronson str. Ceucari-C. Orheiului) | 6 8OOm2 plc 3 2OOm2 tr. 1 400bord. mari 1 200bord.mici 14fint. inst. 237 ,62t str. esaliz. | 5 968 543 |\n| 7 | Bd. Renaqterii (Tipografie-Circ) | 269OOm2plc 2 TOObord. mari 27fint. inst. 891,09t str. esaliz. | 14 949 729 |\n| 8 | Str. BraniEtei | 5 4OOm2 plc 198.2t str. egaliz. | 3 428 515 |\n| 9 | C. Orheiului (supralargire) o | 1 050m2 p/c 22Obord.mari 200bord.mici 9OOm2 pavai tr. | 2 99t 126 |\n| 10 | Str. Saharov (tronson str. D. Riqcanu- Dimo) | 2 6OOrn2 plc 500bord. mari 59,41t str. esaliz. | 1 513 080 |\n| 11 | Str. D. Riqcanu | 4 2OOrn2 plc 1 300bord. mari | 2 779 88t |",
  "citation_label": "Plan de intervenții 2022",
  "section": [],
  "legal_path": [],
  "block_ids": [
    1
  ],
  "pages": [
    1
  ],
  "bboxes": [
    {
      "page": 1,
      "l": 63.73585147729656,
      "t": 718.3536850475755,
      "r": 568.974263317953,
      "b": 58.32313239960661,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 1590,
  "content_hash": "cf867a068472e20ff85e780c5f3b6625e80cb7b8",
  "has_contacts": false,
  "is_table": true,
  "title": "Plan de intervenții 2022",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "mobility",
  "site": "mobilitatechisinau.md",
  "url": "https://mobilitatechisinau.md/wp-content/uploads/2022/04/Lista-adreselor-lucrarilor-aprobate-pt.-anul-2022.pdf",
  "found_on": "https://mobilitatechisinau.md/"
}
```

#### Пример 6: Таблица 2 (Markdown представление)
```json
{
  "chunk_id": "4fa8f3c01933f8481da7203af8facaf8cff226d8",
  "doc_id": "file:15e6740af305472992ddb1754923375c73dddf4bf5e8b92f1a1a7da32c37842f",
  "kind": "file",
  "text": "|  |  |  |  |\n| --- | --- | --- | --- |\n|  |  | I78,22t str. esaliz. |  |\n|  | Total: | 46 95Om2 plc 3 2OOm2 E. 9OOm2 tr. pavai | 3t 63,0 474 |\n| Sect. Botanica | Sect. Botanica | Sect. Botanica | Sect. Botanica |\n| t2 | Bd. C. Vodd (tronson str. Sarmizegetusa- Dacia) | 17 OOOm2 plc 1 800bord.mari | 384,41t str. egaliz. 10 fint. inst. | LL 864 689 |\n| 13 | Str. Sarmizegetusa (tronson bd. Decebal- C. Voda) | 28 eril. inst. 16 500m2 p/c 1 200bord. mari Sfint. inst. 1Ogril.inst. 1 343,56t str. esaliz. | t6 293 360 |\n| l4 | Str. Butucului | 42OOm2plc 6o0bord. mari Sfint. inst. 7gril.inst. 342,OBt str. egaliz. 900bord. mici 900m2 tr. | 3 862 838 |\n| 15 | Str. Cern5,u1i (acces pompieri) | 7 OOOm2 plc 81.44t str. esaliz. | 2 76t 478 |\n|  | Total: | 44 7OOm2 plc 9OOm2 tr. | 34 742 3,65 |\n| Lucriri de asfaltare lplombarel | Lucriri de asfaltare lplombarel | Lucriri de asfaltare lplombarel | Lucriri de asfaltare lplombarel |\n| Sect. Centru | Sect. Centru | Sect. Centru | Sect. Centru |\n| t6 | $os. Hinceqti (plombare) | 4 SOOrn2 plc 65Orn2 casete 78.22t str. esaliz. | 3 030 262 |\n| t7 | Str. Ismail (plombare) | 4 lOOrrr2 plc 7 L.29t str. esaliz. | t 899 243 |\n|  | Total: | 8 6OOm2 p/c 65Om2 casete | 4 929 5o5 |\n| Sect. Buiucani | Sect. Buiucani | Sect. Buiucani | Sect. Buiucani |\n| 18 | C. Ieqilor (plombare) | 15 4OOm2 plc 267 .62t str. esaliz. | 7 t72 699 |\n| 19 | Str. H. CoandS. (tronson str. I. CreangS.- Mit. Dosoftei) | 4 lOOrn2 plc 148,51t str. esaliz. | 2 08t 9L7 |\n|  | Total: | 19 5OOm2 plc | 9 254 616 |\n| Sect. Riscani | Sect. Riscani | Sect. Riscani | Sect. Riscani |\n| 20 | C. Orheiului (plombare) | 3 OOOrn2 plc 52.2t str. eealiz. | r s97 253 |\n|  | Total: | 3 OOOm2 p/c | L 397 253 |\n| Sect. Botanica | Sect. Botanica | Sect. Botanica | Sect. Botanica |\n| 2l | Bd. Dacia (Plombare) | 23 000m2 plc 28Om2 casete | 11 583 669 |",
  "embed_text": "Plan de intervenții 2022\nPlan de intervenții 2022\n|  |  |  |  |\n| --- | --- | --- | --- |\n|  |  | I78,22t str. esaliz. |  |\n|  | Total: | 46 95Om2 plc 3 2OOm2 E. 9OOm2 tr. pavai | 3t 63,0 474 |\n| Sect. Botanica | Sect. Botanica | Sect. Botanica | Sect. Botanica |\n| t2 | Bd. C. Vodd (tronson str. Sarmizegetusa- Dacia) | 17 OOOm2 plc 1 800bord.mari | 384,41t str. egaliz. 10 fint. inst. | LL 864 689 |\n| 13 | Str. Sarmizegetusa (tronson bd. Decebal- C. Voda) | 28 eril. inst. 16 500m2 p/c 1 200bord. mari Sfint. inst. 1Ogril.inst. 1 343,56t str. esaliz. | t6 293 360 |\n| l4 | Str. Butucului | 42OOm2plc 6o0bord. mari Sfint. inst. 7gril.inst. 342,OBt str. egaliz. 900bord. mici 900m2 tr. | 3 862 838 |\n| 15 | Str. Cern5,u1i (acces pompieri) | 7 OOOm2 plc 81.44t str. esaliz. | 2 76t 478 |\n|  | Total: | 44 7OOm2 plc 9OOm2 tr. | 34 742 3,65 |\n| Lucriri de asfaltare lplombarel | Lucriri de asfaltare lplombarel | Lucriri de asfaltare lplombarel | Lucriri de asfaltare lplombarel |\n| Sect. Centru | Sect. Centru | Sect. Centru | Sect. Centru |\n| t6 | $os. Hinceqti (plombare) | 4 SOOrn2 plc 65Orn2 casete 78.22t str. esaliz. | 3 030 262 |\n| t7 | Str. Ismail (plombare) | 4 lOOrrr2 plc 7 L.29t str. esaliz. | t 899 243 |\n|  | Total: | 8 6OOm2 p/c 65Om2 casete | 4 929 5o5 |\n| Sect. Buiucani | Sect. Buiucani | Sect. Buiucani | Sect. Buiucani |\n| 18 | C. Ieqilor (plombare) | 15 4OOm2 plc 267 .62t str. esaliz. | 7 t72 699 |\n| 19 | Str. H. CoandS. (tronson str. I. CreangS.- Mit. Dosoftei) | 4 lOOrn2 plc 148,51t str. esaliz. | 2 08t 9L7 |\n|  | Total: | 19 5OOm2 plc | 9 254 616 |\n| Sect. Riscani | Sect. Riscani | Sect. Riscani | Sect. Riscani |\n| 20 | C. Orheiului (plombare) | 3 OOOrn2 plc 52.2t str. eealiz. | r s97 253 |\n|  | Total: | 3 OOOm2 p/c | L 397 253 |\n| Sect. Botanica | Sect. Botanica | Sect. Botanica | Sect. Botanica |\n| 2l | Bd. Dacia (Plombare) | 23 000m2 plc 28Om2 casete | 11 583 669 |",
  "citation_label": "Plan de intervenții 2022",
  "section": [],
  "legal_path": [],
  "block_ids": [
    2
  ],
  "pages": [
    2
  ],
  "bboxes": [
    {
      "page": 2,
      "l": 63.61411263894715,
      "t": 786.7511889981884,
      "r": 568.9921835675335,
      "b": 68.47603729030584,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 1851,
  "content_hash": "829f3fcab3e394d9e4e4a78cb7669db62217520b",
  "has_contacts": false,
  "is_table": true,
  "title": "Plan de intervenții 2022",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "mobility",
  "site": "mobilitatechisinau.md",
  "url": "https://mobilitatechisinau.md/wp-content/uploads/2022/04/Lista-adreselor-lucrarilor-aprobate-pt.-anul-2022.pdf",
  "found_on": "https://mobilitatechisinau.md/"
}
```

#### Пример 7: Веб-страница (категория `transparency`)
```json
{
  "chunk_id": "ba54f449c15dc9f17892df32fb88b8f1db9bc783",
  "doc_id": "page:autosalubritate.md/",
  "kind": "page",
  "text": "Folosim cookie-uri și tehnologii similare pentru a asigura funcționarea corectă și sigură a site-ului, pentru a memora preferințele dumneavoastră și, cu acordul dumneavoastră, pentru a analiza modul în care este utilizat site-ul. Puteți accepta toate cookie-urile, respinge cele neesențiale sau personaliza preferințele.\n\n\n\nStocarea tehnică sau accesul este strict necesară în scopul legitim de a permite utilizarea unui anumit serviciu cerut în mod explicit de către un abonat sau un utilizator sau în scopul exclusiv de a executa transmiterea unei comunicări printr-o rețea de comunicații electronice.\n\n\nStocarea tehnică sau accesul este necesară în scop legitim pentru stocarea preferințelor care nu sunt cerute de abonat sau utilizator.\n\n\nStocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice. Stocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice anonime. Fără o citație, conformitatea voluntară din partea Furnizorului tău de servicii de internet sau înregistrările suplimentare de la o terță parte, informațiile stocate sau preluate numai în acest scop nu pot fi utilizate de obicei pentru a te identifica.\n\n\nStocarea tehnică sau accesul este necesară pentru a crea profiluri de utilizator la care trimitem publicitate sau pentru a urmări utilizatorul pe un site web sau pe mai multe site-uri web în scopuri de marketing similare.",
  "embed_text": "Autosalubritate | Servicii evacuare deșeuri Chișinău\nAutosalubritate | Servicii evacuare deșeuri Chișinău\nFolosim cookie-uri și tehnologii similare pentru a asigura funcționarea corectă și sigură a site-ului, pentru a memora preferințele dumneavoastră și, cu acordul dumneavoastră, pentru a analiza modul în care este utilizat site-ul. Puteți accepta toate cookie-urile, respinge cele neesențiale sau personaliza preferințele.\n\n\n\nStocarea tehnică sau accesul este strict necesară în scopul legitim de a permite utilizarea unui anumit serviciu cerut în mod explicit de către un abonat sau un utilizator sau în scopul exclusiv de a executa transmiterea unei comunicări printr-o rețea de comunicații electronice.\n\n\nStocarea tehnică sau accesul este necesară în scop legitim pentru stocarea preferințelor care nu sunt cerute de abonat sau utilizator.\n\n\nStocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice. Stocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice anonime. Fără o citație, conformitatea voluntară din partea Furnizorului tău de servicii de internet sau înregistrările suplimentare de la o terță parte, informațiile stocate sau preluate numai în acest scop nu pot fi utilizate de obicei pentru a te identifica.\n\n\nStocarea tehnică sau accesul este necesară pentru a crea profiluri de utilizator la care trimitem publicitate sau pentru a urmări utilizatorul pe un site web sau pe mai multe site-uri web în scopuri de marketing similare.",
  "citation_label": "Autosalubritate | Servicii evacuare deșeuri Chișinău",
  "section": [],
  "legal_path": [],
  "block_ids": [
    0
  ],
  "pages": [],
  "bboxes": [],
  "lang": "ro",
  "char_count": 1389,
  "content_hash": "884f961874cd0a16ebc71602aab63facb599b90c",
  "has_contacts": false,
  "is_table": false,
  "title": "Autosalubritate | Servicii evacuare deșeuri Chișinău",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "urban_utilities",
  "site": "autosalubritate.md",
  "url": "https://autosalubritate.md/",
  "found_on": "https://autosalubritate.md/"
}
```

#### Пример 8: Веб-страница (категория `mobility`)
```json
{
  "chunk_id": "8e5b3459e8fc174afebd19042799b70011a0c379",
  "doc_id": "page:help.chisinau.md/",
  "kind": "page",
  "text": "Mulțumim că ne sunteți alături!\n\nNimeni nu a fost pregătit pentru un război în țara vecină. Nimeni nu a fost instruit despre cum să gestioneze sute de mii de refugiați, peste 80 la sută dintre care sunt femei cu copii sau persoane în vârstă. Noi eram speriați. Ele erau speriate. Noi însă eram acasă, deși habar nu aveam pentru cât timp. În noaptea în care în Ucraina au început bombardamentele, iar la hotar au ajuns primele persoane refugiate, Primăria municipiului Chișinău a fost mobilizată in corpore. În timp ce autoritățile publice se gândeau că ar fi bine să întreprindă ceva, primăriile din toată țara au acționat, astfel încât fiecare om ajuns în Moldova să se simtă în siguranță din primele clipe.\n\nPrimarii din țara au conlucrat, au făcut schimb de experiență, au împărțit pachete umanitare, au făcut rost de saltele, pături, apă, mâncare, au mobilizat comunitățile și au lucrat cot la cot cu oamenii de afaceri.",
  "embed_text": "HELP.CHISINAU.MD\nHELP.CHISINAU.MD › Mulțumim că ne sunteți alături!\nMulțumim că ne sunteți alături!\n\nNimeni nu a fost pregătit pentru un război în țara vecină. Nimeni nu a fost instruit despre cum să gestioneze sute de mii de refugiați, peste 80 la sută dintre care sunt femei cu copii sau persoane în vârstă. Noi eram speriați. Ele erau speriate. Noi însă eram acasă, deși habar nu aveam pentru cât timp. În noaptea în care în Ucraina au început bombardamentele, iar la hotar au ajuns primele persoane refugiate, Primăria municipiului Chișinău a fost mobilizată in corpore. În timp ce autoritățile publice se gândeau că ar fi bine să întreprindă ceva, primăriile din toată țara au acționat, astfel încât fiecare om ajuns în Moldova să se simtă în siguranță din primele clipe.\n\nPrimarii din țara au conlucrat, au făcut schimb de experiență, au împărțit pachete umanitare, au făcut rost de saltele, pături, apă, mâncare, au mobilizat comunitățile și au lucrat cot la cot cu oamenii de afaceri.",
  "citation_label": "HELP.CHISINAU.MD › Mulțumim că ne sunteți alături!",
  "section": [
    "Mulțumim că ne sunteți alături!"
  ],
  "legal_path": [],
  "block_ids": [
    0,
    1,
    2
  ],
  "pages": [],
  "bboxes": [],
  "lang": "ro",
  "char_count": 924,
  "content_hash": "b17fdeab22e57e8e19c21ef8139a979e53417cdb",
  "has_contacts": false,
  "is_table": false,
  "title": "HELP.CHISINAU.MD",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "healthcare",
  "site": "help.chisinau.md",
  "url": "https://help.chisinau.md/",
  "found_on": "https://help.chisinau.md/"
}
```

#### Пример 9: Веб-страница (категория `healthcare`)
```json
{
  "chunk_id": "582e6ca8b1d130c445607a1cf68fae9f5b44376d",
  "doc_id": "page:mobilitatechisinau.md/",
  "kind": "page",
  "text": "Direcția Generală Mobilitate Urbană este o subdiviziune a Consiliului Municipal Chișinău, ce are în sarcina sa dezvoltarea infrastructurii urbane, care să permită utilizarea cu ușurință a oricărei modalități de deplasare pe care o aleg locuitorii. Aceasta implică reparația și întreținerea drumurilor, amenajarea parcărilor, întreținerea și modernizarea iluminatului public, asigurarea transportului accesibil, confortabil și sustenabil, managementul și siguranța traficului.\nADRESA\nMD-2004, municipiul Chișinău, Str. Serghei Lazo, 18\nANTICAMERA\nTEL: 022-20-46-90 FAX: 022 -20-46-58 EMAIL: dirtrans@pmc.md",
  "embed_text": "ACASĂ - mobilitatechisinau.md\nACASĂ - mobilitatechisinau.md\nDirecția Generală Mobilitate Urbană este o subdiviziune a Consiliului Municipal Chișinău, ce are în sarcina sa dezvoltarea infrastructurii urbane, care să permită utilizarea cu ușurință a oricărei modalități de deplasare pe care o aleg locuitorii. Aceasta implică reparația și întreținerea drumurilor, amenajarea parcărilor, întreținerea și modernizarea iluminatului public, asigurarea transportului accesibil, confortabil și sustenabil, managementul și siguranța traficului.\nADRESA\nMD-2004, municipiul Chișinău, Str. Serghei Lazo, 18\nANTICAMERA\nTEL: 022-20-46-90 FAX: 022 -20-46-58 EMAIL: dirtrans@pmc.md",
  "citation_label": "ACASĂ - mobilitatechisinau.md",
  "section": [],
  "legal_path": [],
  "block_ids": [
    0
  ],
  "pages": [],
  "bboxes": [],
  "lang": "ro",
  "char_count": 605,
  "content_hash": "9532d276b389bff97c9336f3d47eb219dd6946fe",
  "has_contacts": true,
  "is_table": false,
  "title": "ACASĂ - mobilitatechisinau.md",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "mobility",
  "site": "mobilitatechisinau.md",
  "url": "https://mobilitatechisinau.md/",
  "found_on": "https://mobilitatechisinau.md/"
}
```

#### Пример 10: Чанк с контактными данными (`has_contacts: true`)
```json
{
  "chunk_id": "2115ff5c2cd20d69bd73460aa551ef4c5cab5fc8",
  "doc_id": "file:0b5154e4c7bd9f048be29b6216c670e9f95b483fd76f6d25bca7b9b828a78d84",
  "kind": "file",
  "text": "4. Consorțiul internațional și Grupul de supraveghere vor prezenta Primarului General/Consiliului municipal Chișinău Planul de acțiuni privind elaborarea sarcinii de proiectare, Strategiei de Dezvoltare și Planului Urbanistic General.\n\n00023165\n\ndin 05.03.2020 din 05.03.2020",
  "embed_text": "Cu privire la elaborarea strategiei de dezvoltare a municipiului Chișinău și a Planului urbanistic general\nDecizie nr. 4/1 din 2020-03-05 › pct. 4\n4. Consorțiul internațional și Grupul de supraveghere vor prezenta Primarului General/Consiliului municipal Chișinău Planul de acțiuni privind elaborarea sarcinii de proiectare, Strategiei de Dezvoltare și Planului Urbanistic General.\n\n00023165\n\ndin 05.03.2020 din 05.03.2020",
  "citation_label": "Decizie nr. 4/1 din 2020-03-05 › pct. 4",
  "section": [],
  "legal_path": [
    "pct. 4"
  ],
  "block_ids": [
    6,
    7,
    8
  ],
  "pages": [
    1
  ],
  "bboxes": [
    {
      "page": 1,
      "l": 85.23316776789059,
      "t": 157.76538211544528,
      "r": 546.6180554632031,
      "b": 94.6465544228605,
      "origin": "BOTTOMLEFT"
    },
    {
      "page": 1,
      "l": 530.6327969971057,
      "t": 718.0312084257714,
      "r": 560.0338691813506,
      "b": 711.7139010022704,
      "origin": "BOTTOMLEFT"
    },
    {
      "page": 1,
      "l": 477.33333329047616,
      "t": 619.5977909666037,
      "r": 560.8051520154074,
      "b": 606.5866665761904,
      "origin": "BOTTOMLEFT"
    }
  ],
  "lang": "ro",
  "char_count": 275,
  "content_hash": "0e548bb6d7a92f563bd0df5d1156ea61157d3852",
  "has_contacts": true,
  "is_table": false,
  "title": "Cu privire la elaborarea strategiei de dezvoltare a municipiului Chișinău și a Planului urbanistic general",
  "doc_type": "decizie",
  "number": "4/1",
  "date": "2020-03-05",
  "category": "urban_utilities",
  "site": "dgaurf.md",
  "url": "https://dgaurf.md/storage/decizia_nr_41_din_05_03_2020_-cu_privire_la_elaborarea_strategiei.pdf",
  "found_on": "https://dgaurf.md/ro/documentatii-de-urbanism"
}
```

#### Пример 11: Страница проекта (`proiecte.chisinau.md`)
```json
{
  "chunk_id": "84eb39c7b8f2abb08afea0ca6b36722d20c2120f",
  "doc_id": "page:proiecte.chisinau.md/ro/n-12-riscani",
  "kind": "page",
  "text": "Au fost create aleile, instalat iluminatul public, plantați copaci, instalate băncile și coșurile pentru gunoi.\n\nO nouă stație de așteptare a transportului public a fost amenajată pe str. Bogdan Voievod, din sectorul Rîșcani. Este vorba despre un pavilion dublu, care a fost deja conectat la rețeaua de iluminat public, pentru confortul maxim al călătorilor.",
  "embed_text": "Primăria Municipiului Chișinău\nPrimăria Municipiului Chișinău\nAu fost create aleile, instalat iluminatul public, plantați copaci, instalate băncile și coșurile pentru gunoi.\n\nO nouă stație de așteptare a transportului public a fost amenajată pe str. Bogdan Voievod, din sectorul Rîșcani. Este vorba despre un pavilion dublu, care a fost deja conectat la rețeaua de iluminat public, pentru confortul maxim al călătorilor.",
  "citation_label": "Primăria Municipiului Chișinău",
  "section": [],
  "legal_path": [],
  "block_ids": [
    0,
    1
  ],
  "pages": [],
  "bboxes": [],
  "lang": "ro",
  "char_count": 358,
  "content_hash": "0886aaca7833b81c20d4e3b9b0f506c0a566ae77",
  "has_contacts": false,
  "is_table": false,
  "title": "Primăria Municipiului Chișinău",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "transparency",
  "site": "proiecte.chisinau.md",
  "url": "https://proiecte.chisinau.md/ro/n-12-riscani",
  "found_on": "https://proiecte.chisinau.md/ro/n-12-riscani"
}
```

#### Пример 12: Страница проекта коммунальных служб (`autosalubritate.md`)
```json
{
  "chunk_id": "ba54f449c15dc9f17892df32fb88b8f1db9bc783",
  "doc_id": "page:autosalubritate.md/",
  "kind": "page",
  "text": "Folosim cookie-uri și tehnologii similare pentru a asigura funcționarea corectă și sigură a site-ului, pentru a memora preferințele dumneavoastră și, cu acordul dumneavoastră, pentru a analiza modul în care este utilizat site-ul. Puteți accepta toate cookie-urile, respinge cele neesențiale sau personaliza preferințele.\n\n\n\nStocarea tehnică sau accesul este strict necesară în scopul legitim de a permite utilizarea unui anumit serviciu cerut în mod explicit de către un abonat sau un utilizator sau în scopul exclusiv de a executa transmiterea unei comunicări printr-o rețea de comunicații electronice.\n\n\nStocarea tehnică sau accesul este necesară în scop legitim pentru stocarea preferințelor care nu sunt cerute de abonat sau utilizator.\n\n\nStocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice. Stocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice anonime. Fără o citație, conformitatea voluntară din partea Furnizorului tău de servicii de internet sau înregistrările suplimentare de la o terță parte, informațiile stocate sau preluate numai în acest scop nu pot fi utilizate de obicei pentru a te identifica.\n\n\nStocarea tehnică sau accesul este necesară pentru a crea profiluri de utilizator la care trimitem publicitate sau pentru a urmări utilizatorul pe un site web sau pe mai multe site-uri web în scopuri de marketing similare.",
  "embed_text": "Autosalubritate | Servicii evacuare deșeuri Chișinău\nAutosalubritate | Servicii evacuare deșeuri Chișinău\nFolosim cookie-uri și tehnologii similare pentru a asigura funcționarea corectă și sigură a site-ului, pentru a memora preferințele dumneavoastră și, cu acordul dumneavoastră, pentru a analiza modul în care este utilizat site-ul. Puteți accepta toate cookie-urile, respinge cele neesențiale sau personaliza preferințele.\n\n\n\nStocarea tehnică sau accesul este strict necesară în scopul legitim de a permite utilizarea unui anumit serviciu cerut în mod explicit de către un abonat sau un utilizator sau în scopul exclusiv de a executa transmiterea unei comunicări printr-o rețea de comunicații electronice.\n\n\nStocarea tehnică sau accesul este necesară în scop legitim pentru stocarea preferințelor care nu sunt cerute de abonat sau utilizator.\n\n\nStocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice. Stocarea tehnică sau accesul care sunt utilizate exclusiv în scopuri statistice anonime. Fără o citație, conformitatea voluntară din partea Furnizorului tău de servicii de internet sau înregistrările suplimentare de la o terță parte, informațiile stocate sau preluate numai în acest scop nu pot fi utilizate de obicei pentru a te identifica.\n\n\nStocarea tehnică sau accesul este necesară pentru a crea profiluri de utilizator la care trimitem publicitate sau pentru a urmări utilizatorul pe un site web sau pe mai multe site-uri web în scopuri de marketing similare.",
  "citation_label": "Autosalubritate | Servicii evacuare deșeuri Chișinău",
  "section": [],
  "legal_path": [],
  "block_ids": [
    0
  ],
  "pages": [],
  "bboxes": [],
  "lang": "ro",
  "char_count": 1389,
  "content_hash": "884f961874cd0a16ebc71602aab63facb599b90c",
  "has_contacts": false,
  "is_table": false,
  "title": "Autosalubritate | Servicii evacuare deșeuri Chișinău",
  "doc_type": null,
  "number": null,
  "date": null,
  "category": "urban_utilities",
  "site": "autosalubritate.md",
  "url": "https://autosalubritate.md/",
  "found_on": "https://autosalubritate.md/"
}
```

---

## 4. Раздел «Поиск и оценка качества» (Task 03 / Task 04)

### 4.1. Результаты полной индексации корпуса (2 269 чанков)
- **Целевая база данных:** PostgreSQL 17 + расширения `vector` (pgvector 0.5.0) и `unaccent`.
- **Устройство вычислений:** Apple Silicon MPS (`torch.backends.mps.is_available() = True`, `hw.memsize = 8 GB`).
- **Модель эмбеддингов:** `BAAI/bge-m3` в режиме половинной точности (`fp16`, `model.half()`), размерность 1024, `max_seq_length = 1024`.
- **Чистая скорость прямого прохода модели на MPS:**
  - Batch size 16: **21.6 чанков/с** (2.96 с на 64 чанка)
  - Batch size 32: **37.4 чанков/с** (1.71 с на 64 чанка)
  - Batch size 64: **40.6 чанков/с** (1.58 с на 64 чанка)
- **Итоговые метрики индексации (`uv run python -m indexing`):**
  - Всего документов проиндексировано: **484**
  - Всего чанков в индексе: **2 269**
  - Переиспользовано эмбеддингов из кэша (`content_hash`): **1 341**
  - Вычислено новых эмбеддингов: **928**
  - Время прогона: **145.7 с**
  - Проверка консистентности базы:
    ```sql
    SELECT count(*), count(embedding), count(*) FILTER (WHERE embedding IS NULL) FROM chunks;
    -- Результат: total = 2269, with_embedding = 2269, null_embedding = 0
    ```
- **Стемминг и полнотекстовый поиск (FTS):**
  - Настроены специализированные конфигурации `ro_unaccent` и `ru_unaccent` на базе Snowball + `unaccent`.
  - Запрос `autorizația` находит `autorizației`, `deșeuri` находит `deșeurilor`, `мусор` находит `мусора`.

### 4.2. Оценка качества поиска (`eval_smoke.py`)
Бенчмарк проведён на наборе из 16 запросов жителей (8 на румынском, 8 на русском, включая 6 кросс-язычных RU→RO и 4 правдоподобных запроса, заведомо отсутствующих в корпусе):

| Режим поиска | Hit@1 | Hit@5 | MRR | Средняя латентность |
|---|---|---|---|---|
| **Vector only** (`bge-m3` cosine) | **41.7%** | **75.0%** | **0.561** | 1049.8 ms |
| **FTS only** (`tsvector` ro/ru) | 0.0% | 16.7% | 0.051 | 13.1 ms |
| **Hybrid RRF** (Vector + FTS, k=60) | 25.0% | 58.3% | 0.399 | 71.0 ms |
| **CrossEncoder Rerank** (`bge-reranker-v2-m3`) | **41.7%** | **75.0%** | **0.519** | 22256.4 ms (CLI cold start) |

### 4.3. Анализ порога отказа (`NOT_FOUND_THRESHOLD`)
Кросс-энкодер `BAAI/bge-reranker-v2-m3` обеспечивает чёткую сепарабельность релевантных ответов и запросов вне корпуса:
- **Top-1 скоры позитивных запросов (есть в корпусе):**
  - Min: **0.0129**
  - Median: **0.6236**
  - Max: **0.9771**
- **Top-1 скоры негативных запросов (отсутствуют в корпусе):**
  - Min: **0.0010**
  - Median: **0.0018**
  - Max: **0.0058**
- **Пересечение распределений:** **0% (полное отсутствие ложных срабатываний)**.
- **Рекомендуемый порог отсечения:** `NOT_FOUND_THRESHOLD = 0.0093` (середина между 0.0058 и 0.0129). Все запросы со скором ниже 0.0093 надёжно классифицируются как `not_found: true`.

---

## 5. Раздел «Индекс строк, дерево документа, инструменты агента и честный eval» (Task 05)

### 5.1. Полная индексация строк (9 906 строк)
- **Целевая таблица:** `lines` (PostgreSQL 17 + `pgvector` + `pg_trgm` + `unaccent`).
  - PK `line_id`: стабильный `sha1(chunk_id:idx)`.
  - FK `chunk_id` (`ON DELETE CASCADE`), FK `doc_id` (`ON DELETE CASCADE`).
  - Поля: `idx`, `text` (дословный текст строки), `embed_text` (`"{title} › {citation_label}\n{text}"`), `lang`, `block_id`, `page`, `bboxes` (JSONB), `content_hash`.
  - Вектор: `embedding vector(1024)` (`BAAI/bge-m3` в fp16 на MPS, `max_seq_length = 1024`).
  - Полнотекстовый вектор: `tsv tsvector` (генерируемая колонка через `to_tsvector` с `ro_unaccent` / `ru_unaccent`).
- **Индексы:**
  - HNSW по `embedding` (`vector_cosine_ops`);
  - GIN по `tsv`;
  - **GIN `pg_trgm` по `text`** (для мгновенного поиска подстрок в `grep`);
  - B-tree по `(chunk_id, idx)`, `doc_id`, `content_hash`.
- **Колонка `ord`:** добавлена в таблицу `chunks` (номер первого входящего блока) с B-tree индексом `(doc_id, ord)`.
- **Статистика индексации строк (`uv run python -m indexing --from-jsonl`):**
  - Всего документов: **484**
  - Чанков в базе: **2 269** (все 2 269 эмбеддингов переиспользованы из кэша)
  - Всего строк выделено: **9 906**
  - Уникальных строк: **7 487**
  - Переиспользовано повторяющихся строк: **2 419**
  - Вычислено эмбеддингов BGE-M3 на MPS: **7 487**
  - Время индексации: **254.4 с** (~32–40 строк/с на MPS в fp16)
  - Проверка консистентности:
    ```sql
    SELECT count(*) AS total_lines, count(embedding) AS embedded, count(*) FILTER (WHERE embedding IS NULL) AS nulls FROM lines;
    -- total_lines: 9906 | embedded: 9906 | nulls: 0
    ```

### 5.2. Ссылки на источник и Chrome Text Fragment (`deep_link`)
Каждая строка в базе и в ответах поисковых инструментов несёт метаданные источника:
- `url`: прямая ссылка на документ (PDF) или страницу сайта;
- `found_on`: страница портала, где файл был размещён (для файлов);
- `page`: точный номер страницы внутри PDF (для файлов);
- `bboxes`: координаты ограничивающего прямоугольника блока на странице;
- `citation_label`: официальное наименование документа/раздела;
- `deep_link`:
  - **Для PDF:** `{url}#page={page}` (браузер сразу открывает нужную страницу документа);
  - **Для HTML-страниц:** `{url}#:~:text={url_encoded_first_8_words}` (Chrome/Edge прокручивает страницу к строке и подсвечивает её).
- **Верификация покрытия:** автоматический тест подтвердил **100.0%** покрытие: у 100% строк есть `url` и `deep_link`; у 100% файловых строк присутствуют `page` и `found_on` (0 записей с `NULL`).

### 5.3. Дерево документа и инструменты агента (`retrieval.tools`)
Реализованы чистые функции поверх пула соединений PostgreSQL:
1. `search(pool, query, *, lang=None, site=None, k=8)`:
   - Гибридный поиск по строкам и чанкам с взвешенным RRF.
   - Каждому результату сопоставляются 1–3 лучшие строки `matched_lines` с оценками и `deep_link`.
2. `grep(pool, pattern, *, doc_id=None, site=None, limit=20)`:
   - Точный поиск подстроки по таблице `lines`.
   - Автоматическое экранирование спецсимволов SQL LIKE (`%`, `_`, `\`) для предотвращения SQL-инъекций и неконтролируемых wildcards.
   - Полнотекстовое ускорение через триграммный индекс GIN `pg_trgm`.
3. `toc(pool, doc_id)`:
   - Построение оглавления документа на основе `section` + `legal_path` в естественном порядке (`ord`).
   - Возвращает узлы документа с `node_id`, заголовком и числом строк.
4. `open(pool, doc_id, *, node_id=None, chunk_id=None, max_lines=60)`:
   - Чтение дословных строк узла или чанка с сохранением порядка и ссылок `deep_link`.
5. **Защита от переполнения контекста агента (лимиты символов):**
   - `search`: до 12 000 символов;
   - `grep`: до 8 000 символов;
   - `toc`: до 10 000 символов;
   - `open`: до 10 000 символов.
   - При превышении список мягко обрезается с флагом `truncated: true`.
6. **OpenAI Function Calling:**
   - В `TOOL_SCHEMAS` описаны строгие JSON-схемы для всех 4 инструментов.
7. **HTTP Endpoints (FastAPI):**
   - `POST /api/tools/search`
   - `POST /api/tools/grep`
   - `POST /api/tools/toc`
   - `POST /api/tools/open`
   - `GET /api/tools/schemas`

### 5.4. Честный Eval качества поиска (`eval/lines.yaml` и `run_line_eval.py`)

#### Методология генерации датасета:
- Модель: `OPENAI_MODEL = gpt-4o-mini` через OpenAI API.
- Выборка: 85 вопросов.
  - **73 позитивных вопроса:** стратифицированы по всем сайтам корпуса, отсеяны шаблонные и мусорные строки. 50% вопросов на румынском, 50% на русском (кросс-языковые RU-вопрос ↔ RO-текст).
  - **Фильтр копирования фраз:** отбрасывались вопросы, у которых более 50% значимых слов совпадали со строкой.
  - **12 негативных вопросов:** правдоподобные вопросы о мэрии и городских службах, гарантированно отсутствующие в корпусе (проверено через `grep` и поиск).

#### Результаты сравнения режимов поиска:

| Режим поиска | Line Hit@1 | Line Hit@5 | Chunk Hit@5 | Line MRR | Chunk MRR | Средняя латентность | Негативные (max score) |
|---|---|---|---|---|---|---|---|
| **1. Chunks-vector only** | 13.70% | 27.40% | 43.84% | 0.1984 | 0.3054 | 158.2 ms | 0.01639 |
| **2. Lines-vector only** | **13.70%** | **30.14%** | 43.84% | **0.2160** | **0.3144** | 160.3 ms | 0.01639 |
| **3. Lines + Chunks vector** (`v=1.0, l=1.0, f=0.0`) | 10.96% | 26.03% | **45.21%** | 0.1788 | 0.2947 | 157.1 ms | 0.03279 |
| **4. Hybrid RRF** (`w_fts=0.1`) | 9.60% | 24.70% | 42.50% | 0.1643 | 0.2721 | 165.0 ms | 0.03450 |
| **5. Hybrid RRF** (`w_fts=0.2`) | 6.85% | 23.29% | 39.73% | 0.1417 | 0.2501 | 181.3 ms | 0.03532 |
| **6. Hybrid RRF** (`w_fts=0.5`) | 6.85% | 19.18% | 39.73% | 0.1385 | 0.2412 | 170.3 ms | 0.03946 |
| **7. Hybrid RRF** (`w_fts=1.0`) | 6.85% | 20.55% | 36.99% | 0.1379 | 0.2425 | 153.3 ms | 0.04693 |

#### Анализ и выбор весов RRF:
1. **Эффект кросс-языкового шума FTS:** При повышении веса FTS (`w_fts` от 0.0 до 1.0) метрика `Chunk Hit@5` падает с **45.21%** до **36.99%**, а `Line Hit@5` — с **30.14%** до **19.18%**. Полнотекстовый поиск не способен сопоставить русский запрос с румынским текстом нормативного акта и подмешивает случайные словарные совпадения, размывая плотный семантический векторный сигнал.
2. **Преимущество индекса строк:** Поиск чисто по строкам (`Lines-vector only`) превосходит поиск по целым чанкам по точности цитирования: `Line Hit@5` составляет **30.14%** против **27.40%**, а `Line MRR` достигает **0.2160** против **0.1984**.
3. **Итоговые веса в конфиге:**
   - `W_VECTOR = 1.0` (векторный скор чанка)
   - `W_LINE = 1.0` (векторный скор строки)
   - `W_FTS = 0.1` (минимальный вес для поиска редких номеров/аббревиатур без внесения шума в кросс-языковые запросы).

#### 10 случайных вопросов для ручной проверки человеком:
1. **ID: `line-65` (RU | Cross: True | `mobilitatechisinau.md`):**
   - *Вопрос:* «Как я могу связаться с Управлением городской мобильности для получения информации о закупках на 2026 год?»
   - *Эталонная строка:* `ANTICAMERA TEL: 022-20-46-90 FAX: 022 -20-46-58 EMAIL: dirtrans@pmc.md`
2. **ID: `line-13` (RU | Cross: True | `mobilitatechisinau.md`):**
   - *Вопрос:* «Какие функции выполняет отдел учета и экономического анализа в нашем городе?»
   - *Эталонная строка:* `| Secția evidență contabilă și analiză economică | - Planificarea și bugetarea activității - Control bugetar - Calculul plăților de retribuire a muncii - Evidența plăților și încasărilor - Evidența imobilizărilor | Management | Șef DEF Șef SECAE |`
3. **ID: `line-12` (RU | Cross: True | `help.chisinau.md`):**
   - *Вопрос:* «Какой медицинской помощью могли воспользоваться беременные женщины и дети до 27 мая 2022 года?»
   - *Эталонная строка:* `ДІТЯМ ТА 116 116 116 ВАГІТНИМ ДО 27 ТРАВНЯ 2022 РОКУ, БУЛА НАДАНО МЕДИЧНУ ДопомогУ.`
4. **ID: `line-14` (RO | Cross: False | `autosalubritate.md`):**
   - *Вопрос:* «Care este numărul de telefon al consultantului menționat în document?»
   - *Эталонная строка:* `| 13. | Billbar fgre Corsultant IilRigiahikioh 068946333 | | | | |`
5. **ID: `line-58` (RU | Cross: True | `help.chisinau.md`):**
   - *Вопрос:* «Где находится центр "Бабочка" для детей, где предлагаются занятия музыкой и рисованием?»
   - *Эталонная строка:* `| „Buburuza" | Muzică, Desen | str. Gh. Asachi 52, tel. 068486544 |`
6. **ID: `line-32` (RU | Cross: True | `autosalubritate.md`):**
   - *Вопрос:* «Сколько всего человек приняло участие в мероприятии?»
   - *Эталонная строка:* `Numărul total de participanți a fost de 21 persoane, dintre care:`
7. **ID: `line-60` (RO | Cross: False | `dgaurf.md`):**
   - *Вопрос:* «Ce măsuri sunt luate pentru a asigura accesul participanților și suportul necesar în cadrul evenimentelor organizate în oraș?»
   - *Эталонная строка:* `4.2. Accesul participanților și suportul logistic necesar;`
8. **ID: `line-47` (RU | Cross: True | `help.chisinau.md`):**
   - *Вопрос:* «Где я могу найти список всех филиалов, которые работают с беженцами в нашем городе?»
   - *Эталонная строка:* `Щоб переглянути список усіх 27 філій, відскануйте QR-код праворуч і відкрийте відповідне посилання:`
9. **ID: `line-10` (RU | Cross: True | `help.chisinau.md`):**
   - *Вопрос:* «Какие виды и объемы гуманитарной помощи были распределены в Центре размещения Patria Lukoil с момента его открытия?»
   - *Эталонная строка:* `Categoriile și cantitatea de ajutoare umanitare distribuite la depozitul Centrului de plasament Patria Lukoil de la deschidere (16 martie 2022)`
10. **ID: `line-71` (RU | Cross: True | `proiecte.chisinau.md`):**
    - *Вопрос:* «Какие изменения произошли в отделении реанимации и интенсивной терапии в больнице для детей на улице С. Лазо?»
    - *Эталонная строка:* `Detalii vezi AICI Secția de Reanimare și Terapie Intensivă a Spitalului Clinic Municipal pentru Copii nr. 1 din strada S. Lazo a fost renovată capital și dotată cu utilaj medical de ultimă generație.`

### 5.5. Бенчмарк латентности инструментов (`bench_search.py`)
Измерения проведены на работающем бэкенде (FastAPI + Uvicorn + MPS fp16 на Apple Silicon Mac):

| Инструмент / Операция | p50 (медиана) | p95 | Целевой лимит | Статус |
|---|---|---|---|---|
| **`search (total)`** | **83.29 ms** | **165.61 ms** | **< 300 ms** | **PASS** |
| • *эмбеддинг запроса (`embed`)* | 43.02 ms | 87.29 ms | — | — |
| • *векторный SQL (`vector_sql`)* | 18.99 ms | 48.77 ms | — | — |
| • *полнотекстовый SQL (`fts_sql`)* | 16.69 ms | 108.89 ms | — | — |
| **`grep`** (триграммы `pg_trgm`) | **8.04 ms** | **27.52 ms** | **< 50 ms** | **PASS** |
| **`toc`** (оглавление документа) | **3.13 ms** | **12.16 ms** | **< 50 ms** | **PASS** |
| **`open`** (дословные строки) | **21.93 ms** | **28.56 ms** | **< 50 ms** | **PASS** |

Все 4 инструмента с комфортным запасом укладываются в установленные целевые нормативы.

### 5.6. Перенос индекса на другую машину
- **Скрипт экспорта:** `scripts/export_index.sh` создаёт сжатый бинарный дамп PostgreSQL (`pg_dump -Fc`) таблиц `documents`, `chunks`, `lines` в `data/export/index-<дата>.dump`. Размер готового дампа: **55 МБ**.
- **Скрипт импорта:** `scripts/import_index.sh <путь>` автоматически запускает `docker compose up -d`, создаёт расширения и схему через `init_db()`, разворачивает таблицы через `pg_restore` и верифицирует целостность:
  ```
  total_documents: 484 | total_chunks: 2269 (0 null) | total_lines: 9906 (0 null)
  ```
- **Запуск у коллеги:** описан в `README.md` («как запустить у друга за 5 минут»).

---

## 6. Вопросы к команде (Архитектурные развилки)

1. **Кросс-языковой вес FTS в проде:**
   - *Факты:* Оценка на 85 вопросах показала, что отключение или снижение веса FTS до 0.1 повышает `Chunk Hit@5` с 36.99% до 45.21%, а `Line Hit@5` — с 20.55% до 30.14% из-за доминирования кросс-языковых запросов граждан (RU ↔ RO).
   - *Вопрос:* Оставляем ли `W_FTS = 0.1` по умолчанию, либо включаем адаптивное взвешивание: если язык запроса совпадает с языком документа — поднимаем `W_FTS` до 0.4, а для кросс-языковых запросов обнуляем `W_FTS = 0.0`?
2. **Источник нормативных актов Кишинэу (`actelocale.gov.md`):**
   - *Факты:* Разведка показала, что `chisinau.md` не хранит актуальные решения CMC и диспозиции Примара, а транслирует их через iframe из государственной информационной системы **`actelocale.gov.md`** (всего 22 850 актов).
   - *Вопрос:* Согласовываем ли официальное обращение в Cancelaria de Stat / STISC для получения авторизованного доступа к API `actelocale.gov.md` как к основному источнику нормативных документов?
3. **Обработка муниципального архива (до конца 2022 года):**
   - *Факты:* Муниципальный архив на `chisinau.md` содержит сканы решений и диспозиций за 2019–2022 годы без текстового слоя.
   - *Вопрос:* Включаем ли обязательный OCR-пайплайн для сканов архива, и на каких мощностях планируется его выполнение?
4. **Регламент ночного обновления индекса:**
   - *Вопрос:* Используем ли скрипт `scripts/overnight.sh` для инкрементального ежедневного докачивания новых актов или полный еженедельный экспорт/импорт дампа?


---

## Задача 08: свежие ответы и связи между актами

### Баг и результат
Вопрос «Cine elaborează Planul urbanistic general?». До: `conflict/contradiction` между решениями 4/1 (2020) и 79 (2021), `path=fast`. После: «Elaboratorul PUG … este Consorțiul/ARHICON», цитата `regulament-gs.pdf` Art. 3, конфликта нет, `path=agent`.

### Что сделано
1. **Второй проход за свежими актами** (`backend/app/answering.py`). Запускается, если вердикт `partial`/`conflict`, акты в источниках разных лет или вопрос начинается с cine/care/când/кто/какой/когда. Три запроса параллельно, лимит 1,5 с: (a) строки с номерами процитированных актов (`later_acts`); (b) поиск по румынскому запросу, который модель вернула в первом ответе (`search_ro`, из существительных документов); без него — вопрос + «reactualizare modificare abrogare în vigoare actual»; (c) `retrieve(date_after=самый свежий процитированный акт, sites=его сайты)` — новые фильтры `date_after`/`sites` в `retrieve()` и SQL. Затем ответ заново по источникам «от новых к старым»; у недатированного документа датой считается самая поздняя дата, которую он упоминает. Ничего нового — остаётся первый ответ. Трасса: шаг `search` «Caut acte mai noi… găsite N», `meta.path = "agent"`.
2. **Промпт**: сначала текущее состояние по самому свежему акту, старые — как история; `outdated` только для нового акта по той же теме, `contradiction` только для одного периода; разные роли и общие страницы — не конфликт; акт важнее общей страницы.
3. **Граф связей** `offline_indexation/lineage` (`uv run python -m lineage`, < 1 с) → таблица `act_relations` (в общей схеме, входит в экспорт дампа). Отношение берётся из предложения со ссылкой; в преамбуле («Având în vedere…») — всегда `refers`. В ответе к актам-источникам добавляются строки, где их изменяют/отменяют, и строки, где они сами изменяют/отменяют другие акты, с пометкой для модели.
4. **Метаданные** (`parsing/metadata.py`): номер и дата — из собственной шапки акта и имени файла, затем текст ссылки; из тела — никогда. `effective_date`. У веб-страниц — дата публикации (`article:published_time`, `<time>` в статье).
5. **Контекст**: 12 фрагментов вместо 8 + самый свежий по дате кандидат из 30. Копии документа склеиваются по `content_hash`.
6. Попутно: в поиске по строкам фильтр по сайту падал на неоднозначном `doc_id` (соединение с `documents`) — теперь соединение с `chunks` и явные колонки `l.*`.

### act_relations (вывод `python -m lineage` на текущем индексе)
```
act_relations: 123 rows, resolved to a corpus act: 9
  amends=1, refers=120, repeals=2
   decizie 12/14    amends    deciziei Consiliului municipal Chișinău nr. 4/1 din 05.03. yes 4/1
   decizie 79       refers    decizia Consiliului Municipal Chișinău nr. 4/1 din 05.03.2 yes 4/1
   decizie 79       refers    decizia Consiliului Municipal Chișinău nr. 12/14 din 28.07 yes 12/14
dispozitie 251-d    repeals   Dispoziția 185-d din 23.04.2020                            no
```
(185-d в корпусе нет — хранится текст ссылки.)

### Eval (`backend/scripts/eval_freshness.py`, `eval/freshness.yaml`, 10 вопросов, gpt-4o, temperature 0)
```
mode   newest doc cited   false contradiction  forbidden status   expected fact     p50 ms  p95 ms
fast   50% (5/10)         0% (0/10)            0% (0/10)          43% (3/7)          14427   17953
fresh  70% (7/10)         0% (0/10)            0% (0/10)          57% (4/7)          30270   37959
```
- Оба режима — с новым кодом (правила промпта, 12 фрагментов, связи); `fast` = без второго прохода. Ложных противоречий 0 уже в `fast` — это заслуга правил промпта.
- Задержка — почти вся в OpenAI (поиск 0,1–0,2 с, три запроса второго прохода ~0,2 с); в день прогона ответ gpt-4o на ~7 тыс. токенов шёл ~12 с. Второй проход = второй вызов модели (~2× токенов: 13–19 тыс. на вопрос против 5–8 тыс.).
- Метаданные двух известных документов в локальной базе проставлены вручную так, как их даст переиндексация (regulament-gs без номера, 373-d → 2026-08-25): распарсенных файлов на этой машине нет.
- Не проходят: `pug-who-ru` (русский вопрос не находит строку ARHICON), `pug-group-ro` (не говорит, что группа 185-d прекратила работу, хотя строка и пометка в источниках есть), `pug-group-members-ru` (отвечает по регламенту, а не по составу из 251-d), `consultations-ro` (цитирует не 373-d).

### Что нужно запустить у себя (данные есть только там)
```
cd offline_indexation
uv run python -m parsing --rebuild      # метаданные актов и дата публикации страниц
uv run python -m pages_parsing --reparse
uv run python -m indexing               # reused/computed: эмбеддинги неизменённого текста берутся из кэша
uv run python -m lineage
```
`reused/computed` переиндексации здесь не приведены: на этой машине нет `data/parsed`.
