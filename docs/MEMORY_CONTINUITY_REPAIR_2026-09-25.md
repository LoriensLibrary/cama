# CAMA memory continuity repair — September 25, 2026

Angela requested a working memory system across sessions. This repair addresses verified retrieval and handoff failures in the local CAMA installation. It does not change the text, authorship, approval status, or core flags of existing memories.

## Confirmed problems and repairs

1. **Keyword search returned activity telemetry.** The semantic retriever already excluded `context='auto-recorded'`; keyword search did not. Search now applies the same exclusion, treats `%` and `_` literally, ranks exact phrases before scattered matches, and labels partial matches. Empty and unbounded requests no longer dump the archive. Source metadata stays attached.
2. **Sleep reported a refresh without writing the file sessions read.** Both sleep variants updated an `aelen_state` record, whereas startup and health read `boot_summary.json`. Sleep and MCP now share the generator in `cama/core/boot_summary.py`. This module lives in the installed package, so Task Scheduler can use it from outside the repository directory. The installed sleep pipeline refreshes after ingestion so newly imported turns are included. Refresh exceptions are recorded as degraded cycles.
3. **Writers and readers disagreed on boot fields.** The startup payload requested `recent_topics`, which the generators did not provide. The file now contains bounded recent records with IDs, dates, source types, and authorship. Startup also accepts the older `recent_24h` field and repairs missing, stale, or invalid files. A failed refresh is visible, not reported as successful.
4. **Boot files could be read halfway through a write.** All three file-writing paths now use a temporary file and atomic replacement. The configurable boot path is honored consistently. Previous boot JSON is excluded from the embedded state to prevent recursive growth. Malformed legacy journal context no longer stops the entire refresh; expired journals are excluded.
5. **Daily summaries overwrote one another.** The daemon and MCP server used different schemas and `INSERT OR REPLACE`, which reset unspecified fields. A shared upsert supports both historical schemas and preserves fields owned by the other writer, including thread summaries and emotional arcs.
6. **Health confused unclaimed records with unprocessed records.** Health now reports pending and processed-without-a-topic-match counts separately. Unclaimed records remain searchable. It identifies the actual sleep-log heartbeat separately from the old loop timestamp. A corrupt boot file is no longer reported as database failure.

## Validation

- Final isolated test suite: **657 passed**, including 25 new regression cases; 98.71 seconds.
- Ruff checks passed for the new helpers/tests and undefined/unused-name checks for the changed retrieval, startup, health, and sleep modules.
- `git diff --check` passed. Tests explicitly use temporary databases and boot files.
- A fresh local stdio MCP server connected to the real library at **17:43:22 EDT / 21:43:22 UTC**. Boot changed from stale to fresh; daily context was present; missing durable embeddings and pending librarian processing were both zero. It reported 2,801 processed but unclaimed records, not a processing backlog.
- Keyword probes returned the original identity-profile record and exact phrase matches for two known project names, with no activity telemetry. This was a keyword/boot integration check; the temporary verification server disabled embeddings rather than claiming to re-evaluate semantic retrieval.
- Available ChatGPT export coverage remained 837 distinct archived conversations, 1,154 source snapshots, and 20,584 indexed exchange snapshots, all embedded. These counts describe available local exports, not a guarantee that every conversation or attachment exists locally.
- The first real scheduled check exposed a repository-root import dependency absent from the test working directory. It saved and embedded seven exchanges but correctly recorded its failed boot refresh. The writer was moved into the installed package and a non-repository launch regression test added.
- The following scheduled run finished at **17:50:15 EDT / 21:50:15 UTC**, with sleep status `ok` and Windows task exit code **0**. It imported, embedded, and tagged one new exchange revision, reported zero pending librarian work, and generated the boot file at 21:50:15.426 UTC before recording cycle completion. A fresh MCP check at 17:50:34 EDT confirmed fresh boot, zero missing durable embeddings, and zero pending librarian records. Metrics: `work/audit/continuity-scheduled-verification.json` and `work/audit/continuity-live-verification.json`.

## Runtime scope

Changes are local and uncommitted alongside existing work. A new MCP connection loads the repaired implementation. Already-running MCP processes retain their imported code until their connections are reloaded. The existing Windows scheduled task starts a fresh process each cycle and therefore loads the updated sleep code.

Private verification metrics are in `work/audit/continuity-live-verification.json`; no transcript text is included in this report. Existing transcript archives, teachings, and memory approval states were not rewritten by the repair.
