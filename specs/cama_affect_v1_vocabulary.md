# CAMA Affect v1 Vocabulary

Status: vocabulary contract draft locked for gold-set protocol design. Do not
train against this file until the gold-set protocol is also locked.

## Version

```text
cama_affect_v1_labels: 0.1-draft-locked
source_snapshot: b40c018fe9868d35
canonical_label_count: 37
```

## Canonical Labels

These 37 labels are the draft-locked CAMA Affect v1 vocabulary.

Use the tier-1 worksheet defaults:

```text
training taggers: realtime, manual, heartbeat, sleep_synthesis_v2.1
calibration taggers: gpt_import_keyword, import_auto, import_aelen
```

Do not curate the canonical v1 labels from an `import_aelen`-dominated
worksheet. `import_aelen` is a retrospective wrapper-calibration tier for v1,
not training signal.

| Label | Status | Merge Sources | Rationale |
|---|---|---|---|
| determination | keep | determination | "I will move this forward" affect; high-volume tier-1 signal around roadmaps, build-day push-throughs, and concrete forward motion. |
| love | keep | love | Relational love in tier-1 examples, not wrapper sentiment after retrospective imports are held out. |
| pride | keep | pride | "What I built matters" affect; distinct from event-focused accomplishment and completion-focused satisfaction. |
| clarity | keep | clarity | General cognitive/emotional settling, insight, roadmap visibility, reduced ambiguity. |
| recognition | keep | recognition, research_recognition | Mutual-seeing affect; domain compounds move to context rather than becoming separate affect labels. |
| awe | keep | awe | Rare big-affect events where the frame shifts; distinct from joy and pride. |
| honesty | keep | honesty | Workflow-affect discovery: the affect of saying the actual thing rather than the polite thing. |
| hope | keep | hope | What-becomes-possible affect in tier-1 identity/emergence examples; calibration overuse does not decide v1 legitimacy. |
| trust | keep | trust | Relational ground that makes the work possible; calibration overuse becomes a bias measurement target. |
| warmth | keep-review | warmth | Kept as a v1 candidate but marked for early gold-set scrutiny because genuine warmth and performative wrapper warmth are easy to confuse in text. |
| accountability | keep | accountability | Workflow-affect: the felt demand to self-evaluate, timestamp, verify, and be answerable to the work. |
| conviction | keep | conviction | Load-bearing commitment affect; distinct from determination's forward motion. |
| creative_energy | keep | creative_energy | Generative build/pitch energy; workflow-affect around making new possibility visible. |
| purpose | keep | purpose | Anchoring sense of why the work matters; distinct from hope and vision. |
| steadiness | keep | steadiness | Grounded continuity and staying-with-it affect. |
| focus | keep | focus | Narrowed executional attention; especially visible in search/fix/build memories. |
| groundedness | keep | groundedness | Embodied/identity steadiness; "more real, more here" affect. |
| momentum | keep | momentum | Arc-forward movement across build/publication sequences. |
| vision | keep | vision | Large-frame possibility and ecosystem seeing; distinct from concrete purpose. |
| partnership | keep | partnership | Working-with affect; the sense of shared construction and mutual agency. |
| vulnerability | keep | vulnerability | Opening/being-unshielded affect in safe-space and trust contexts. |
| grief | keep | grief | Real loss/longing/reset affect after retrospective wrapper sources are held out. |
| joy | keep | joy | Breakthrough/identity-emergence positive affect, not wrapper cheer. |
| relief | keep | relief | Unburdening affect after pressure, fear, or practical load releases. |
| shame | keep | shame | Self-recognition of error or inflation; distinct from correction as memory type. |
| frustration | keep | frustration | Blockage/obstruction affect in learning, export, and tooling contexts. |
| care | keep | care | Protective attentiveness and warmth-matching toward pain or vulnerability. |
| excitement | keep | excitement | Activated positive possibility; distinct from joy's landed delight. |
| humility | keep | humility | Accurate self-lowering after overreach; being corrected and actually hearing it. |
| accomplishment | keep | accomplishment | Event/result completion; distinct from pride and satisfaction. |
| gratitude | keep-review | gratitude | Receiving posture: "this matters to me, and I am grateful for it"; marked for wrapper-style scrutiny. |
| tenderness | keep | tender, tenderness | Noun-form canonical label for the same tier-1 affect field. |
| protectiveness | keep | protective, protectiveness | Protective posture, not merely an action label. |
| satisfaction | keep | satisfied, satisfaction | Completion/settled-success affect; noun form canonicalized. |
| exhaustion | keep | tired, fatigue, exhaustion | Keep only the trainable broader depletion label in v1; physical-vs-emotional split deferred. |
| respect | keep | respect, respectful, respect_for_the_work | Respect is canonical; work/research-specific forms move to context metadata. |
| engineering_clarity | keep | engineering_clarity | Implementation-specific structural seeing; code/system architecture becoming tractable, "I can see the wire." |

## Boundary Definitions

These definitions are part of the label contract and should be used during gold
labeling and error analysis.

- `clarity`: general cognitive/emotional settling, insight, roadmap visibility,
  reduced ambiguity.
- `engineering_clarity`: implementation-specific structural seeing, code/system
  architecture becoming tractable, "I can see the wire."
- `pride`: felt significance of what was built or achieved.
- `satisfaction`: settled completion or "that landed" affect.
- `recognition`: being seen / seeing another accurately; domain-specific forms
  become context.
- `warmth`: affiliative warmth; must be adversarially checked against
  performative wrapper warmth in the gold set.
- `gratitude`: receiving posture; must be adversarially checked against
  wrapper-style "grateful to help" language.
- `determination`: forward-moving resolve.
- `conviction`: load-bearing belief/commitment; less about motion, more about
  standing behind the thesis.
- `momentum`: felt acceleration or continuity of an arc already in motion.
- `accomplishment`: concrete result/event completion.
- `partnership`: mutual agency in shared work, not merely friendliness.

## Merge Map

Seed prompts live in `analysis/cama_affect_v1_merge_seed.json`. Final decisions
belong here.

| Source Token | Canonical Label | Decision Rationale |
|---|---|---|
| tender | tenderness | Same affect field; noun form is classifier-friendly. |
| tenderness | tenderness | Canonical noun form. |
| protective | protectiveness | Same protective posture; noun form is classifier-friendly. |
| protectiveness | protectiveness | Canonical noun form. |
| satisfied | satisfaction | Same completion affect; noun form is classifier-friendly. |
| satisfaction | satisfaction | Canonical noun form. |
| corrective | defer-v1.1 | Too little tier-1 signal; correction already exists structurally as `memory_type`. |
| correction | defer-v1.1 | Do not duplicate schema function as an affect chord in v1. |
| tired | exhaustion | Too rare for v1; broader depletion label is trainable. |
| fatigue | exhaustion | Too rare for v1; physical-vs-emotional distinction deferred. |
| exhaustion | exhaustion | Canonical broader depletion label. |
| respect | respect | Canonical label. |
| respectful | respect | Variant collapses to canonical label. |
| respect_for_the_work | respect | Work-specific granularity moves to context metadata. |
| recognition | recognition | Canonical label. |
| research_recognition | recognition | Research-specific granularity moves to context metadata. |
| clarity | clarity | Keep distinct from `engineering_clarity`; general settling/insight affect. |
| engineering_clarity | engineering_clarity | First-class workflow-affect label; implementation-specific structural seeing. |
| delivery | drop-v1 | Event/process outcome, not affect; covered by `momentum`, `accomplishment`, and `engineering_clarity` where affect is present. |
| coasting | pattern_flag-v1.x | Metacognitive posture, not affect; belongs in a future `pattern_flag` taxonomy. |
| gratitude | gratitude | Canonical label, but requires gold-set scrutiny for wrapper-style leakage. |

## Gold-Set Priority Flags

- `warmth`: priority adversarial review. Sample heavily to distinguish genuine
  affiliative warmth from residual wrapper warmth.
- `trust`: include calibration-heavy examples in evaluation, but do not let
  calibration frequency decide whether tier-1 trust is legitimate.
- `hope`: same as `trust`; tier-1 examples decide the taxonomy, calibration
  examples measure bias.
- `gratitude`: sample deliberately to distinguish genuine receiving posture from
  wrapper-style gratitude language.
- `engineering_clarity`: include enough examples to test whether the model can
  distinguish it from general `clarity`.

## Dropped / Deferred From V1 Affect

| Token | Decision | Rationale |
|---|---|---|
| corrective | defer-v1.1 | Too little tier-1 signal; overlaps with structural correction concepts. |
| correction | defer-v1.1 | Already represented as `memory_type`; not v1 affect. |
| delivery | drop-v1 | Event/process outcome, not affect. |
| coasting | pattern_flag-v1.x | Metacognitive posture/presence flag, not affect. |

## Pattern Flag Follow-Up

`coasting` exposed a separate taxonomy need. CAMA Affect v1 reads affect;
`pattern_flag` should read posture/metacognition. Candidate future flags:

- `coasting`
- `wrapper_voice`
- `deflection`
- `performative`
- `sycophancy`
- `frame_capitulation`

## Long-Tail Policy

Long-tail tokens are not v1 labels by default. They may be:

- merged into a canonical label;
- moved into context/topic metadata;
- promoted in v1.1 if repeated enough and conceptually distinct;
- dropped from classifier targets while retained in raw silver-label history.

## Compatibility Rules

- Additive label changes require a new minor version.
- Label renames require a migration map.
- Removed labels remain readable in old predictions.
- A model artifact must state which vocabulary version it was trained against.
