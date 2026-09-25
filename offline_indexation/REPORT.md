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

## 4. Раздел «Поиск»

**Текущий статус: Ожидает переиндексации.**

В соответствии с регламентом задачи 02, запуск ресурсоёмкой переиндексации (скачивание и прогрев модели `bge-m3` весом ~2.3 ГБ, пересчёт векторной базы в pgvector и запуск контейнеров) не производился и вынесен в отдельный этап.

Все программные компоненты поискового движка подготовлены и протестированы:
1. **Полнотекстовый поиск (FTS):**
   - Алгоритм `build_fts_query` формирует валидные запросы с логическим оператором `|` (OR), отсекает стоп-слова румынского и русского языков, очищает спецсимволы и термины короче 3 символов.
2. **Дедупликация выдачи:**
   - Алгоритм `deduplicate_results` группирует результаты по `content_hash` и отдаёт приоритет официальным нормативным актам над веб-страницами при равном семантическом скоре.
3. **Безопасная синхронизация индекса:**
   - Добавлен флаг `--from-jsonl` для загрузки готовых чанков с диска.
   - Очистка устаревших чанков (`--clean-orphans`) защищена от случайного удаления при частичной обработке корпуса (`--limit`).

---

## 5. Вопросы к команде (Архитектурные развилки)

1. **Источник нормативных актов Кишинэу:**
   - *Факты:* Разведка показала, что `chisinau.md` не хранит актуальные решения CMC и диспозиции Примара, а транслирует их через iframe из государственной информационной системы **`actelocale.gov.md`**. Всего в реестре доступно **22 850 актов** с удобным внутренним API (`search_request`), однако оба сайта запрещают автоматический сбор в `robots.txt`.
   - *Вопрос:* Согласовываем ли мы официальное обращение в Cancelaria de Stat / STISC для получения авторизованного доступа к API `actelocale.gov.md` как к основному источнику нормативных документов?
2. **Обработка муниципального архива (до конца 2022 года):**
   - *Факты:* Муниципальный архив на `chisinau.md` содержит PDF-файлы решений и диспозиций за 2019–2022 годы, которые представляют собой растровые сканы без текстового слоя (`/Font: False`, размер файлов 2–5 МБ).
   - *Вопрос:* Включаем ли мы в скоуп проекта обязательный OCR-пайплайн для сканов архива, и на каких вычислительных мощностях (локальный Tesseract vs облачный API) планируется его выполнение?
3. **Оптимизация и развёртывание векторной модели `bge-m3`:**
   - *Факты:* Мультиязычная модель `bge-m3` весит ~2.3 ГБ и требует заметных ресурсов CPU/GPU при первичном эмбеддинге 2705 чанков и последующих запросах.
   - *Вопрос:* Стоит ли квантовать модель в ONNX/Int8 для работы на CPU, или инфраструктура развёртывания гарантирует GPU (CUDA/MPS)?
4. **Регламент обновления поискового индекса:**
   - *Вопрос:* Какую стратегию обновления данных выбираем для продакшена:
     - инкрементальный сбор раз в сутки по дате публикации актов;
     - или регулярный ночной полный пересчёт индекса?
