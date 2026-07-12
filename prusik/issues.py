"""Issue sync coordinator.

Reads `.claude/sprint-config.yaml` `issues:` section, dispatches to the right
plugin, writes `.sprint/issues.db.jsonl`. Graceful no-op when tracker is
unconfigured or prerequisites are missing.
"""

from __future__ import annotations

import json
from pathlib import Path

from prusik import phases
from prusik.issue_plugins import get as get_plugin, PluginUnavailable
from prusik.ledger import project_root, append


def sync(root: Path | None = None) -> int:
    root = root or project_root()
    config = phases.load_sprint_config(root)
    if not config:
        print("[issues] no sprint-config.yaml; skipping")
        return 0
    issues_cfg = config.get("issues") or {}
    tracker = issues_cfg.get("tracker")
    if not tracker or tracker == "none":
        print("[issues] no tracker configured (sprint-config.yaml issues.tracker); skipping")
        return 0
    try:
        plugin = get_plugin(tracker)
    except PluginUnavailable as e:
        print(f"[issues] {e}")
        return 0

    try:
        issues = plugin(issues_cfg, root)
    except PluginUnavailable as e:
        print(f"[issues] {e}")
        append("issues_sync_skipped", tracker=tracker, reason=str(e))
        return 0

    out = root / ".sprint" / "issues.db.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for i in issues:
            f.write(json.dumps(i) + "\n")
    append("issues_synced", tracker=tracker, count=len(issues))
    print(f"[issues] synced {len(issues)} issue(s) from {tracker} → {out.relative_to(root)}")
    return 0


def search(query: str, limit: int = 5, root: Path | None = None) -> list[dict]:
    """Cheap keyword search over the synced issue db. Zero tokens."""
    root = root or project_root()
    db = root / ".sprint" / "issues.db.jsonl"
    if not db.exists():
        return []
    terms = [t.lower() for t in query.split() if len(t) >= 3]
    if not terms:
        return []
    scored: list[tuple[int, dict]] = []
    for line in db.read_text().splitlines():
        if not line.strip():
            continue
        try:
            issue = json.loads(line)
        except json.JSONDecodeError:
            continue
        hay = (issue.get("title", "") + " " + issue.get("body", "")).lower()
        score = sum(hay.count(t) for t in terms)
        if score > 0:
            scored.append((score, issue))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:limit]]
