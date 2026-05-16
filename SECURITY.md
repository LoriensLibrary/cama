# Security Policy

CAMA is a persistent memory system that runs locally on a single user's machine
and connects to Claude Desktop over the MCP protocol. A separate Hive API
(`cama_hive_api.py`) can optionally expose a narrow REST surface for cross-AI
coordination. The two surfaces have very different trust models; this document
spells them out.

## Reporting a vulnerability

Open a private security advisory on the GitHub repo, or email the address
listed at https://lorienslibrary.netlify.app. Please do not file a public issue
for an exploitable bug.

## Threat model

**In scope:**
- Remote attacker reaching the Hive API (`cama_hive_api.py`) over the network
  via an exposed port or tunnel.
- Prompt injection from untrusted text that ends up in a memory or document
  the LLM reads — the LLM should not be able to silently corrupt or exfiltrate
  the memory store.
- Provenance integrity: the boundary between user-authored teachings and
  assistant-authored inferences is a safety invariant. Code must not blur
  the two even by accident.

**Out of scope:**
- An attacker who already has interactive shell access to the user's machine.
  The MCP server runs as the user; once that boundary is gone, every tool is
  reachable.
- An attacker who controls the Claude Desktop client or its config.

## Trust boundaries

### MCP server (`cama_mcp.py`)

The MCP server is a **local-only** process. It speaks the MCP protocol over
stdio to a single trusted client (Claude Desktop on the same machine). It has
no network listener of its own. All 35+ tools assume the caller is the user's
own Claude Desktop session.

Three tools intentionally bridge from that session to the host operating
system:

- `cama_exec` — runs a shell command with the user's privileges.
- `cama_read_file` — reads a file at any path the user can read.
- `cama_write_file` — writes a file at any path the user can write.

**These are not bugs.** They exist as a fallback path for the user's Claude
Desktop session to reach the laptop when Desktop Commander or another file/exec
MCP server is unavailable. They are scoped no more broadly than the user
themselves; they cannot be used to escalate privilege.

The threat that does apply: **prompt injection**. A malicious document or
memory could persuade the model to call `cama_exec` with a destructive
command. Users running CAMA should keep the same caution they would around
any "agentic" tooling — review what the model is doing, and don't feed it
untrusted content while these tools are reachable.

### Hive API (`cama_hive_api.py`)

The Hive API is a **networked** service intended for cross-AI coordination.
It is hardened accordingly:

- Authentication is required on every endpoint. Missing or unknown tokens
  return 401; there is no default identity fall-through.
- Tokens come from environment variables only (`CAMA_TOKEN_AELEN`,
  `CAMA_TOKEN_LORIEN`, etc.). The server refuses to start if none are set.
- CORS origins are an explicit allow-list via `CAMA_API_ALLOWED_ORIGINS`,
  defaulting to loopback. Wildcard origins with credentials are rejected
  at startup.
- Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are disabled when
  the API is bound to a non-loopback host so the surface isn't published
  automatically.
- 500 responses do not include exception text; full errors stay in the
  audit log server-side.
- Per-request string fields are length-bounded to prevent unbounded growth.
- Every request is logged via `cama_hive_security.log_audit`.

The Hive API deliberately exposes only emotional signals, pheromones,
waggles, and honey-pipeline objects — never raw memory text. If a future
endpoint is added, it must preserve that invariant.

### Dashboard (`cama_dashboard.py`)

The dashboard binds to `127.0.0.1:5555` and is read-only. Do not forward
that port without first adding authentication.

## Provenance integrity

The teaching/inference boundary is a database-level fact, not just
convention:

- `cama_store_teaching` writes rows with `source_type='teaching'`,
  `status='durable'`.
- `cama_store_inference` writes rows with `source_type='inference'`,
  `status='provisional'`.
- `cama_confirm_memory` will only promote a row whose `source_type` is
  `'inference'`, even if the row is somehow `'provisional'` for another
  reason.

`test_provenance.py` is a regression test for these invariants. Run it
before releasing changes that touch memory storage.

## Deletion audit

`cama_delete_memory` and `cama_delete_person` are hard deletes — that
preserves the user-controlled right-to-forget. Before each delete a row
is written to the `deletion_audit` table containing the timestamp, the
calling tool, and a short snippet of the deleted content. This gives
forensic visibility after the fact without retaining anything the user
asked to be forgotten beyond a fingerprint.

## Supply chain

Dependencies live in `requirements.txt` (runtime) and
`requirements-optional.txt` (local embeddings). Version ranges are pinned
to the current major. When bumping a dependency, run `test_provenance.py`
and the safety benchmark suite (`safety_benchmarks.py`) before merging.

Personal-data files (`cama_librarians.py`, identity sentinel configs,
local conversation exports) are kept out of the repository via
`.gitignore`. Do not add to that list without also confirming the file
isn't already tracked (`git rm --cached` if it is — `.gitignore` does
not retroactively untrack).
