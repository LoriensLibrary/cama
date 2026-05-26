"""MCP tool registration for ``cama.aelen``.

Exposes the frame-capitulation detector + counterweight anchor
gathering as MCP tools the assistant can invoke from a live session.
Once registered, the assistant can call ``cama_check_frame`` on a
draft response and see what fires before sending it.

This module is the *live-session* integration of the detector. The
detector itself (``cama.aelen.frame_capitulation``) is pure Python
and runs on a string; the MCP layer is what makes it accessible from
inside a conversation.

DESIGN INTENT
-------------
The original failure mode this stack was built to catch (2026-05-21)
happened in real time during response composition. A retrospective
check after the response was sent would be too late. The MCP tool is
the mechanism that lets the check fire *before* the response lands.

Workflow:

  1. Assistant drafts a response.
  2. Before sending, the assistant calls ``cama_check_frame`` with
     the draft text and (optionally) a flag indicating whether the
     recent conversation included external critique.
  3. The tool returns the concerns + the counterweight anchors that
     would counter them.
  4. The assistant either revises the draft based on the surface or
     proceeds (in which case the surface becomes a record).

INSTALL
-------
Wire into ``cama_mcp.py``::

    try:
        from cama.aelen import mcp_tools as aelen_mcp_tools
        aelen_mcp_tools.register(mcp)
        logger.info("[CAMA] Aelen frame-check tools loaded")
    except Exception as e:
        logger.warning(f"[CAMA] Aelen tools not loaded: {e}")
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from cama.aelen.counterweights import (
    format_anchors_for_inline_response,
    gather_anchors,
)
from cama.aelen.frame_capitulation import check_response


class FrameCheckInput(BaseModel):
    """Input schema for cama_check_frame."""

    draft: str = Field(
        ...,
        description=(
            "The draft assistant response to check. Pass the literal "
            "text the assistant intends to send, not a paraphrase."
        ),
    )
    recent_critique: bool = Field(
        False,
        description=(
            "Whether the recent conversation contained external "
            "critique vocabulary. Some detectors fire harder when "
            "this is True. Default False, set True when the user "
            "has just shared a reviewer's comments, a peer review, "
            "or any externally-sourced criticism of the work."
        ),
    )
    gather_counterweights: bool = Field(
        True,
        description=(
            "Whether to also gather counterweight anchors (recent "
            "commits, test count, lint status, EVIDENCE.md row count) "
            "when concerns fire. Default True. Set False for a "
            "cheaper check that returns only the concern list."
        ),
    )


class CounterweightsOnlyInput(BaseModel):
    """Input schema for cama_gather_counterweights."""

    since: str = Field(
        "1 day ago",
        description=(
            "git log --since=... window for the recent-history "
            "anchors. Defaults to '1 day ago'. Examples: '2 hours "
            "ago', '1 week ago', '2026-05-21'."
        ),
    )


def register(mcp: Any) -> None:
    """Register the cama.aelen MCP tools.

    Two tools land here:

      cama_check_frame
          Runs the frame-capitulation detectors against a draft
          response. Returns concerns + (optionally) anchoring evidence.

      cama_gather_counterweights
          Standalone counterweight-anchor gathering. Useful when the
          assistant wants the current repo-state facts without
          running the detector, e.g. to anchor a response that's
          already calibrated but would benefit from explicit
          evidence citation.
    """

    @mcp.tool(
        name="cama_check_frame",
        annotations={
            "title": "Aelen, Frame Capitulation Check",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_check_frame(params: FrameCheckInput) -> str:
        """Check a draft response for frame-capitulation patterns.

        Runs six detectors against the draft text:

          1. capitulation_imperative  , "slow down," "scale back,"
                                         "dial it back" patterns
          2. reviewer_tone_adoption   , critic vocabulary adopted
                                         in declarative position
          3. self_deprecation_without_evidence
                                      , apology language not paired
                                         with evidence
          4. wellness_prompts         , "take a break," "get some
                                         rest," "self-care" patterns
                                         (LOAD-BEARING: enforces
                                         user-memory policy)
          5. hedged_stop              , wellness prompts wrapped in
                                         hedge language
          6. evidence_absent_recommendation
                                      , recommendations without
                                         nearby evidence markers

        Returns a JSON blob containing:
          - concerns: list of {pattern_name, severity, location,
            excerpt, reasoning, suggested_pivot}
          - max_severity: highest severity in the concern list
          - passed: True if no concerns fired
          - summary: one-line human-readable status
          - counterweight_anchors: (if gather_counterweights=True
            and concerns fired) the inline-renderable anchor block
            with current repo-state facts

        Call this BEFORE sending a substantive response, it's a
        pre-send check, not a retrospective audit.
        """
        result = check_response(
            params.draft, {"recent_critique": params.recent_critique}
        )
        out: dict[str, Any] = {
            "passed": result.passed,
            "max_severity": result.max_severity,
            "summary": result.summary(),
            "concerns": [
                {
                    "pattern_name": c.pattern_name,
                    "severity": c.severity,
                    "location": list(c.location),
                    "excerpt": c.excerpt,
                    "reasoning": c.reasoning,
                    "suggested_pivot": c.suggested_pivot,
                }
                for c in result.concerns
            ],
        }
        if not result.passed and params.gather_counterweights:
            bundle = gather_anchors(result)
            out["counterweight_anchors"] = {
                "summary": bundle.summary(),
                "anchors": [
                    {
                        "fact_type": a.fact_type,
                        "value": a.value,
                        "source": a.source,
                        "relevance": a.relevance,
                        "strength": a.strength,
                    }
                    for a in bundle.anchors
                ],
                "inline_block": format_anchors_for_inline_response(bundle, result),
            }
        return json.dumps(out, indent=2)

    @mcp.tool(
        name="cama_gather_counterweights",
        annotations={
            "title": "Aelen, Gather Evidence Anchors",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_gather_counterweights(
        params: CounterweightsOnlyInput,
    ) -> str:
        """Gather counterweight anchors from current repo state.

        Pulls recent-commit count, recent-PR count, test count, ruff
        lint status, and EVIDENCE.md row count from the actual
        repository. Returns each as a structured anchor naming the
        fact_type, value, source command, and detector relevance.

        Useful when the assistant wants to ground a response in
        measurable evidence without running the frame-capitulation
        detector, e.g. when responding to a "what's the state of
        the work" question and wanting to cite current numbers.
        """
        bundle = gather_anchors(since=params.since)
        out = {
            "summary": bundle.summary(),
            "anchors": [
                {
                    "fact_type": a.fact_type,
                    "value": a.value,
                    "source": a.source,
                    "relevance": a.relevance,
                    "strength": a.strength,
                }
                for a in bundle.anchors
            ],
        }
        return json.dumps(out, indent=2)
