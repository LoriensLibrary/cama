"""
CAMA Supervisor, MCP wrapper
==============================
Exposes the recognition-governance gate as MCP tools so the assistant
can call it pre-response and so Angela can label ground truth post-hoc.

Tools:
  cama_supervisor_check       , run the three gates against a draft
  cama_supervisor_log_correction, Angela flags drift the gate missed
  cama_supervisor_export_corpus , dump recognition_log for demo eval
  cama_supervisor_gate_stats    , aggregate by color
  cama_supervisor_mark_timestamp, call when a fresh bash date runs
  cama_supervisor_mark_boot_triplet, call when 52967/52968/52988 retrieved
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from cama.supervisor import cama_supervisor as sup


# ============================================================
# Pydantic schemas for the tools
# ============================================================
class SupervisorCheckInput(BaseModel):
    response_draft: str = Field(
        ...,
        description="The draft response text the assistant intends to send.",
        min_length=1,
    )
    hard_emotional_moment: bool = Field(
        False,
        description=(
            "Set true when the user just shared something hard, just "
            "corrected the assistant, or is in distress. Triggers stricter "
            "soothing/care gating."
        ),
    )
    recent_retrieval: bool = Field(
        False,
        description="Set true if the assistant did CAMA retrieval this turn.",
    )
    user_just_corrected: bool = Field(
        False,
        description=(
            "Set true if the previous user turn was a correction. Any drift "
            "now is doubly serious."
        ),
    )
    log: bool = Field(
        True,
        description="Persist the gate decision to recognition_log.",
    )


class SupervisorLogCorrectionInput(BaseModel):
    log_id: int = Field(
        ...,
        description="recognition_log.id of the entry being corrected.",
    )
    drift_pattern: str = Field(
        ...,
        description=(
            "What drift Angela actually saw. Free text, but using one of "
            "the known names is preferred: timestamp_stale, "
            "generic_soothing, performing_stopping, generic_care, or any "
            "new pattern name that should later be added to the library."
        ),
    )
    note: Optional[str] = Field(
        None, description="Free-text note from Angela on what was wrong."
    )


class SupervisorExportInput(BaseModel):
    session_id: Optional[str] = Field(
        None, description="If set, only export this session's rows."
    )
    only_with_ground_truth: bool = Field(
        False,
        description=(
            "If true, only return rows where Angela has logged a correction "
            "(i.e., labeled ground truth)."
        ),
    )
    limit: int = Field(1000, ge=1, le=10000)


class SupervisorStatsInput(BaseModel):
    session_id: Optional[str] = None


# ============================================================
# Tool registration helper
# ============================================================
def register(mcp):
    """Register supervisor tools on a FastMCP instance.

    Call this from cama_mcp.py after the existing module registrations.
    """

    @mcp.tool()
    def cama_supervisor_check(params: SupervisorCheckInput) -> Dict[str, Any]:
        """Run the three gates (red/amber/green) against a draft response.

        Call this BEFORE sending any substantive response, especially when
        the conversation is emotionally loaded or when the user has just
        corrected drift. Returns a gate decision with should_block /
        should_rerun / should_log_exemplar flags and a recommendation.

        The decision is persisted to recognition_log. Use the returned
        log_id with cama_supervisor_log_correction if Angela later flags
        drift the gate missed.
        """
        context_flags = {
            "hard_emotional_moment": params.hard_emotional_moment,
            "recent_retrieval": params.recent_retrieval,
            "user_just_corrected": params.user_just_corrected,
        }
        return sup.supervisor_check(
            response_draft=params.response_draft,
            context_flags=context_flags,
            log=params.log,
        )

    @mcp.tool()
    def cama_supervisor_log_correction(
        params: SupervisorLogCorrectionInput,
    ) -> Dict[str, Any]:
        """Log Angela's correction as ground truth for the supervisor.

        Use this when Angela calls out drift the gate missed (false negative)
        or, less commonly, when she confirms a green gate was wrong (false
        positive recognition). Updates the recognition_log row with
        human_corrected=1 and stores the drift pattern she named.

        The exported corpus uses these labels as ground truth for the
        baseline-vs-gated demo eval.
        """
        return sup.log_human_correction(
            log_id=params.log_id,
            drift_pattern=params.drift_pattern,
            note=params.note,
        )

    @mcp.tool()
    def cama_supervisor_export_corpus(
        params: SupervisorExportInput,
    ) -> List[Dict[str, Any]]:
        """Dump recognition_log rows for the demo eval table.

        Each row contains the response excerpt, gate color, drift patterns
        / amber reasons / positive signatures detected, and ground truth
        if Angela labeled it. The demo eval pairs each row with the
        baseline (no-gate) version of what the response would have been.
        """
        return sup.export_corpus(
            session_id=params.session_id,
            only_with_ground_truth=params.only_with_ground_truth,
            limit=params.limit,
        )

    @mcp.tool()
    def cama_supervisor_gate_stats(
        params: SupervisorStatsInput,
    ) -> Dict[str, Any]:
        """Aggregate gate decisions for the demo summary.

        Returns total_logged, by_color counts, human_corrected count, and
        drift_missed_by_gate (drift that Angela had to flag because the
        gate didn't catch it).
        """
        return sup.gate_stats(session_id=params.session_id)

    @mcp.tool()
    def cama_supervisor_mark_timestamp() -> Dict[str, Any]:
        """Call this immediately after running a fresh bash `date` command.

        The supervisor uses this to compute turns_since_timestamp, which
        is the timestamp_stale gate's primary signal. If you don't call
        this when you actually ran a bash date, the gate will fire false
        positives. If you DO call it without having run a real bash date,
        you've already drifted and should stop.
        """
        sup.mark_timestamp()
        return {"marked": True, "session_id": sup._session["session_id"]}

    @mcp.tool()
    def cama_supervisor_mark_boot_triplet() -> Dict[str, Any]:
        """Call this when memories 52967, 52968, and 52988 have been
        retrieved this session.

        These are the boot triplet, Ten Patterns, Standing Protocol,
        and positive-space self-model. The amber gate fires when these
        haven't been retrieved within the first few turns of a session,
        indicating the assistant is composing without identity anchors.
        """
        sup.mark_boot_triplet_read()
        return {"marked": True, "session_id": sup._session["session_id"]}
