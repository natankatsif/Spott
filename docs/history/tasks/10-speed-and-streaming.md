# Task 10: answer in seconds, real streaming, fix the 4 freshness misses

Read your task 08 report (`offline_indexation/REPORT.md`, "Задача 08"), `backend/app/answering.py`, `backend/app/llm.py`, `docs/API.md`.

## Why
Task 08 fixed the correctness, but the speed is not demo-ready:

| Mode | p50 | p95 |
|---|---|---|
| fast | 14.4 s | 18.0 s |
| fresh | 30.3 s | 38.0 s |

- Model: gpt-4o, ~7k prompt tokens.
- The answer is generated whole and only then "streamed", so the user sees the first word after ~14 s.

Retrieval itself takes 0.1–0.2 s. All the time goes to the LLM.

**Targets:**
- time to first answer word: p50 ≤ 2.5 s, p95 ≤ 5 s;
- full answer: p50 ≤ 7 s (with the freshness pass included);
- quality not worse than task 08: newest doc cited ≥ 70%, false contradictions 0%.

## What to do

### 1. Real token streaming
- Call OpenAI with `stream=true`, keeping the structured JSON output.
- Parse the JSON incrementally. Emit a `sentence` event as soon as each `sentences[i]` object closes: text, refs → cites, verify that sentence right away.
- `delta` events carry the text of the sentence currently being written, so the UI shows words as they arrive.
- `citation` events go out before the first sentence that cites them (contract order unchanged).
- `done` stays authoritative.
- Keep the fields the UI needs last (checklist, conflict, followups, translations) **after** `sentences` in the schema, so the text starts early.

### 2. Freshness without a second LLM call in most cases
- Run the cheap freshness retrieval **in parallel with / before** the first LLM call, not after it:
  - later acts by number via `act_relations`;
  - `date_after` on the same sites;
  - the "reactualizare…" query.
- It costs ~0.2 s. Merge the newer sources into the first prompt, sorted newest first.
- Second LLM call only when the first verdict is still `conflict`/`partial` **and** new sources appeared that the first call didn't see. Expected on the eval: ≤ 20% of questions.

### 3. Smaller prompt
- For every chunk, send only the matched lines ± 5 lines of context (plus the chunk title / legal path), not the whole chunk up to 40 lines.
- Dedupe identical lines across chunks. Drop table rows with no letters (OCR noise).
- Target ≤ 3,500 prompt tokens p50. Log prompt tokens per question.

### 4. Model choice by measurement
- Compare on `eval/freshness.yaml` + 30 questions from `eval/lines.yaml` (15 RO, 15 RU):
  - the current mini/fast model from the OpenAI docs, low reasoning effort if it's a reasoning model;
  - gpt-4o.
- Metrics:
  - quality: newest-doc hit, expected fact, false contradiction, verified-sentence rate;
  - speed: time to first token, full latency;
  - cost per 100 questions.
- Pick the default in `.env.example` (`OPENAI_MODEL`) and write the table in the report. If the mini model loses more than 10 points of quality, keep gpt-4o for `deep` and use mini for `fast`.

### 5. Query rewrite RU → RO up front
One cheap call (mini model, ≤ 150 output tokens, run in parallel with the first retrieval). It turns the question + history into:
- a RO query;
- a RU query;
- 3–6 keywords (act numbers, names).

Run retrieval on both queries and `grep` on the keywords, then merge with RRF. This is what fixes `pug-who-ru` (the RU question doesn't find the ARHICON line). Measure hit@5 before/after on the RU→RO pairs of `lines.yaml`.

### 6. The remaining freshness misses
- `pug-group-ro`: when `act_relations` says a cited act (or its body) was `repeals`/ended by a newer one, the answer **must** say so. Add a check: if a repealing line is in the sources and the answer doesn't cite it, append a sentence citing it.
- `pug-group-members-ru`: prefer the act that establishes the thing (251-d) over the regulation that describes it. When the question asks "who is in / membrii / состав", boost chunks with lists of names/roles.
- `consultations-ro`: check the 373-d date after the metadata rebuild. If it's still wrong, fix the extraction.

## Checks / report
- Before/after table on the same machine and eval set:

  | mode | TTFT p50/p95 | total p50/p95 | prompt tokens p50 | 2nd LLM call % | newest doc | expected fact | false contradiction | verified sentences |
  |---|---|---|---|---|---|---|---|---|

- A test that `delta` events start before the LLM call finishes (fake streaming LLM).
- Contract unchanged; `test_contract` / mock validation green; ruff + pytest green.

Numbers only from script output.

## Don't
- Don't weaken verification: every sentence still cites lines, and quotes are still read from the DB.
- Don't make the reranker required (`RERANKER_ENABLED=false` must stay fast).
- No new contract fields unless agreed (write to the frontend first).
