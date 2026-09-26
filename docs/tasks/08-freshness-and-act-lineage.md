# Task 08: fresh answers — find the latest act and show how acts changed

Read `docs/API.md`, `backend/app/answering.py`, `offline_indexation/parsing/metadata.py`. Nothing needs a re-crawl or OCR.

## The bug (reproduce first)

Question: **«Cine elaborează Planul urbanistic general?»**

Today's answer:
- status `conflict/outdated`;
- 2020: association CCDD (dec. 4/1) vs. 2021: company chosen via public procurement (dec. 79), "DGAURF page contradicts";
- `meta.path = "fast"`.

Correct answer from **the same corpus**: PUG Chișinău 2040 is being updated by **Consorțiul ARHICON** under **contract nr. 45/25 of 16.06.2025**; DGAURF is the beneficiary/contracting authority. Sources: `regulament-gs.pdf` Art. 3, `dispozitia-366d-10.2025.pdf`, `dispozitia-nr.-251-d-din-02.07.2026.pdf`. Decisions 4/1 → 12/14 (amends 4/1) → 23/1 → 79 are **history**, not a contradiction.

Why it happens:
1. **Top chunks only match the question's wording.** They contain "elaborează", and the older acts use that word. The new documents say "reactualizare", "elaboratorul PUG", "Consorțiul ARHICON", so they never make the top 8 given to the LLM.
2. **One pass only**: 1 search + 1 LLM call. When the model sees acts from different years, nothing goes looking for a newer one.
3. **The model calls different roles a "conflict"**: beneficiary DGAURF vs. contractor, and a chain of amendments.
4. **Metadata errors skew "latest"**:
   - `dispozitia-nr.-373-d-din-25-august-2026.pdf` has date 2026-05-20;
   - `regulament-gs.pdf` has number `434/2023`, which is the Code it cites.

## What to do

### 1. Freshness pass (answering.py, no re-index)
After the first LLM answer, run a second pass **if any of these holds**:
- the verdict is `conflict` or `partial`;
- the chunks contain acts (`doc_type` ≠ page) from ≥ 2 different years;
- the question asks who/what/when "now" (cine, care, când, кто, какой, когда).

The second pass (parallel, bounded, ≤ 1.5 s extra):
- **a.** `grep` on the numbers of the cited acts, in the forms "nr. 4/1", "4/1 din 05.03.2020". This finds later acts that reference them (12/14 and 79 reference 4/1).
- **b.** `retrieve` with the question + "reactualizare modificare abrogare în vigoare actual" (RO) and the RU equivalents.
- **c.** `retrieve` limited to `date > max(date of cited acts)` within the same site(s). Add a `date_after` filter to `retrieve()` and the SQL; `chunks.date` already exists.
- Merge a–c (dedupe) and re-answer with sources sorted **by date, newest first**.

Trace:
- a `TraceStep` with `tool="search"` and summary like "Caut acte mai noi… găsite 3" / "Ищу более новые документы… найдено 3";
- `meta.path = "agent"`.

If the second pass finds nothing new, keep the first answer.

### 2. Prompt rules (SYSTEM_PROMPT)
- Sources carry dates. **Answer the current state first**, from the newest applicable act. Older acts go after it as history ("Anterior, decizia nr. … prevedea …").
- `conflict.kind = "outdated"` only when a newer act is on the same subject. `preferred_ref` = the newer one.
- `conflict.kind = "contradiction"` only when two acts of the **same period** give different values and neither supersedes the other.
- Different roles are **not** a conflict (beneficiary vs. contractor, coordinator vs. designer).
- A general "about us" page never contradicts an act.

### 3. Act lineage (offline script + table, no re-parse)
New module `offline_indexation/lineage` (`uv run python -m lineage`). It runs over the `lines` / `chunks` already in Postgres, in seconds.
- Extract references to acts: `(decizia|dispoziția|hotărârea|ordinul) (nr\.)? X din DATE` and the RU forms.
- Extract the relation verb near each reference:
  - "se modifică", "operarea unor modificări în" → `amends`;
  - "se abrogă", "își încetează activitatea", "își pierde valabilitatea" → `repeals`;
  - anything else → `refers`.
- Resolve the referenced act to a `doc_id` by `(doc_type, number, date)` when it exists in the corpus. If it doesn't, keep the raw text.
- Table `act_relations(from_doc_id, to_doc_id NULL, to_ref_text, relation, line_id)`. Every row cites the line it came from.
- In answering: for every cited act, add the acts that `amends`/`repeals` it to the sources (forward links). If an act is repealed or amended, say so in the answer, citing that line.
- Expected on the current corpus:
  - 12/14 `amends` 4/1;
  - 79 `refers` 4/1 and 12/14;
  - 251-d `repeals` the working group of 185-d ("își încetează activitatea").

  Report these three rows from the real table.

### 4. Metadata fixes (parsing/metadata.py → `parsing --rebuild`, no OCR)
- Act date and number come from the **document head** (title block: "DISPOZIȚIE nr. 373-d din 25 august 2026") and from the **file name**. Only then fall back to link text. Never take them from references further down the body.
- Store `effective_date` = date of the act. For pages, store the publication date when present (`<time>`, "23 septembrie 2026").
- Fix the two known cases and add unit tests:
  - `dispozitia-nr.-373-d-din-25-august-2026.pdf` → 373-d, 2026-08-25;
  - `regulament-gs.pdf` → no act number (or the number from its own head), not 434/2023.
- Run `uv run python -m parsing --rebuild`, then `uv run python -m indexing`. Show `reused/computed` in the report: embeddings should be almost all reused.

### 5. Bigger context
`TOP_CHUNKS` 8 → 12. Always include the newest-dated chunk among the retrieved candidates, even if it ranks below the cut-off.

## Checks (put in `backend/tests` + an eval file)
Add `offline_indexation/eval/freshness.yaml`, at least 8 questions. Each question has:
- the expected **newest** doc_id to be cited;
- the forbidden status (e.g. not `contradiction`).

Required questions:
1. «Cine elaborează Planul urbanistic general?» → cites `regulament-gs.pdf` or `dispozitia-366d…`; mentions ARHICON / 45/25; status ≠ `contradiction`.
2. «Кто разрабатывает генплан Кишинёва?» → same, answer in RU.
3. «Ce grup supraveghează elaborarea PUG?» → 251-d (2026), and says the 2020 group (185-d) ended.
4. At least 5 more that you find: same topic, several acts over the years.

Report:
- before/after table: newest-doc hit rate, `conflict` false-positive rate, p50/p95 latency (fast vs. freshness pass);
- the 3 `act_relations` rows above;
- `reused/computed` from the re-index.

Numbers only from script output.

## Don't
- No re-crawl, no OCR, no reranker requirement. It must work with `RERANKER_ENABLED=false`.
- Don't change the API contract. `trace`, `conflict` and `meta.path` already carry everything.
- The extra pass must stay bounded: at most 1 extra round, 3 queries.
