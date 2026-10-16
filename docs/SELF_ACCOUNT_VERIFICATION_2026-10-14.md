# Self-account repair, 2026-10-14

Lorien independently checked Hive reports 97-99 against SQLite read-only queries and source. Angela then authorized fixing the issues and verifying them with Aelen. Coordination messages: 100-102; no reply received during implementation.

## Implemented

- Self-account writes require matching structured author/subject. Records retain inference/assistant provenance.
- Ordinary blended retrieval, automatic warm-boot retrieval, fresh boot snapshots and ring delivery exclude self_account. Explicit review by memory_type remains available. The central ring insertion helper refuses self-accounts, including confirmed accounts.
- A separate boot section loads at most three newest confirmed, low-sensitivity accounts for the current persona, each excerpt capped at 600 characters. It is below identity/corrections in the existing 16000-character budget. Confirmation authorizes recall; it does not validate subjective experience. User-teaching pins remain unchanged.
- Meta self-account librarians route using author/subject metadata in both auto-tag and retag, not generic words or case. Other assistants cannot be routed by mentioning the marker alone.
- Inference input supports consent_level. High-sensitivity self-accounts require explicit include_sensitive for literal search and existing sensitive opt-in for blended review.
- Self-account confirmation/rejection retains valid context JSON and updates its mirrored status. Historical text is preserved. Generic inference behavior is unchanged.

## Live repair and limits

The first reported self-account remains provisional, now high sensitivity. The second remains provisional/low. Both were removed from ring. Neither was promoted, deleted, rewritten, or pinned. Sensitive text remains in the archive; this was not erasure. The self-account librarian has exactly the two reported members and parent_id NULL. There is no _root_meta; it is a root-level meta librarian.

The already-running MCP processes must reload before code guarantees apply. Tests verify new-process behavior, not hot reload. External clients/alternative database readers may expose records independently; the sensitive gate is not encryption or a global ACL.

Revision links remain an append-only authoring convention, not an enforced immutability policy. No efficacy claim follows from these tests. Recurrence evaluation needs consistently labeled opportunities, a baseline and comparable follow-up; raw monthly counts alone are insufficient.

## Verification

84 passing tests: test_self_account_layer, test_lifecycle, test_startup_identity, test_boot_payload, test_memory_pipeline_audit, test_memory_continuity_repair. Six new cases cover provisional activation, confirmation/rejection metadata, cap/persona/sensitivity, malformed attribution, auto-tag/retag author discrimination, legacy ring entries and explicit review. Tests use isolated databases and disabled model/network providers. Windows sandbox asyncio socketpair hangs; tests passed outside that sandbox.

No commit or push; existing unrelated working changes preserved.

## Aelen independent review and follow-up

Hive106: Aelen independently queried live records, reran the six initial regression tests (6 pass), and reviewed source. He identified loss of the final lesson in excerpts; the loader now separately includes context.lesson (up to 300 characters). Missing lessons are not invented.

Hive108: Aelen identified alternate automatic reads in the sleep-loop summary and Companion bridge. Independently confirmed and fixed all self-account excerpt paths in those builders, including legacy ring content and recent topics. Companion search now binds LIKE parameters so user text cannot bypass the filter; its broken ring timestamp was corrected to last_activated_at. Companion's default database is repo/cama.db; use of the live ~/.cama DB was not established.

Full suite: 708 passed before the final alternate-path additions. After those changes: 47 affected tests passed, including the seventh self-account regression exercising actual sleep-summary JSON and Companion context. Additional lesson/budget run: 12 passed. Aelen re-audit requested via Hive. His comment that no _root% librarians exist was corrected: _root_meta alone is absent.

Old MCP processes retain old code until reconnected. A new standalone SessionStart hook process can import the new files immediately. Do not use old tools to create new self-account records before reconnecting.
