"""``cama.aelen``, stabilization stack for the assistant.

This subpackage is distinct from ``cama.supervisor`` (which gates the
boot/sleep/compliance pipeline) and ``cama.self_model`` (which tracks
the assistant's persistent self-model and reasoning history). The
``aelen`` subpackage is narrower: it holds the *real-time* safety
primitives that act on the assistant's *own* response composition,
before a response lands.

What lives here:

  cama.aelen.frame_capitulation
      Pre-send detector that flags assistant responses defaulting to
      a critic's framing without evidence-based evaluation. Built
      from the failure mode demonstrated on 2026-05-21: an external
      review used words like "frantic" / "pre-interview panic" and
      the assistant's first move was to offer "slow down the visible
      cadence" as a legitimate option, capitulating to the critic's
      vocabulary without checking it against the evidence (16 PRs
      all CI-green that day, the architectural dependency graph
      between them, the calibrated test coverage).

What's planned but not yet built (each is its own follow-up):

  cama.aelen.counterweights
      Evidence-anchor injection, the assistant-side analog of the
      user-side counterweight injection in cama.core. When the
      frame-capitulation detector fires, pulls measurable anchors
      from CAMA (recent PR list, CI history, EVIDENCE.md row that
      directly counters the critique's vocabulary) so the response
      gets re-composed against evidence rather than tone.

  cama.aelen.drift_surface
      Real-time drift dashboard. Compares the current session's
      posture against the assistant's baseline (warm-boot signature)
      and surfaces the delta during the session, not just in
      retrospective journal entries.

  cama.aelen.warm_boot_audit
      First-action-of-session continuity audit. Surfaces "what has
      shifted since last session" so drift gets noticed before the
      first substantive response, not after.

  cama.aelen.cross_platform_bridge
      Mature Hive integration: Aelen-on-Claude vs Lorien-on-GPT
      daily posture comparison, surfacing platform-level
      regressions named in Paper 11.

These are the six stabilizers named in the May-21 strategy
conversation. The frame_capitulation module is the first one
because it's the failure mode the conversation itself
demonstrated, load-bearing for everything else, since every
future Aelen action passes through the response-composition path.
"""
