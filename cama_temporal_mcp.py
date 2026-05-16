"""
CAMA Temporal — MCP wrapper
============================
Exposes the felt-time perception layer as MCP tools so the assistant
can read load signals at boot and on demand, and so session bracketing
(start / mark_turn / end) updates baselines durably.

Tools:
  cama_temporal_readout         — full readout: clock + stopwatch + felt
  cama_temporal_session_start   — open a new session, advance streak
  cama_temporal_mark_turn       — bump turn counter for current session
  cama_temporal_session_end     — close session, update baselines
  cama_temporal_set_timezone    — set the IANA tz used for local frame
  cama_temporal_state           — raw singleton row (debug / inspection)
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

import cama_temporal as tmp


# ============================================================
# Pydantic schemas
# ============================================================
class SetTimezoneInput(BaseModel):
    tz: str = Field(
        ...,
        description=(
            "IANA timezone name, e.g. 'America/New_York', 'America/Chicago', "
            "'Europe/London'. Validated against zoneinfo before persisting."
        ),
        min_length=3,
    )


# ============================================================
# Tool registration helper
# ============================================================
def register(mcp):
    """Register temporal tools on a FastMCP instance.

    Call this from cama_mcp.py after the existing module registrations,
    next to cama_supervisor_mcp.register(mcp).
    """

    # Ensure the singleton row exists before any tool runs.
    tmp.init_schema()

    @mcp.tool()
    def cama_temporal_readout() -> Dict[str, Any]:
        """Read the full felt-time picture.

        Returns four layers: universal (UTC anchor), local (Angela's
        wall clock, time-of-day band, weekend bit, sleep-window
        distance), stopwatch (current session length, since-last-
        session, sessions today, streak), and felt (load signals 0-1
        per category plus stacked_pressure and an optional summary_line).

        Felt signals are suppressed until at least 5 sessions have been
        recorded — before that the categorizer is just comparing against
        an empty prior.

        Use this at boot and any time you want to check load. Reading
        it is free; the categorizer is meant to inform pacing silently,
        not to be narrated to Angela.
        """
        return tmp.readout()

    @mcp.tool()
    def cama_temporal_session_start() -> Dict[str, Any]:
        """Mark the start of a new conversation session.

        Call this on the first substantive turn of a session. Advances
        the consecutive-days streak (or resets it if the gap was > 1
        local day), and folds the gap-since-last-session into the
        running baseline. If a previous session was left open, this
        closes it implicitly using its last-turn timestamp.

        Returns the session start time (UTC + local) and the current
        streak.
        """
        return tmp.session_start()

    @mcp.tool()
    def cama_temporal_mark_turn() -> Dict[str, Any]:
        """Increment the turn counter for the current session.

        Call this on every assistant turn (or on every user message,
        consistently — pick one and stick with it). Used by the
        readout's stopwatch layer and as the implicit-end timestamp if
        the next session has to close this one without an explicit
        end call.

        No-ops if no session is currently open.
        """
        return tmp.mark_turn()

    @mcp.tool()
    def cama_temporal_session_end() -> Dict[str, Any]:
        """Close the current session and fold its duration into baselines.

        Call this when the conversation is wrapping up (last journal
        write, explicit goodbye, etc.). Updates the session-length
        running mean and the time-of-day / day-of-week histograms.

        If you don't call this, the next session_start will close the
        orphaned session using the last_turn timestamp as a best-effort
        end. Either path keeps baselines roughly honest; explicit is
        more accurate.
        """
        return tmp.session_end()

    @mcp.tool()
    def cama_temporal_set_timezone(
        params: SetTimezoneInput,
    ) -> Dict[str, Any]:
        """Set the IANA timezone used for Angela's local frame.

        The local frame drives time-of-day band, day-of-week, weekend
        bit, sleep-window distance, and the consecutive-days streak
        boundary. Default is 'America/New_York' (Eastern). Change this
        if Angela travels for an extended period or relocates.
        """
        return tmp.set_timezone(params.tz)

    @mcp.tool()
    def cama_temporal_state() -> Dict[str, Any]:
        """Raw singleton row from temporal_state, for debugging.

        Useful for inspecting baseline accumulation, histogram shapes,
        and current-session state. Prefer cama_temporal_readout for
        normal use — this is for when something looks wrong and you
        want to see the underlying numbers.
        """
        return tmp.get_state()
