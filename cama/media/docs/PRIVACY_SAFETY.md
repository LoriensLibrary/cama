# PRIVACY AND SAFETY: CAMA Media Memory

This media touches minors, pets, homes, and locations. Privacy and safety are
requirements, not features. This document sits under the repo's existing
DATA_HANDLING.md, THREAT_MODEL.md, and SECURITY.md and adds media-specific rules.

## Hard rules (non-negotiable)

1. **Local-first.** Originals and derived memory stay on the owner's device by
   default. Nothing is uploaded to any third party without an explicit,
   per-action opt-in.
2. **No automatic facial identification of unknown people.** Face *regions* may
   be detected; *identity* comes only from explicit user labeling. No automatic
   clustering or naming of strangers. (ADR-0005.)
3. **Cloud vision is opt-in and off by default,** and is **never** used on assets
   flagged `is_minor` or high sensitivity. Sending identifiable people to cloud
   vision creates biometric-law obligations (notice, consent, deletion) for the
   operator; local processing avoids them. (ADR-0004.)
4. **Provenance and correction.** Every AI-derived claim is provisional, carries
   its source and confidence, and is user-correctable. Corrections outrank the
   model, always. (ADR-0003.)
5. **Right to forget is real.** Deleting an asset cascades to its observations,
   edges, and derived memories, and removes the preserved original and thumbnail.
   Delete means delete.
6. **No publishing on the user's behalf in the MVP.** Export only until the
   review workflow is trusted. (ADR-0007.)

## Minors

- Assets depicting children are flagged `is_minor` and forced to high
  sensitivity. High-sensitivity assets are excluded from any cloud call and from
  any bulk export unless individually and explicitly included.
- The module should make it easy to keep children out of location exposure:
  **strip GPS on export by default** (opt-in to retain), and never surface
  real-time location.
- Design intent: support the owner in posting selectively and after the fact,
  consistent with pediatric guidance on sharing images of one's own children.

## Location safety

- GPS is stored for clustering and recall but is **redacted on export by
  default**. Retaining location in an exported/published asset must be a
  deliberate choice.
- "On this day" and place clustering operate on stored data locally; they do not
  publish location.

## Consent and sensitivity

- Reuse CAMA sensitivity levels (low / medium / high). Media adds `is_minor` as a
  forcing condition for high.
- Other people's children who appear in content require the owner's explicit
  inclusion and, per legal guidance, a real-world release; the module should
  never auto-include or auto-publish them.

## AI content and honesty

- Track `ai_generated` on assets so AI-generated or AI-edited images can be
  disclosed at publish time per platform rules. (ADR-0008.)
- AI-written captions are plain text and are the assistant's draft; the human
  approves wording. The module never silently posts machine text as the owner's.

## What this module must never do

- Auto-identify or auto-tag unknown people.
- Upload media, especially minors, to any cloud service by default.
- Post to any platform without explicit human action (MVP).
- Overwrite a user's correction with a later model guess.
- Hard-delete originals as a side effect of any operation other than an explicit
  delete.

## Needs a human expert, not just this doc

Earnings handling for content featuring minors, business-entity and tax choices,
and model releases for other people's children are legal questions. They are
flagged here so the software does not pretend to resolve them; the product owner
should confirm them with a licensed attorney. This document is engineering
guidance, not legal advice.
