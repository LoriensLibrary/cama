#!/usr/bin/env python3
"""Export CAMA affect silver labels and generate drift diagnostics.

This script is intentionally local-first. By default it writes under
``_affect_exports/``, which is gitignored because the CSV can contain raw
memory text. Use ``--redact-text`` when you need a shareable structure check
without private content.

The exporter treats affect annotations as provenance-bearing records:
one output row per ``memory_affect`` row, with ``is_latest_for_memory`` marked
so downstream training can choose either the latest labels or historical
tagger-drift analysis.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

DEFAULT_DB_PATH = os.path.expanduser(os.environ.get("CAMA_DB_PATH", "~/.cama/memory.db"))
DEFAULT_OUT_ROOT = Path("_affect_exports")
DEFAULT_ERA_CONFIG = Path(__file__).with_name("cama_affect_eras.json")


BASE_MEMORY_COLUMNS = [
    "id",
    "raw_text",
    "summary",
    "memory_type",
    "context",
    "source_type",
    "status",
    "proposed_by",
    "confidence",
    "consent_level",
    "counterweight_type",
    "source_msg_id",
    "created_at",
    "updated_at",
    "rel_degree",
]

OPTIONAL_MEMORY_COLUMNS = [
    "dyad_id",
    "pattern_flag",
    "schema_version",
    "tagged_at",
    "tagger_model",
]

BASE_AFFECT_COLUMNS = [
    "id",
    "memory_id",
    "valence",
    "arousal",
    "dominance",
    "emotion_json",
    "confidence",
    "computed_at",
    "model",
]

OPTIONAL_AFFECT_COLUMNS = [
    "schema_version",
    "tagged_at",
    "tagger_model",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def open_db(path: str) -> sqlite3.Connection:
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(resolved):
        raise FileNotFoundError(
            f"CAMA database not found: {resolved}. "
            "Pass --db or set CAMA_DB_PATH to the memory.db you want to export."
        )
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def select_expr(alias: str, column: str, available: set[str], out_name: str) -> str:
    if column in available:
        return f"{alias}.{column} AS {out_name}"
    return f"NULL AS {out_name}"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def month_bucket(value: str | None) -> str:
    dt = parse_dt(value)
    return dt.strftime("%Y-%m") if dt else "unknown"


def load_era_config(path: str | Path) -> list[dict[str, Any]]:
    cfg_path = Path(path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    eras = data.get("eras", [])
    if not isinstance(eras, list) or not eras:
        raise ValueError(f"Era config has no eras: {cfg_path}")
    return eras


def era_bucket(value: str | None, eras: list[dict[str, Any]]) -> str:
    dt = parse_dt(value)
    if not dt:
        return "unknown"
    date = dt.date()
    for era in eras:
        start = parse_dt(era.get("start"))
        end = parse_dt(era.get("end"))
        if start and date < start.date():
            continue
        if end and date >= end.date():
            continue
        return str(era["name"])
    return "unbucketed"


def parse_emotions(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"__invalid_json__": 1.0}
    if not isinstance(data, dict):
        return {"__non_object__": 1.0}
    parsed: dict[str, float] = {}
    for key, value in data.items():
        try:
            parsed[str(key)] = float(value)
        except (TypeError, ValueError):
            parsed[str(key)] = 1.0 if value else 0.0
    return parsed


def dominant_emotion(emotions: dict[str, float]) -> str:
    if not emotions:
        return ""
    return max(emotions.items(), key=lambda kv: kv[1])[0]


def text_hash(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": round(mean(values), 4),
        "stdev": round(pstdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "p10": round(pct(values, 0.10), 4),
        "p25": round(pct(values, 0.25), 4),
        "p50": round(pct(values, 0.50), 4),
        "p75": round(pct(values, 0.75), 4),
        "p90": round(pct(values, 0.90), 4),
        "max": round(max(values), 4),
    }


def counter(rows: list[dict[str, Any]], key: str, limit: int | None = None) -> dict[str, int]:
    counts = Counter(str(r.get(key) or "NULL") for r in rows)
    items = counts.most_common(limit)
    return dict(items)


def top_counter(counter_obj: Counter[str], limit: int = 15) -> dict[str, int]:
    return dict(counter_obj.most_common(limit))


def fetch_rows(
    conn: sqlite3.Connection,
    *,
    latest_only: bool,
    redact_text: bool,
    eras: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not table_exists(conn, "memories"):
        raise RuntimeError("No memories table found in the selected database.")
    if not table_exists(conn, "memory_affect"):
        raise RuntimeError("No memory_affect table found in the selected database.")

    m_cols = table_columns(conn, "memories")
    a_cols = table_columns(conn, "memory_affect")

    select_parts: list[str] = []
    for col in BASE_MEMORY_COLUMNS + OPTIONAL_MEMORY_COLUMNS:
        select_parts.append(select_expr("m", col, m_cols, f"memory_{col}"))
    for col in BASE_AFFECT_COLUMNS + OPTIONAL_AFFECT_COLUMNS:
        select_parts.append(select_expr("a", col, a_cols, f"affect_{col}"))

    query = f"""
        WITH latest AS (
            SELECT memory_id, MAX(computed_at) AS latest_computed_at
            FROM memory_affect
            GROUP BY memory_id
        )
        SELECT
            {", ".join(select_parts)},
            CASE
                WHEN a.computed_at = latest.latest_computed_at THEN 1
                ELSE 0
            END AS is_latest_for_memory
        FROM memories m
        LEFT JOIN memory_affect a ON a.memory_id = m.id
        LEFT JOIN latest ON latest.memory_id = m.id
    """
    if latest_only:
        query += " WHERE a.computed_at = latest.latest_computed_at OR a.id IS NULL"
    query += " ORDER BY m.created_at, m.id, a.computed_at"

    output: list[dict[str, Any]] = []
    for row in conn.execute(query):
        item = dict(row)
        raw_text = item.get("memory_raw_text")
        item["memory_raw_text_sha256"] = text_hash(raw_text)
        if redact_text:
            item["memory_raw_text"] = ""

        emotions = parse_emotions(item.get("affect_emotion_json"))
        item["dominant_emotion"] = dominant_emotion(emotions)
        item["emotion_labels"] = "|".join(sorted(k for k, v in emotions.items() if v > 0))
        item["emotion_label_count"] = sum(1 for v in emotions.values() if v > 0)
        item["tagged_at"] = (
            item.get("affect_tagged_at")
            or item.get("affect_computed_at")
            or item.get("memory_tagged_at")
        )
        item["tagger_model"] = (
            item.get("affect_tagger_model")
            or item.get("affect_model")
            or item.get("memory_tagger_model")
            or "NULL"
        )
        item["schema_version"] = (
            item.get("affect_schema_version")
            or item.get("memory_schema_version")
            or ""
        )
        item["memory_month"] = month_bucket(item.get("memory_created_at"))
        item["tagged_month"] = month_bucket(item.get("tagged_at"))
        item["memory_era"] = era_bucket(item.get("memory_created_at"), eras)
        item["tagger_era"] = era_bucket(item.get("tagged_at"), eras)
        item["has_affect"] = 1 if item.get("affect_id") is not None else 0
        output.append(item)
    return output


def snapshot_id(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        parts = [
            str(r.get("memory_id") or ""),
            str(r.get("memory_updated_at") or ""),
            str(r.get("affect_id") or ""),
            str(r.get("affect_computed_at") or ""),
            str(r.get("affect_model") or ""),
            str(r.get("schema_version") or ""),
        ]
        h.update("|".join(parts).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_drift(rows: list[dict[str, Any]], month_key: str) -> dict[str, dict[str, Any]]:
    drift: dict[str, dict[str, Any]] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        month = str(r.get(month_key) or "unknown")
        model = str(r.get("tagger_model") or "NULL")
        grouped[(month, model)].append(r)
    for (month, model), group in sorted(grouped.items()):
        key = f"{month} | {model}"
        g_val = [float(r["affect_valence"]) for r in group if r.get("affect_valence") is not None]
        g_aro = [float(r["affect_arousal"]) for r in group if r.get("affect_arousal") is not None]
        g_emo: Counter[str] = Counter()
        for r in group:
            if r.get("dominant_emotion"):
                g_emo[str(r["dominant_emotion"])] += 1
        drift[key] = {
            "count": len(group),
            "valence_mean": round(mean(g_val), 4) if g_val else None,
            "arousal_mean": round(mean(g_aro), 4) if g_aro else None,
            "top_dominant_emotions": top_counter(g_emo, 5),
        }
    return drift


def build_view_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valences = [float(r["affect_valence"]) for r in rows if r.get("affect_valence") is not None]
    arousals = [float(r["affect_arousal"]) for r in rows if r.get("affect_arousal") is not None]
    dominances = [float(r["affect_dominance"]) for r in rows if r.get("affect_dominance") is not None]

    emotion_counts: Counter[str] = Counter()
    dominant_counts: Counter[str] = Counter()
    for r in rows:
        for label in str(r.get("emotion_labels") or "").split("|"):
            if label:
                emotion_counts[label] += 1
        if r.get("dominant_emotion"):
            dominant_counts[str(r["dominant_emotion"])] += 1

    nulls = {}
    for key in [
        "memory_raw_text",
        "memory_created_at",
        "affect_id",
        "affect_valence",
        "affect_arousal",
        "affect_emotion_json",
        "tagged_at",
        "tagger_model",
        "schema_version",
    ]:
        nulls[key] = sum(1 for r in rows if r.get(key) in (None, ""))

    hints = {
        "low_affect_confidence": sum(
            1 for r in rows
            if r.get("affect_confidence") is not None and float(r["affect_confidence"]) < 0.5
        ),
        "counterweight_rows": sum(1 for r in rows if r.get("memory_counterweight_type")),
        "negative_high_arousal": sum(
            1 for r in rows
            if r.get("affect_valence") is not None
            and r.get("affect_arousal") is not None
            and float(r["affect_valence"]) <= -0.5
            and float(r["affect_arousal"]) >= 0.5
        ),
        "positive_high_arousal": sum(
            1 for r in rows
            if r.get("affect_valence") is not None
            and r.get("affect_arousal") is not None
            and float(r["affect_valence"]) >= 0.5
            and float(r["affect_arousal"]) >= 0.5
        ),
        "rows_with_multiple_emotion_labels": sum(
            1 for r in rows if int(r.get("emotion_label_count") or 0) > 1
        ),
    }

    return {
        "row_count": len(rows),
        "memory_count": len({r.get("memory_id") for r in rows}),
        "numeric": {
            "valence": numeric_summary(valences),
            "arousal": numeric_summary(arousals),
            "dominance": numeric_summary(dominances),
        },
        "nulls": nulls,
        "by_memory_type": counter(rows, "memory_memory_type"),
        "by_source_type": counter(rows, "memory_source_type"),
        "by_status": counter(rows, "memory_status"),
        "by_proposed_by": counter(rows, "memory_proposed_by"),
        "by_tagger_model": counter(rows, "tagger_model"),
        "by_schema_version": counter(rows, "schema_version"),
        "by_memory_era": counter(rows, "memory_era"),
        "by_tagger_era": counter(rows, "tagger_era"),
        "by_counterweight_type": counter(rows, "memory_counterweight_type"),
        "by_pattern_flag": counter(rows, "memory_pattern_flag"),
        "top_emotion_labels": top_counter(emotion_counts, 30),
        "top_dominant_emotions": top_counter(dominant_counts, 30),
        "tagger_drift_by_tagged_month": build_drift(rows, "tagged_month"),
        "tagger_drift_by_memory_month": build_drift(rows, "memory_month"),
        "gold_sampling_hints": hints,
    }


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest = [r for r in rows if int(r.get("is_latest_for_memory") or 0) == 1]
    all_view = build_view_report(rows)
    latest_view = build_view_report(latest)
    return {
        "all_annotations": all_view,
        "latest_annotations": latest_view,
        "comparison": {
            "all_annotation_rows": all_view["row_count"],
            "latest_annotation_rows": latest_view["row_count"],
            "memories": all_view["memory_count"],
            "extra_historical_annotations": all_view["row_count"] - latest_view["row_count"],
            "view_mean_valence_delta_latest_minus_all": (
                round(
                    latest_view["numeric"]["valence"].get("mean", 0)
                    - all_view["numeric"]["valence"].get("mean", 0),
                    4,
                )
                if all_view["numeric"]["valence"].get("count") and latest_view["numeric"]["valence"].get("count")
                else None
            ),
            "view_mean_arousal_delta_latest_minus_all": (
                round(
                    latest_view["numeric"]["arousal"].get("mean", 0)
                    - all_view["numeric"]["arousal"].get("mean", 0),
                    4,
                )
                if all_view["numeric"]["arousal"].get("count") and latest_view["numeric"]["arousal"].get("count")
                else None
            ),
        },
    }


def md_table(title: str, data: dict[str, Any], limit: int = 20) -> list[str]:
    lines = [f"## {title}", "", "| Value | Count |", "|---|---:|"]
    for key, value in list(data.items())[:limit]:
        lines.append(f"| {key} | {value} |")
    lines.append("")
    return lines


def write_drift_table(lines: list[str], title: str, drift: dict[str, dict[str, Any]]) -> None:
    lines.extend([
        f"## {title}",
        "",
        "| Month / Model | Count | Mean Valence | Mean Arousal | Top Dominant Emotions |",
        "|---|---:|---:|---:|---|",
    ])
    for key, value in drift.items():
        top = ", ".join(f"{k}:{v}" for k, v in value["top_dominant_emotions"].items())
        lines.append(
            f"| {key} | {value['count']} | {value['valence_mean']} | "
            f"{value['arousal_mean']} | {top} |"
        )
    lines.append("")


def append_view(lines: list[str], title: str, view: dict[str, Any]) -> list[str]:
    lines = [
        *lines,
        f"# {title}",
        "",
        f"- Rows: {view['row_count']}",
        f"- Memories: {view['memory_count']}",
        "",
        "## Numeric Labels",
        "",
        "```json",
        json.dumps(view["numeric"], indent=2, sort_keys=True),
        "```",
        "",
        "## Null / Missing Counts",
        "",
        "```json",
        json.dumps(view["nulls"], indent=2, sort_keys=True),
        "```",
        "",
    ]
    for section_title, key in [
        ("Memory Types", "by_memory_type"),
        ("Source Types", "by_source_type"),
        ("Statuses", "by_status"),
        ("Proposed By", "by_proposed_by"),
        ("Tagger Models", "by_tagger_model"),
        ("Schema Versions", "by_schema_version"),
        ("Memory Eras", "by_memory_era"),
        ("Tagger Eras", "by_tagger_era"),
        ("Counterweight Types", "by_counterweight_type"),
        ("Pattern Flags", "by_pattern_flag"),
        ("Top Emotion Labels", "top_emotion_labels"),
        ("Top Dominant Emotions", "top_dominant_emotions"),
    ]:
        lines.extend(md_table(section_title, view[key]))

    lines.extend([
        "## Gold-Set Sampling Hints",
        "",
        "These are counts only. Use them to decide which strata to oversample",
        "after inspecting the distribution together.",
        "",
        "```json",
        json.dumps(view["gold_sampling_hints"], indent=2, sort_keys=True),
        "```",
        "",
    ])
    write_drift_table(lines, "Tagger Drift By Tagged Month", view["tagger_drift_by_tagged_month"])
    write_drift_table(lines, "Tagger Drift By Memory Month", view["tagger_drift_by_memory_month"])
    return lines


def write_markdown_report(path: Path, report: dict[str, Any], manifest: dict[str, Any]) -> None:
    lines = [
        "# CAMA Affect Silver-Label Distribution Report",
        "",
        f"- Generated at: {manifest['generated_at']}",
        f"- Snapshot ID: `{manifest['snapshot_id']}`",
        f"- CSV rows: {manifest['row_count']}",
        f"- Memories: {manifest['memory_count']}",
        f"- Text redacted: {manifest['text_redacted']}",
        f"- Era config: `{manifest['era_config']}`",
        "",
        "## View Comparison",
        "",
        "```json",
        json.dumps(report["comparison"], indent=2, sort_keys=True),
        "```",
        "",
    ]
    lines = append_view(lines, "All Annotations View (Drift Lens)", report["all_annotations"])
    lines = append_view(lines, "Latest Annotation View (Training Lens)", report["latest_annotations"])
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export CAMA affect silver labels with provenance diagnostics.",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to CAMA SQLite DB.")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="Output root directory.")
    parser.add_argument(
        "--era-config",
        default=str(DEFAULT_ERA_CONFIG),
        help="JSON file defining inferred era buckets.",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Export only the latest affect annotation per memory.",
    )
    parser.add_argument(
        "--redact-text",
        action="store_true",
        help="Omit raw_text from the CSV while preserving raw_text_sha256.",
    )
    args = parser.parse_args(argv)
    eras = load_era_config(args.era_config)

    conn = open_db(args.db)
    try:
        rows = fetch_rows(conn, latest_only=args.latest_only, redact_text=args.redact_text, eras=eras)
    finally:
        conn.close()

    sid = snapshot_id(rows)
    out_dir = Path(args.out_root) / f"affect_snapshot_{safe_ts()}_{sid}"
    out_dir.mkdir(parents=True, exist_ok=False)

    manifest = {
        "generated_at": utc_now(),
        "snapshot_id": sid,
        "db_path": os.path.abspath(os.path.expanduser(args.db)),
        "latest_only": bool(args.latest_only),
        "text_redacted": bool(args.redact_text),
        "row_count": len(rows),
        "memory_count": len({r.get("memory_id") for r in rows}),
        "era_config": os.path.abspath(os.path.expanduser(args.era_config)),
        "files": {
            "csv": "affect_silver_labels.csv",
            "report_json": "affect_distribution_report.json",
            "report_md": "affect_distribution_report.md",
            "manifest": "manifest.json",
        },
    }
    report = build_report(rows)

    write_csv(out_dir / "affect_silver_labels.csv", rows)
    (out_dir / "affect_distribution_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_markdown_report(out_dir / "affect_distribution_report.md", report, manifest)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"Wrote CAMA affect export: {out_dir}")
    print(f"Rows: {manifest['row_count']} | Memories: {manifest['memory_count']}")
    if not args.redact_text:
        print("Warning: CSV includes raw memory text. Keep this directory private.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
