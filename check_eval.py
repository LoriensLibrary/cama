import sqlite3, os
c = sqlite3.connect(os.path.expanduser('~/.cama/memory.db'), timeout=5)
rows = c.execute("SELECT router_name, COUNT(*) AS n FROM eval_results WHERE run_label='phase2_eval_apr29' GROUP BY router_name").fetchall()
print(rows)
c.close()