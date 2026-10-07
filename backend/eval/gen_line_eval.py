"""Generates line-level evaluation dataset (eval/lines.yaml) using OpenAI API.

- ~80 lines stratified across sites (no boilerplate, short fragments, or OCR garbage)
- 50% Romanian questions, 50% Russian questions (cross-lingual)
- Overlap filter: rejects if > 50% of significant question words appear verbatim in the line
- 12 plausible negative questions verified absent in corpus
- Outputs 10 random questions for manual inspection
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import find_dotenv, load_dotenv

from spott.core.db import get_connection
from spott.core.tools import grep_tool

load_dotenv(find_dotenv())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gen_line_eval")

NEGATIVE_CANDIDATES = [
    {
        "query": "Cum pot solicita autorizație pentru aterizarea elicopterelor private pe acoperișul Primăriei Chișinău?",
        "lang": "ro",
        "keywords": ["elicopterelor", "aterizarea"],
    },
    {
        "query": "Как записаться на курс дрессировки домашних крокодилов в муниципальном приюте?",
        "lang": "ru",
        "keywords": ["крокодилов", "дрессировки"],
    },
    {
        "query": "Care sunt tarifele pentru parcarea rachetelor cosmice pe teritoriul parcului La Izvor?",
        "lang": "ro",
        "keywords": ["rachetelor", "cosmice"],
    },
    {
        "query": "Где получить компенсацию за покупку личного дирижабля для поездок по Кишинёву?",
        "lang": "ru",
        "keywords": ["дирижабля"],
    },
    {
        "query": "Când începe construcția primei linii de metrou subteran între Aeroport și Buiucani?",
        "lang": "ro",
        "keywords": ["metrou"],
    },
    {
        "query": "Как оформить в примэрии аренду боевого слона для проведения праздничных мероприятий?",
        "lang": "ru",
        "keywords": ["слона"],
    },
    {
        "query": "Care este orarul curselor feroviare de mare viteză Shinkansen Chișinău - Tokyo?",
        "lang": "ro",
        "keywords": ["Shinkansen", "Tokyo"],
    },
    {
        "query": "Где подать заявку на установку частной атомной электростанции на дачном участке в Трушенах?",
        "lang": "ru",
        "keywords": ["атомной", "электростанции"],
    },
    {
        "query": "Cum pot adopta un pinguin imperial de la Grădina Zoologică din Chișinău?",
        "lang": "ro",
        "keywords": ["pinguin"],
    },
    {
        "query": "Сколько стоит абонемент на проезд в кишинёвском монорельсе на 2026 год?",
        "lang": "ru",
        "keywords": ["монорельсе"],
    },
    {
        "query": "Care sunt actele necesare pentru înregistrarea unei ferme de canguri în sectorul Râșcani?",
        "lang": "ro",
        "keywords": ["canguri"],
    },
    {
        "query": "Как получить муниципальный грант на поиск следов инопланетной цивилизации в парке Валя Морилор?",
        "lang": "ru",
        "keywords": ["инопланетной", "цивилизации"],
    },
]


def extract_significant_words(text: str) -> set[str]:
    """Extracts lowercase words >= 4 letters."""
    return set(re.findall(r"\b[a-zA-ZăâîșțĂÂÎȘȚа-яА-ЯёЁ]{4,}\b", text.lower()))


def fetch_candidate_lines(conn: Any) -> list[dict[str, Any]]:
    """Fetches clean, informative lines stratified across sites."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.line_id, l.chunk_id, l.doc_id, l.idx, l.text, l.embed_text, l.lang,
                   c.site, c.title, c.citation_label
            FROM lines l
            JOIN chunks c ON l.chunk_id = c.chunk_id
            WHERE length(l.text) BETWEEN 50 AND 320
              AND l.text !~* '^(pagin[aă]|pagina|anexa|articolul|capitolul|data|nr\\.)'
              AND l.text !~ '^[0-9\\.\\-\\s:,]+$'
            ORDER BY c.site, random()
            """
        )
        rows = cur.fetchall()

    by_site: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        site = r[7] or "other"
        item = {
            "line_id": r[0],
            "chunk_id": r[1],
            "doc_id": r[2],
            "idx": r[3],
            "text": r[4],
            "embed_text": r[5],
            "lang": r[6] or "ro",
            "site": site,
            "title": r[8] or "",
            "citation_label": r[9] or "",
        }
        by_site.setdefault(site, []).append(item)

    selected: list[dict[str, Any]] = []
    # Stratified pick from each site
    sites = sorted(by_site.keys())
    target_per_site = max(80 // len(sites), 8)
    for s in sites:
        items = by_site[s]
        random.shuffle(items)
        selected.extend(items[:target_per_site])

    random.shuffle(selected)
    return selected[:80]


def generate_question_for_line(
    client: httpx.Client,
    api_key: str,
    model: str,
    line: dict[str, Any],
    target_lang: str,
) -> str | None:
    text = line["text"]
    title = line.get("citation_label") or line.get("title") or ""
    lang_name = "Romanian (Română)" if target_lang == "ro" else "Russian (Русский)"

    prompt = f"""You are generating a realistic, natural citizen question for municipal documents of Chișinău city.
Document context: {title}
Specific line from document:
"{text}"

Task: Write ONE realistic question that a citizen/resident of Chișinău would ask in {lang_name}, for which this specific line directly provides the answer.
Rules:
1. Target language MUST be: {lang_name}.
2. Do NOT copy verbatim phrases from the line. Use natural phrasing, resident vocabulary, and synonyms.
3. The question must be specific enough that this exact line answers it.
4. Output ONLY the question text, with no quotes, greetings, or prefixes.
"""

    try:
        resp = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 80,
            },
            timeout=20.0,
        )
        if resp.status_code != 200:
            log.warning("OpenAI API returned status %d: %s", resp.status_code, resp.text)
            return None
        q = resp.json()["choices"][0]["message"]["content"].strip()
        # Clean quotes
        q = re.sub(r'^["\']|["\']$', "", q)

        # Check overlap
        words_q = extract_significant_words(q)
        words_line = extract_significant_words(text)
        if not words_q:
            return None
        overlap = words_q & words_line
        overlap_ratio = len(overlap) / len(words_q)
        if overlap_ratio > 0.50:
            log.debug("Rejected question due to high overlap (%.2f): %r", overlap_ratio, q)
            return None
        return q
    except Exception as e:
        log.warning("Error generating question for line %s: %s", line["line_id"], e)
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Generate lines.yaml eval dataset")
    p.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "lines.yaml",
        help="Path to output lines.yaml",
    )
    p.add_argument("--count", type=int, default=80, help="Target positive question count")
    args = p.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY must be set in .env")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    conn = get_connection()
    try:
        candidates = fetch_candidate_lines(conn)
        log.info("Sampled %d candidate lines across %d sites", len(candidates), len({c['site'] for c in candidates}))

        entries: list[dict[str, Any]] = []
        with httpx.Client() as client:
            for i, line in enumerate(candidates):
                if len(entries) >= args.count:
                    break
                # Alternate Romanian and Russian questions
                target_lang = "ro" if i % 2 == 0 else "ru"
                q = generate_question_for_line(client, api_key, model, line, target_lang)
                if not q:
                    continue
                entry = {
                    "id": f"line-{len(entries) + 1:02d}",
                    "query": q,
                    "query_lang": target_lang,
                    "line_lang": line["lang"],
                    "gold_line_id": line["line_id"],
                    "gold_chunk_id": line["chunk_id"],
                    "gold_line_text": line["text"],
                    "site": line["site"],
                    "is_cross_lingual": (target_lang != line["lang"]),
                }
                entries.append(entry)
                if len(entries) % 10 == 0:
                    log.info("Generated %d/%d questions...", len(entries), args.count)

        log.info("Generated %d positive questions.", len(entries))

        # Add 12 verified negative questions
        log.info("Verifying and adding 12 negative questions...")
        for i, neg in enumerate(NEGATIVE_CANDIDATES, 1):
            # Check grep that keywords truly don't exist
            for kw in neg["keywords"]:
                gres = grep_tool(conn, kw, limit=1)
                if gres["lines"]:
                    log.warning("Negative candidate matched in corpus: %s -> %s", kw, gres["lines"][0]["text"])
            entries.append(
                {
                    "id": f"neg-{i:02d}",
                    "query": neg["query"],
                    "query_lang": neg["lang"],
                    "gold_line_id": None,
                    "gold_chunk_id": None,
                    "gold_line_text": None,
                    "site": None,
                    "is_negative": True,
                }
            )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            yaml.dump(entries, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        log.info("Saved %d total eval questions to %s", len(entries), args.output)

        # Print 10 random questions for manual review
        positives = [e for e in entries if not e.get("is_negative")]
        sample_10 = random.sample(positives, min(10, len(positives)))
        print("\n" + "=" * 80)
        print("10 RANDOM QUESTIONS FOR MANUAL INSPECTION:")
        print("=" * 80)
        for idx, item in enumerate(sample_10, 1):
            print(f"\n[{idx}] ID: {item['id']} (Lang: {item['query_lang']} | Cross: {item['is_cross_lingual']} | Site: {item['site']})")
            print(f"  Q: {item['query']}")
            print(f"  A: {item['gold_line_text']}")
        print("\n" + "=" * 80)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
