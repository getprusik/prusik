"""`prusik permissions audit` — diagnose missing CC permissions.allow entries.

Background: Claude Code layers a separate per-command permission gate on top
of prusik's hook-based phase enforcement. A role's `tools: Read, Write, Bash`
frontmatter says "this role MAY use these tools," but CC's permission gate
decides whether each specific Bash invocation or Write path is actually
allowed. Subagents get scoped permissions and can't answer interactive
prompts, so anything not in `permissions.allow` is silently denied.

Discovered live: backend-builder + test-writer subagents in cli-foundation
sprint had `Read, Write, Edit, Glob, Grep, Bash` in their tool frontmatter
but couldn't run `uv ...`, `python3 ...`, or `Write(...)` because the
project's `.claude/settings.local.json` allow list was minimal.

This audit reads the project's settings.json + settings.local.json
permissions.allow, compares against prusik's recommended baseline, and
prints missing entries with a paste-ready JSON block.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from prusik.ledger import project_root

# A permission rule: a Tool name, optionally with a (pattern). e.g. `WebFetch`,
# `Bash(pnpm *)`, `Read(/abs/**)`.
_RULE_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*(\([^)]*\))?$")

# Self-escalation guard for `permissions add`: refuse to grant arbitrary shell or
# a destructive pattern through the controlled path. Truly intended? Edit
# settings.local.json by hand — a deliberate operator action, not an agent's.
_OVERBROAD = {"Bash", "Bash(*)", "Bash( *)", "Bash(**)", "Bash(* *)"}
_DANGEROUS_BASH = ("rm ", "rm-", "sudo", "mkfs", "shutdown", "reboot", ":(){",
                   "dd ", "> /dev/sd", "chmod 777", "| bash", "| sh", "eval ")


# Recommended baseline. Prusik's roles realistically need these to function
# in subagent contexts. Tighter sets are fine — users who want stricter
# security can prune. Looser sets are fine too — but watch for surprises.
RECOMMENDED_ALLOW = [
    # Prusik itself
    "Bash(prusik *)",
    # Common shell/file inspection
    "Bash(ls *)", "Bash(cat *)", "Bash(grep *)", "Bash(find *)",
    "Bash(mkdir *)", "Bash(cp *)", "Bash(mv *)", "Bash(echo *)",
    "Bash(pwd)", "Bash(test *)", "Bash(which *)",
    "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(diff *)",
    "Bash(sed *)", "Bash(awk *)", "Bash(tree *)",
    # Git (read + write; prusik gate's deny_commands handles per-phase scoping)
    "Bash(git status)", "Bash(git diff *)", "Bash(git log *)",
    "Bash(git branch *)", "Bash(git checkout *)", "Bash(git add *)",
    "Bash(git commit *)", "Bash(git merge *)", "Bash(git push *)",
    "Bash(git fetch *)", "Bash(git pull *)", "Bash(git stash *)",
    "Bash(git rebase *)",
    # Python ecosystem
    "Bash(python *)", "Bash(python3 *)", "Bash(pip *)",
    "Bash(uv *)", "Bash(uvx *)", "Bash(pipx *)",
    "Bash(pytest *)", "Bash(ruff *)", "Bash(mypy *)",
    "Bash(black *)", "Bash(isort *)",
    # Node ecosystem
    "Bash(node *)", "Bash(npm *)", "Bash(npx *)",
    "Bash(yarn *)", "Bash(pnpm *)",
    # Other languages
    "Bash(go *)", "Bash(cargo *)", "Bash(rustc *)",
    # Build & infra
    "Bash(make *)", "Bash(docker *)", "Bash(docker-compose *)",
    "Bash(alembic *)", "Bash(psql *)", "Bash(curl *)",
    # File-tool unscoped — prusik gate enforces phase-specific writable patterns
    "Write(**)", "Edit(**)",
]


def _read_allow(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return set()
    return set((data.get("permissions") or {}).get("allow") or [])


def missing(root: Path | None = None) -> list[str]:
    """Return baseline allow entries NOT present in settings.json or
    settings.local.json. Empty list means project has full coverage.

    Pure-data: no printing, no side effects. Callers like
    `gate.sprint_init` use this for hard-block enforcement.
    """
    root = root or project_root()
    settings = root / ".claude" / "settings.json"
    settings_local = root / ".claude" / "settings.local.json"
    current = _read_allow(settings) | _read_allow(settings_local)
    return [entry for entry in RECOMMENDED_ALLOW if entry not in current]


def _refuse_reason(rule: str) -> str | None:
    """A reason to REFUSE adding this rule (self-escalation guard), or None."""
    if rule in _OVERBROAD:
        return "grants arbitrary shell — far too broad"
    if rule.startswith("Bash(") and rule.endswith(")"):
        inner = rule[len("Bash("):-1].strip()
        if inner in ("*", "**", "* *"):
            return "grants arbitrary shell — far too broad"
        low = inner.lower()
        for s in _DANGEROUS_BASH:
            if s in low:
                return f"matches a destructive/dangerous pattern ({s.strip()!r})"
    return None


def add(rule: str, root: Path | None = None, reason: str = "") -> int:
    """Add ONE permission rule to .claude/settings.local.json's allow list — the
    controlled, audited path to grant a permission without opening the writable
    gate (which correctly blocks raw edits to .claude/ mid-sprint). Validates the
    format, REFUSES dangerously-broad rules (self-escalation guard), is
    idempotent, and records a ledger event so every grant is visible."""
    from prusik import ledger
    root = root or project_root()
    rule = rule.strip()
    if not _RULE_RE.match(rule):
        print(f"[permissions] invalid rule format: {rule!r}. Expected `Tool` or "
              f"`Tool(pattern)`, e.g. 'Bash(pnpm *)' or 'WebFetch'.")
        return 2
    danger = _refuse_reason(rule)
    if danger:
        print(f"[permissions] REFUSED: {rule!r} {danger}.")
        print("  Self-escalation guard. If you truly intend this, edit "
              ".claude/settings.local.json by hand (a deliberate operator action).")
        ledger.append("permission_add_refused", rule=rule, reason=danger)
        return 2
    path = root / ".claude" / "settings.local.json"
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"[permissions] {path} is not valid JSON — fix it first.")
            return 2
    if not isinstance(data, dict):
        data = {}
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    if rule in allow:
        print(f"[permissions] already present: {rule}")
        return 0
    allow.append(rule)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    ledger.append("permission_added", rule=rule, reason=reason.strip())
    print(f"[permissions] added to settings.local.json allow: {rule}")
    if reason.strip():
        print(f"  reason: {reason.strip()}")
    return 0


def audit(root: Path | None = None) -> int:
    root = root or project_root()
    settings = root / ".claude" / "settings.json"
    settings_local = root / ".claude" / "settings.local.json"

    if not settings.exists() and not settings_local.exists():
        print(f"[prusik-permissions-audit] no settings.json or settings.local.json under {root}/.claude/")
        print("  Run `prusik init` first.")
        return 1

    missing_entries = missing(root)

    print("[prusik-permissions-audit] prusik baseline vs project allow list")
    print(f"  settings.json:        {len(_read_allow(settings))} entries")
    print(f"  settings.local.json:  {len(_read_allow(settings_local))} entries")
    print(f"  baseline:             {len(RECOMMENDED_ALLOW)} entries")
    print(f"  missing:              {len(missing_entries)}")
    print()

    if not missing_entries:
        print("All recommended entries present. Subagents should function across phases.")
        return 0

    print("The following baseline entries are NOT present in either settings.json or")
    print("settings.local.json. In headless subagent contexts (team-mode building phase,")
    print("Skill-launched runs, etc.) commands matching these patterns will be SILENTLY")
    print("denied because subagents can't answer interactive permission prompts.")
    print()
    print("Paste-ready JSON to merge into settings.json `permissions.allow`:")
    print()
    print(json.dumps(missing_entries, indent=2))
    print()
    print("Or run `prusik refresh` to bring prusik's template into this project")
    print("(v0.5.8+ surgical-merges baseline entries without clobbering customizations).")
    return 1
