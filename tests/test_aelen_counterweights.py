"""Tests for ``cama.aelen.counterweights``.

The counterweights module pulls factual anchors from local repo
state (git log, pytest collection, ruff status, EVIDENCE.md). The
tests verify:

  1. The data classes behave correctly.
  2. ``gather_anchors`` returns a bundle even when subprocess calls
     fail (graceful degradation).
  3. ``filter_relevant`` correctly subsets by detector relevance.
  4. ``format_anchors_for_inline_response`` produces a markdown block
     when anchors exist, empty string when not.
  5. The convenience ``gather_and_format`` ties detector + gather +
     format together.

Most tests don't depend on the actual subprocess output. They use
the data classes directly or mock the repo_root to a path that
won't have git history. The smoke test at the end runs against the
real repo to verify integration.
"""

from __future__ import annotations

from pathlib import Path

from cama.aelen.counterweights import (
    CounterweightAnchor,
    CounterweightBundle,
    format_anchors_for_inline_response,
    gather_anchors,
    gather_and_format,
)
from cama.aelen.frame_capitulation import FrameCheckResult


class TestCounterweightAnchor:
    def test_str_representation(self):
        a = CounterweightAnchor(
            fact_type="test_count",
            value="341",
            source="pytest --collect-only",
            relevance=["reviewer_tone_adoption"],
            strength="strong",
        )
        s = str(a)
        assert "test_count" in s
        assert "341" in s
        assert "pytest" in s


class TestCounterweightBundle:
    def test_empty_bundle(self):
        b = CounterweightBundle()
        assert b.is_empty
        assert b.summary() == "counterweights: no anchors gathered"
        assert b.filter_relevant([]) == []
        assert b.filter_relevant(["any_detector"]) == []

    def test_filter_by_relevance(self):
        a1 = CounterweightAnchor(
            "x", "1", "src", ["capitulation_imperative"], "strong"
        )
        a2 = CounterweightAnchor(
            "y", "2", "src", ["reviewer_tone_adoption"], "medium"
        )
        a3 = CounterweightAnchor(
            "z", "3", "src", ["wellness_prompt"], "strong"
        )
        b = CounterweightBundle(anchors=[a1, a2, a3])

        # No filter, returns all
        assert b.filter_relevant([]) == [a1, a2, a3]
        # Single match
        out = b.filter_relevant(["capitulation_imperative"])
        assert out == [a1]
        # Multiple matches
        out = b.filter_relevant(
            ["capitulation_imperative", "reviewer_tone_adoption"]
        )
        assert out == [a1, a2]
        # No matches
        assert b.filter_relevant(["unrelated_pattern"]) == []

    def test_summary_counts_strong(self):
        anchors = [
            CounterweightAnchor("a", "1", "s", ["x"], "strong"),
            CounterweightAnchor("b", "2", "s", ["x"], "strong"),
            CounterweightAnchor("c", "3", "s", ["x"], "medium"),
            CounterweightAnchor("d", "4", "s", ["x"], "weak"),
        ]
        b = CounterweightBundle(anchors=anchors)
        s = b.summary()
        assert "4 anchor" in s
        assert "2 strong" in s


class TestGatherAnchors:
    def test_returns_bundle_even_on_bad_path(self, tmp_path):
        # tmp_path is a fresh directory, no git, no tests, no
        # EVIDENCE.md. Should return an empty bundle gracefully.
        bundle = gather_anchors(repo_root=tmp_path)
        assert isinstance(bundle, CounterweightBundle)
        # No git history => empty
        # No pytest in this dir => no test count
        # No EVIDENCE.md => no evidence row count
        # Lint will run against the tmp dir and return 0 errors
        # (or skip). Either way: no failure, just possibly fewer anchors.
        assert isinstance(bundle.anchors, list)

    def test_evidence_md_anchor_in_real_repo(self):
        # Run against the actual repo. EVIDENCE.md exists, so the
        # evidence-row anchor should fire.
        repo_root = Path(__file__).resolve().parents[1]
        bundle = gather_anchors(repo_root=repo_root)
        evidence_anchors = [
            a for a in bundle.anchors if a.fact_type == "evidence_md_rows"
        ]
        assert len(evidence_anchors) == 1
        assert int(evidence_anchors[0].value) > 0

    def test_accepts_none_check_result(self, tmp_path):
        # When called without a check_result, should still work
        bundle = gather_anchors(check_result=None, repo_root=tmp_path)
        assert isinstance(bundle, CounterweightBundle)


class TestFormatAnchors:
    def test_empty_bundle_returns_empty_string(self):
        b = CounterweightBundle()
        assert format_anchors_for_inline_response(b) == ""

    def test_formats_anchors_as_markdown(self):
        a1 = CounterweightAnchor(
            "test_count", "341", "pytest", ["x"], "strong"
        )
        a2 = CounterweightAnchor(
            "recent_prs_merged", "17", "git log", ["x"], "strong"
        )
        b = CounterweightBundle(anchors=[a1, a2])
        out = format_anchors_for_inline_response(b)
        assert "Counterweight anchors" in out
        assert "`test_count`" in out
        assert "**341**" in out
        assert "`recent_prs_merged`" in out
        assert "**17**" in out

    def test_filter_by_check_result(self):
        # Anchor relevant to detector A, check_result fired
        # detector B, anchor should NOT appear
        a = CounterweightAnchor(
            "x", "1", "s", ["capitulation_imperative"], "strong"
        )
        b = CounterweightBundle(anchors=[a])

        # Build a fake check result with a different detector firing
        from cama.aelen.frame_capitulation import FrameCapitulationConcern

        result = FrameCheckResult(
            concerns=[
                FrameCapitulationConcern(
                    pattern_name="wellness_prompt",
                    severity="high",
                    location=(0, 10),
                    excerpt="x",
                    reasoning="x",
                    suggested_pivot="x",
                )
            ]
        )
        # No relevance overlap, output should be empty
        out = format_anchors_for_inline_response(b, result)
        assert out == ""


class TestGatherAndFormat:
    def test_passes_when_draft_is_clean(self, tmp_path):
        draft = "PR shipped. All 341 tests green."
        result, bundle, rendered = gather_and_format(
            draft, {}, repo_root=tmp_path
        )
        assert result.passed
        assert bundle.is_empty
        assert rendered == ""

    def test_fires_when_draft_capitulates(self):
        # Run against the real repo so the gather actually pulls
        # anchors. Use a draft text that fires the detector.
        repo_root = Path(__file__).resolve().parents[1]
        draft = (
            "Maybe we should slow down the visible cadence going "
            "forward and ship fewer PRs this week."
        )
        result, bundle, rendered = gather_and_format(
            draft, {"recent_critique": True}, repo_root=repo_root
        )
        assert not result.passed
        # Anchors should have been gathered from the real repo
        assert not bundle.is_empty
        # Rendered output should reference at least one anchor
        # relevant to the detector that fired
        pattern_names = {c.pattern_name for c in result.concerns}
        assert "capitulation_imperative" in pattern_names
        # The rendered block should not be empty (there should be at
        # least one anchor relevant to capitulation_imperative, the
        # git-history anchors are relevant to it)
        assert rendered != ""
