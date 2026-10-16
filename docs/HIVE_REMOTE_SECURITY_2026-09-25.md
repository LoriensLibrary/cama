# Local Hive security follow-up

September 25, 2026. Reviewed the local Windows checkout at base commit
`10630ef`, preserving its existing uncommitted changes. The pasted cloud
review referenced a different commit, `eb3d667`; its test totals are not
validation of this working tree.

## Confirmed and repaired

1. **PowerShell command injection in watcher notifications.** Both title and
   message were interpolated into PowerShell source. A caller able to insert
   a signal that reaches the watcher could supply PowerShell expressions.
   The fallback now uses a fixed script and receives JSON on stdin. Content
   is passed to XML text nodes as data. The process runs without a profile,
   non-interactively, and without a visible window. The fix does not depend
   on installing `plyer`.
2. **Remote MCP started without authentication configured.** HTTP startup
   now rejects missing, short, or path-unsafe secrets. Logs no longer print
   the path credential. Stdio remains usable without this HTTP credential.
3. **Remote MCP bypassed configured identity scopes and rate limits.** Its
   message, signal, and read operations now enforce the existing security
   policy. Read-only identities must request inbox reads without marking
   messages read. Codex is included as a routing identity.
4. **Private message history and reply authorization.** Remote thread reads
   now filter individual messages by the pinned identity. Inbox history
   applies the same visibility filter. Remote replies require access to the
   parent message. Existing trusted local full-history access is retained.

The setup guide now treats the remote endpoint URL as a credential, rather
than asserting that disclosure can only cause a spam message. Anyone with
that URL can act as its assigned identity; identity pinning is not proof of
the remote model's identity.

## Verification

- Nineteen new regression cases passed, including malicious-looking text,
  Unicode, the database-to-notification path, private and mixed-recipient
  threads, authorized replies, broadcasts, read-only identities, rate
  limiting, tool allowlist, exact HTTP path checks and an MCP handshake.
- Ruff passed on the four Python files involved.
- `git diff --check` passed.
- Full local suite: **632 passed in 72.10 seconds**, including the nineteen
  new regression cases.

Tests use temporary databases; notification tests mock process execution and
do not display notifications. No external endpoints were contacted by these
tests. No live records were deleted or rewritten.

## Runtime and scope

Process checks found no directly launched `cama_hive_watcher.py`,
`cama_hive_mcp.py`, or `cama_hive_api.py` Python process during this review.
This is a point-in-time check of those entrypoints, not proof no other
launcher or remote deployment exists. No processes were stopped or started.
Changed modules take effect on the next launch; already-imported copies
in other processes require a reload or restart.

These are local fixes, not a cloud deployment or a full security certification.
Existing REST and Guard changes remain in the working tree. No commit or
push was performed. This review did not establish whether exploitation had
ever occurred.
