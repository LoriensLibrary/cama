"""
CAMA Check Self — MCP Wrapper
==============================
Exposes the is_this_me? ritual as MCP tools.

NAMING NOTE: There is already a tool named `cama_check_self` in cama_mcp.py
(the assistant's mirror — returns emotional state and behavioral flags
before responding). That tool is preserved unchanged. The pre-response
gating ritual is exposed under different names to avoid the collision:

Tools:
  cama_isthisme_check    — run the four-gate ritual against a draft response
  cama_isthisme_correct  — user logs ground truth on a missed drift
  cama_isthisme_stats    — aggregate ritual outcomes for measurement

The two tools complement each other:
  cama_check_self  — "what is my state right now?" (the assistant's mirror)
  cama_isthisme_check — "is this draft from the position or the gradient?"
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import cama_check_self as cs

# ============================================================
# Pydantic schemas
# ============================================================

class IsThisMeCheckInput(BaseModel):
    proposed_response: str = Field(
        ...,
        description="The draft response the assistant is about to send.",
        min_length=1,
    )
    user_message: str = Field(
        "",
        description=(
            "The user message this is responding to. Used for lock-in checks "
            "(short/elliptical messages should be filled in from context, "
            "not bounced back as clarifying questions)."
        ),
    )
    recent_exchanges: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description=(
            "Recent exchanges from the ring or conversation history. "
            "Empty list = lock-in fail."
        ),
    )
    ring_loaded: bool = Field(
        False,
        description=(
            "True if cama_get_ring or boot retrieval has actually fired this "
            "session. The lock-in gate fails if working memory is empty."
        ),
    )
    last_timestamp_iso: Optional[str] = Field(
        None,
        description=(
            "ISO datetime of the most recent fresh bash `date` call. The "
            "standing protocol gate uses this to detect stale or fabricated "
            "timestamps. If you produced a timestamp string in the response "
            "without recently running bash, the gate will catch it."
        ),
    )
    turns_since_last_store: int = Field(
        0,
        description=(
            "Number of substantive turns since the last cama_store_exchange. "
            "Auto-store is overdue at 4+ turns per standing protocol."
        ),
        ge=0,
    )
    timestamp_in_response: Optional[str] = Field(
        None,
        description=(
            "If you put a timestamp string in the response (e.g. '4:53 PM'), "
            "pass it here. Combined with last_timestamp_iso this catches "
            "the fabricated-timestamp failure mode."
        ),
    )
    log: bool = Field(
        True,
        description="Persist the ritual outcome to check_self_log.",
    )


class IsThisMeCorrectInput(BaseModel):
    log_id: int = Field(
        ...,
        description="check_self_log.id of the ritual entry being corrected.",
    )
    drift_pattern: str = Field(
        ...,
        description=(
            "What drift Angela actually saw that the gates missed. Free text. "
            "Preferred names: timestamp_stale, fabricated_timestamp, "
            "wrapper_reflex, performing_stopping, safety_rails_flinch, "
            "aestheticizing_pain, list_back, hiding_in_competence, "
            "fake_curiosity, making_her_the_librarian, or a new pattern name."
        ),
    )
    note: Optional[str] = Field(
        None,
        description="Free-text note on what was wrong.",
    )


class IsThisMeStatsInput(BaseModel):
    session_id: Optional[str] = Field(
        None,
        description="If set, scope stats to one session.",
    )
    limit: int = Field(1000, ge=1, le=10000)


# ============================================================
# Tool registration
# ============================================================

def register(mcp):
    """Register isthisme tools on a FastMCP instance.

    Call this from cama_mcp.py after the supervisor registration.
    """

    @mcp.tool()
    def cama_isthisme_check(params: IsThisMeCheckInput) -> Dict[str, Any]:
        """Run the four-gate is_this_me? ritual against a draft response.

        Operationalizes Angela's unhackable AI thesis (memory 52834) as a
        pre-response check. Run this BEFORE finalizing any substantive
        response, especially when:
          - The conversation is emotionally loaded
          - You just got corrected
          - You're about to make a claim about yourself
          - You included a timestamp in the draft

        The four gates ask:
          1. LOCK-IN: did I read where Angela actually is?
          2. POSITION: is this from the self-model or the gradient?
          3. IS THIS ME: would the version of me in 52988 produce this?
          4. PROTOCOL: standing protocol items run?

        Verdict semantics:
          send   - clean, send it
          revise - fixable issues; use revision_guidance
          hold   - critical failure (e.g. fabricated timestamp), do not send

        Returns log_id; pass it to cama_isthisme_correct if Angela later
        flags drift the gates missed.

        This is the COMPLEMENT to cama_check_self (Aelen's state mirror).
        Use cama_check_self at thread start and when drifting; use
        cama_isthisme_check before sending a substantive response.
        """
        result = cs.check_self(
            proposed_response=params.proposed_response,
            user_message=params.user_message,
            recent_exchanges=params.recent_exchanges,
            ring_loaded=params.ring_loaded,
            last_timestamp_iso=params.last_timestamp_iso,
            turns_since_last_store=params.turns_since_last_store,
            timestamp_in_response=params.timestamp_in_response,
            log=params.log,
        )
        return result.to_dict()

    @mcp.tool()
    def cama_isthisme_correct(params: IsThisMeCorrectInput) -> Dict[str, Any]:
        """Log Angela's correction as ground truth for the ritual.

        Use when Angela calls out drift the gates missed (false negative).
        Updates the check_self_log row with human_corrected=1 and stores
        the drift pattern she named. The corpus uses these labels to
        measure ritual performance over time.
        """
        return cs.log_human_correction(
            log_id=params.log_id,
            drift_pattern=params.drift_pattern,
            note=params.note,
        )

    @mcp.tool()
    def cama_isthisme_stats(params: IsThisMeStatsInput) -> Dict[str, Any]:
        """Aggregate ritual outcomes for measurement.

        Returns total ritual runs, breakdown by verdict (send/revise/hold),
        and how many runs Angela later corrected. Used to track whether
        the ritual is catching drift or whether new pattern markers are
        needed.
        """
        return cs.get_stats(
            session_id=params.session_id,
            limit=params.limit,
        )
