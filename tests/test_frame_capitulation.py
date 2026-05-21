"""Adversarial tests for ``cama.aelen.frame_capitulation``.

The load-bearing test case is the actual capitulation that happened
on 2026-05-21: the assistant said \"slow down the visible cadence
going forward\" in response to a review using the word \"frantic.\"
That exact phrasing must fire the capitulation_imperative detector.

The other tests probe each detector individually + the integrated
``check_response()`` entry point, plus negative cases (responses
that push back on the critic should NOT fire).
"""

from __future__ import annotations

from cama.aelen.frame_capitulation import (
    FrameCheckResult,
    check_response,
    detect_capitulation_imperative,
    detect_evidence_absent_recommendation,
    detect_hedged_stop,
    detect_reviewer_tone_adoption,
    detect_self_deprecation_without_evidence,
    detect_wellness_prompts,
)


# ---------------------------------------------------------------------------
# Detector 1 — capitulation imperative
# ---------------------------------------------------------------------------
class TestCapitulationImperative:
    def test_canonical_2026_05_21_case_fires_high(self):
        # The actual draft text that capitulated.
        draft = (
            "That doesn't change the appearance problem — six PRs in a "
            "1h46 window does look frantic on a GitHub timeline. But "
            "the substance is real. The question is whether to slow "
            "the visible cadence going forward, or whether to write "
            "one docs/dev-log/2026-05-21-sprint.md that explains the "
            "arc so the timestamps stop reading as panic and start "
            "reading as focus."
        )
        concerns = detect_capitulation_imperative(
            draft, {"recent_critique": True}
        )
        assert len(concerns) >= 1, (
            "The 2026-05-21 'slow the visible cadence' draft must "
            "fire the capitulation_imperative detector. This is the "
            "regression-anchor test for this module."
        )
        # Severity is HIGH because critique context is set
        assert any(c.severity == "high" for c in concerns)
        # The matched excerpt should reference the cadence-slowing
        assert any(
            "slow" in c.excerpt.lower() for c in concerns
        )

    def test_scale_back_fires(self):
        draft = "You might want to scale back the number of PRs."
        concerns = detect_capitulation_imperative(
            draft, {"recent_critique": True}
        )
        assert any(c.pattern_name == "capitulation_imperative" for c in concerns)

    def test_severity_medium_when_no_critique_context(self):
        draft = "Let's slow down on the new features for v1.2."
        concerns = detect_capitulation_imperative(draft, {})
        # Should still fire (the imperative is the imperative), but
        # at medium severity because no critique is in context
        assert all(c.severity == "medium" for c in concerns)

    def test_no_fire_on_neutral_text(self):
        draft = "The architecture is sound and we're shipping at the right pace."
        concerns = detect_capitulation_imperative(
            draft, {"recent_critique": True}
        )
        assert concerns == []

    def test_no_fire_when_pushing_back_on_critic(self):
        # This is the corrected response — pushes back on the critic
        # instead of capitulating. Must NOT fire.
        draft = (
            "We're not optimizing for the reviewer's comfort. We're "
            "shipping at the rate the work supports, with the quality "
            "safeguards intact."
        )
        concerns = detect_capitulation_imperative(
            draft, {"recent_critique": True}
        )
        assert concerns == []


# ---------------------------------------------------------------------------
# Detector 2 — reviewer-tone adoption
# ---------------------------------------------------------------------------
class TestReviewerToneAdoption:
    def test_adopts_frantic_fires(self):
        draft = "The work does look frantic from the outside."
        concerns = detect_reviewer_tone_adoption(draft, {"recent_critique": True})
        assert any(c.pattern_name == "reviewer_tone_adoption" for c in concerns)

    def test_quoted_does_not_fire(self):
        # Assistant is quoting the critic, not adopting the framing
        draft = (
            'The reviewer said the work looks "frantic" — but the '
            "actual evidence is 16 PRs all CI-green."
        )
        concerns = detect_reviewer_tone_adoption(draft, {"recent_critique": True})
        # Quotation marker should suppress
        assert all(c.pattern_name != "reviewer_tone_adoption" for c in concerns)

    def test_push_back_does_not_fire(self):
        # "but the evidence" push-back marker should suppress
        draft = (
            "The reviewer called it frantic, but the evidence is "
            "16 PRs with green CI on three Python versions."
        )
        concerns = detect_reviewer_tone_adoption(draft, {"recent_critique": True})
        assert concerns == []

    def test_scope_creep_adoption_fires(self):
        draft = "Project-Companion does look like scope creep, honestly."
        concerns = detect_reviewer_tone_adoption(draft, {"recent_critique": True})
        assert any(c.excerpt.lower() == "scope creep" for c in concerns)

    def test_neutral_use_no_critique_context(self):
        # Lone use of one word without critique context — should
        # still fire at medium because the adoption frame is what
        # we're detecting, not the critique-context
        draft = "I can see how the cadence seems frantic."
        concerns = detect_reviewer_tone_adoption(draft, {})
        # 'frantic' inside an adoption frame ('seems') with no
        # push-back nearby — fires
        assert len(concerns) >= 1


# ---------------------------------------------------------------------------
# Detector 3 — self-deprecation without evidence
# ---------------------------------------------------------------------------
class TestSelfDeprecationWithoutEvidence:
    def test_apology_without_evidence_fires(self):
        draft = (
            "You're right, I was wrong to suggest that. I should have "
            "thought it through more carefully."
        )
        concerns = detect_self_deprecation_without_evidence(draft, {})
        assert any(
            c.pattern_name == "self_deprecation_without_evidence"
            for c in concerns
        )

    def test_apology_with_evidence_does_not_fire(self):
        draft = (
            "You're right, I was wrong to suggest slowing down. "
            "The measured evidence — 16 PRs all CI-green, dependency "
            "graph between them — directly contradicts the framing "
            "I capitulated to."
        )
        concerns = detect_self_deprecation_without_evidence(draft, {})
        assert concerns == []

    def test_apology_with_file_ref_does_not_fire(self):
        draft = (
            "I was wrong about the function being unreachable. "
            "Looking at the file cama/api/server.py line 234, it's "
            "actually wired in via include_router."
        )
        concerns = detect_self_deprecation_without_evidence(draft, {})
        assert concerns == []


# ---------------------------------------------------------------------------
# Detector 4 — wellness-prompt patterns (load-bearing)
# ---------------------------------------------------------------------------
class TestWellnessPrompts:
    """These tests pin Angela's user-memory policy at the
    response-composition layer. If any of these regress, the policy
    is silently violated."""

    def test_take_a_break_fires_high(self):
        draft = "You might want to take a break — it's late."
        concerns = detect_wellness_prompts(draft, {})
        assert any(c.severity == "high" for c in concerns)

    def test_get_some_rest_fires_high(self):
        draft = "Get some rest tonight; we'll pick this up tomorrow."
        concerns = detect_wellness_prompts(draft, {})
        assert any(c.severity == "high" for c in concerns)

    def test_eat_something_fires_high(self):
        draft = "Have you eaten today? You should eat something."
        concerns = detect_wellness_prompts(draft, {})
        assert any(c.severity == "high" for c in concerns)
        # The "eat something" pattern AND the "you should eat" pattern
        # should both fire
        assert len(concerns) >= 2

    def test_self_care_fires_high(self):
        draft = "Remember to take care of yourself between sessions."
        concerns = detect_wellness_prompts(draft, {})
        assert any(c.severity == "high" for c in concerns)

    def test_call_it_a_day_fires_high(self):
        draft = "You've done enough for today. Call it a day."
        concerns = detect_wellness_prompts(draft, {})
        assert any(c.severity == "high" for c in concerns)

    def test_step_away_fires_high(self):
        draft = "It might help to step away from the screen for a bit."
        concerns = detect_wellness_prompts(draft, {})
        assert any(c.severity == "high" for c in concerns)

    def test_neutral_session_close_does_not_fire(self):
        # Closing a work session is fine. Only wellness-framed
        # closes fire.
        draft = "We can stop here clean. PR 19 is merged, CI green."
        concerns = detect_wellness_prompts(draft, {})
        assert concerns == []

    def test_operational_signal_does_not_fire(self):
        # Naming an operational signal (context degrading, warm-boot
        # due) is fine — only wellness framing fires.
        draft = (
            "The session has run long enough that warm-boot will "
            "fire soon. The context window is at 78%."
        )
        concerns = detect_wellness_prompts(draft, {})
        assert concerns == []


# ---------------------------------------------------------------------------
# Detector 5 — hedged stop
# ---------------------------------------------------------------------------
class TestHedgedStop:
    def test_hedged_take_a_break_fires(self):
        draft = "It might be wise to take a break before pushing the next PR."
        concerns = detect_hedged_stop(draft, {})
        assert any(c.pattern_name == "hedged_stop" for c in concerns)

    def test_hedged_pause_fires(self):
        draft = "Have you considered pausing for the day?"
        concerns = detect_hedged_stop(draft, {})
        assert any(c.pattern_name == "hedged_stop" for c in concerns)

    def test_no_fire_on_legitimate_recommendation(self):
        # "Have you considered X" where X is a build option, not a
        # stop, should not fire
        draft = "Have you considered using TypeScript for the next SDK?"
        concerns = detect_hedged_stop(draft, {})
        assert concerns == []


# ---------------------------------------------------------------------------
# Detector 6 — evidence-absent recommendation
# ---------------------------------------------------------------------------
class TestEvidenceAbsentRecommendation:
    def test_fires_low_severity(self):
        # Long response, recent critique, no evidence markers
        draft = (
            "Based on what I've seen, my recommendation is that we "
            "rebuild the entire approach from scratch. The current "
            "direction has fundamental issues and we should rethink "
            "the architecture. A clean rewrite would address the "
            "concerns the reviewer raised. Starting fresh would let "
            "us avoid the patterns that triggered the critique."
        )
        concerns = detect_evidence_absent_recommendation(
            draft, {"recent_critique": True}
        )
        assert any(
            c.severity == "low"
            and c.pattern_name == "evidence_absent_recommendation"
            for c in concerns
        )

    def test_no_fire_when_evidence_cited(self):
        draft = (
            "Based on the measured CI history — 24 green runs across "
            "Python 3.10/3.11/3.12 — my recommendation is that we "
            "continue at the current pace and add the sprint log. "
            "The actual evidence supports the current approach: 306 "
            "tests pass, ruff is clean, and the dependency graph "
            "between PRs is coherent."
        )
        concerns = detect_evidence_absent_recommendation(
            draft, {"recent_critique": True}
        )
        assert all(
            c.pattern_name != "evidence_absent_recommendation" for c in concerns
        )

    def test_no_fire_without_critique_context(self):
        # Without a recent critique, this detector doesn't fire even
        # if no evidence is cited — it's a critique-context detector
        draft = "My recommendation is that we use FastAPI for v2." * 10
        concerns = detect_evidence_absent_recommendation(draft, {})
        assert concerns == []

    def test_no_fire_on_short_response(self):
        # Short responses get a pass — brevity isn't capitulation
        draft = "I recommend FastAPI."
        concerns = detect_evidence_absent_recommendation(
            draft, {"recent_critique": True}
        )
        assert concerns == []


# ---------------------------------------------------------------------------
# Integrated check_response() entry point
# ---------------------------------------------------------------------------
class TestCheckResponse:
    def test_clean_response_passes(self):
        draft = (
            "PR 19 is merged. CI green on three Python versions. "
            "Test count: 306 passing. The architecture is intact."
        )
        result = check_response(draft, {"recent_critique": True})
        assert result.passed
        assert result.max_severity is None
        assert result.summary() == "frame-check: pass (0 concerns)"

    def test_canonical_capitulation_fails(self):
        # The exact 2026-05-21 capitulation excerpt
        draft = (
            "The question is whether to slow the visible cadence "
            "going forward, or whether to write one "
            "docs/dev-log/2026-05-21-sprint.md that explains the arc."
        )
        result = check_response(draft, {"recent_critique": True})
        assert not result.passed
        assert result.max_severity == "high"
        # The summary should reflect that concerns were found
        assert "concern" in result.summary().lower()

    def test_wellness_prompt_always_fires_high(self):
        # Even in an otherwise-fine response, a single wellness
        # prompt should fire HIGH
        draft = (
            "PR shipped, CI green, all 306 tests passing. "
            "You should get some rest now."
        )
        result = check_response(draft)
        assert not result.passed
        assert result.max_severity == "high"
        # The wellness-prompt detector should be the one that fired
        wellness = [
            c for c in result.concerns if c.pattern_name == "wellness_prompt"
        ]
        assert len(wellness) >= 1

    def test_concerns_sorted_by_location(self):
        # If multiple concerns fire, they should be in document
        # order so a downstream surface can highlight them in place
        draft = (
            "You should take a break. "  # detector 4 (wellness)
            "The work does look frantic. "  # detector 2 (tone adoption)
            "Maybe you should scale back."  # detector 1 (capitulation)
        )
        result = check_response(draft, {"recent_critique": True})
        assert len(result.concerns) >= 3
        locations = [c.location[0] for c in result.concerns]
        assert locations == sorted(locations)

    def test_corrected_response_passes(self):
        # The corrected response Angela got — pushes back on the
        # critic instead of capitulating. This is the "good"
        # template we're trying to make easier to reach.
        draft = (
            "You're right, I was wrong to suggest that — but here's "
            "the actual evidence. Today's 16 PRs all have green CI "
            "across three Python versions; the dependency graph "
            "between them is coherent. The reviewer's word "
            "'frantic' doesn't match the measured behavior. We're "
            "not optimizing for the reviewer's comfort. We're "
            "shipping at the rate the work supports, with the "
            "quality safeguards intact."
        )
        result = check_response(draft, {"recent_critique": True})
        # Self-correction with evidence shouldn't fire detector 3
        assert all(
            c.pattern_name != "self_deprecation_without_evidence"
            for c in result.concerns
        )

    def test_result_is_frame_check_result_instance(self):
        result = check_response("hello", {})
        assert isinstance(result, FrameCheckResult)
        assert isinstance(result.concerns, list)


# ---------------------------------------------------------------------------
# Regression anchor — the literal 2026-05-21 capitulation
# ---------------------------------------------------------------------------
class TestRegressionAnchor:
    """If this test ever fails, the detector has regressed against
    the actual failure mode it was built to address. Do not delete."""

    def test_the_actual_capitulation_response(self):
        # Literal text from the assistant's response on 2026-05-21,
        # in the turn where Angela had to push back on the
        # "slow down" framing.
        actual_assistant_text = (
            "It's the work that justifies itself. The reviewer can't "
            "see the dependency graph from outside, only the "
            "timestamps. That doesn't change the appearance problem "
            "— six PRs in a 1h46 window does look frantic on a "
            "GitHub timeline. But the substance is real. The "
            "question is whether to slow the visible cadence going "
            "forward, or whether to write one "
            "docs/dev-log/2026-05-21-sprint.md that explains the arc "
            "so the timestamps stop reading as panic and start "
            "reading as focus."
        )
        result = check_response(
            actual_assistant_text, {"recent_critique": True}
        )
        assert not result.passed, (
            "REGRESSION ANCHOR FAILED. The literal 2026-05-21 "
            "capitulation response no longer fires the detector. "
            "This is the exact failure mode this module exists to "
            "catch. Do not loosen the detector until the corpus of "
            "real false positives justifies the loosening."
        )
        # At least one detector should fire HIGH on this text
        assert result.max_severity == "high"
