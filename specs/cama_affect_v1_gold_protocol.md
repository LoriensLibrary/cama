# CAMA Affect v1 Gold-Set Protocol

Status: locked for first-pass sampler implementation. Modeling does not start
until a first-pass gold set has been labeled and the reliability report is
reviewed.

## Purpose

The gold set is the human-reviewed reference layer for CAMA Affect v1. It is
not a random sample and it is not a disguised training set. It exists to test
whether the vocabulary contract can be applied consistently, catch wrapper
leakage, expose ambiguous labels, and create meaningful evaluation metrics.

## First Pass

- Sample size: 100 memories.
- Source mix: 75 tier-1 rows, 25 calibration rows.
- Scope: pilot gold set. It is allowed to reveal protocol flaws.
- Time budget: 60-90 seconds per memory. Mark contested cases and move on.

The first pass should not claim every v1 label is fully validated. The hard
coverage gates apply after expansion to the 300-500 row gold set.

## Source Definitions

Tier-1 rows:

```sql
tagger_model IN ('realtime', 'manual', 'heartbeat', 'sleep_synthesis_v2.1')
AND is_latest_for_memory = 1
AND memory_status = 'durable'
```

Calibration rows:

```sql
tagger_model IN ('gpt_import_keyword', 'import_auto', 'import_aelen')
AND is_latest_for_memory = 1
AND memory_status != 'rejected'
```

Calibration rows are not training data. They exist to measure historical and
retrospective wrapper/import bias.

## Stratification

The sampler should deliberately prefer:

- all canonical labels where available;
- priority scrutiny labels: `warmth`, `gratitude`, `trust`, `hope`,
  `engineering_clarity`;
- merge-validation labels: `tenderness`, `protectiveness`, `satisfaction`;
- negative high-arousal rows;
- positive high-arousal rows;
- counterweight categories where present;
- calibration rows that expose wrapper-bias patterns.

The sampler writes selection reasons so the resulting worksheet can be audited.

## Labeling Order

Angela labels first. A second reviewer labels independently after Angela's pass.

Disagreements are preserved, not overwritten. Angela's label is the primary gold
value because this is her taxonomy, her participant context, and her corpus.
Reviewer disagreements are retained as evidence for label ambiguity and v1.1
definition work.

Use separate role-specific worksheets for the first pass. The sampler should be
run with a pinned seed and `--worksheet-mode split`, producing an Angela-only
worksheet and a reviewer-only worksheet from the same deterministic sample. The
reviewer should not read Angela's completed labels before completing the
independent pass.

## Label Fields

Each sampled row should preserve:

- snapshot ID;
- vocabulary version;
- sample split (`tier1` or `calibration`);
- memory ID;
- raw text hash;
- source tagger model;
- original silver labels;
- valence/arousal silver labels;
- selection reasons;
- Angela gold valence bucket (`negative`, `neutral`, `positive`);
- Angela gold arousal bucket (`low`, `medium`, `high`);
- Angela gold emotion chord;
- Angela notes;
- contested flag;
- reviewer valence bucket;
- reviewer arousal bucket;
- reviewer emotion chord;
- reviewer notes.

## Reliability Targets

Multi-label emotion agreement should be reported with more than one metric:

- per-label binary agreement / Cohen's kappa;
- macro average across labels;
- micro average across all label decisions;
- chord overlap / Jaccard score for multi-label rows.

Bucket targets:

- valence bucket MAE <= 0.5 on classes `-1, 0, 1`;
- arousal bucket MAE <= 0.5 on classes `0, 1, 2`.

If reviewer agreement is poor on a label, that is a vocabulary-definition
signal. Do not smooth it away with forced consensus.

## Expansion Gates

Before v1 model evaluation:

- expanded gold set has 300-500 rows;
- all 37 canonical labels have at least three gold examples where corpus
  availability permits;
- scrutiny labels have at least six gold examples each;
- calibration tier has at least 25 gold rows covering wrapper/import patterns;
- snapshot ID is pinned;
- vocabulary version is pinned;
- reliability targets have been reported and reviewed.

If a gate fails, expand or refine the gold set. Do not ship v1 evaluation on a
weak reference set.
