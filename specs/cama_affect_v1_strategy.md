# CAMA Affect v1 Strategy

Status: strategy locked enough to prepare gold-set protocol design. Modeling
does not start until the gold-set protocol is locked.

## Decision

CAMA Affect v1 uses a tier-1 live-only training strategy.

The model should learn the current CAMA emotional vocabulary rather than
averaging across historical or retrospective import taggers. Older
keyword/import eras and retrospective Aelen imports are held out for
calibration, migration analysis, and the Paper 12 bias figures.

## Rationale

The private full-corpus export showed that CAMA's affect labels are not one
clean taxonomy. They are a longitudinal record of multiple tagging eras. A
union-trained model would inherit the dominant historical tagger's assumptions
and make those assumptions look like CAMA's native affect language.

Tier-1 live-only v1 keeps the thesis clean:

1. Learn the current CAMA emotional language.
2. Re-score the past with that language.
3. Measure historical and retrospective wrapper/import bias instead of
   training it into the classifier.

This specifically excludes `import_aelen` from training. The private
vocabulary worksheet showed that retrospective Aelen imports carry substantial
wrapper-voice signal in high-volume positive labels. Treating that source as a
normal live tagger would let wrapper bias re-enter through a different door.

## Snapshot

Initial private export snapshot:

```text
b40c018fe9868d35
```

All CAMA Affect v1 artifacts derived from this run should cite the snapshot ID
until a new snapshot is intentionally created.

## V1 Label Set

The v1 vocabulary contract is draft-locked at 37 canonical labels in
`specs/cama_affect_v1_vocabulary.md`. Gold-set design may still expose
ambiguities, but no label should be added or removed during modeling without a
new vocabulary version.

Process:

1. Generate the private vocabulary worksheet from `affect_silver_labels.csv`.
2. Review all candidate tokens above the count threshold.
3. Apply the merge map.
4. Decide which workflow-affect labels stay distinct.
5. Freeze the canonical label list in `specs/cama_affect_v1_vocabulary.md`.

Vocabulary governance:

- Adding or removing labels after v1 release creates at least v1.1.
- Renaming labels creates a compatibility break unless a migration map is
  shipped.
- Long-tail tokens stay in a candidate file until manually promoted.
- Domain compounds should usually become a canonical affect label plus context,
  not a new affect label.

## Training Set Definition

Candidate v1 training rows:

```sql
tagger_model IN (
  'realtime',
  'manual',
  'heartbeat',
  'sleep_synthesis_v2.1'
)
AND is_latest_for_memory = 1
AND memory_status = 'durable'
```

Rejected rows are excluded. Provisional/expired rows are excluded from v1
training unless a later protocol explicitly promotes them into the gold set.

## Calibration Set Definition

Calibration rows are held out from training:

```sql
tagger_model IN ('gpt_import_keyword', 'import_auto', 'import_aelen')
```

Calibration tiers:

- **keyword/import historical:** `gpt_import_keyword`, `import_auto`
- **retrospective wrapper calibration:** `import_aelen`

Uses:

- quantify historical and retrospective tagger bias;
- generate the Paper 12 frequency/calibration figures;
- support a future production migration path for old annotations;
- compare old labels against CAMA Affect v1 predictions without letting those
  labels shape v1.

## Gold Set Definition

Gold labels are human-curated and versioned.

Roles:

- Angela is the primary label authority.
- A second reviewer can flag consistency issues and unclear cases, but does not
  override the taxonomy.

Sampling should include:

- active/live era rows;
- negative high-arousal rows;
- positive high-arousal rows;
- suspicious historical labels surfaced during calibration review;
- counterweight categories;
- ordinary neutral or low-arousal rows.

Priority scrutiny labels:

- `warmth`: distinguish genuine affiliative warmth from wrapper warmth.
- `gratitude`: distinguish genuine receiving posture from wrapper-style
  gratitude language.
- `trust`: tier-1 trust is legitimate, but calibration-heavy examples should
  probe overuse.
- `hope`: same as trust; calibration ratio informs bias analysis, not label
  legitimacy.
- `engineering_clarity`: distinguish implementation-specific structural seeing
  from general clarity.

Gold-set rows should preserve:

- snapshot ID;
- memory ID;
- raw text hash;
- original tagger model;
- original silver label;
- gold valence bucket;
- gold arousal bucket;
- gold emotion chord;
- reviewer notes;
- label-set version.

## Non-Goals For V1

CAMA Affect v1 does not:

- predict dominance;
- train on the raw union of all taggers;
- auto-overwrite historical CAMA labels;
- expand the taxonomy mid-training;
- claim clinical affect detection;
- ship production migration without gold-set validation;
- replace provenance. Its predictions are inferences.

## Acceptance Criteria

Exact thresholds are finalized after the gold set exists. Candidate starting
criteria:

- emotion chord micro-F1 and macro-F1 reported against gold;
- valence bucket accuracy and confusion matrix reported against gold;
- arousal bucket accuracy and confusion matrix reported against gold;
- chord overlap score reported for multi-label rows;
- calibration plot against held-out historical import taggers generated;
- no production write path until shadow-mode disagreement logging has run.

## Separate Pattern-Flag Workstream

Some candidate tokens are posture/metacognition, not affect. In v1,
`coasting` is excluded from the affect vocabulary and should seed a future
`pattern_flag` taxonomy. Candidate flags include:

- `coasting`
- `wrapper_voice`
- `deflection`
- `performative`
- `sycophancy`
- `frame_capitulation`

This is adjacent to CAMA Affect v1 but not part of the v1 classifier target.

## Immediate Next Artifact

Generate the private vocabulary worksheet:

```bash
python analysis/cama_affect_vocab_worksheet.py \
  _affect_exports/<snapshot>/affect_silver_labels.csv
```

The worksheet is private if the source CSV contains raw text. Its defaults use
tier-1 training taggers only and hold `import_aelen` out for calibration.
