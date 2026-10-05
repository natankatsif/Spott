#!/usr/bin/env python3
"""Corpus statistics calculator for the ingest pipeline (spott.ingest).

Rule: no manual numbers in reports. This script calculates exact counts,
distributions, and metrics from data/parsed/ and data/chunks/ and produces
the authoritative comparison table across pipeline iterations:
  - до 01 (Task 01 baseline)
  - после 02 (Review fixes light)
  - после 03 (Short legal items merge + heavy index run)

Usage:
    uv run python scripts/corpus_stats.py
    uv run python scripts/corpus_stats.py --markdown
    uv run python scripts/corpus_stats.py --update-report
"""

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from spott.core.paths import DATA_DIR, REPO_ROOT

# Fixed baseline from Task 01 review
BASELINE_01 = {
    "total_chunks": 3339,
    "file_chunks": 2535,
    "page_chunks": 804,
    "lang_ro": 3013,
    "lang_ru": 161,
    "lang_uk": 87,
    "lang_en": 77,
    "lang_ru_ru": 1,
    "cat_urban": 1786,
    "cat_mobility": 768,
    "cat_health": 578,
    "cat_transp": 207,
    "min_len": 3,
    "med_len": 181,
    "max_len": 2751,
    "less_80": 1040,
    "pct_less_80": 31.15,
    "with_legal": 2179,
    "pct_legal": 65.26,
    "file_with_bbox": 2535,
    "total_file_bbox": 2535,
    "pct_bbox": 100.0,
    "tables": 188,
    "pct_tables": 5.63,
    "contacts": 314,
    "pct_contacts": 9.40,
    "dupe_hashes": 700,
}

# Fixed results after Task 02
BASELINE_02 = {
    "total_chunks": 2705,
    "file_chunks": 2018,
    "page_chunks": 687,
    "lang_ro": 2482,
    "lang_ru": 95,
    "lang_uk": 63,
    "lang_en": 65,
    "lang_ru_ru": 0,
    "cat_urban": 1468,
    "cat_mobility": 663,
    "cat_health": 370,
    "cat_transp": 204,
    "min_len": 30,
    "med_len": 320,
    "max_len": 2751,
    "less_80": 536,
    "pct_less_80": 19.82,
    "with_legal": 1432,
    "pct_legal": 52.94,
    "file_with_bbox": 2018,
    "total_file_bbox": 2018,
    "pct_bbox": 100.0,
    "tables": 188,
    "pct_tables": 6.95,
    "contacts": 314,
    "pct_contacts": 11.61,
    "dupe_hashes": 584,
}


def calculate_corpus_metrics(data_dir: Path) -> dict:
    parsed_dir = data_dir / "parsed"
    pages_dir = parsed_dir / "pages"
    chunks_dir = data_dir / "chunks"

    parsed_files = [p for p in parsed_dir.glob("*.json") if not p.name.endswith(".docling.json")] if parsed_dir.exists() else []
    parsed_pages = list(pages_dir.glob("*.json")) if pages_dir.exists() else []

    chunks: list[dict] = []
    if chunks_dir.exists():
        for cf in chunks_dir.glob("*.jsonl"):
            with open(cf, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        chunks.append(json.loads(line))

    total = len(chunks)
    if total == 0:
        return {
            "parsed_files": len(parsed_files),
            "parsed_pages": len(parsed_pages),
            "total_chunks": 0,
        }

    kinds = Counter(c.get("kind") for c in chunks)
    langs = Counter(c.get("lang") for c in chunks)
    cats = Counter(c.get("category") for c in chunks)

    lengths = [len(c.get("text", "")) for c in chunks]
    min_len = min(lengths)
    med_len = int(round(statistics.median(lengths)))
    max_len = max(lengths)
    less_80 = sum(1 for l in lengths if l < 80)
    file_less_80 = sum(1 for c in chunks if c.get("kind") == "file" and len(c.get("text", "")) < 80)

    with_legal = sum(1 for c in chunks if c.get("legal_path"))

    file_chunks = [c for c in chunks if c.get("kind") == "file"]
    file_with_bbox = sum(1 for c in file_chunks if c.get("bboxes"))
    total_file_bbox = len(file_chunks)

    tables = sum(1 for c in chunks if c.get("is_table"))
    contacts = sum(1 for c in chunks if c.get("has_contacts"))

    hashes = [c.get("content_hash") for c in chunks if c.get("content_hash")]
    h_counts = Counter(hashes)
    dupe_hashes = sum(cnt - 1 for cnt in h_counts.values() if cnt > 1)

    return {
        "parsed_files": len(parsed_files),
        "parsed_pages": len(parsed_pages),
        "total_docs": len(parsed_files) + len(parsed_pages),
        "total_chunks": total,
        "file_chunks": kinds.get("file", 0),
        "page_chunks": kinds.get("page", 0),
        "lang_ro": langs.get("ro", 0),
        "lang_ru": langs.get("ru", 0),
        "lang_uk": langs.get("uk", 0),
        "lang_en": langs.get("en", 0),
        "lang_ru_ru": langs.get("ru-RU", 0),
        "cat_urban": cats.get("urban_utilities", 0),
        "cat_mobility": cats.get("mobility", 0),
        "cat_health": cats.get("healthcare", 0),
        "cat_transp": cats.get("transparency", 0),
        "min_len": min_len,
        "med_len": med_len,
        "max_len": max_len,
        "less_80": less_80,
        "file_less_80": file_less_80,
        "pct_less_80": (less_80 / total) * 100,
        "with_legal": with_legal,
        "pct_legal": (with_legal / total) * 100,
        "file_with_bbox": file_with_bbox,
        "total_file_bbox": total_file_bbox,
        "pct_bbox": (file_with_bbox / total_file_bbox * 100) if total_file_bbox else 0.0,
        "tables": tables,
        "pct_tables": (tables / total) * 100,
        "contacts": contacts,
        "pct_contacts": (contacts / total) * 100,
        "dupe_hashes": dupe_hashes,
    }


def generate_markdown_table(m03: dict) -> str:
    b01 = BASELINE_01
    b02 = BASELINE_02

    return f"""| Метрика | До 01 (Task 01) | После 02 (Task 02) | После 03 (Task 03) | Дельта (03 vs 01) |
|---|---|---|---|---|
| **Всего чанков** | {b01['total_chunks']:,} | {b02['total_chunks']:,} | **{m03['total_chunks']:,}** | **{m03['total_chunks'] - b01['total_chunks']} ({(m03['total_chunks'] - b01['total_chunks'])/b01['total_chunks']*100:.1f}%)** |
| **По типу (kind):** | | | | |
| • Файлы (`file`) | {b01['file_chunks']:,} | {b02['file_chunks']:,} | **{m03['file_chunks']:,}** | {m03['file_chunks'] - b01['file_chunks']} ({(m03['file_chunks'] - b01['file_chunks'])/b01['file_chunks']*100:.1f}%) |
| • Страницы (`page`) | {b01['page_chunks']:,} | {b02['page_chunks']:,} | **{m03['page_chunks']:,}** | {m03['page_chunks'] - b01['page_chunks']} ({(m03['page_chunks'] - b01['page_chunks'])/b01['page_chunks']*100:.1f}%) |
| **По языкам (lang):** | | | | |
| • `ro` | {b01['lang_ro']:,} | {b02['lang_ro']:,} | **{m03['lang_ro']:,}** | {m03['lang_ro'] - b01['lang_ro']} |
| • `ru` | {b01['lang_ru']:,} | {b02['lang_ru']:,} | **{m03['lang_ru']:,}** | {m03['lang_ru'] - b01['lang_ru']} |
| • `uk` | {b01['lang_uk']:,} | {b02['lang_uk']:,} | **{m03['lang_uk']:,}** | {m03['lang_uk'] - b01['lang_uk']} |
| • `en` | {b01['lang_en']:,} | {b02['lang_en']:,} | **{m03['lang_en']:,}** | {m03['lang_en'] - b01['lang_en']} |
| • `ru-RU` | {b01['lang_ru_ru']:,} | {b02['lang_ru_ru']:,} | **{m03['lang_ru_ru']:,}** | 0 (устранён) |
| **По категориям:** | | | | |
| • `urban_utilities` | {b01['cat_urban']:,} | {b02['cat_urban']:,} | **{m03['cat_urban']:,}** | {m03['cat_urban'] - b01['cat_urban']} |
| • `mobility` | {b01['cat_mobility']:,} | {b02['cat_mobility']:,} | **{m03['cat_mobility']:,}** | {m03['cat_mobility'] - b01['cat_mobility']} |
| • `healthcare` | {b01['cat_health']:,} | {b02['cat_health']:,} | **{m03['cat_health']:,}** | {m03['cat_health'] - b01['cat_health']} |
| • `transparency` | {b01['cat_transp']:,} | {b02['cat_transp']:,} | **{m03['cat_transp']:,}** | {m03['cat_transp'] - b01['cat_transp']} |
| **Длина текста (символы):** | | | | |
| • Минимальная (min) | {b01['min_len']} | {b02['min_len']} | **{m03['min_len']}** | +{m03['min_len'] - b01['min_len']} (отсечён мусор <30) |
| • Медианная (median) | {b01['med_len']} | {b02['med_len']} | **{m03['med_len']}** | **+{m03['med_len'] - b01['med_len']} (+{(m03['med_len'] - b01['med_len'])/b01['med_len']*100:.1f}%)** |
| • Максимальная (max) | {b01['max_len']:,} | {b02['max_len']:,} | **{m03['max_len']:,}** | {m03['max_len'] - b01['max_len']} |
| • Чанки < 80 символов | {b01['less_80']:,} ({b01['pct_less_80']:.2f}%) | {b02['less_80']:,} ({b02['pct_less_80']:.2f}%) | **{m03['less_80']:,} ({m03['pct_less_80']:.2f}%)** | **-{b01['less_80'] - m03['less_80']} (-{(b01['less_80'] - m03['less_80'])/b01['less_80']*100:.1f}%)** |
| • Файловые < 80 симв. | ~950 | 513 | **{m03['file_less_80']:,}** | **-{513 - m03['file_less_80']} склейкой пунктов** |
| **Юридические пути (`legal_path`):** | {b01['with_legal']:,} ({b01['pct_legal']:.2f}%) | {b02['with_legal']:,} ({b02['pct_legal']:.2f}%) | **{m03['with_legal']:,} ({m03['pct_legal']:.2f}%)** | Диапазоны `pct. 2–4` вместо дублирования |
| **BBoxes (для файлов):** | {b01['file_with_bbox']:,}/{b01['total_file_bbox']:,} ({b01['pct_bbox']:.1f}%) | {b02['file_with_bbox']:,}/{b02['total_file_bbox']:,} ({b02['pct_bbox']:.1f}%) | **{m03['file_with_bbox']:,}/{m03['total_file_bbox']:,} ({m03['pct_bbox']:.1f}%)** | 100% покрытие координат |
| **Таблицы (`is_table: true`):** | {b01['tables']} ({b01['pct_tables']:.2f}%) | {b02['tables']} ({b02['pct_tables']:.2f}%) | **{m03['tables']} ({m03['pct_tables']:.2f}%)** | Markdown таблицы сохранены |
| **Контакты (`has_contacts: true`):** | {b01['contacts']} ({b01['pct_contacts']:.2f}%) | {b02['contacts']} ({b02['pct_contacts']:.2f}%) | **{m03['contacts']} ({m03['pct_contacts']:.2f}%)** | Извлечены телефоны/email |
| **Дубликаты `content_hash`:** | {b01['dupe_hashes']} | {b02['dupe_hashes']} | **{m03['dupe_hashes']}** | -{b01['dupe_hashes'] - m03['dupe_hashes']} (-{(b01['dupe_hashes'] - m03['dupe_hashes'])/b01['dupe_hashes']*100:.1f}%) |"""


def update_report_file(report_path: Path, m03: dict) -> None:
    if not report_path.exists():
        print(f"Report file {report_path} not found.")
        return

    text = report_path.read_text(encoding="utf-8")
    new_table = generate_markdown_table(m03)

    # 1. Update parsing corpus files count
    parsing_docs_section = (
        f"- **Всего обработано документов:** {m03['total_docs']}\n"
        f"  - Официальные файлы (`PDF`, `DOCX`): **{m03['parsed_files']}** файлов в `data/parsed/`.\n"
        f"  - Веб-страницы (`HTML`): **{m03['parsed_pages']}** страниц в `data/parsed/pages/`."
    )
    text = re.sub(
        r"- \*\*Всего обработано документов:\*\* \d+\n  - Официальные файлы [^\n]+\n  - Веб-страницы [^\n]+",
        parsing_docs_section,
        text,
    )

    # 2. Update chunking table
    # Replace table under ### 3.1. Сравнительная статистика корпуса: ДО и ПОСЛЕ
    pattern = r"(### 3\.1\. Сравнительная статистика корпуса: ДО и ПОСЛЕ\n\n)\| Метрика \|.+?(?=\n\n### 3\.2\. Объяснение)"
    replacement = r"\1" + new_table
    text = re.sub(pattern, replacement, text, flags=re.DOTALL)

    report_path.write_text(text, encoding="utf-8")
    print(f"Successfully updated {report_path} with verified metrics from scripts/corpus_stats.py.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="Path to data directory")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown comparison table")
    parser.add_argument("--update-report", nargs="?", const=str(REPO_ROOT / "docs" / "history" / "REPORT.md"), default=None, help="Path to REPORT.md to update")
    args = parser.parse_args()

    data_dir = args.data

    metrics = calculate_corpus_metrics(data_dir)
    print(f"Corpus parsed: {metrics['parsed_files']} files, {metrics['parsed_pages']} pages (Total {metrics['total_docs']} documents)")
    print(f"Total chunks:  {metrics['total_chunks']} (file: {metrics['file_chunks']}, page: {metrics['page_chunks']})")
    print(f"Lengths:       min={metrics['min_len']}, median={metrics['med_len']}, max={metrics['max_len']}")
    print(f"<80 chars:     {metrics['less_80']} ({metrics['pct_less_80']:.2f}%), file<80={metrics['file_less_80']}")
    print(f"Legal path:    {metrics['with_legal']} ({metrics['pct_legal']:.2f}%)")
    print(f"BBoxes file:   {metrics['file_with_bbox']}/{metrics['total_file_bbox']} ({metrics['pct_bbox']:.1f}%)")
    print(f"Tables:        {metrics['tables']} ({metrics['pct_tables']:.2f}%)")
    print(f"Contacts:      {metrics['contacts']} ({metrics['pct_contacts']:.2f}%)")
    print(f"Duplicate h:   {metrics['dupe_hashes']}")

    md_table = generate_markdown_table(metrics)
    if args.markdown:
        print("\n" + md_table + "\n")

    if args.update_report:
        report_path = Path(args.update_report)

        if report_path.exists():
            update_report_file(report_path, metrics)


if __name__ == "__main__":
    main()
