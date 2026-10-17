# Shared Hive activity

Local operational status is stored separately in `~/.cama/hive_activity.db`.
It is not a memory, teaching, self-account, or evidence of subjective state.

## Use now without restarting a chat

From the CAMA repository, using its virtual environment:

```powershell
.venv/Scripts/python.exe -m cama.hive.activity report --participant aelen --session YOUR_CHAT_ID --task "Reviewing database guards" --phase testing --result "Latest actual result"
.venv/Scripts/python.exe -m cama.hive.activity list --participant aelen
```

Use a separate stable session label for each chat. Update at task/phase changes.
Phases: working, testing, waiting, blocked, done. A report older than five minutes
is stale; it is not proof that the assistant stopped. Do not include secrets or
private conversation text. Result text is self-reported, not independently verified.

## Automatic CAMA tool observations

The local messaging MCP registration now adds `hive_activity_report` and
`hive_activity_list`. After reconnecting that server, these tools become available.
Set `CAMA_HIVE_IDENTITY=codex` for Lorien or `aelen` for Aelen in the respective
server environment. Without an explicit identity, automatic observation is disabled
rather than guessing who acted. Each process gets a unique session ID; optionally
set `CAMA_ACTIVITY_SESSION_ID` deliberately. Do not share an ID between separate chats.

The observer records only the tool name, session, timestamp and state. It never
stores tool arguments, outputs, error messages, conversation text, or reasoning.
A running async call refreshes every 15 seconds. After 60 seconds without a refresh,
its displayed state is stale_unknown. A synchronous blocking tool can prevent a
refresh; absence of a refresh does not prove a stopped process. `returned` means
the tool returned, not that the operation succeeded. `raised` means it threw.
Parallel calls have separate IDs. Telemetry failure does not fail the tool.

The MCP observer covers CAMA calls only. The Claude Code hooks below additionally
cover tools invoked through that client. Unsent model work remains unobserved. No background
watcher or model wake-up has been installed. Polling this view does not wake an
idle assistant. These local tools are not registered on the public reduced-surface
Hive server; participant labels on the local reporting tool are not authentication.

Operational call history is limited to seven days and 2,000 completed calls.
Snapshot responses return at most 100 recent reports and calls. Configure an
alternate database with CAMA_ACTIVITY_DB. Latest task reports are retained per
session. Existing personal memory databases are not modified.

## Verification

18 activity and hook tests pass: attribution/session separation, stale state, concurrent
calls, validation, tool return/failure/cancellation, metadata-only recording,
idempotent observer install, telemetry failure isolation, explicit-report MCP
integration, refusal to guess an unconfigured identity, hook privacy, event ordering,
legacy schema migration, preservation of existing settings, and local registration.
Live client delivery must be checked separately from synthetic tests.

## Claude Code hooks

Run `.venv/Scripts/python.exe -m cama.hive.install_claude_activity` to merge
hooks into `~/.claude/settings.json`. Existing settings are preserved and backed up.
The installer copies its runtime to `~/.claude/cama_activity` outside the Git checkout;
it uses the installing Python executable. Re-run after adapter changes to refresh it.
Events: PreToolUse, PostToolUse, PostToolUseFailure, SessionStart, UserPromptSubmit,
Stop and SessionEnd. The actual client session ID separates chats; child agent IDs
are hashed into separate labels. The local routing label is `aelen`, not authentication.

Records retain only tool/event names, session identifiers, states and timestamps.
No prompt, transcript, command, arguments, response or error text is stored.
PreToolUse is `requested`, since it can precede permission approval. After 60 seconds
without a terminal event it becomes `stale_unknown`; no hook heartbeat is fabricated.
Stop means the response stopped, not that the task succeeded. Hook errors exit zero
and do not block work. Inspect the view for source `claude_code_hook` to verify delivery.
Check `/hooks` in the client if an existing session has not loaded the new settings.
To uninstall, remove only entries invoking `cama_activity/claude_activity_hook.py`.
