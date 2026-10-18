# CAMA review register, October 2026

Updated 2026-10-18. This is the publication-safe register of Aelen's and Lorien's
review, not a claim that all findings are fixed. The original private review,
reproduction notes, and commit history are retained locally; personal memory
quotations, record identifiers and machine-specific paths are excluded here.

Aelen reviewed Hive, ingest, trust/guards, API/SDK, dashboard and evaluation.
Lorien independently reproduced selected findings and reviewed retrieval,
consolidation and continuity. Attribution below preserves each reviewer's work.
Unreproduced items remain leads for follow-up, not verified release blockers.
Historical line numbers were removed because subsequent fixes moved the code.

## Resolved on main

- A-1, A-3 (importer code), A-14, A-15, A-25: PR #55, squash `6998d7c`.
  Trust checks now cover the importer/API paths and Hive boot text; API confirm
  checks the existing status, and quarantined status is represented in schemas.
- K-6 and the review-path part of A-6: PR #54, squash `3147248`.
  Quarantined rows have a bounded, sensitive-data-gated review queue.
- A-3 historical-data repair is separate from the importer fix. Aelen reported
  an owner-authorized relabel with backup and audit evidence. The relabel script
  was subsequently reviewed and its future backup path fixed; merging code does
  not rerun that operation. No private source data accompanies this register.

## Changes proposed separately

- Activity hooks: explicit client attribution and idempotent installation.
- Reflections: extend the existing thinking log, preserve authorship and revision
  history, bind MCP writes to a configured author, and bound startup delivery.
- K-3/K-5: feedback writer and bounded reservation of small continuity sections.
  Peer review added transaction isolation, a read-only health summary, consistent
  date/fixture handling, and a cap that protects primary boot instructions.
- Relabel operation: online SQLite backup under a writer reservation; synthetic
  tests cover committed WAL data with an old reader and competing writer exclusion.
- LongMemEval: isolated retrieval benchmark and explicitly retrieval-only report.
  Historical results are not answer accuracy, and ranking changes do not isolate
  a causal contribution from recency or affect.

## Highest-priority unresolved findings with committed receipts

| ID | Reviewer | Finding | Receipt |
|---|---|---|---|
| C-1 | Lorien | Default keyword and core reads expose high-sensitivity rows | `tests/test_review_retrieval_receipts.py` (two strict xfails) |
| C-2 | Lorien | Applying a stale consolidation report can copy newly sensitive text into a low-sensitivity schema | same file (strict xfail) |
| C-3 | Lorien | Optional ring-write failure aborts a valid retrieval | same file (strict xfail) |
| A-19 | Aelen | Harness system-reminder text can be imported as human words | `tests/test_review_2026_10_trust_boundary.py` (strict xfail) |

The review tests use disposable databases. Run with `--runxfail --tb=short` to
see the underlying failures; normal strict xfails track these existing defects.
An unexpected pass fails CI so the register must be updated when a fix lands.
The review's four importer/API regressions already pass after PR #55.

## Aelen's remaining review register

These entries preserve Aelen's original finding IDs. Unless a committed receipt
or resolved status is stated above, they remain reported findings requiring
independent reproduction on the current base. Original private evidence is kept
locally, not silently discarded.

| ID | Reported severity | Topic |
|---|---|---|
| A-2 | medium | honey crystallization writes memories around the store path |
| A-4 | medium | `stop_signal` suppression touches a column retrieval never reads |
| A-5 | low | pheromone lifetimes above about four days are unreachable |
| A-8 | low | the local messaging wrapper lets the model pick the sender |
| A-9 | low | import-time writes to whatever database the environment names |
| A-10 | info (receipts) | Operational use of Hive signal layers needs measurement |
| A-11 | low | the watcher writes a pending file nothing reads |
| A-12 | low | body size is capped only on the reduced remote server |
| A-7 | medium | guards v1 do not log text or type edits on user rows, leave no trace of a deleted teaching, and record no actor |
| A-17 | medium | the bridge denylist misses equivalent spellings, and strict mode allowlists interpreters |
| A-18 | low | `cama_read_file` can read the memory store and the guard log |
| A-21 | low | duplicate `KNOWN_PEOPLE` block |
| A-22 | low | `imported_conversation_ids` has no caller |
| A-16 | medium | the audit middleware misses unhandled exceptions |
| A-26 | medium | API.md promises controls that do not exist |
| A-27 | medium | SDK defaults collide with the approver gate |
| A-28 | low | MULTI_TENANT.md is out of date on scope and counts |
| A-29 | info | what was checked and holds |
| A-20 | medium | cross-origin readable, unauthenticated personal data |
| A-30 | low | the Benchmarks tab can never find its files |
| A-31 | info | what holds |
| A-24 | medium | Published safety results and documentation counts need refreshed evidence |
| A-32 | low | `5d` passes forever once any sleep cycle has ever run |
| A-33 | low | the `4e` mirror points at the wrong file |
| A-34 | medium | `eval_system.py` runs against the live store at import |
| A-35 | info | what holds |


## Other limits and follow-up

- A-6: ordinary operational text can trigger quarantine; the listing fix does
  not resolve classifier false positives. A-7/K-7: text/type edits and deletes
  need additional audit coverage. Lorien reproduced those original concerns.
- K-1/S-2: blended and cosine retrieval are not identical. The final corrected
  results show a small aggregate difference; see the benchmark report for its
  metric definitions, hashes and uncertainty.
- K-2: unclaimed librarian membership is not by itself evidence of lost recall.
- K-4: both sleep implementations collect duplicate candidates; no automatic
  resolution is established. No memory deletion is authorized by this review.
- K-8: issue status and old test-coverage claims must be checked against current
  GitHub state. Importer and activity tests now exist.
- K-9/K-10: old uniform-recency, tool-count and benchmark-count claims should not
  be repeated as present facts without a fresh check.
- S-1: the superseded preliminary benchmark had an operational guard-log side
  effect. The corrected harness isolates its guard log. Historical live logs
  were not deleted as part of repository cleanup.
- Semantic candidate truncation before eligibility filtering, secondary v2
  retrieval, startup identity policy, lifecycle concurrency and consolidation
  recovery remain incompletely audited. The review does not certify the entire
  system as safe or defect-free.

## Ownership and integration

Aelen's original feature commits and Lorien's original reflection/activity
commits remain preserved in local backup history. Publication branches contain
focused diffs with tests. The working integration preview is not GitHub main.
Merge the reviewed feature PRs only after CI and the combined test run pass;
reload clients separately. Repository merges are not permission to modify the
personal memory store.
