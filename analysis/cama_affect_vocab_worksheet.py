#!/usr/bin/env python3
"""Generate a private CAMA Affect v1 vocabulary curation worksheet.

Input is an ``affect_silver_labels.csv`` produced by
``analysis/cama_affect_dataset.py``. Output is a local worksheet directory next
to that CSV by default. If raw text is present in the export, snippets will be
included, so keep generated worksheet files under ``_affect_exports/``.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_TIER1_TAGGERS = [
    "realtime",
    "manual",
    "heartbeat",
    "sleep_synthesis_v2.1",
]
DEFAULT_CALIBRATION_TAGGERS = ["gpt_import_keyword", "import_auto", "import_aelen"]
DEFAULT_MERGE_SEED = Path(__file__).with_name("cama_affect_v1_merge_seed.json")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def split_csv_arg(value: str) -> set[str]:
    return {v.strip() for v in value.split(",") if v.strip()}


def labels_for_row(row: dict[str, str]) -> list[str]:
    labels = row.get("emotion_labels", "")
    if labels:
        return [x for x in labels.split("|") if x]
    raw = row.get("affect_emotion_json", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for key, value in data.items():
        try:
            if float(value) > 0:
                out.append(str(key))
        except (TypeError, ValueError):
            if value:
                out.append(str(key))
    return sorted(out)


def row_is_latest(row: dict[str, str]) -> bool:
    return str(row.get("is_latest_for_memory", "")).strip() in {"1", "true", "True"}


def snippet(text: str, width: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= width:
        return cleaned
    return cleaned[: max(0, width - 3)] + "..."


def load_merge_seed(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    token_map: dict[str, dict[str, str]] = {}
    for cluster in data.get("clusters", []):
        canonical = str(cluster.get("canonical_candidate", "")).strip()
        question = str(cluster.get("question", "")).strip()
        for token in cluster.get("tokens", []):
            token_map[str(token)] = {
                "canonical_candidate": canonical,
                "merge_question": question,
                "cluster_tokens": "|".join(cluster.get("tokens", [])),
            }
    return token_map


def filter_rows(
    rows: list[dict[str, str]],
    *,
    taggers: set[str],
    latest_only: bool,
    durable_only: bool,
    exclude_rejected: bool,
) -> list[dict[str, str]]:
    out = []
    for row in rows:
        if taggers and row.get("tagger_model") not in taggers:
            continue
        if latest_only and not row_is_latest(row):
            continue
        if durable_only and row.get("memory_status") != "durable":
            continue
        if exclude_rejected and row.get("memory_status") == "rejected":
            continue
        out.append(row)
    return out


def collect_counts(rows: list[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(labels_for_row(row))
    return counts


def collect_examples(
    rows: list[dict[str, str]],
    *,
    max_examples: int,
    snippet_width: int,
) -> dict[str, list[dict[str, str]]]:
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        for label in labels_for_row(row):
            if len(examples[label]) >= max_examples:
                continue
            examples[label].append(
                {
                    "memory_id": row.get("memory_id", ""),
                    "tagger_model": row.get("tagger_model", ""),
                    "status": row.get("memory_status", ""),
                    "valence": row.get("affect_valence", ""),
                    "arousal": row.get("affect_arousal", ""),
                    "snippet": snippet(row.get("memory_raw_text", ""), snippet_width),
                }
            )
    return examples


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if not rows:
        with path.open("w", newline="", encoding="utf-8") as f:
            if fieldnames:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()
        return
    resolved_fieldnames: list[str] = list(fieldnames or [])
    seen: set[str] = set(resolved_fieldnames)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                resolved_fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=resolved_fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def worksheet_fieldnames() -> list[str]:
    return [
        "token",
        "tier1_count",
        "tier1_share",
        "calibration_count",
        "calibration_share",
        "ratio_calibration_to_tier1",
        "proposed_action",
        "canonical_label",
        "cluster_tokens",
        "merge_question",
        "notes",
    ]


def bias_fieldnames() -> list[str]:
    return [
        "token",
        "tier1_count",
        "tier1_share",
        "calibration_count",
        "calibration_share",
        "ratio_calibration_to_tier1",
    ]


def markdown_examples(examples: list[dict[str, str]]) -> str:
    if not examples:
        return "_No raw-text examples available in this export._"
    lines = []
    for ex in examples:
        lines.append(
            f"- `{ex['memory_id']}` v={ex['valence']} a={ex['arousal']} "
            f"({ex['tagger_model']}, {ex['status']}): {ex['snippet']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a private CAMA Affect v1 vocabulary curation worksheet.",
    )
    parser.add_argument("csv_path", help="Path to affect_silver_labels.csv")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory. Defaults to <csv_dir>/vocab_worksheet.",
    )
    parser.add_argument(
        "--tier1-taggers",
        "--live-taggers",
        dest="tier1_taggers",
        default=",".join(DEFAULT_TIER1_TAGGERS),
        help=(
            "Comma-separated tagger_model values used for v1 vocabulary "
            "candidates. Alias: --live-taggers."
        ),
    )
    parser.add_argument(
        "--calibration-taggers",
        default=",".join(DEFAULT_CALIBRATION_TAGGERS),
        help="Comma-separated tagger_model values held out for calibration comparison.",
    )
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--max-examples", type=int, default=3)
    parser.add_argument("--snippet-width", type=int, default=220)
    parser.add_argument("--include-nonlatest", action="store_true")
    parser.add_argument("--include-nondurable", action="store_true")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument(
        "--merge-seed",
        default=str(DEFAULT_MERGE_SEED),
        help="JSON seed merge clusters for worksheet prompts.",
    )
    args = parser.parse_args(argv)

    csv_path = Path(args.csv_path)
    rows = read_rows(csv_path)
    tier1_taggers = split_csv_arg(args.tier1_taggers)
    calibration_taggers = split_csv_arg(args.calibration_taggers)

    tier1_rows = filter_rows(
        rows,
        taggers=tier1_taggers,
        latest_only=not args.include_nonlatest,
        durable_only=not args.include_nondurable,
        exclude_rejected=not args.include_rejected,
    )
    calibration_rows = filter_rows(
        rows,
        taggers=calibration_taggers,
        latest_only=not args.include_nonlatest,
        durable_only=False,
        exclude_rejected=not args.include_rejected,
    )

    tier1_counts = collect_counts(tier1_rows)
    calibration_counts = collect_counts(calibration_rows)
    examples = collect_examples(
        tier1_rows,
        max_examples=args.max_examples,
        snippet_width=args.snippet_width,
    )
    merge_seed = load_merge_seed(Path(args.merge_seed))
    total_tier1 = sum(tier1_counts.values())
    total_calibration = sum(calibration_counts.values())

    tokens = sorted(tier1_counts, key=lambda t: (-tier1_counts[t], t))
    candidate_rows: list[dict[str, Any]] = []
    long_tail_rows: list[dict[str, Any]] = []
    for token in tokens:
        count = tier1_counts[token]
        calibration_count = calibration_counts.get(token, 0)
        row: dict[str, Any] = {
            "token": token,
            "tier1_count": count,
            "tier1_share": round(count / total_tier1, 6) if total_tier1 else 0,
            "calibration_count": calibration_count,
            "calibration_share": round(calibration_count / total_calibration, 6)
            if total_calibration else 0,
            "ratio_calibration_to_tier1": round(
                (calibration_count / max(total_calibration, 1))
                / max(count / max(total_tier1, 1), 1e-9),
                4,
            ),
            "proposed_action": "candidate" if count >= args.min_count else "long_tail",
            "canonical_label": merge_seed.get(token, {}).get("canonical_candidate", ""),
            "cluster_tokens": merge_seed.get(token, {}).get("cluster_tokens", ""),
            "merge_question": merge_seed.get(token, {}).get("merge_question", ""),
            "notes": "",
        }
        if count >= args.min_count:
            candidate_rows.append(row)
        else:
            long_tail_rows.append(row)

    out_dir = Path(args.out_dir) if args.out_dir else csv_path.parent / "vocab_worksheet"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "v1_candidate_tokens.csv", candidate_rows, worksheet_fieldnames())
    write_csv(out_dir / "v1_long_tail_tokens.csv", long_tail_rows, worksheet_fieldnames())

    bias_rows = []
    all_tokens = set(tier1_counts) | set(calibration_counts)
    for token in sorted(all_tokens):
        tier1_count = tier1_counts.get(token, 0)
        calibration_count = calibration_counts.get(token, 0)
        tier1_share = tier1_count / total_tier1 if total_tier1 else 0
        calibration_share = calibration_count / total_calibration if total_calibration else 0
        bias_rows.append(
            {
                "token": token,
                "tier1_count": tier1_count,
                "tier1_share": round(tier1_share, 6),
                "calibration_count": calibration_count,
                "calibration_share": round(calibration_share, 6),
                "ratio_calibration_to_tier1": round(
                    calibration_share / max(tier1_share, 1e-9),
                    4,
                ),
            }
        )
    bias_rows.sort(key=lambda r: (-r["ratio_calibration_to_tier1"], -r["calibration_count"], r["token"]))
    write_csv(out_dir / "calibration_vs_tier1_frequency.csv", bias_rows, bias_fieldnames())

    md = [
        "# CAMA Affect v1 Vocabulary Worksheet",
        "",
        "This is a private curation worksheet. If the source export included raw",
        "memory text, this file includes snippets and should remain under",
        "`_affect_exports/`.",
        "",
        "## Filters",
        "",
        f"- Source CSV: `{csv_path}`",
        f"- Tier-1 training taggers: `{', '.join(sorted(tier1_taggers))}`",
        f"- Calibration taggers: `{', '.join(sorted(calibration_taggers))}`",
        f"- Latest only: `{not args.include_nonlatest}`",
        f"- Durable only: `{not args.include_nondurable}`",
        f"- Exclude rejected: `{not args.include_rejected}`",
        f"- Min candidate count: `{args.min_count}`",
        "",
        "## Summary",
        "",
        f"- Tier-1 rows after filters: {len(tier1_rows)}",
        f"- Calibration rows after filters: {len(calibration_rows)}",
        f"- Tier-1 emotion-token observations: {total_tier1}",
        f"- Candidate tokens: {len(candidate_rows)}",
        f"- Long-tail tokens: {len(long_tail_rows)}",
        "",
        "## Candidate Tokens",
        "",
    ]
    for row in candidate_rows:
        md.extend(
            [
                f"### `{row['token']}`",
                "",
                f"- Tier-1 count: {row['tier1_count']} ({row['tier1_share']:.4%})",
                f"- Calibration count: {row['calibration_count']} ({row['calibration_share']:.4%})",
                f"- Suggested canonical label: `{row['canonical_label']}`",
                f"- Merge cluster: `{row['cluster_tokens']}`",
                f"- Curation question: {row['merge_question'] or '_None yet._'}",
                "",
                markdown_examples(examples.get(str(row["token"]), [])),
                "",
                "**Decision:**",
                "",
                "- canonical label:",
                "- keep / merge / drop:",
                "- rationale:",
                "",
            ]
        )
    md.extend(
        [
            "## Long Tail",
            "",
            "Long-tail tokens are not candidates for v1 unless manually promoted.",
            "See `v1_long_tail_tokens.csv` for the full list.",
            "",
            "## Calibration Frequency Table",
            "",
            "See `calibration_vs_tier1_frequency.csv`. This is the seed for the",
            "Paper 12 wrapper/import-bias figure.",
            "",
        ]
    )
    (out_dir / "v1_vocabulary_worksheet.md").write_text("\n".join(md), encoding="utf-8")

    manifest = {
        "source_csv": str(csv_path),
        "tier1_taggers": sorted(tier1_taggers),
        "calibration_taggers": sorted(calibration_taggers),
        "latest_only": not args.include_nonlatest,
        "durable_only": not args.include_nondurable,
        "exclude_rejected": not args.include_rejected,
        "min_count": args.min_count,
        "tier1_rows": len(tier1_rows),
        "calibration_rows": len(calibration_rows),
        "candidate_tokens": len(candidate_rows),
        "long_tail_tokens": len(long_tail_rows),
        "files": [
            "v1_vocabulary_worksheet.md",
            "v1_candidate_tokens.csv",
            "v1_long_tail_tokens.csv",
            "calibration_vs_tier1_frequency.csv",
        ],
    }
    (out_dir / "worksheet_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Wrote vocabulary worksheet: {out_dir}")
    print(f"Candidate tokens: {len(candidate_rows)} | Long tail: {len(long_tail_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
