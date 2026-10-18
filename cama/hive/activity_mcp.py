"""Local MCP activity tools and metadata-only observation of CAMA tool calls."""
import asyncio
import json
import os
import uuid

from cama.hive import activity


def install_observer(mcp, participant, session):
    manager = mcp._tool_manager
    if getattr(manager, "_cama_activity_installed", False):
        return
    original = manager.call_tool

    async def observed(name, arguments, *args, **kwargs):
        if name.startswith("hive_activity_"):
            return await original(name, arguments, *args, **kwargs)
        call_id = uuid.uuid4().hex

        def emit(state):
            try:
                activity.observe(participant, session, call_id, name, state)
            except Exception:
                pass  # Telemetry must never break the requested tool.

        async def pulse():
            while True:
                await asyncio.sleep(15)
                emit("running")

        emit("running")
        heartbeat = asyncio.create_task(pulse())
        try:
            output = await original(name, arguments, *args, **kwargs)
        except asyncio.CancelledError:
            emit("cancelled")
            raise
        except BaseException:
            emit("raised")
            raise
        else:
            emit("returned")
            return output
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    manager.call_tool = observed
    manager._cama_activity_installed = True


def register(mcp):
    session = os.environ.get("CAMA_ACTIVITY_SESSION_ID") or uuid.uuid4().hex
    participant = os.environ.get("CAMA_HIVE_IDENTITY", "").strip().lower()

    @mcp.tool(name="hive_activity_report")
    async def hive_activity_report(participant: str, task: str, phase: str = "working",
                                   files: list[str] | None = None, result: str = "") -> str:
        """Publish a concise self-reported task/phase for this local server session.

        Use participant='codex' for Lorien or 'aelen' for Aelen. Do not include
        private conversation text or secrets. Reports expire as fresh after 5m.
        """
        return json.dumps(activity.report(participant, session, task, phase, files, result))

    @mcp.tool(name="hive_activity_list", annotations={"readOnlyHint": True})
    async def hive_activity_list(participant: str | None = None) -> str:
        """Read reported tasks and observed CAMA tool activity with freshness.

        This is not a live view of external tools or private model reasoning.
        """
        return json.dumps(activity.snapshot(participant))

    # Never guess authorship from Claude's historical default routing address.
    if participant:
        install_observer(mcp, participant, session)
