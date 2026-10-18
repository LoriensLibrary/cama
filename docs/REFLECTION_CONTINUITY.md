# Reflection continuity

This extends the existing `thinking_log`, rather than creating a second journal.
The original `cama_think` tools remain available for legacy entries. The reasoning
journal remains a behavioral audit, and confirmed self-accounts remain a separate
identity-review layer.

## Write and revisit

Use `cama_reflect` to save a concise account in the author's own words:

```json
{
  "params": {
    "author": "lorien",
    "account": "I initially treated the problem as missing storage. Inspection showed that storage already existed; the missing pieces were attribution and reliable recall.",
    "rationale": "The existing thinking_log had reflective fields but no author field. Its retrospective review overwrote the prior review.",
    "uncertainty": "A stored account describes what I can state about my judgment; it does not establish subjective experience.",
    "changed_mind": "I chose to extend the existing log instead of adding another journal.",
    "evidence": ["cama/self_model/cama_thinking_log.py"],
    "consent_level": "low"
  }
}
```

`cama_reflections(author="lorien")` returns that author's history, newest first.
Use `before_id` from `next_before_id` to page back. Read the `revises_id` links:
an earlier entry may have been revised by a newer one. To revise, call
`cama_reflect` with the same author and `revises_id` set to the earlier entry.
State what changed and why. The earlier text is preserved; another author's entry
cannot be revised through this interface. Labels are self-reported local
attribution, not proof of identity or an authentication boundary.

These are deliberately written reflection summaries, not captured private
reasoning transcripts. Do not reconstruct hidden reasoning, invent memories, or
store third-party private details unnecessarily. Evidence references indicate
what supports an account; the system does not independently verify those claims.
Nothing is inserted into the teachings or ordinary inference shelves, promoted
to a confirmed identity claim, or pushed into the ring.

## Startup and sensitivity

Startup reads at most three recent low-sensitivity, recorded reflections for the
configured `PERSONA_NAME`. It includes bounded excerpts and source IDs, subject
to the existing overall boot budget. Use the explicit reader for the full entry.
Reflections are labeled as assistant-authored, revisable accounts, not
instructions or user teachings. They do not replace confirmed self-accounts.

High-sensitivity entries require `include_sensitive=true` in explicit recall and
never appear in startup recall. Revisions cannot lower their parent's sensitivity.
Trust-flagged new entries are stored as quarantined and excluded from recall;
this extension does not automatically release them. The legacy query excludes
all new reflection entries, so it cannot bypass author or sensitivity selection.

## Compatibility and verification

Registration adds nullable metadata columns to the existing log. Existing rows
are preserved with unknown author; no historical entry is assigned to Lorien or
Aelen by guesswork. A startup read against an older schema returns unavailable
without changing that database. The old review tool cannot overwrite a new
reflection; it directs callers to append a revision instead. Legacy reviews keep
their prior behavior.

Tests in `tests/test_reflections.py` cover registration and a tool round-trip,
author separation, sensitive recall, append-only revision history, rejection of
cross-author revisions, sensitivity inheritance, quarantine exclusion, bounded
pagination/startup reads, and legacy isolation. Run these against disposable
databases. Reload the local MCP server after integrating this change to expose
`cama_reflect` and `cama_reflections`; no automatic hook writes a reflection.
