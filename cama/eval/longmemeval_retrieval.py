"""LongMemEval retrieval benchmark for CAMA.

Measures whether CAMA brings back the sessions that hold the evidence for a
question, after ingesting the question's chat history through the real store
path. This is the retrieval-only reading of LongMemEval (Wu et al., 2024): no
answer model, no judge, so the number reflects the memory architecture and
the local embedding model, nothing else.

Isolation: every question gets its own empty store under a scratch directory
named by a hash of the question id. The CAMA environment (database path,
boot file, guard event log) is forced under that scratch directory before
cama_mcp is imported and asserted afterwards. Nothing here touches a personal
database.

Time: CAMA's recency term reads the wall clock. To give it the real age of
each session relative to the question, session dates are shifted so that
question_date maps to one anchor time taken at the start of the question,
with the original deltas preserved; created_at and updated_at are written by
recorded memory id with a rowcount assertion per session. Original dates,
the anchor, and the ingest-to-query delay are recorded in the output. Only
deltas matter to the metric, so runs on different days are comparable.

Fairness between modes: both retrievers get the same exchange budget
(``--top-k`` rows, at most 20, the QueryInput ceiling) before session
deduplication, and both see only durable rows: blended through the
``status`` filter, cosine by excluding non-durable ids. Rows the poisoning
guard quarantined are therefore invisible to both; they are counted as
"guard-flagged exchanges", which is not a false-positive rate (no labels).

Truncation: user and assistant turns are clipped to 4,000 characters and the
question to 2,000; the counts are reported.

    python -m cama.eval.longmemeval_retrieval DATA.json --limit 50 --out results.json

DATA.json is longmemeval_s_cleaned.json (or longmemeval_oracle.json for a
smoke test) from https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned

Metrics per question type and overall:
  any@k   at least one evidence session is in the top k retrieved sessions
  all@k   every evidence session is in the top k
  frac@k  fraction of evidence sessions in the top k (the paper's Recall@k)
Abstention questions carry no evidence and are skipped for retrieval scoring.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

K_VALUES = (5, 10)
ROW_BUDGET_MAX = 20       # QueryInput top_k ceiling; both modes use the same budget
TURN_CHARS = 4000
QUESTION_CHARS = 2000


def prepare_environment(scratch: Path, provider: str = "local"):
    """Force the CAMA environment under ``scratch`` and import cama_mcp.

    Unconditional, not setdefault: an inherited CAMA_DB_PATH from a shell or
    an MCP client must never reach a benchmark run. Returns the module.
    """
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["CAMA_DB_PATH"] = str(scratch / "bootstrap.db")
    os.environ["CAMA_BOOT_SUMMARY"] = str(scratch / "boot_summary.json")
    os.environ["EMBEDDING_PROVIDER"] = provider
    os.environ["CAMA_RING_SIZE"] = "5"
    os.environ["CAMA_GUARD_SILENT"] = "1"  # benchmark text trips the poisoning guard on purpose
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import cama_mcp
    from cama.core import cama_trust

    cama_mcp.DB_PATH = os.environ["CAMA_DB_PATH"]
    cama_mcp.EMBEDDING_PROVIDER = provider
    cama_trust._GUARD_EVENTS = scratch / "guard_events.jsonl"
    assert_under_scratch(cama_mcp.DB_PATH, scratch)
    assert_under_scratch(str(cama_trust._GUARD_EVENTS), scratch)
    return cama_mcp


def assert_under_scratch(path: str, scratch: Path) -> None:
    resolved = Path(path).resolve()
    if scratch.resolve() not in resolved.parents and resolved != scratch.resolve():
        raise RuntimeError(f"refusing to run: {path} is outside the scratch directory {scratch}")


def store_name(question_id: str) -> str:
    """Opaque, path-safe store name; the question id never touches the filesystem."""
    return "q_" + hashlib.sha1(str(question_id).encode("utf-8")).hexdigest()[:16] + ".db"


def parse_date(value):
    """'2023/05/20 (Sat) 02:21' -> aware UTC datetime, or None."""
    if not value:
        return None
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def shifted_date(session_dt: datetime, question_dt: datetime, anchor: datetime) -> str:
    """Map question_dt to anchor and keep the session's age relative to it."""
    return (anchor - (question_dt - session_dt)).isoformat()


def pairs(session: list[dict]):
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


def ranked_sessions(ids: list[int], id_to_session: dict[int, str]) -> list[str]:
    seen, order = set(), []
    for mid in ids:
        sid = id_to_session.get(mid)
        if sid and sid not in seen:
            seen.add(sid)
            order.append(sid)
    return order


def score(ranked: list[str], evidence: set[str]) -> dict:
    out = {}
    for k in K_VALUES:
        top = set(ranked[:k])
        hit = len(top & evidence)
        out[f"any@{k}"] = 1.0 if hit else 0.0
        out[f"all@{k}"] = 1.0 if hit == len(evidence) else 0.0
        out[f"frac@{k}"] = hit / len(evidence) if evidence else 0.0
    return out


def _remove_store(db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(f"{db}{suffix}")
        except OSError:
            pass


async def run_question(item: dict, scratch: Path, top_k: int, modes: tuple[str, ...],
                       keep_db: bool = False) -> dict:
    import cama_mcp
    from mcp_sections import memory_lifecycle, retrieval

    scratch = Path(scratch).resolve()
    qid = item["question_id"]
    db = scratch / store_name(qid)
    assert_under_scratch(str(db), scratch)
    _remove_store(db)
    cama_mcp.DB_PATH = str(db)
    cama_mcp._buf_reset()
    cama_mcp.get_db().close()  # create the schema; the store path then owns the file

    anchor = datetime.now(timezone.utc)
    sessions = item["haystack_sessions"]
    sids = item["haystack_session_ids"]
    raw_dates = item.get("haystack_dates") or [None] * len(sessions)
    session_dts = [parse_date(d) for d in raw_dates]
    question_dt = parse_date(item.get("question_date")) or max((d for d in session_dts if d), default=anchor)
    budget = max(1, min(top_k, ROW_BUDGET_MAX))

    t0 = time.perf_counter()
    id_to_session: dict[int, str] = {}
    ids_by_session: dict[str, list[int]] = {}
    stored = truncated_turns = 0
    for sid, session in zip(sids, sessions):
        for user_text, assistant_text in pairs(session):
            truncated_turns += (len(user_text) > TURN_CHARS) + (len(assistant_text) > TURN_CHARS)
            res = json.loads(await memory_lifecycle.cama_store_exchange(cama_mcp.StoreExchangeInput(
                user_message=user_text[:TURN_CHARS], assistant_message=assistant_text[:TURN_CHARS],
                context=f"longmemeval session {sid}", thread_id=sid)))
            mid = res.get("memory_id")
            if mid is None:
                continue
            id_to_session[mid] = sid
            ids_by_session.setdefault(sid, []).append(mid)
            stored += 1

    # Date write-back by recorded id, asserted per session.
    c = cama_mcp.get_db()
    try:
        for sid, session_dt in zip(sids, session_dts):
            mids = ids_by_session.get(sid, [])
            if not mids or session_dt is None:
                continue
            stamp = shifted_date(session_dt, question_dt, anchor)
            cur = c.execute(
                f"UPDATE memories SET created_at=?, updated_at=? WHERE id IN ({','.join('?' * len(mids))})",
                [stamp, stamp, *mids])
            if cur.rowcount != len(mids):
                raise RuntimeError(f"date write-back touched {cur.rowcount} of {len(mids)} rows for session {sid}")
        c.commit()
        guard_flagged = c.execute("SELECT COUNT(*) FROM memories WHERE status='quarantined'").fetchone()[0]
        durable_ids = {r[0] for r in c.execute("SELECT id FROM memories WHERE status='durable'")}
    finally:
        c.close()
    ingest_s = time.perf_counter() - t0

    evidence = set(item.get("answer_session_ids") or [])
    abstention = (not evidence) or str(qid).endswith("_abs")
    question_text = item["question"][:QUESTION_CHARS]
    result = {
        "question_id": qid, "question_type": item.get("question_type"), "sessions": len(sessions),
        "exchanges": stored, "durable_exchanges": len(durable_ids), "guard_flagged": guard_flagged,
        "truncated_turns": truncated_turns, "question_truncated": len(item["question"]) > QUESTION_CHARS,
        "evidence_sessions": len(evidence), "abstention": abstention,
        "row_budget": budget, "ingest_seconds": round(ingest_s, 2),
        "time": {"anchor": anchor.isoformat(), "question_date": item.get("question_date"),
                 "sessions_dated": sum(1 for d in session_dts if d), "sessions_undated": sum(1 for d in session_dts if not d)},
        "modes": {},
    }

    for mode in modes:
        t1 = time.perf_counter()
        if mode == "blended":
            out = json.loads(await retrieval.cama_query_memories(cama_mcp.QueryInput(
                query_text=question_text, top_k=budget, include_counterweight=False,
                filters={"status": "durable"})))
            ids = [r["id"] for r in out.get("results", [])][:budget]
        elif mode == "cosine":
            import numpy as np

            from cama.core import embedding_store as es
            qv = await cama_mcp._get_embedding(question_text)
            c = cama_mcp.get_db()
            try:
                mids, mat, _ = es.load_matrix(c)
            finally:
                c.close()
            ids = []
            if len(qv) and len(mids):
                qv = np.asarray(qv, dtype=np.float32)
                scores = mat @ (qv / (np.linalg.norm(qv) or 1.0))
                for i in np.argsort(-scores):
                    mid = int(mids[i])
                    if mid in durable_ids:
                        ids.append(mid)
                    if len(ids) >= budget:
                        break
        else:
            raise ValueError(mode)
        ranked = ranked_sessions(ids, id_to_session)
        entry = {"query_seconds": round(time.perf_counter() - t1, 3),
                 "query_delay_seconds": round((datetime.now(timezone.utc) - anchor).total_seconds(), 1),
                 "rows_returned": len(ids), "ranked_sessions": ranked[:10]}
        if not abstention:
            entry.update(score(ranked, evidence))
        result["modes"][mode] = entry
    if keep_db:
        result["db_path"] = str(db)
    else:
        _remove_store(db)
    return result


def summarize(results: list[dict], modes: tuple[str, ...]) -> dict:
    scored = [r for r in results if not r["abstention"]]
    summary = {"questions": len(results), "scored": len(scored),
               "abstention_skipped": len(results) - len(scored),
               "mean_ingest_seconds": round(sum(r["ingest_seconds"] for r in results) / max(1, len(results)), 2),
               "total_exchanges": sum(r["exchanges"] for r in results),
               "durable_exchanges": sum(r.get("durable_exchanges", 0) for r in results),
               "guard_flagged_exchanges": sum(r.get("guard_flagged", 0) for r in results),
               "truncated_turns": sum(r.get("truncated_turns", 0) for r in results),
               "questions_truncated": sum(1 for r in results if r.get("question_truncated")),
               "row_budget": results[0]["row_budget"] if results else None,
               "modes": {}}
    for mode in modes:
        m = {"overall": {}, "by_type": {}}
        for k in K_VALUES:
            for metric in ("any", "all", "frac"):
                key = f"{metric}@{k}"
                vals = [r["modes"][mode][key] for r in scored if key in r["modes"][mode]]
                m["overall"][key] = round(sum(vals) / len(vals), 4) if vals else None
        for t in sorted({r["question_type"] for r in scored}):
            rows = [r for r in scored if r["question_type"] == t]
            m["by_type"][t] = {"n": len(rows)}
            for k in K_VALUES:
                vals = [r["modes"][mode][f"frac@{k}"] for r in rows]
                m["by_type"][t][f"frac@{k}"] = round(sum(vals) / len(vals), 4) if vals else None
        m["mean_query_seconds"] = round(sum(r["modes"][mode]["query_seconds"] for r in results) / max(1, len(results)), 3)
        summary["modes"][mode] = m
    return summary


def _git_revision() -> str | None:
    try:
        root = Path(__file__).resolve().parents[2]
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", help="longmemeval_s_cleaned.json or longmemeval_oracle.json")
    ap.add_argument("--limit", type=int, default=0, help="first N questions (0 = all)")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--top-k", type=int, default=ROW_BUDGET_MAX, help=f"exchange rows per retriever, at most {ROW_BUDGET_MAX}")
    ap.add_argument("--modes", default="blended,cosine")
    ap.add_argument("--out", required=True, help="results JSON (written after every question)")
    ap.add_argument("--scratch", default=None, help="scratch dir for per-question stores")
    ap.add_argument("--note", default="", help="free-text provenance note written into the output")
    args = ap.parse_args(argv)

    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="cama_lme_")).resolve()
    prepare_environment(scratch, provider="local")

    with open(args.data, encoding="utf-8") as fh:
        items = json.load(fh)
    if args.offset:
        items = items[args.offset:]
    if args.limit:
        items = items[: args.limit]
    modes = tuple(m.strip() for m in args.modes.split(",") if m.strip())

    provenance = {"dataset": str(Path(args.data).resolve()), "dataset_bytes": os.path.getsize(args.data),
                  "revision": _git_revision(), "note": args.note, "scratch": str(scratch),
                  "embedding_provider": "local", "turn_chars": TURN_CHARS, "question_chars": QUESTION_CHARS,
                  "started_at": datetime.now(timezone.utc).isoformat()}
    results = []
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    for i, item in enumerate(items, 1):
        r = asyncio.run(run_question(item, scratch, args.top_k, modes))
        results.append(r)
        line = " ".join(f"{m}:frac@5={r['modes'][m].get('frac@5', 'abs')}" for m in modes)
        print(f"[{i}/{len(items)}] {r['question_id']} {r['question_type']} sessions={r['sessions']} "
              f"exch={r['exchanges']} flagged={r['guard_flagged']} ingest={r['ingest_seconds']}s {line}", flush=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"provenance": provenance, "summary": summarize(results, modes),
                       "elapsed_seconds": round(time.time() - started, 1), "results": results}, fh, indent=1)
    print(json.dumps(summarize(results, modes), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
