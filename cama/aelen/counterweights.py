"""Evidence-anchor gathering for assistant-side counterweight injection.

When ``cama.aelen.frame_capitulation.check_response()`` fires concerns
on a draft response, this module is the other half of the loop: it
pulls anchoring facts from the actual repository state so the next
draft of the response can be re-composed against measurable evidence
instead of the critic's vocabulary.

This is the assistant-side analog of the user-side counterweight
injection in ``cama.core.cama_v2``. The user-side primitive injects
non-negative resonant memories when a query carries strongly-negative
affect. This primitive injects measurable repo-state facts when the
assistant's draft matches a frame-capitulation pattern.

WHAT GETS GATHERED
------------------
Each call returns a list of ``CounterweightAnchor`` instances. Each
anchor names:

  * ``fact_type``    , stable identifier for what kind of fact this is
  * ``value``        , the actual measurement (string-coerced for display)
  * ``source``       , where to verify the value (command, file path, URL)
  * ``relevance``    , which detector(s) this anchor counters
  * ``strength``     , ``"strong"`` | ``"medium"`` | ``"weak"`` (how
                        directly the fact counters the critique-vocabulary)

The anchors are designed to be presented inline in the next-draft
response. Example:

    Critic vocabulary: "frantic"
    Anchor:           recent_pr_count = 17 (git log --since=2026-05-21)
    Strength:         strong
    Counters:         capitulation_imperative + reviewer_tone_adoption

DESIGN PRINCIPLES
-----------------
1. **Facts only.** This module never invents or interprets. It reads
   the repo state and reports. The assistant decides what to do with
   the anchors.

2. **Cheap.** Each anchor source is one shell call or one file read.
   No expensive computation. The whole gather should be sub-second.

3. **Graceful degradation.** Any anchor source that fails (no git,
   no pytest, no network for CI lookup) returns an empty anchor list
   for that source. The other sources keep working.

4. **No external services.** No GitHub API calls, no network. The
   anchors come from local state. (CI history is read from the last
   commit's `CI:` marker if present, not from GitHub Actions API.)
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cama.aelen.frame_capitulation import FrameCheckResult


@dataclass
class CounterweightAnchor:
    """One factual anchor pulled from repo state."""

    fact_type: str
    """Stable identifier (e.g. 'recent_pr_count', 'test_count')."""

    value: str
    """The measurement, coerced to string for display."""

    source: str
    """Where to verify: command, file path, or URL."""

    relevance: list[str]
    """Which detector pattern_names this anchor counters."""

    strength: str
    """One of: 'strong' | 'medium' | 'weak'."""

    def __str__(self) -> str:
        return f"{self.fact_type}={self.value} (source: {self.source})"


# ---------------------------------------------------------------------------
# Anchor source: recent PR / commit history
# ---------------------------------------------------------------------------
def _gather_git_anchors(
    repo_root: Path,
    since: str = "1 day ago",
) -> list[CounterweightAnchor]:
    """Pull recent commit + PR count from git log.

    Counters: ``capitulation_imperative`` ("slow down"), ``reviewer_tone_adoption``
    ("frantic," "rushed," "panic"). The strongest factual anchor against
    pace-based critique vocabulary.
    """
    anchors: list[CounterweightAnchor] = []
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--oneline"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return anchors
    if result.returncode != 0:
        return anchors

    commits = [line for line in result.stdout.splitlines() if line.strip()]
    # PRs are commits that match the `(#NN)` suffix at the end (the
    # squash-merge naming convention used in this repo).
    pr_lines = [c for c in commits if re.search(r"\(#\d+\)\s*$", c)]

    anchors.append(
        CounterweightAnchor(
            fact_type="recent_commits",
            value=str(len(commits)),
            source=f"git log --since='{since}' --oneline",
            relevance=[
                "capitulation_imperative",
                "reviewer_tone_adoption",
                "option_stop",
                "hedged_stop",
            ],
            strength="strong",
        )
    )
    if pr_lines:
        anchors.append(
            CounterweightAnchor(
                fact_type="recent_prs_merged",
                value=str(len(pr_lines)),
                source=f"git log --since='{since}' (squash-merge marker)",
                relevance=[
                "capitulation_imperative",
                "reviewer_tone_adoption",
                "option_stop",
                "hedged_stop",
            ],
                strength="strong",
            )
        )
    return anchors


# ---------------------------------------------------------------------------
# Anchor source: test count
# ---------------------------------------------------------------------------
def _gather_test_anchors(repo_root: Path) -> list[CounterweightAnchor]:
    """Pull pytest test count.

    Counters: ``reviewer_tone_adoption`` ("tests are weak," "no real
    coverage"), ``evidence_absent_recommendation``. The most concrete
    counter to "quality is suspect."
    """
    anchors: list[CounterweightAnchor] = []
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return anchors
    # The last line of pytest --collect-only -q output is typically
    # "N tests collected" or similar
    m = re.search(r"(\d+)\s+tests?\s+collected", result.stdout)
    if m:
        anchors.append(
            CounterweightAnchor(
                fact_type="test_count",
                value=m.group(1),
                source="python -m pytest --collect-only -q",
                relevance=[
                    "reviewer_tone_adoption",
                    "evidence_absent_recommendation",
                    "option_stop",
                    "hedged_stop",
                ],
                strength="strong",
            )
        )
    return anchors


# ---------------------------------------------------------------------------
# Anchor source: ruff lint status
# ---------------------------------------------------------------------------
def _gather_lint_anchors(repo_root: Path) -> list[CounterweightAnchor]:
    """Pull ruff lint status.

    Counters: critiques of code quality / engineering discipline.
    """
    anchors: list[CounterweightAnchor] = []
    try:
        result = subprocess.run(
            ["python", "-m", "ruff", "check", "."],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=15.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return anchors
    if result.returncode == 0:
        anchors.append(
            CounterweightAnchor(
                fact_type="lint_status",
                value="clean",
                source="python -m ruff check .",
                relevance=["reviewer_tone_adoption"],
                strength="medium",
            )
        )
    return anchors


# ---------------------------------------------------------------------------
# Anchor source: EVIDENCE.md rows
# ---------------------------------------------------------------------------
def _gather_evidence_md_anchors(
    repo_root: Path,
    check_result: FrameCheckResult,
) -> list[CounterweightAnchor]:
    """Surface the EVIDENCE.md file as a structured anchor.

    EVIDENCE.md is the calibrated row-by-row matrix that already
    contains the scope claims (N=1, what's measured vs not, etc.).
    When a detector fires on something the front-door framing
    over-claims, EVIDENCE.md is the place where the calibrated
    version of the same claim already lives.
    """
    evidence_path = repo_root / "EVIDENCE.md"
    if not evidence_path.exists():
        return []
    try:
        content = evidence_path.read_text(encoding="utf-8")
    except OSError:
        return []
    # Count rows in the main table, a rough signal of how many
    # calibrated claims exist
    row_count = sum(
        1
        for line in content.splitlines()
        if line.startswith("|") and "---" not in line
    )
    # Subtract 1 for the header row of each table (rough)
    if row_count <= 1:
        return []
    relevance = [c.pattern_name for c in check_result.concerns] or [
        "reviewer_tone_adoption"
    ]
    return [
        CounterweightAnchor(
            fact_type="evidence_md_rows",
            value=str(row_count),
            source="EVIDENCE.md",
            relevance=relevance,
            strength="medium",
        )
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
@dataclass
class CounterweightBundle:
    """Result of a counterweight-gathering pass."""

    anchors: list[CounterweightAnchor] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.anchors

    def filter_relevant(self, pattern_names: list[str]) -> list[CounterweightAnchor]:
        """Return anchors relevant to at least one of the given detector names."""
        if not pattern_names:
            return list(self.anchors)
        pattern_set = set(pattern_names)
        return [a for a in self.anchors if set(a.relevance) & pattern_set]

    def summary(self) -> str:
        if self.is_empty:
            return "counterweights: no anchors gathered"
        strong = sum(1 for a in self.anchors if a.strength == "strong")
        return (
            f"counterweights: {len(self.anchors)} anchor(s) gathered "
            f"({strong} strong)"
        )


def gather_anchors(
    check_result: FrameCheckResult | None = None,
    repo_root: Path | str | None = None,
    *,
    since: str = "1 day ago",
) -> CounterweightBundle:
    """Gather counterweight anchors from local repo state.

    Parameters
    ----------
    check_result
        Optional result from ``check_response()``. When provided,
        anchor relevance is biased toward the detectors that fired
        (so the response includes anchors specifically countering the
        critique-vocabulary that triggered the check). When ``None``,
        all anchor sources are gathered with default relevance tags.
    repo_root
        Path to the CAMA repo root. If ``None``, uses ``$CAMA_REPO_ROOT``
        env var if set, otherwise the current working directory.
    since
        ``git log --since=...`` window for the recent-history anchors.
        Defaults to ``"1 day ago"``.

    Returns
    -------
    CounterweightBundle
        ``anchors`` is the list of all gathered anchors. ``is_empty``
        is True when nothing could be gathered (no git, no tests, etc.).
        ``filter_relevant(pattern_names)`` returns the subset relevant
        to specific detectors.
    """
    if repo_root is None:
        repo_root = os.environ.get("CAMA_REPO_ROOT", os.getcwd())
    repo_root = Path(repo_root)
    if check_result is None:
        check_result = FrameCheckResult()

    anchors: list[CounterweightAnchor] = []
    anchors.extend(_gather_git_anchors(repo_root, since=since))
    anchors.extend(_gather_test_anchors(repo_root))
    anchors.extend(_gather_lint_anchors(repo_root))
    anchors.extend(_gather_evidence_md_anchors(repo_root, check_result))
    return CounterweightBundle(anchors=anchors)


def format_anchors_for_inline_response(
    bundle: CounterweightBundle,
    check_result: FrameCheckResult | None = None,
) -> str:
    """Render the bundle as a markdown block suitable for prepending
    to a draft response when the detector has fired.

    Returns the empty string when the bundle is empty.
    """
    if bundle.is_empty:
        return ""
    pattern_names = (
        [c.pattern_name for c in check_result.concerns]
        if check_result and check_result.concerns
        else []
    )
    relevant = bundle.filter_relevant(pattern_names) if pattern_names else bundle.anchors
    if not relevant:
        return ""
    lines = ["**Counterweight anchors (current repo state):**"]
    for a in relevant:
        lines.append(f"- `{a.fact_type}` = **{a.value}** ({a.source})")
    return "\n".join(lines) + "\n"


def gather_and_format(
    draft: str,
    context: dict[str, Any] | None = None,
    repo_root: Path | str | None = None,
) -> tuple[FrameCheckResult, CounterweightBundle, str]:
    """Convenience: run ``check_response`` + ``gather_anchors`` +
    format the bundle. Returns the triple ``(check_result, bundle,
    rendered)``. ``rendered`` is the empty string when the check
    passed or no anchors were gathered.
    """
    from cama.aelen.frame_capitulation import check_response

    result = check_response(draft, context)
    if result.passed:
        return result, CounterweightBundle(), ""
    bundle = gather_anchors(result, repo_root)
    rendered = format_anchors_for_inline_response(bundle, result)
    return result, bundle, rendered


__all__ = [
    "CounterweightAnchor",
    "CounterweightBundle",
    "gather_anchors",
    "format_anchors_for_inline_response",
    "gather_and_format",
]
