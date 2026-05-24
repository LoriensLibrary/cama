# CAMA Affect v1

Working plan for a CAMA-native affect classifier. This is not a commitment to
any outside emotion taxonomy. The classifier should learn from CAMA's own
longitudinal memory record, while remaining subject to the same provenance
rules as every other inference source.

## Frame

We are not importing an emotion model into CAMA. We are teaching CAMA to read
its own emotional memory.

The first artifact is not a model. It is a clean silver-label export and drift
report that lets us understand what the existing labels actually are.

## Required Label Provenance

Every exported silver label should carry:

- `tagged_at`: when the affect annotation was generated. In the current schema
  this maps to `memory_affect.computed_at`.
- `tagger_model`: the source model or process that generated the annotation.
  In the current schema this maps to `memory_affect.model`.
- `schema_version`: emitted if present in the database; otherwise left blank.
- `memory_era` and `tagger_era`: inferred buckets for drift analysis when a
  first-class schema version is absent.
- `is_latest_for_memory`: whether this affect row is the latest annotation for
  its memory.

This keeps us from training on "the average of every tagger that ever touched
CAMA" without knowing it.

Era buckets are defined in `analysis/cama_affect_eras.json`, not buried in the
exporter. If a distribution surprise lands on an era boundary, the rationale
for that boundary should be visible next to the config.

## Today's Export

Run the local export script:

```bash
python analysis/cama_affect_dataset.py --db ~/.cama/memory.db
```

Outputs are written to a timestamped directory under `_affect_exports/`, which
is gitignored because the CSV may contain raw memory text.

For a shareable structure check without raw text:

```bash
python analysis/cama_affect_dataset.py --db ~/.cama/memory.db --redact-text
```

Useful switches:

- `--latest-only`: one affect annotation per memory, using the latest
  `computed_at`.
- `--out-root PATH`: alternate output root. Be careful: only the default
  `_affect_exports/` path is guaranteed ignored by this repo.

Generated files:

- `affect_silver_labels.csv`: one row per affect annotation.
- `affect_distribution_report.md`: human-readable label/drift report.
- `affect_distribution_report.json`: machine-readable report.
- `manifest.json`: snapshot ID, row counts, and export settings.

The Markdown and JSON reports contain two views:

- **All annotations view:** drift lens. Use this to inspect tagger changes over
  time and differences between historical annotations.
- **Latest annotation view:** training lens. Use this as the default candidate
  dataset when building v1 labels.

Both views include tagger drift by `tagged_month` and by `memory_month`. The
second view matters because early imports often tell us when the import/tagging
job ran, not when the remembered conversation happened.

## First-Corpus Lessons

The first private full-corpus run changed the modeling plan. The raw dataset is
not "53k clean affect labels." It is a longitudinal record of multiple tagger
eras with different vocabularies and different calibration behavior.

Design consequences:

- Do not train on the unweighted union until the tagger mix is understood.
- Treat heavily dominant import taggers as calibration/migration targets, not
  automatically as ground truth.
- Prefer a CAMA-live vocabulary for v1 unless we explicitly author a mapping
  table from older/import vocabularies into the live taxonomy.
- Bucket valence first (`negative` / `neutral` / `positive`) when saturation is
  visible; direct regression can collapse into the dominant saturated value.
- Drop `dominance` from CAMA Affect v1 unless upstream starts producing real
  nonzero signal.
- Sample gold labels from contested zones, not random rows alone.

Open vocabulary strategies:

1. **Live-only:** train on `manual`, `realtime`, heartbeat/live-system labels
   and use old import labels only for migration comparison.
2. **Union with mapping:** author an explicit mapping table from historical
   labels into the CAMA-live vocabulary, then train on mapped labels.
3. **Tagger-stratified heads:** model shared text features but keep separate
   output heads/calibration by tagger family.

No model work starts until this choice is made.

## Gold-Set Sampling Direction

The gold set should be stratified after reading the distribution report:

- include the active/live era heavily;
- oversample negative high-arousal rows;
- oversample positive high-arousal rows;
- oversample rows where old import labels look semantically suspicious;
- cover counterweight types (`anchor`, `self_compassion`, `grounding`,
  `agency`, and any future categories);
- include some ordinary neutral/low-arousal rows so the set is not all edge
  cases.

## Baseline Order

1. Export silver labels with full available provenance.
2. Inspect distribution: nulls, tagger models, schema versions, eras, source
   breakdown, emotion skew, and tagger drift over time.
3. Decide gold-set sampling strategy from observed skew and contested zones.
4. Create a human-curated gold set.
5. Train the embarrassingly simple baseline first:
   - TF-IDF plus logistic regression for emotion chords.
   - TF-IDF plus linear regression for valence/arousal.
6. Only then consider representation learning.
7. Integrate classifier predictions in shadow mode first.

## Provenance Rule For The Classifier

CAMA Affect v1 predictions are inferences. They are not teachings.

Each prediction should carry:

- `proposed_by = "cama_affect_v1"`
- model version and code hash
- training-corpus snapshot ID
- generated timestamp
- confidence
- raw predicted valence/arousal/chord

Retraining creates a new version. Old predictions remain auditable and
re-runnable for comparison.
