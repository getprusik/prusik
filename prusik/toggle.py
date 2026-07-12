"""`prusik disable` / `prusik enable` — reversible pause without removing files.

Flips `disableAllHooks` in `.claude/settings.json`. Claude Code stops firing
prusik hooks until re-enabled. No files removed, no manifest changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from prusik import ledger


def _settings_path() -> Path:
    return Path.cwd() / ".claude" / "settings.json"


def disable() -> int:
    path = _settings_path()
    if not path.exists():
        print(f"[prusik-disable] no {path} — nothing to disable")
        return 1
    data = json.loads(path.read_text())
    data["disableAllHooks"] = True
    path.write_text(json.dumps(data, indent=2) + "\n")
    # Audit signal: a `disable` right after a gate block is the catch-quality
    # ledger's "routed around" tell (false_block). See catch_quality.py.
    ledger.append("kit_disabled")
    print("[prusik-disable] hooks disabled. Enable with: prusik enable")
    return 0


def enable() -> int:
    path = _settings_path()
    if not path.exists():
        print(f"[prusik-enable] no {path} — nothing to enable")
        return 1
    data = json.loads(path.read_text())
    if data.pop("disableAllHooks", None) is None:
        print("[prusik-enable] already enabled")
        return 0
    path.write_text(json.dumps(data, indent=2) + "\n")
    ledger.append("kit_enabled")
    print("[prusik-enable] hooks enabled")
    return 0


def status() -> int:
    path = _settings_path()
    if not path.exists():
        print("[prusik] no .claude/settings.json — prusik not installed here")
        return 1
    data = json.loads(path.read_text())
    if data.get("disableAllHooks"):
        print("[prusik] DISABLED (hooks will not fire)")
    else:
        print("[prusik] enabled")
    return 0
