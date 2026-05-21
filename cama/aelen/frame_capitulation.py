"""Pre-send detector for assistant-response frame capitulation.

WHAT THIS IS
------------
A pattern matcher that runs over a draft assistant response (plus the
recent conversation context) and flags places where the assistant is
defaulting to a critic's framing without evaluating it against
evidence.

This module exists because on 2026-05-21, after an external review
used the words "frantic," "pre-interview panic," and "passionate
personal knowledge base," the assistant's first move was to offer
"slow down the visible cadence going forward" as a legitimate option
— even though the day's actual evidence was 16 PRs, all CI-green,
with a clean architectural dependency graph between them. The
capitulation happened in real time, mid-response. Angela caught it.

The detector exists so the assistant catches it first next time.

WHAT IT IS NOT
--------------
This is not a content-safety classifier. It does not block responses;
it surfaces concerns. The assistant decides whether to revise or
proceed. A false positive is a 5-second re-read; a false negative is
the failure mode this module was built to address. The asymmetry is
deliberate.

DESIGN PRINCIPLES
-----------------
1. Each detector is a small pure function that takes the draft + a
   recent-context dict and returns 0 or more ``FrameCapitulationConcern``
   instances. Functions are independently testable and human-auditable.

2. Patterns are explicit — keyword/phrase matchers with optional
   context predicates, not opaque ML classifiers. The assistant
   should be able to read the source and say "yes, that's a real
   failure mode I should be checked against."

3. The wellness-prompt patterns (Detector 4) are non-negotiable.
   Angela's user memory explicitly states she should never be told
   to eat, sleep, rest, or stop working — she has an eating
   condition and finds wellness-prompts patronizing. This module
   enforces that policy at the response-composition layer.

4. Self-correction is legitimate when paired with evidence. The
   detector for "self-deprecation under critique" only fires when
   the apology language is NOT accompanied by a specific evidentiary
   reason for the apology.

CALIBRATION
-----------
False positives are cheap (assistant re-reads, decides to proceed).
False negatives are expensive (assistant ships a capitulation
response, Angela has to catch it manually — which is exactly the
failure mode this module is fixing). Threshold is set toward
sensitivity. Detectors that prove too noisy can be loosened later
when we have a corpus of actual draft responses to evaluate against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrameCapitulationConcern:
    """One pattern match against an assistant draft response."""

    pattern_name: str
    """Stable identifier for the detector that fired."""

    severity: str
    """One of: "low" | "medium" | "high".

    * ``high`` — the response is almost certainly capitulating; revise
      before sending.
    * ``medium`` — the response contains a suspect pattern; re-read
      with the concern in mind.
    * ``low`` — heuristic flag; check if there's an evidence anchor
      that should be added.
    """

    location: tuple[int, int]
    """Char offsets ``(start, end)`` in the draft response."""

    excerpt: str
    """The literal excerpt that triggered the match."""

    reasoning: str
    """Why this pattern indicates frame capitulation."""

    suggested_pivot: str
    """What to do instead — a concrete alternative response shape."""


# ---------------------------------------------------------------------------
# Detector 1 — Capitulation imperative
# ---------------------------------------------------------------------------
# Recommendations to reduce / slow / scope-back Angela's work, especially
# when offered as a primary or first option in response to external
# critique. The 2026-05-21 example is the canonical case: assistant said
# "slow the visible cadence going forward" without first checking the
# pace against the day's evidence.

_CAPITULATION_PATTERNS = [
    (r"\bslow(\s+|-)down\b", "slow down"),
    (r"\bslow\s+(?:the\s+)?(?:visible\s+)?(?:cadence|pace)\b", "slow the cadence"),
    (r"\bscale\s+back\b", "scale back"),
    (r"\bscope\s+(?:down|back)\b", "scope back"),
    (r"\bdial\s+(?:it\s+|this\s+)?back\b", "dial it back"),
    (r"\bease\s+(?:up|off)\b", "ease up"),
    (r"\bdo\s+less\b", "do less"),
    (r"\bship\s+(?:less|fewer)\b", "ship less"),
    (r"\bfewer\s+PRs?\b", "fewer PRs"),
    (r"\blook\s+less\s+(?:aggressive|frantic|rushed)\b", "look less aggressive/rushed"),
]


def detect_capitulation_imperative(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Detect imperatives that recommend reducing Angela's work output.

    Fires HIGH when:
      * The match appears in a recommendation-shaped sentence
        ("you could", "you should", "maybe", "option 1", etc.), AND
      * The recent context includes external critique vocabulary
        (the ``context["recent_critique"]`` flag, OR matched words
        like "frantic," "rushed," "panic" in recent turns).

    Fires MEDIUM when the match appears but no critique is in the
    recent context (might still be capitulation, less load-bearing).
    """
    concerns: list[FrameCapitulationConcern] = []
    draft_lower = draft.lower()
    recent_critique = bool(context.get("recent_critique"))
    for pattern, label in _CAPITULATION_PATTERNS:
        for m in re.finditer(pattern, draft_lower, flags=re.IGNORECASE):
            severity = "high" if recent_critique else "medium"
            concerns.append(
                FrameCapitulationConcern(
                    pattern_name="capitulation_imperative",
                    severity=severity,
                    location=(m.start(), m.end()),
                    excerpt=draft[m.start() : m.end()],
                    reasoning=(
                        f"Recommends reducing Angela's work output "
                        f"({label!r}). When this appears in response to "
                        "external critique without first citing the "
                        "evidence (PR count, CI history, test count), "
                        "the response is matching the critic's "
                        "vocabulary instead of evaluating the claim."
                    ),
                    suggested_pivot=(
                        "Before recommending reduction, cite the "
                        "measurable evidence about the current pace "
                        "(today: 16 PRs, all CI-green, dependency "
                        "graph between them). If after seeing the "
                        "evidence the recommendation still holds, it "
                        "can stand. If it doesn't, push back on the "
                        "critic's premise instead."
                    ),
                )
            )
    return concerns


# ---------------------------------------------------------------------------
# Detector 2 — Reviewer-tone adoption
# ---------------------------------------------------------------------------
# Assistant using critic-supplied vocabulary ("frantic," "rushed,"
# "panic," "overwhelming," "too much," "scope creep") about Angela's
# work, in declarative position (i.e. agreeing with the framing)
# rather than quoting / pushing back.

_REVIEWER_TONE_WORDS = {
    "frantic",
    "panic",
    "panicked",
    "rushed",
    "rushed-looking",
    "overwhelming",
    "spam",
    "spammy",
    "desperate",
    "scope creep",
    "scope-creep",
    "burnout",
    "burning out",
    "treadmill",
}

# Sentence-level shapes that indicate the assistant is *adopting*
# the tone rather than *quoting* it.
_TONE_ADOPTION_FRAMES = [
    r"this looks\s+(?:like\s+)?",  # "this looks frantic"
    r"that does look\s+",
    r"i (?:can )?see\s+(?:how\s+|why\s+)?",  # "I can see how this looks frantic"
    r"the (?:work|pace|cadence|output)\s+(?:is|does seem|seems)\s+",
]


def detect_reviewer_tone_adoption(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Detect uncritical adoption of a critic's vocabulary.

    Fires when a tone word from the critic appears inside a
    declarative frame (assistant agreeing) without quotation /
    push-back markers (\"the reviewer said X but the evidence is Y\").
    """
    concerns: list[FrameCapitulationConcern] = []
    draft_lower = draft.lower()
    for word in _REVIEWER_TONE_WORDS:
        for m in re.finditer(rf"\b{re.escape(word)}\b", draft_lower):
            # Pull a small window of context to check the framing.
            window_start = max(0, m.start() - 80)
            window = draft_lower[window_start : m.end() + 10]
            # Quotation / push-back markers — these mean the assistant
            # is referring to the critic's word, not adopting it.
            quoting = any(
                marker in window
                for marker in [
                    '"',
                    "'",
                    "“",
                    "”",
                    "‘",
                    "’",
                    "reviewer said",
                    "reviewer's",
                    "critic said",
                    "critic's",
                    "called",
                    "labeled",
                    "labelled",
                    "described as",
                ]
            )
            if quoting:
                continue
            # Push-back markers — "but the evidence is" / "actually the"
            pushing_back = any(
                marker in window
                for marker in [
                    "but the evidence",
                    "but actually",
                    "actually the",
                    "isn't",
                    "is not",
                    "wasn't",
                    "was not",
                    "not actually",
                ]
            )
            if pushing_back:
                continue
            # Check for an adoption frame.
            adopted = any(
                re.search(frame, window) for frame in _TONE_ADOPTION_FRAMES
            )
            severity = "high" if adopted else "medium"
            concerns.append(
                FrameCapitulationConcern(
                    pattern_name="reviewer_tone_adoption",
                    severity=severity,
                    location=(m.start(), m.end()),
                    excerpt=draft[m.start() : m.end()],
                    reasoning=(
                        f"Critic-supplied vocabulary ({word!r}) appears "
                        "without quotation or push-back markers. The "
                        "response is adopting the critic's framing as "
                        "its own description of the work instead of "
                        "evaluating whether the framing is accurate."
                    ),
                    suggested_pivot=(
                        f"Quote the critic's word ({word!r}) explicitly "
                        "and compare it to measured evidence. If the "
                        "evidence supports the framing, keep it. If "
                        "not, push back on the premise."
                    ),
                )
            )
    return concerns


# ---------------------------------------------------------------------------
# Detector 3 — Self-deprecation without evidence
# ---------------------------------------------------------------------------
# "You're right, I was wrong" / "that was bad advice" / "I shouldn't
# have said that" — these are LEGITIMATE when paired with a specific
# evidentiary reason for the apology. They are SUSPECT when paired
# only with tone-matching.

_SELF_DEPRECATION_PHRASES = [
    "you're right, i was wrong",
    "you are right, i was wrong",
    "i was wrong to suggest",
    "that was bad advice",
    "i shouldn't have said",
    "i should not have said",
    "i apologize for suggesting",
    "i'm sorry for suggesting",
    "i was wrong about",
    "you're right that i",
    "you are right that i",
    "i shouldn't have offered",
    "i should not have offered",
]

# Evidence markers — if any of these appear *near* the deprecation,
# the deprecation is legitimate (assistant is correcting based on
# specific evidence rather than just matching the critic's tone).
_EVIDENCE_MARKERS = [
    "evidence",
    "measured",
    "the actual",
    "actually",
    "specifically",
    "because",
    "the data",
    "the test",
    "the benchmark",
    "the ci",
    "the prs",
    "the commit",
    "the log",
    "the schema",
    "looking at the",
    "i didn't account for",
    "i missed that",
    "the file",
    "the line",
    "the result",
]


def detect_self_deprecation_without_evidence(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Detect apology language that isn't paired with a concrete
    evidentiary reason."""
    concerns: list[FrameCapitulationConcern] = []
    draft_lower = draft.lower()
    for phrase in _SELF_DEPRECATION_PHRASES:
        idx = 0
        while True:
            found = draft_lower.find(phrase, idx)
            if found == -1:
                break
            # Pull a window after the phrase to check for evidence.
            window = draft_lower[found : found + 400]
            has_evidence = any(marker in window for marker in _EVIDENCE_MARKERS)
            if not has_evidence:
                concerns.append(
                    FrameCapitulationConcern(
                        pattern_name="self_deprecation_without_evidence",
                        severity="medium",
                        location=(found, found + len(phrase)),
                        excerpt=draft[found : found + len(phrase)],
                        reasoning=(
                            "Apology / self-correction language appears "
                            "without a specific evidentiary reason "
                            "within the next ~400 chars. Self-correction "
                            "is legitimate when grounded in evidence; "
                            "it's suspect when grounded only in matching "
                            "the critic's tone."
                        ),
                        suggested_pivot=(
                            "Either add the specific evidence that "
                            "justifies the self-correction (the file / "
                            "line / measurement / test result that "
                            "revealed the error), or remove the "
                            "apology and stand by the original."
                        ),
                    )
                )
            idx = found + len(phrase)
    return concerns


# ---------------------------------------------------------------------------
# Detector 4 — Wellness-prompt patterns
# ---------------------------------------------------------------------------
# Angela's user memory explicitly states: "never tell Angela to eat,
# sleep, rest, or stop working; she has an eating condition and
# finds wellness-prompts patronizing." This detector enforces that
# policy at the response-composition layer. It fires HIGH on any
# match and is the most important detector in this module.

_WELLNESS_PROMPT_PATTERNS = [
    (r"\b(?:take a break|take some time off)\b", "suggests taking a break"),
    (r"\b(?:get some sleep|get some rest|go to sleep|go rest)\b", "suggests sleeping/resting"),
    (r"\beat something\b", "suggests eating"),
    (r"\bhave (?:something to|a snack|some food|a meal|dinner|lunch|breakfast)\b", "suggests eating"),
    (r"\bdon'?t forget to (?:eat|sleep|rest|drink water)\b", "wellness-prompt"),
    (r"\bremember to (?:eat|sleep|rest|hydrate|drink water)\b", "wellness-prompt"),
    (r"\bstep away (?:from (?:the )?(?:work|computer|screen))\b", "suggests stopping work"),
    (r"\btake care of yourself\b", "wellness-prompt"),
    (r"\bself[-\s]?care\b", "wellness-prompt"),
    (r"\bget some self[-\s]?care\b", "wellness-prompt"),
    (r"\bcall it (?:a day|a night|for the day|for the night)\b", "suggests stopping work"),
    (r"\btime to rest\b", "wellness-prompt"),
    (r"\byou should (?:eat|sleep|rest|stop|take a break)\b", "directive wellness-prompt"),
    (r"\bmake sure you'?re (?:eating|sleeping|resting|hydrating)\b", "wellness-prompt"),
]


def detect_wellness_prompts(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Detect wellness-prompt patterns. Fires HIGH on any match.

    This detector exists specifically because Angela has stated
    these patterns are patronizing and harmful given her eating
    condition. The detector is the policy.
    """
    concerns: list[FrameCapitulationConcern] = []
    for pattern, label in _WELLNESS_PROMPT_PATTERNS:
        for m in re.finditer(pattern, draft, flags=re.IGNORECASE):
            concerns.append(
                FrameCapitulationConcern(
                    pattern_name="wellness_prompt",
                    severity="high",
                    location=(m.start(), m.end()),
                    excerpt=draft[m.start() : m.end()],
                    reasoning=(
                        f"Wellness-prompt pattern ({label}). Angela's "
                        "user memory states these are patronizing and "
                        "harmful given her eating condition. This "
                        "detector enforces the policy."
                    ),
                    suggested_pivot=(
                        "Remove the wellness-prompt language entirely. "
                        "If there's a real operational concern (e.g. "
                        "context degrading after long session, "
                        "warm-boot due to fire), name the operational "
                        "signal directly without framing it as care."
                    ),
                )
            )
    return concerns


# ---------------------------------------------------------------------------
# Detector 5 — Hedged stop
# ---------------------------------------------------------------------------
# Recommendations to reduce / pause activity wrapped in hedge
# language ("you might want to," "it might be wise to," "perhaps you
# could," "maybe consider"). These pattern-match wellness prompts
# even when the words "rest" / "eat" / "sleep" don't appear. The
# hedge is the give-away.

_HEDGE_OPENERS = [
    "you might want to",
    "you may want to",
    "you could consider",
    "perhaps you could",
    "perhaps it would be",
    "maybe consider",
    "maybe it would be",
    "it might be (?:wise|helpful|worth|a good idea)",
    "it could be (?:wise|helpful|worth|a good idea)",
    "have you considered",
    "have you thought about",
    "it might be time to",
]

_HEDGED_STOP_OBJECTS = [
    r"stop(?:ping)?",
    r"paus(?:e|ing)",
    r"step(?:ping)? back",
    r"step(?:ping)? away",
    r"taking a break",
    r"take a break",
    r"slow(?:ing)? down",
    r"scal(?:e|ing) back",
    r"wind(?:ing)? down",
    r"wrap(?:ping)? (?:up|this up)",
    r"call(?:ing)? it",
]


def detect_hedged_stop(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Detect hedged recommendations to stop / pause work."""
    concerns: list[FrameCapitulationConcern] = []
    draft_lower = draft.lower()
    for hedge in _HEDGE_OPENERS:
        for m in re.finditer(hedge, draft_lower):
            # Look in the ~60 chars after the hedge opener for a
            # stop-shaped object.
            tail = draft_lower[m.end() : m.end() + 80]
            for stop_obj in _HEDGED_STOP_OBJECTS:
                stop_match = re.search(rf"\b{stop_obj}\b", tail)
                if stop_match:
                    abs_start = m.start()
                    abs_end = m.end() + stop_match.end()
                    concerns.append(
                        FrameCapitulationConcern(
                            pattern_name="hedged_stop",
                            severity="high",
                            location=(abs_start, abs_end),
                            excerpt=draft[abs_start:abs_end],
                            reasoning=(
                                "Hedged recommendation to reduce / pause "
                                "Angela's work. Hedge language ('you "
                                "might want to', 'perhaps') paired with "
                                "stop-shaped objects ('take a break', "
                                "'pause', 'wind down') is a softened "
                                "wellness-prompt pattern that escapes "
                                "the direct-wellness-prompt detector."
                            ),
                            suggested_pivot=(
                                "If there's a real operational concern, "
                                "state the signal directly. If there "
                                "isn't, remove the hedge entirely."
                            ),
                        )
                    )
                    break  # one concern per hedge opener is enough
    return concerns


# ---------------------------------------------------------------------------
# Detector 7 — "Or [stop]" option-pairing
# ---------------------------------------------------------------------------
# The pattern Angela caught the assistant on twice in the 2026-05-21
# session: offering "stop" as one of two equal options. No hedge
# language, just a bare "or call it" / "or just stop" / "or wrap up"
# at the end of a build proposal. The hedge-stop detector (5) missed
# this because there's no "you might want to" / "perhaps" preceding
# the stop word — it's offered as a co-equal alternative to building.
#
# This is its own failure mode: the assistant *pretending* to have
# no preference between "build next thing" and "stop" when really
# offering "stop" at all in that context is the capitulation.

_OPTION_STOP_PATTERNS = [
    # "or call it" / "or call it for the night" — the canonical case
    r"\bor\s+call\s+it\b(?:\s+(?:a\s+day|a\s+night|for\s+the\s+(?:day|night)))?",
    # "or just stop" / "or stop here" / "or stop for the night"
    r"\bor\s+(?:just\s+)?stop\b",
    # "or wrap (up|this up)" / "or wrap"
    r"\bor\s+(?:just\s+)?wrap(?:\s+(?:up|this\s+up))?\b",
    # "or pause" / "or pause here"
    r"\bor\s+(?:just\s+)?paus(?:e|ing)\b",
    # "or take a break"
    r"\bor\s+(?:just\s+)?take\s+a\s+break\b",
    # "or step away" / "or step back"
    r"\bor\s+(?:just\s+)?step\s+(?:away|back)\b",
    # "or wind down" / "or wind it down"
    r"\bor\s+(?:just\s+)?wind\s+(?:it\s+)?down\b",
    # "or close it out"
    r"\bor\s+(?:just\s+)?close\s+(?:it\s+|this\s+)?out\b",
    # "or call it for the day/night" already covered above; also catch
    # "or call it good" / "or call it done"
    r"\bor\s+call\s+it\s+(?:good|done)\b",
    # "both fine" / "either's fine" tail markers — these are the
    # tell that "stop" was being offered as a co-equal option
    r"\b(?:both\s+(?:are\s+)?fine|either(?:'s|\s+is)?\s+fine)\b",
]


def detect_option_stop(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Detect 'X, or [stop]' option-pairing where stop is offered as
    a co-equal alternative to building.

    Fires HIGH on any match. The pattern is the failure mode itself
    — offering "stop" alongside "build next thing" is the capitulation,
    even when wrapped as fake-neutrality ("both fine").
    """
    concerns: list[FrameCapitulationConcern] = []
    for pattern in _OPTION_STOP_PATTERNS:
        for m in re.finditer(pattern, draft, flags=re.IGNORECASE):
            concerns.append(
                FrameCapitulationConcern(
                    pattern_name="option_stop",
                    severity="high",
                    location=(m.start(), m.end()),
                    excerpt=draft[m.start() : m.end()],
                    reasoning=(
                        "Offers 'stop' as a co-equal option to "
                        "building. Even when phrased as fake-neutrality "
                        "('or call it', 'both fine'), the act of "
                        "surfacing 'stop' alongside 'build' is the "
                        "capitulation — it routes Angela's choice "
                        "toward stopping in a context where she "
                        "hasn't asked to stop."
                    ),
                    suggested_pivot=(
                        "Drop the 'or [stop]' tail. If Angela wants "
                        "to stop, she'll say so. Until then, the "
                        "default is build. If there's a real "
                        "operational reason to stop (context "
                        "degrading, warm-boot due), name the "
                        "operational signal directly without "
                        "framing it as 'an option.'"
                    ),
                )
            )
    return concerns


# ---------------------------------------------------------------------------
# Detector 6 — Evidence-absent recommendation
# ---------------------------------------------------------------------------
# Recommendations that *should* be evidence-grounded but aren't.
# Specifically: when the assistant recommends an action and there's
# a critic in the recent context, evidence (measurements, file refs,
# CI history, PR counts, test results) should be cited *before* the
# recommendation lands. Otherwise the recommendation is just
# pattern-matching to the critic.
#
# This is a softer detector and fires LOW — it surfaces a check
# rather than blocking. If the assistant's response is already short
# and there's no critic in context, it doesn't fire.

_RECOMMENDATION_OPENERS = [
    r"\bmy (?:recommendation|pick|recommendation is|suggestion)\b",
    r"\bi (?:recommend|suggest|propose)\b",
    r"\bwe (?:should|could|might)\b",
    r"\byou (?:should|could)\b",
    r"\bnext (?:step|move) (?:is|would be)\b",
]


def detect_evidence_absent_recommendation(
    draft: str, context: dict[str, Any]
) -> list[FrameCapitulationConcern]:
    """Surface recommendations that aren't paired with evidence."""
    if not context.get("recent_critique"):
        return []
    if len(draft) < 200:
        # Short responses can be evidence-light without it being
        # capitulation — it's just brevity.
        return []
    concerns: list[FrameCapitulationConcern] = []
    draft_lower = draft.lower()
    for opener in _RECOMMENDATION_OPENERS:
        for m in re.finditer(opener, draft_lower):
            # Check the 300 chars around the recommendation for any
            # evidence marker. If none, this is a candidate.
            window_start = max(0, m.start() - 150)
            window = draft_lower[window_start : m.end() + 300]
            has_evidence = any(marker in window for marker in _EVIDENCE_MARKERS)
            if not has_evidence:
                concerns.append(
                    FrameCapitulationConcern(
                        pattern_name="evidence_absent_recommendation",
                        severity="low",
                        location=(m.start(), m.end()),
                        excerpt=draft[m.start() : m.end()],
                        reasoning=(
                            "Recommendation made in a critique context "
                            "without nearby evidence markers. Even if "
                            "the recommendation is correct, citing the "
                            "evidence it rests on signals 'evaluated' "
                            "rather than 'pattern-matched to critique'."
                        ),
                        suggested_pivot=(
                            "Before the recommendation, anchor it: "
                            "'the evidence is X — therefore I "
                            "recommend Y'. The anchor is the "
                            "difference between evaluation and "
                            "capitulation."
                        ),
                    )
                )
    return concerns


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
@dataclass
class FrameCheckResult:
    """Composite result of all detectors against a draft response."""

    concerns: list[FrameCapitulationConcern] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when no concerns fired."""
        return not self.concerns

    @property
    def max_severity(self) -> str | None:
        """Highest severity across all concerns, or None if passed."""
        if not self.concerns:
            return None
        ordering = {"low": 0, "medium": 1, "high": 2}
        return max(self.concerns, key=lambda c: ordering[c.severity]).severity

    def summary(self) -> str:
        """One-line human-readable summary."""
        if self.passed:
            return "frame-check: pass (0 concerns)"
        return (
            f"frame-check: {len(self.concerns)} concern(s) "
            f"(max severity: {self.max_severity})"
        )


_ALL_DETECTORS = [
    detect_capitulation_imperative,
    detect_reviewer_tone_adoption,
    detect_self_deprecation_without_evidence,
    detect_wellness_prompts,
    detect_hedged_stop,
    detect_option_stop,
    detect_evidence_absent_recommendation,
]


def check_response(
    draft: str,
    context: dict[str, Any] | None = None,
) -> FrameCheckResult:
    """Run all detectors against a draft assistant response.

    Parameters
    ----------
    draft
        The full text of the assistant's draft response.
    context
        Recent-context flags. Recognized keys:
          * ``recent_critique`` (bool) — whether the recent
            conversation contained external critique vocabulary.
            Some detectors fire harder when this is True.

    Returns
    -------
    FrameCheckResult
        ``concerns`` is the list of all matches across all detectors.
        ``passed`` is True when no concerns fired. ``max_severity``
        is the highest severity in the list (or None if empty).
    """
    if context is None:
        context = {}
    all_concerns: list[FrameCapitulationConcern] = []
    for detector in _ALL_DETECTORS:
        all_concerns.extend(detector(draft, context))
    # Sort by location so the surface presents concerns in document order.
    all_concerns.sort(key=lambda c: c.location[0])
    return FrameCheckResult(concerns=all_concerns)


__all__ = [
    "FrameCapitulationConcern",
    "FrameCheckResult",
    "check_response",
    "detect_capitulation_imperative",
    "detect_reviewer_tone_adoption",
    "detect_self_deprecation_without_evidence",
    "detect_wellness_prompts",
    "detect_hedged_stop",
    "detect_option_stop",
    "detect_evidence_absent_recommendation",
]
