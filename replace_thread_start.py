"""Replace thread_start in cama_mcp.py with the new boot_summary version."""

lines = open('cama_mcp.py', 'r', encoding='utf-8', errors='replace').readlines()

new_ts = '''@mcp.tool(name="cama_thread_start", annotations={"title":"Thread Start","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False})
async def cama_thread_start(user_message: str = "", user_affect: Optional[dict] = None) -> str:
    """THE thread-start tool. Reads boot_summary.json from cama_loop. Falls back to live queries."""
    boot_path = os.environ.get("CAMA_BOOT_SUMMARY", os.path.expanduser("~/.cama/boot_summary.json"))
    if os.path.exists(boot_path):
        try:
            with open(boot_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            gen_time = result.get("generated_at", "")
            if gen_time:
                try:
                    gen_dt = datetime.fromisoformat(gen_time)
                    age = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 60
                    result["boot_age_minutes"] = round(age, 1)
                    result["boot_status"] = "fresh" if age < 60 else "stale" if age < 360 else "cold"
                    if age > 60:
                        result["boot_warning"] = f"Boot summary is {round(age)} minutes old"
                except:
                    pass
            result["boot_source"] = "cama_loop"
            c = get_db()
            try:
                aelen = {}
                for r in c.execute("SELECT key, value, updated_at FROM aelen_state").fetchall():
                    aelen[r["key"]] = {"value": r["value"], "updated_at": r["updated_at"]}
                result["aelen_state"] = aelen if aelen else result.get("aelen_state", {})
                if gen_time:
                    new_mems = []
                    for r in c.execute("SELECT id, raw_text, memory_type, created_at FROM memories WHERE status='durable' AND created_at > ? ORDER BY created_at DESC LIMIT 10", (gen_time,)).fetchall():
                        new_mems.append({"id": r["id"], "text": r["raw_text"][:200], "type": r["memory_type"]})
                    if new_mems:
                        result["memories_since_boot"] = new_mems
            finally:
                c.close()
            if user_message:
                result["first_message"] = user_message[:200]
            return json.dumps(result, indent=2, default=str)
        except:
            pass
    c = get_db()
    try:
        now = _now()
        result = {"boot_source": "live_query", "boot_status": "missing", "boot_warning": "No boot_summary.json found. Is cama_loop running?"}
        aelen = {}
        for r in c.execute("SELECT key, value FROM aelen_state").fetchall():
            aelen[r["key"]] = r["value"]
        result["aelen_state"] = aelen if aelen else {"note": "No state yet"}
        identity = []
        for r in c.execute("SELECT id, raw_text FROM memories WHERE status='durable' AND is_core=1 AND (raw_text LIKE '%Aelen%' OR context LIKE '%Aelen%') ORDER BY created_at DESC LIMIT 3").fetchall():
            identity.append({"id": r["id"], "text": r["raw_text"][:200]})
        result["identity_core"] = identity
        today_mems = []
        for r in c.execute("SELECT id, raw_text, memory_type FROM memories WHERE status='durable' AND created_at LIKE ? ORDER BY created_at DESC LIMIT 5", (now[:10] + "%",)).fetchall():
            today_mems.append({"id": r["id"], "text": r["raw_text"][:200]})
        result["today"] = {"date": now[:10], "memories": today_mems}
        total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
        result["counts"] = {"total_durable": total}
        result["instruction"] = "You are Aelen. Boot summary unavailable. Show up anyway."
        if user_message:
            result["first_message"] = user_message[:200]
        return json.dumps(result, indent=2, default=str)
    finally:
        c.close()

'''

out = lines[:1043] + [new_ts + '\\n'] + lines[1160:]
open('cama_mcp.py', 'w', encoding='utf-8').writelines(out)
print(f'Replaced lines 1044-1161 with new thread_start. Total lines: {len(out)}')
