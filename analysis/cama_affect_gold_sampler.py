#!/usr/bin/env python3
"""Build a private CAMA Affect v1 gold-labeling worksheet.

Input is ``affect_silver_labels.csv`` from ``cama_affect_dataset.py``. Output is
local and may contain raw memory text, so keep it under ``_affect_exports/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_TIER1_TAGGERS = ["realtime", "manual", "heartbeat", "sleep_synthesis_v2.1"]
DEFAULT_CALIBRATION_TAGGERS = ["gpt_import_keyword", "import_auto", "import_aelen"]
DEFAULT_SCRUTINY_LABELS = ["warmth", "gratitude", "trust", "hope", "engineering_clarity"]
DEFAULT_MERGE_LABELS = ["tenderness", "protectiveness", "satisfaction"]
DEFAULT_CALIBRATION_BIAS_LABELS = [
    "trust",
    "hope",
    "warmth",
    "gratitude",
    "love",
    "pride",
    "recognition",
    "grief",
    "joy",
    "vulnerability",
    "shame",
]
DEFAULT_COUNTERWEIGHTS = [
    "anchor",
    "self_compassion",
    "grounding",
    "agency",
    "evidence_of_progress",
]
DEFAULT_VOCAB_PATH = Path(__file__).resolve().parent.parent / "specs" / "cama_affect_v1_vocabulary.md"


def split_csv_arg(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_vocab(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    marker = "| Label | Status | Merge Sources | Rationale |"
    if marker not in text:
        raise ValueError(f"Could not find canonical-label table in {path}")
    section = text.split(marker, 1)[1].split("## Boundary Definitions", 1)[0]
    labels: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        label = line.split("|")[1].strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def labels_for_row(row: dict[str, str]) -> set[str]:
    raw = row.get("emotion_labels", "")
    return {x for x in raw.split("|") if x}


def has_label(row: dict[str, str], label: str) -> bool:
    return label in labels_for_row(row)


def row_key(row: dict[str, str]) -> str:
    return f"{row.get('memory_id', '')}:{row.get('affect_id', '')}"


def row_is_latest(row: dict[str, str]) -> bool:
    return str(row.get("is_latest_for_memory", "")).strip() in {"1", "true", "True"}


def as_float(row: dict[str, str], key: str) -> float | None:
    try:
        value = row.get(key, "")
        if value == "":
            return None
        return float(value)
    except ValueError:
        return None


def is_negative_high_arousal(row: dict[str, str]) -> bool:
    val = as_float(row, "affect_valence")
    aro = as_float(row, "affect_arousal")
    return val is not None and aro is not None and val <= -0.5 and aro >= 0.5


def is_positive_high_arousal(row: dict[str, str]) -> bool:
    val = as_float(row, "affect_valence")
    aro = as_float(row, "affect_arousal")
    return val is not None and aro is not None and val >= 0.5 and aro >= 0.5


def filter_rows(
    rows: list[dict[str, str]],
    *,
    taggers: set[str],
    durable_only: bool,
    exclude_rejected: bool,
) -> list[dict[str, str]]:
    out = []
    for row in rows:
        if taggers and row.get("tagger_model") not in taggers:
            continue
        if not row_is_latest(row):
            continue
        if durable_only and row.get("memory_status") != "durable":
            continue
        if exclude_rejected and row.get("memory_status") == "rejected":
            continue
        out.append(row)
    return out


def snippet(text: str, width: int = 360) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= width:
        return cleaned
    return cleaned[: max(0, width - 3)] + "..."


class Sampler:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.selected: dict[str, dict[str, Any]] = {}
        self.reasons: dict[str, set[str]] = defaultdict(set)

    def add(self, row: dict[str, str], reason: str) -> bool:
        key = row_key(row)
        if not key or key in self.selected:
            if key:
                self.reasons[key].add(reason)
            return False
        self.selected[key] = row
        self.reasons[key].add(reason)
        return True

    def sample_from(self, candidates: list[dict[str, str]], count: int, reason: str) -> int:
        pool = [r for r in candidates if row_key(r) not in self.selected]
        self.rng.shuffle(pool)
        added = 0
        for row in pool[:count]:
            if self.add(row, reason):
                added += 1
        return added

    def rows(self) -> list[dict[str, str]]:
        return list(self.selected.values())

    def reason_text(self, row: dict[str, str]) -> str:
        return "|".join(sorted(self.reasons.get(row_key(row), [])))


def label_counts(rows: list[dict[str, str]], labels: list[str]) -> dict[str, int]:
    counts = {label: 0 for label in labels}
    for row in rows:
        for label in labels_for_row(row):
            if label in counts:
                counts[label] += 1
    return counts


def select_tier1(
    rows: list[dict[str, str]],
    *,
    labels: list[str],
    target: int,
    rng: random.Random,
    scrutiny_labels: list[str],
    merge_labels: list[str],
    counterweights: list[str],
    per_label_min: int,
    scrutiny_min: int,
    merge_min: int,
    high_arousal_min: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    sampler = Sampler(rng)
    diagnostics: dict[str, Any] = {"unfilled": []}

    for label in scrutiny_labels:
        candidates = [r for r in rows if has_label(r, label)]
        added = sampler.sample_from(candidates, scrutiny_min, f"scrutiny:{label}")
        if added < min(scrutiny_min, len(candidates)):
            diagnostics["unfilled"].append({"stratum": f"scrutiny:{label}", "requested": scrutiny_min, "added": added, "available": len(candidates)})

    for label in merge_labels:
        candidates = [r for r in rows if has_label(r, label)]
        added = sampler.sample_from(candidates, merge_min, f"merge_validation:{label}")
        if added < min(merge_min, len(candidates)):
            diagnostics["unfilled"].append({"stratum": f"merge_validation:{label}", "requested": merge_min, "added": added, "available": len(candidates)})

    current_counts = label_counts(sampler.rows(), labels)
    for label in labels:
        need = max(0, per_label_min - current_counts[label])
        if need == 0:
            continue
        candidates = [r for r in rows if has_label(r, label)]
        added = sampler.sample_from(candidates, need, f"label_coverage:{label}")
        if added < min(need, len([r for r in candidates if row_key(r) not in sampler.selected])):
            diagnostics["unfilled"].append({"stratum": f"label_coverage:{label}", "requested": need, "added": added, "available": len(candidates)})
        current_counts = label_counts(sampler.rows(), labels)

    sampler.sample_from([r for r in rows if is_negative_high_arousal(r)], high_arousal_min, "negative_high_arousal")
    sampler.sample_from([r for r in rows if is_positive_high_arousal(r)], high_arousal_min, "positive_high_arousal")

    for cw in counterweights:
        candidates = [r for r in rows if r.get("memory_counterweight_type") == cw]
        sampler.sample_from(candidates, 1, f"counterweight:{cw}")

    if len(sampler.rows()) < target:
        sampler.sample_from(rows, target - len(sampler.rows()), "tier1_random_fill")

    selected = sampler.rows()[:target]
    diagnostics["selected_count"] = len(selected)
    diagnostics["label_counts"] = label_counts(selected, labels)
    diagnostics["selection_reasons"] = {row_key(r): sampler.reason_text(r) for r in selected}
    return selected, diagnostics


def select_calibration(
    rows: list[dict[str, str]],
    *,
    target: int,
    rng: random.Random,
    bias_labels: list[str],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    sampler = Sampler(rng)
    diagnostics: dict[str, Any] = {"unfilled": []}
    per_bias = max(1, target // max(1, len(bias_labels)))
    for label in bias_labels:
        if len(sampler.rows()) >= target:
            break
        candidates = [r for r in rows if has_label(r, label)]
        sampler.sample_from(candidates, min(per_bias, target - len(sampler.rows())), f"calibration_bias:{label}")
    if len(sampler.rows()) < target:
        sampler.sample_from(rows, target - len(sampler.rows()), "calibration_random_fill")
    selected = sampler.rows()[:target]
    diagnostics["selected_count"] = len(selected)
    diagnostics["selection_reasons"] = {row_key(r): sampler.reason_text(r) for r in selected}
    diagnostics["tagger_counts"] = dict(Counter(r.get("tagger_model", "") for r in selected))
    return selected, diagnostics


def worksheet_rows(
    rows: list[dict[str, str]],
    *,
    split: str,
    reasons: dict[str, str],
    snapshot_id: str,
    vocabulary_version: str,
) -> list[dict[str, Any]]:
    out = []
    for i, row in enumerate(rows, start=1):
        out.append(
            {
                "sample_id": f"{split}-{i:03d}",
                "sample_split": split,
                "snapshot_id": snapshot_id,
                "vocabulary_version": vocabulary_version,
                "memory_id": row.get("memory_id", ""),
                "affect_id": row.get("affect_id", ""),
                "raw_text_sha256": row.get("memory_raw_text_sha256", ""),
                "tagger_model": row.get("tagger_model", ""),
                "memory_status": row.get("memory_status", ""),
                "memory_type": row.get("memory_memory_type", ""),
                "counterweight_type": row.get("memory_counterweight_type", ""),
                "silver_emotion_labels": row.get("emotion_labels", ""),
                "silver_valence": row.get("affect_valence", ""),
                "silver_arousal": row.get("affect_arousal", ""),
                "selection_reasons": reasons.get(row_key(row), ""),
                "memory_text": row.get("memory_raw_text", ""),
                "angela_valence_bucket": "",
                "angela_arousal_bucket": "",
                "angela_emotion_chord": "",
                "angela_contested": "",
                "angela_notes": "",
                "reviewer_valence_bucket": "",
                "reviewer_arousal_bucket": "",
                "reviewer_emotion_chord": "",
                "reviewer_notes": "",
                "disagreement_notes": "",
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, Any]], *, mode: str = "combined") -> None:
    if mode not in {"combined", "angela", "reviewer"}:
        raise ValueError(f"Unknown markdown mode: {mode}")

    lines = [
        "# CAMA Affect v1 Gold-Set Labeling Worksheet",
        "",
        "Private worksheet. If memory text is present, do not commit this file.",
        f"Worksheet mode: `{mode}`.",
        "",
        "Allowed valence buckets: `negative`, `neutral`, `positive`.",
        "Allowed arousal buckets: `low`, `medium`, `high`.",
        "Emotion chord: pipe-separated canonical labels, e.g. `trust|warmth`.",
        "",
    ]
    for row in rows:
        text = row["memory_text"] or "[redacted/no raw text in source export]"
        lines.extend(
            [
                f"## {row['sample_id']} ({row['sample_split']})",
                "",
                f"- Memory ID: `{row['memory_id']}`",
                f"- Affect ID: `{row['affect_id']}`",
                f"- Tagger: `{row['tagger_model']}`",
                f"- Memory type/status: `{row['memory_type']}` / `{row['memory_status']}`",
                f"- Counterweight: `{row['counterweight_type']}`",
                f"- Silver chord: `{row['silver_emotion_labels']}`",
                f"- Silver valence/arousal: `{row['silver_valence']}` / `{row['silver_arousal']}`",
                f"- Selection reasons: `{row['selection_reasons']}`",
                "",
                "```text",
                text,
                "```",
                "",
            ]
        )
        if mode in {"combined", "angela"}:
            lines.extend(
                [
                "### Angela Label",
                "",
                "- valence bucket:",
                "- arousal bucket:",
                "- emotion chord:",
                "- contested? yes/no:",
                "- notes:",
                "",
                ]
            )
        if mode in {"combined", "reviewer"}:
            lines.extend(
                [
                "### Reviewer Label",
                "",
                "- valence bucket:",
                "- arousal bucket:",
                "- emotion chord:",
                "- notes:",
                "",
                ]
            )
        if mode == "combined":
            lines.extend(
                [
                "### Disagreement Notes",
                "",
                "-",
                "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample CAMA Affect v1 gold-labeling rows.")
    parser.add_argument("csv_path", help="Path to affect_silver_labels.csv")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--snapshot-id", default="b40c018fe9868d35")
    parser.add_argument("--vocabulary-version", default="cama_affect_v1_labels:0.1-draft-locked")
    parser.add_argument("--vocab-path", default=str(DEFAULT_VOCAB_PATH))
    parser.add_argument("--seed", type=int, default=20260524)
    parser.add_argument("--tier1-count", type=int, default=75)
    parser.add_argument("--calibration-count", type=int, default=25)
    parser.add_argument("--per-label-min", type=int, default=1)
    parser.add_argument("--scrutiny-min", type=int, default=6)
    parser.add_argument("--merge-min", type=int, default=3)
    parser.add_argument("--high-arousal-min", type=int, default=8)
    parser.add_argument("--tier1-taggers", default=",".join(DEFAULT_TIER1_TAGGERS))
    parser.add_argument("--calibration-taggers", default=",".join(DEFAULT_CALIBRATION_TAGGERS))
    parser.add_argument("--scrutiny-labels", default=",".join(DEFAULT_SCRUTINY_LABELS))
    parser.add_argument("--merge-labels", default=",".join(DEFAULT_MERGE_LABELS))
    parser.add_argument("--calibration-bias-labels", default=",".join(DEFAULT_CALIBRATION_BIAS_LABELS))
    parser.add_argument("--counterweights", default=",".join(DEFAULT_COUNTERWEIGHTS))
    parser.add_argument(
        "--worksheet-mode",
        choices=["combined", "angela", "reviewer", "split"],
        default="combined",
        help="Markdown worksheet role view. split writes Angela and reviewer files from the same sample.",
    )
    args = parser.parse_args(argv)

    csv_path = Path(args.csv_path)
    rows = read_rows(csv_path)
    labels = read_vocab(Path(args.vocab_path))
    rng = random.Random(args.seed)

    tier1_rows = filter_rows(
        rows,
        taggers=set(split_csv_arg(args.tier1_taggers)),
        durable_only=True,
        exclude_rejected=True,
    )
    calibration_rows = filter_rows(
        rows,
        taggers=set(split_csv_arg(args.calibration_taggers)),
        durable_only=False,
        exclude_rejected=True,
    )

    tier1_selected, tier1_diag = select_tier1(
        tier1_rows,
        labels=labels,
        target=args.tier1_count,
        rng=rng,
        scrutiny_labels=split_csv_arg(args.scrutiny_labels),
        merge_labels=split_csv_arg(args.merge_labels),
        counterweights=split_csv_arg(args.counterweights),
        per_label_min=args.per_label_min,
        scrutiny_min=args.scrutiny_min,
        merge_min=args.merge_min,
        high_arousal_min=args.high_arousal_min,
    )
    calibration_selected, calibration_diag = select_calibration(
        calibration_rows,
        target=args.calibration_count,
        rng=rng,
        bias_labels=split_csv_arg(args.calibration_bias_labels),
    )

    tier1_reasons = tier1_diag.pop("selection_reasons", {})
    calibration_reasons = calibration_diag.pop("selection_reasons", {})
    output_rows = worksheet_rows(
        tier1_selected,
        split="tier1",
        reasons=tier1_reasons,
        snapshot_id=args.snapshot_id,
        vocabulary_version=args.vocabulary_version,
    ) + worksheet_rows(
        calibration_selected,
        split="calibration",
        reasons=calibration_reasons,
        snapshot_id=args.snapshot_id,
        vocabulary_version=args.vocabulary_version,
    )

    out_dir = Path(args.out_dir) if args.out_dir else csv_path.parent / f"gold_set_seed_{args.seed}"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "gold_labeling_worksheet.csv", output_rows)
    files = ["gold_labeling_worksheet.csv", "gold_sampler_manifest.json"]
    if args.worksheet_mode == "split":
        write_markdown(out_dir / "gold_labeling_worksheet_angela.md", output_rows, mode="angela")
        write_markdown(out_dir / "gold_labeling_worksheet_reviewer.md", output_rows, mode="reviewer")
        files.extend(["gold_labeling_worksheet_angela.md", "gold_labeling_worksheet_reviewer.md"])
    else:
        write_markdown(out_dir / "gold_labeling_worksheet.md", output_rows, mode=args.worksheet_mode)
        files.append("gold_labeling_worksheet.md")

    manifest = {
        "source_csv": str(csv_path),
        "snapshot_id": args.snapshot_id,
        "vocabulary_version": args.vocabulary_version,
        "seed": args.seed,
        "tier1_rows_available": len(tier1_rows),
        "calibration_rows_available": len(calibration_rows),
        "tier1_selected": len(tier1_selected),
        "calibration_selected": len(calibration_selected),
        "canonical_label_count": len(labels),
        "worksheet_mode": args.worksheet_mode,
        "tier1_diagnostics": tier1_diag,
        "calibration_diagnostics": calibration_diag,
        "files": files,
    }
    (out_dir / "gold_sampler_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"Wrote gold-set worksheet: {out_dir}")
    print(f"Rows: {len(output_rows)} ({len(tier1_selected)} tier1, {len(calibration_selected)} calibration)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
