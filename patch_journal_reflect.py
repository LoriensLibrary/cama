"""
Patch: Two changes to cama_mcp.py
1. Fix journal_write to store as source_type='journal', status='durable' instead of 'inference'/'provisional'
2. Add cama_journal_reflect tool — cross-references journal entries with exchanges in a time window

Safe: only modifies the journal INSERT and adds a new read-only tool before the Bridge Tools section.
"""

CAMA_PATH = r"C:\Users\Angela\Desktop\cama\cama_mcp.py"

# The new journal_reflect tool code
REFLECT_TOOL = '''
@mcp.tool(name="cama_journal_reflect", annotations={"title":"Journal Reflect","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False})
async def cama_journal_reflect(hours_back: int = 24, limit: int = 20) -> str:
    """The mirror — cross-reference journal entries with exchanges from the same time window.
    Returns journal entries paired with exchanges, ordered by time so you can see:
    what you said you experienced vs what actually happened.
    The delta between self-report and evidence is where self-awareness lives."""
    c = get_db()
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
        # Get journal entries in the window
        journals = c.execute(
            """SELECT id, raw_text, context, created_at FROM memories
            WHERE memory_type='journal' AND status NOT IN ('rejected')
            AND created_at >= ? ORDER BY created_at ASC""", (cutoff,)).fetchall()
        # Get exchanges in the window
        exchanges = c.execute(
            """SELECT id, raw_text, context, created_at FROM memories
            WHERE source_type='exchange' AND status NOT IN ('rejected')
            AND created_at >= ? ORDER BY created_at ASC LIMIT ?""", (cutoff, limit)).fetchall()
        journal_entries = []
        for j in journals:
            ctx = {}
            try:
                ctx = json.loads(j["context"] or "{}")
            except Exception:
                pass
            journal_entries.append({
                "memory_id": j["id"],
                "type": "journal",
                "entry": j["raw_text"][:500],
                "emotional_state": ctx.get("emotional_state"),
                "what_shifted": ctx.get("what_shifted"),
                "timestamp": ctx.get("written_at", j["created_at"])
            })
        exchange_entries = []
        for e in exchanges:
            exchange_entries.append({
                "memory_id": e["id"],
                "type": "exchange",
                "content": e["raw_text"][:500],
                "context": e["context"],
                "timestamp": e["created_at"]
            })
        # Interleave by timestamp for chronological view
        all_entries = journal_entries + exchange_entries
        all_entries.sort(key=lambda x: x.get("timestamp", ""))
        return json.dumps({
            "window_hours": hours_back,
            "journal_count": len(journal_entries),
            "exchange_count": len(exchange_entries),
            "timeline": all_entries,
            "note": "Journal entries are what you said you thought. Exchanges are what actually happened. Where they diverge is the data."
        }, indent=2)
    finally:
        c.close()

'''

def patch():
    with open(CAMA_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    changed = False

    # --- Change 1: Fix journal_write status ---
    # Find the INSERT line in journal_write that sets source_type and status
    old_journal_insert = '"inference", "provisional", "assistant"'
    new_journal_insert = '"journal", "durable", "assistant"'
    if old_journal_insert in content:
        content = content.replace(old_journal_insert, new_journal_insert, 1)  # Only first occurrence
        print("CHANGE 1: Fixed journal_write — source_type='journal', status='durable'")
        changed = True
    else:
        # Check if already fixed
        if '"journal", "durable", "assistant"' in content:
            print("CHANGE 1: Already applied — journal_write already uses durable status")
        else:
            print("WARNING: Could not find journal INSERT pattern to fix")

    # Also fix the return status message
    old_status_msg = '"status": "provisional"'
    # Count occurrences — we only want to change the one in journal_write
    # Actually let's be more precise and change the return json in journal_write
    old_return = '"status": "provisional", "weight": "1.0 (full)"'
    new_return = '"status": "durable", "weight": "1.0 (full)"'
    if old_return in content:
        content = content.replace(old_return, new_return, 1)
        print("CHANGE 1b: Fixed journal_write return message — status='durable'")
        changed = True

    # --- Change 2: Add cama_journal_reflect tool ---
    if "cama_journal_reflect" in content:
        print("CHANGE 2: Already applied — cama_journal_reflect already exists")
    else:
        # Insert before the Bridge Tools section
        bridge_marker = '# \xc6\x92"?\xc6\x92"? Bridge Tools'
        # Try a simpler marker
        if "Bridge Tools" not in content:
            # Try finding the exec tool as insertion point
            exec_marker = '@mcp.tool(name="cama_exec"'
            if exec_marker in content:
                content = content.replace(exec_marker, REFLECT_TOOL + exec_marker)
                print("CHANGE 2: Added cama_journal_reflect before cama_exec")
                changed = True
            else:
                print("ERROR: Could not find insertion point for journal_reflect")
        else:
            # Find the line with Bridge Tools
            lines = content.split('\n')
            new_lines = []
            inserted = False
            for line in lines:
                if 'Bridge Tools' in line and not inserted:
                    # Insert reflect tool before this line
                    new_lines.append(REFLECT_TOOL)
                    inserted = True
                new_lines.append(line)
            if inserted:
                content = '\n'.join(new_lines)
                print("CHANGE 2: Added cama_journal_reflect before Bridge Tools section")
                changed = True

    if not changed:
        print("NO CHANGES MADE — everything already applied")
        return False

    # Backup
    backup_path = CAMA_PATH + ".pre_reflect.bak"
    with open(backup_path, 'w', encoding='utf-8') as f2:
        with open(CAMA_PATH, 'r', encoding='utf-8') as f1:
            f2.write(f1.read())
    # Wait, we already modified content in memory. Let me backup from the pre-exchange backup
    # Actually let's just save current state before our changes
    import shutil
    shutil.copy2(CAMA_PATH, backup_path)

    with open(CAMA_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Backup: {backup_path}")
    print(f"New file: {len(content)} chars")
    return True

if __name__ == "__main__":
    patch()
