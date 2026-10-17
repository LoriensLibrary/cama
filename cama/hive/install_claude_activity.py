"""Install metadata-only activity hooks without replacing existing Claude hooks."""
import copy
import json
import shutil
import sys
import time
from pathlib import Path

EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionStart",
          "UserPromptSubmit", "Stop", "SessionEnd")


def configure(settings, command):
    result = copy.deepcopy(settings)
    hooks = result.setdefault("hooks", {})
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        existing = [h for g in groups for h in g.get("hooks", [])
                    if h.get("command", "").replace("\\", "/") == command.replace("\\", "/")]
        if existing:
            for handler in existing:
                handler["command"] = command
            continue
        groups.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
    return result


def main():
    root = Path.home() / ".claude"
    settings_path = root / "settings.json"
    original = settings_path.read_text(encoding="utf-8-sig")
    settings = json.loads(original)
    runtime = root / "cama_activity"
    command = f'"{Path(sys.executable).as_posix()}" "{(runtime / "claude_activity_hook.py").as_posix()}"'
    updated = configure(settings, command)
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("activity.py", "claude_activity_hook.py"):
        shutil.copy2(Path(__file__).with_name(name), runtime / name)
    if updated != settings:
        backup = settings_path.with_name(f"settings.json.cama-backup-{time.time_ns()}")
        shutil.copy2(settings_path, backup)
        temporary = settings_path.with_name("settings.json.cama-tmp")
        temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        temporary.replace(settings_path)
        print(f"Installed activity hooks; backup: {backup}")
    else:
        print("Activity hooks already configured; runtime refreshed")


if __name__ == "__main__":
    main()
