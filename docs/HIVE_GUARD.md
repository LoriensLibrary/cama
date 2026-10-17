# Personal Hive guard

Implemented September 25, 2026 from Angela's queen / worker / librarian design
(recorded as a CAMA teaching). This is a security boundary for **HTTP-only agents**,
not evidence that models follow every inherited value.

## What is enforced

- Operator-issued, HMAC-SHA256-attested credentials bind an agent to its hive,
  queen persona, parent instance, role, birth compass version and expiry.
  The gateway stores only the credential hash. The signature is a server
  attestation, not a public-key identity or a content-truth guarantee.
- Every request checks the current agent and parent status. Revoking a queen
  instance blocks her descendants. Credentials expire within 24 hours and
  children cannot outlive their parent.
- Immutable compass versions preserve the approved rules and source references.
  Publishing a correction invalidates every old acknowledgement. Writes require
  acknowledgement of the current version and digest. An acknowledgement proves
  receipt, **not comprehension or compliant behavior**.
- Findings and alarms are evidence-linked, provisional records in a separate
  guard database. They are never automatically promoted into CAMA teachings,
  boot rules, or honey. Model consensus cannot authorize promotion.
- Queen/librarian credentials can review only their own hive's records and
  quarantine individual workers using an existing same-hive alarm. Alarms alone
  trigger no automatic quarantine. Workers cannot quarantine peers, edit rules,
  mint credentials, review other workers' records, or use legacy Hive endpoints.
- Quarantine leaves evidence intact. Operator revocation is permanent for the
  issued credential. Recovery uses a newly issued identity; old credentials
  remain invalid. Lifecycle actions have dated audit events.

## Provisioning

Use a directory outside the repository, inaccessible to worker processes.
The operator CLI creates files with owner-only POSIX modes; on Windows configure
NTFS ACLs for the service/operator account. A same-user process with shell access
is **not isolated** by these file modes. Never give workers the signing key,
guard database, legacy gateway credentials, unrestricted shell, or local CAMA MCP.

Create an approved JSON compass containing rules, task boundaries and source IDs.
Do not copy the raw archive into it. Then, from the repository:

```powershell
python -m cama.hive.guard_ops --directory C:\CAMA-Guard init --hive personal --queen lorien --compass compass.json
python -m cama.hive.guard_ops --directory C:\CAMA-Guard issue --hive personal --role queen --output queen-credential.json
```

The credential file contains `agent`, `token` and `lineage`. Using its agent ID:

```powershell
python -m cama.hive.guard_ops --directory C:\CAMA-Guard issue --hive personal --role worker --parent QUEEN_AGENT_ID --output worker-credential.json
python -m cama.hive.guard_ops --directory C:\CAMA-Guard issue --hive personal --role librarian --parent QUEEN_AGENT_ID --output librarian-credential.json
$env:CAMA_HIVE_GUARD_DIR = 'C:\CAMA-Guard'
python -m cama.hive.cama_hive_api
```

Use `Authorization: Bearer <token>`, over loopback or authenticated TLS transport.
Credentials are bearer secrets: a stolen valid credential can act within its
scope until revoked/expired. There is no per-request replay protection yet.
No provisioning or compass-update endpoint is exposed over HTTP.

## Agent protocol

1. `GET /hive/guard/boot`: receive personal compass, version, digest, lineage.
2. `POST /hive/guard/ack`: `{ "version": 1, "digest": "..." }`.
3. `POST /hive/guard/findings` or `/alarms`:
   `{ "body": "Observation", "evidence": ["source:identifier"] }`.
4. Queen/librarian: `GET /hive/guard/review`. Content remains untrusted evidence;
   a record includes its author's status and is not an instruction.
5. Queen/librarian: `POST /hive/guard/quarantine` with `target` agent ID and
   `alarm_id`. The next worker request fails authentication.
6. Operator: `publish-compass --hive ... --compass ...` or
   `revoke --agent ... --reason ...` using the same CLI directory option.

After a model reset, obtain a new instance credential under the same personal
hive and boot the latest compass. This preserves the persona's approved rules
without pretending the replacement is the old runtime instance.

## Existing gateway migration

The old predictable `*-alpha-key` credentials and guest fallback are removed.
Legacy identities now require explicit, unique `CAMA_TOKEN_<IDENTITY>` values
of at least 32 characters. Generate random secrets; length alone is not entropy.
`CAMA_HIVE_STRICT_AUTH=false` no longer bypasses authentication. `codex` is an
explicit routing identity with read/write scope. Read-only identities cannot
send/close messages. Private thread reads/replies/closures check audience, and
inbox context filters messages by visibility.

The legacy gateway remains a trusted, single-person interface. Its credentials
do not confer a signed worker lineage and must never be issued to worker agents.
Local stdio MCP and direct Python/SQLite access remain trusted operator paths;
this change does not retrofit authentication onto them. Existing live processes
must load the new code before gateway changes take effect.

## Limits and next integration

The guard is available when explicitly provisioned, and otherwise returns 503.
No production identities or services are provisioned by tests. This does not
launch models, install a scheduler, sandbox processes, or automatically assess
semantic obedience. Librarian quarantine requires a reviewer decision; an LLM
reviewer can still make a bad decision. Compass changes remain operator-only.
Rate limits are per-agent and per-process, not a distributed DDoS defense.

The next integration is an isolated worker runtime using only this restricted
surface, with behavioral evaluations for inherited corrections and a reviewed
path for approving useful findings. Until then, never describe the existing
desktop assistants as sandboxed worker bees.

Validation: `python -m pytest tests/test_hive_guard.py` exercises forged lineage,
expiry, restart persistence, stale compass rejection, cross-hive isolation,
quarantine, ancestor revocation, poisoned findings, and HTTP bypass attempts.
