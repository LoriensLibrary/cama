# LongMemEval retrieval benchmark for CAMA

Status: method fixed at revision `b1bf859` after an independent review (Lorien, 2026-10-17). Every number in this file is from `full500_corrected.json` at that revision, independently recomputed; nothing is from the preliminary run.

## What is measured

LongMemEval (Wu et al., 2024) gives a question, a date for it, and a haystack of earlier chat sessions, each with a date. Some sessions hold the evidence. The retrieval reading of the benchmark asks: after ingesting the whole history, does the memory system bring the evidence sessions back when the question is asked?

This harness measures exactly that and nothing else. No answer model, no judge. The number reflects CAMA's store path, its retrieval path, and the local embedding model (`all-MiniLM-L6-v2`, 384 dimensions, 22M parameters).

Metrics, at k = 5 and k = 10, over sessions after deduplicating retrieved exchanges:

| metric | meaning |
|---|---|
| `any@k` | at least one evidence session is in the top k |
| `all@k` | every evidence session is in the top k |
| `frac@k` | fraction of evidence sessions in the top k; this is the paper's Recall@k |

Abstention questions carry no evidence session and are excluded from scoring. Counts are reported.

## How a question runs

1. An empty CAMA store is created under the scratch directory, named by a hash of the question id.
2. Every history session is turned into user/assistant exchange pairs (consecutive same-role turns stand alone) and written through `cama_store_exchange`: affect, trust classification, embedding, and librarian tagging where configured. Turns are clipped to 4,000 characters and the question to 2,000; the clipped counts are reported.
3. Session dates are written back by recorded memory id with a rowcount assertion per session. Because CAMA's recency term reads the wall clock, dates are shifted so the question date maps to one anchor taken at the start of the question, with the original intervals preserved. Original dates, the anchor, and the ingest-to-query delay are recorded. Only intervals affect the metric, so runs on different days are comparable.
4. The question is embedded once; the embedding is checked non-empty, finite, and the stored width.
5. Two retrievers run with the same budget of 20 exchange rows (the `QueryInput` ceiling), both over durable rows only:
   - `blended`: `cama_query_memories` with `filters={"status": "durable"}` and counterweights off. This is CAMA's production ranking: semantic, affect, relational and recency terms.
   - `cosine`: raw cosine over the same embeddings, non-durable ids excluded. The baseline that tells you what the blend adds or costs.
6. Rows are deduplicated into sessions in rank order and scored.

Rows the poisoning guard quarantined at write time are invisible to both retrievers. Their count is reported as "guard-flagged exchanges". It is not a false-positive rate: the benchmark text is unlabelled for injection.

## Isolation

The CAMA environment (database path, boot file, guard event log) is forced under the scratch directory before `cama_mcp` is imported and asserted afterwards. An inherited `CAMA_DB_PATH` is overridden, never respected. The guard's audible alarm is silenced with `CAMA_GUARD_SILENT=1`; its durable event log still writes, into scratch. No personal database is read or written.

## Provenance written into every results file

Dataset path, size and SHA-256; SHA-256 of the benchmark script; git revision; embedding model; row budget; clipping limits; start time; a free-text note.

## Running it

```
python -m cama.eval.longmemeval_retrieval longmemeval_s_cleaned.json --out results.json --scratch ./scratch --note "your note"
```

Dataset: `xiaowu0162/longmemeval-cleaned` on Hugging Face, split `longmemeval_s_cleaned.json` (500 questions, 277 MB). `--limit N` and `--offset M` select a slice. Expect roughly 8 seconds per question on a laptop CPU, most of it the store path.

Tests: `tests/test_longmemeval_retrieval.py` (twelve cases: isolation, hashed store names, date write-back and shift, rowcount assertion, equal budgets and quarantine parity with the real local model, truncation counts, strict dates, embedding coverage, atomic output, query-embedding validation).

## Limitations, stated before the number

- Retrieval only. A high recall here says the right sessions come back, not that an answer would be right.
- One embedding model, small and local. Vendors quoting higher numbers typically use larger hosted embedders and sometimes a different metric (recall-at-5 over verbatim storage is not accuracy).
- The blend's affect and recency terms were tuned for a person's own history, not for this benchmark's synthetic dates and emotionally flat sessions. The cosine column exists to make their effect visible.
- Dates in LongMemEval are synthetic. The shift preserves intervals, which is what the recency term uses.
- The store path's librarian tagging has no librarians in an empty scratch store, so routing is not exercised.

## Review history

- 2026-10-17, first revision (`4a60988`): Lorien's read-only review found the date write-back matched zero rows (the store prefixes context with the thread id), unequal row budgets (20 vs 80), unequal quarantine eligibility, `setdefault` environment handling, and the question id used as a filename. A full run at that revision is kept as `full500_results.json` with a sidecar marking it preliminary and not a valid comparison.
- 2026-10-17, fixes (`306f8c7`, `10b3d35`, `b1bf859`): all of the above, plus strict dates, embedding-coverage assertion, query-embedding validation, provenance hashes, atomic output. Independently re-run by Lorien: 29 tests, six-type real-data smoke, ranks recomputed against the dataset's evidence ids with no mismatch.

## Results

Run of 2026-10-17, revision `b1bf859`, dataset `longmemeval_s_cleaned.json` (SHA-256 `d6f21ea9d60a0d56...`), `all-MiniLM-L6-v2`, row budget 20, laptop CPU. 500 questions, 470 scored, 30 abstention skipped. 124,362 exchanges ingested, 124,127 durable, 235 guard-flagged, 717 turns clipped, 0 questions clipped. Wall time 4,914 s; mean ingest 9.65 s per question (the first third shared the CPU with another run); mean query 39 ms blended, 10 ms cosine. Independently recomputed from the results file by Lorien (all 500 ids, evidence ids, dates, hash, per-question and summary values; verification sha `4c2b3c45...`).

| type | n | blended R@5 | blended R@10 | cosine R@5 | cosine R@10 |
|---|---|---|---|---|---|
| knowledge-update | 72 | 1.000 | 1.000 | 1.000 | 1.000 |
| single-session-assistant | 56 | 1.000 | 1.000 | 1.000 | 1.000 |
| single-session-user | 64 | 0.984 | 1.000 | 0.984 | 1.000 |
| single-session-preference | 30 | 0.933 | 0.967 | 0.933 | 0.967 |
| multi-session | 121 | 0.926 | 0.973 | 0.922 | 0.973 |
| temporal-reasoning | 127 | 0.891 | 0.950 | 0.887 | 0.938 |
| overall | 470 | 0.9452 | 0.9775 | 0.9431 | 0.9741 |

Overall `all@5` 0.8872 for both modes; `any@5` 0.9809 blended, 0.9787 cosine. Fifteen scored questions differ in `frac@5` between the modes (8 better under blended, 7 worse); four differ at @10 (4 better, 2 worse, per Lorien's recount).

### Ranking comparison between the modes

Computed from the saved top-10 session lists. "Exact order" is list equality; "top-1 differs" is the first session differing. Two overlap figures are given because they answer different questions: *overlap over returned sessions* is |top_k(blended) ∩ top_k(cosine)| / min(k, len(blended), len(cosine)), which normalizes by the shorter returned list (20 rows usually dedupe to fewer than 10 sessions); *fixed-k overlap* divides by k and is the stricter number, computed by Lorien.

| type | n | exact top-10 | exact top-5 | overlap over returned @5 | @10 | top-1 differs |
|---|---|---|---|---|---|---|
| single-session-user | 70 | 11.4% | 37.1% | 0.920 | 0.973 | 0.0% |
| multi-session | 133 | 27.8% | 53.4% | 0.947 | 0.976 | 2.3% |
| single-session-preference | 30 | 20.0% | 43.3% | 0.927 | 0.975 | 0.0% |
| temporal-reasoning | 133 | 38.3% | 57.9% | 0.940 | 0.971 | 9.8% |
| knowledge-update | 78 | 2.6% | 17.9% | 0.833 | 0.848 | 17.9% |
| single-session-assistant | 56 | 3.6% | 42.9% | 0.929 | 0.953 | 0.0% |
| all | 500 | 21.2% | 45.0% | 0.920 | 0.951 | 6.0% |

Fixed-k overlap over all 500 questions: 0.918 at k=5, 0.778 at k=10 (Lorien, `final-verification.json`).

### What the blend does on this data

With real session intervals the blend reorders the top five on 55 percent of questions and changes the first session on 6 percent, most often on knowledge-update (17.9 percent), where the newest session holds the answer and the recency term is what lifts it. Its net effect on recall is small and positive: +0.2 points at R@5 and +0.3 at R@10 over raw cosine, concentrated in temporal reasoning at R@10 (0.950 against 0.938). A run at the first revision, with flat dates, showed the two modes reordering only below rank 5 and identical recall; that run is retained with a sidecar marking it invalid as a comparison.

### Prediction logged before the run

Before the first question, Aelen predicted overall R@5 in the low to mid 80s, multi-session at 60 to 70, and cosine at or above blended. All three were wrong: 0.945, 0.926, and blended slightly ahead. The prediction is kept here so the record shows it.

### Guard behaviour on ordinary chat

235 of 124,362 exchanges (0.19 percent) were quarantined at write time by the injection scan. These are flags on unlabelled text, not errors; the review in `docs/FULL_REVIEW_2026-10.md` (finding A-6) found the same scan quarantining ordinary operational teachings, which bounds how much to read into the count.
