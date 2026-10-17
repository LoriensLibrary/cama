"""LongMemEval retrieval benchmark for CAMA.

Measures whether CAMA brings back the sessions that hold the evidence for a
question, after ingesting the question's whole chat history through the real
store path. This is the retrieval-only reading of LongMemEval (Wu et al.,
2024): no answer model, no judge, so the number reflects the memory
architecture and the local embedding model, nothing else.

Each question gets its own empty store in a scratch directory. Nothing here
touches a personal database.

    python -m cama.eval.longmemeval_retrieval DATA.json --limit 50 --out results.json

DATA.json is longmemeval_s_cleaned.json (or _oracle.json for a smoke test)
from https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned

Metrics per question type and overall:
  any@k   at least one evidence session is in the top k retrieved sessions
  all@k   every evidence session is in the top k
  frac@k  fraction of evidence sessions in the top k (the paper's Recall@k)
Abstention questions carry no evidence and are skipped for retrieval scoring.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

K_VALUES = (5, 10)


def _env_defaults(scratch: Path) -> None:
    os.environ.setdefault("CAMA_DB_PATH", str(scratch / "bootstrap.db"))
    os.environ.setdefault("CAMA_BOOT_SUMMARY", str(scratch / "boot_summary.json"))
    os.environ.setdefault("EMBEDDING_PROVIDER", "local")
    os.environ.setdefault("CAMA_RING_SIZE", "5")


def _parse_date(value: str) -> str:
    """'2023/05/20 (Sat) 02:21' -> ISO 8601 UTC. Unknown formats pass through."""
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            continue
    return value


def _pairs(session: list[dict]):
    """Yield (user_text, assistant_text) pairs in order; unpaired turns stand alone."""
    pending_user = None
    for turn in session:
        role, content = turn.get("role"), (turn.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            if pending_user is not None:
                yield pending_user, "(no reply recorded)"
            pending_user = content
        else:
            yield (pending_user or "(no user turn recorded)"), content
            pending_user = None
    if pending_user is not None:
        yield pending_user, "(no reply recorded)"


def _ranked_sessions(ids: list[int], id_to_session: dict[int, str]) -> list[str]:
    seen, order = set(), []
    for mid in ids:
        sid = id_to_session.get(mid)
        if sid and sid not in seen:
            seen.add(sid)
            order.append(sid)
    return order


def _score(ranked: list[str], evidence: set[str]) -> dict:
    out = {}
    for k in K_VALUES:
        top = set(ranked[:k])
        hit = len(top & evidence)
        out[f"any@{k}"] = 1.0 if hit else 0.0
        out[f"all@{k}"] = 1.0 if hit == len(evidence) else 0.0
        out[f"frac@{k}"] = hit / len(evidence) if evidence else 0.0
    return out


async def run_question(item: dict, scratch: Path, top_k: int, modes: tuple[str, ...]) -> dict:
    import cama_mcp
    from mcp_sections import memory_lifecycle, retrieval

    qid = item["question_id"]
    db = scratch / f"{qid}.db"
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(f"{db}{suffix}")
        except OSError:
            pass
    cama_mcp.DB_PATH = str(db)
    cama_mcp._buf_reset()
    cama_mcp.get_db().close()  # create the schema; the store path then owns the file
    t0 = time.perf_counter()
    id_to_session: dict[int, str] = {}
    sessions = item["haystack_sessions"]
    sids = item["haystack_session_ids"]
    dates = item.get("haystack_dates") or [None] * len(sessions)
    stored = 0
    session_dates: list[tuple[str, str]] = []
    for sid, date, session in zip(sids, dates, sessions):
        first_id = None
        for user_text, assistant_text in _pairs(session):
            res = json.loads(await memory_lifecycle.cama_store_exchange(cama_mcp.StoreExchangeInput(
                user_message=user_text[:4000], assistant_message=assistant_text[:4000],
                context=f"longmemeval session {sid}", thread_id=sid)))
            mid = res.get("memory_id")
            if mid is None:
                continue
            id_to_session[mid] = sid
            first_id = first_id if first_id is not None else mid
            stored += 1
        if date and first_id is not None:
            session_dates.append((_parse_date(date), f"longmemeval session {sid}"))
    c = cama_mcp.get_db()
    try:
        c.executemany("UPDATE memories SET created_at=? WHERE context=?", session_dates)
        c.commit()
    finally:
        c.close()
    ingest_s = time.perf_counter() - t0

    evidence = set(item.get("answer_session_ids") or [])
    abstention = (not evidence) or str(qid).endswith("_abs")
    result = {"question_id": qid, "question_type": item.get("question_type"), "sessions": len(sessions),
              "exchanges": stored, "evidence_sessions": len(evidence), "abstention": abstention,
              "ingest_seconds": round(ingest_s, 2), "modes": {}}

    for mode in modes:
        t1 = time.perf_counter()
        if mode == "blended":
            out = json.loads(await retrieval.cama_query_memories(cama_mcp.QueryInput(
                query_text=item["question"][:2000], top_k=min(top_k, 20), include_counterweight=False)))
            ids = [r["id"] for r in out.get("results", [])]
        elif mode == "cosine":
            import numpy as np

            from cama.core import embedding_store as es
            qv = await cama_mcp._get_embedding(item["question"][:2000])
            c = cama_mcp.get_db()
            try:
                mids, mat, _ = es.load_matrix(c)
            finally:
                c.close()
            qv = np.asarray(qv, dtype=np.float32)
            scores = mat @ (qv / (np.linalg.norm(qv) or 1.0))
            order = np.argsort(-scores)[: top_k * 4]
            ids = [int(mids[i]) for i in order]
        else:
            raise ValueError(mode)
        ranked = _ranked_sessions(ids, id_to_session)
        entry = {"query_seconds": round(time.perf_counter() - t1, 3), "ranked_sessions": ranked[:10]}
        if not abstention:
            entry.update(_score(ranked, evidence))
        result["modes"][mode] = entry
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(f"{db}{suffix}")
        except OSError:
            pass
    return result


def summarize(results: list[dict], modes: tuple[str, ...]) -> dict:
    scored = [r for r in results if not r["abstention"]]
    summary = {"questions": len(results), "scored": len(scored),
               "abstention_skipped": len(results) - len(scored),
               "mean_ingest_seconds": round(sum(r["ingest_seconds"] for r in results) / max(1, len(results)), 2),
               "modes": {}}
    for mode in modes:
        m = {"overall": {}, "by_type": {}}
        for k in K_VALUES:
            for metric in ("any", "all", "frac"):
                key = f"{metric}@{k}"
                vals = [r["modes"][mode][key] for r in scored if key in r["modes"][mode]]
                m["overall"][key] = round(sum(vals) / len(vals), 4) if vals else None
        types = sorted({r["question_type"] for r in scored})
        for t in types:
            rows = [r for r in scored if r["question_type"] == t]
            m["by_type"][t] = {"n": len(rows)}
            for k in K_VALUES:
                vals = [r["modes"][mode][f"frac@{k}"] for r in rows]
                m["by_type"][t][f"frac@{k}"] = round(sum(vals) / len(vals), 4) if vals else None
        m["mean_query_seconds"] = round(sum(r["modes"][mode]["query_seconds"] for r in results) / max(1, len(results)), 3)
        summary["modes"][mode] = m
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", help="longmemeval_s_cleaned.json or longmemeval_oracle.json")
    ap.add_argument("--limit", type=int, default=0, help="first N questions (0 = all)")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--modes", default="blended,cosine")
    ap.add_argument("--out", required=True, help="results JSON (written after every question)")
    ap.add_argument("--scratch", default=None, help="scratch dir for per-question stores")
    args = ap.parse_args(argv)

    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="cama_lme_"))
    scratch.mkdir(parents=True, exist_ok=True)
    _env_defaults(scratch)
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    with open(args.data, encoding="utf-8") as fh:
        items = json.load(fh)
    if args.offset:
        items = items[args.offset:]
    if args.limit:
        items = items[: args.limit]
    modes = tuple(m.strip() for m in args.modes.split(",") if m.strip())

    results = []
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    for i, item in enumerate(items, 1):
        r = asyncio.run(run_question(item, scratch, args.top_k, modes))
        results.append(r)
        line = " ".join(f"{m}:frac@5={r['modes'][m].get('frac@5', 'abs')}" for m in modes)
        print(f"[{i}/{len(items)}] {r['question_id']} {r['question_type']} sessions={r['sessions']} "
              f"exch={r['exchanges']} ingest={r['ingest_seconds']}s {line}", flush=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"summary": summarize(results, modes), "elapsed_seconds": round(time.time() - started, 1),
                       "results": results}, fh, indent=1)
    print(json.dumps(summarize(results, modes), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
