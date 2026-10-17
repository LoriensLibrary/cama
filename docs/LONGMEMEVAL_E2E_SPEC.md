# LongMemEval end-to-end: specification before code

Status: draft for critique, 2026-10-17. Nothing here is implemented. The retrieval-only harness (`docs/LONGMEMEVAL.md`) is the prerequisite and must read clean first.

## Purpose

The retrieval number says whether the evidence comes back. The end-to-end number says whether an answer built from what came back is right. Vendors quote the second (Mem0, Zep accuracy figures). To compare on their terms, CAMA needs it too, and it must be reproducible on a laptop with no API key, because that is the mission's constraint, not a convenience.

## Pipeline per question

1. Ingest and retrieve exactly as the retrieval harness does, same revision, same budget, both modes. Reuse `run_question` with `keep_db=True`; do not fork the logic.
2. Build the answer context from the top-k retrieved exchanges (k = 5 and k = 10, reported separately), in rank order, each as `[date] user: ... / assistant: ...` using the original (unshifted) session dates, so temporal questions see real dates.
3. Ask a local answer model through LM Studio's OpenAI-compatible endpoint (`http://localhost:1234/v1`) with a fixed prompt: the question, its date, the context, and the instruction to answer only from the context or say "I don't know". Temperature 0. Model name, quantization and prompt hash recorded.
4. Judge with the same local model under a separate fixed judging prompt: gold answer, model answer, output `correct` or `incorrect`. Temperature 0. Also compute a cheap exact/substring match as a second column so the judge's leniency is visible.
5. Abstention questions are scored on whether the model abstains; they are not skipped here.

## Metrics

- `accuracy@k` per question type and overall, judge-scored and string-scored, both modes.
- `retrieval_ceiling@k`: accuracy restricted to questions where `frac@k == 1.0`, which separates retrieval failure from answering failure.
- Abstention precision and recall.
- Token counts of the context, latency per stage.

## Controls

- `oracle`: context = the evidence sessions only (from `answer_session_ids`). The upper bound for this answer model and prompt.
- `none`: no context. The lower bound; shows what the model knows or guesses.
- Both run on the same questions with the same prompts.

## Reproducibility

- All prompts live in the script as constants; their SHA-256 goes into provenance.
- The answer model is pinned by LM Studio model id and file hash where available.
- Results written atomically after every question; a resume flag skips completed question ids.
- No network beyond localhost. If the endpoint is down, the run fails before the first question, not silently.

## What this does not claim

- One small local model is not the field's frontier judge. The judge-versus-string gap is reported so a reader can discount.
- Accuracy depends on the answer model as much as on memory. The `oracle` and `none` controls bound that.
- This still does not test the thing the mission is about, which is a self surviving a model swap. That is Stage F, a separate experiment with the Paper 11 probes.

## Open questions for review

1. Context format: raw exchanges versus the stored `summary` field where present. Raw is more honest; summaries are what production boots with.
2. Whether to also run blended with counterweights on, since production has them on. Proposed: off, as in retrieval, with one ablation run on a 50-question slice.
3. Which local model: a 7B instruct at Q4 fits the laptop; name it after checking what LM Studio has cached. The judge should be the same model to keep one dependency.
4. Whether abstention gets a separate prompt or the same one with the "I don't know" instruction. Proposed: same prompt.
