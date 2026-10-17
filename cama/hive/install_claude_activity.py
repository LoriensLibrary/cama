"""Install metadata-only activity hooks without replacing existing Claude hooks."""
import copy
import json
import re
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
        # Recognize only our stable runtime command, not arbitrary hooks that
        # happen to mention CAMA. Replace it when the interpreter changes.
        retained = []
        for group in groups:
            handlers = []
            for handler in group.get("hooks", []):
                old = handler.get("command", "").replace("\\", "/")
                owned = re.fullmatch(r'"[^"\r\n]+" "[^"\r\n]+/cama_activity/claude_activity_hook\.py"', old)
                if old != command.replace("\\", "/") and not owned:
                    handlers.append(handler)
            if handlers or not group.get("hooks"):
                group["hooks"] = handlers
                retained.append(group)
        retained.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
        hooks[event] = retained
    return result


def main():
    root = Path.home() / ".claude"
    settings_path = root / "settings.json"
    original = settings_path.read_text(encoding="utf-8-sig") if settings_path.exists() else "{}"
    settings = json.loads(original)
    runtime = root / "cama_activity"
    command = f'"{Path(sys.executable).as_posix()}" "{(runtime / "claude_activity_hook.py").as_posix()}"'
    updated = configure(settings, command)
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("activity.py", "claude_activity_hook.py"):
        shutil.copy2(Path(__file__).with_name(name), runtime / name)
    if updated != settings:
        backup = settings_path.with_name(f"settings.json.cama-backup-{time.time_ns()}")
        if settings_path.exists():
            shutil.copy2(settings_path, backup)
        temporary = settings_path.with_name("settings.json.cama-tmp")
        temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        temporary.replace(settings_path)
        print(f"Installed activity hooks; backup: {backup}")
    else:
        print("Activity hooks already configured; runtime refreshed")


if __name__ == "__main__":
    main()
