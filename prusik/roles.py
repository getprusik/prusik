"""Proposed-role staffing check (v0.76.0, field finding #12).

The planner emits roles under `## Proposed roles` (`backend-builder-repo`,
`frontend-builder-css`, …) and plan-critic approves them — but if the agent
library doesn't ship a matching agent, the dispatch silently falls back to a
manual stand-in, on EVERY sprint that plans that role. (An adopter: `frontend-builder`
planned but unshipped.) This flags, at plan time, any proposed role with no
shipped agent — so an under-staffed plan is caught before the build, not
discovered mid-sprint.

A role `<base>-<suffix>` is staffed by agent `<base>` (the dispatch uses the base
type; the suffix is just an ownership label). So a role is covered when some
shipped agent name equals it or is a `-`-prefix of it.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROLE_RE = re.compile(r"^\s*[-*]\s*\*\*([a-z][\w-]*)\*\*")


def proposed_roles(plan_text: str) -> list[str]:
    """Role names from the plan's `## Proposed roles` bold-bullet list."""
    from prusik import schema
    body = schema.parse_sections(plan_text).get("## Proposed roles", "")
    out: list[str] = []
    for line in body.splitlines():
        m = _ROLE_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def shipped_agents(root: Path) -> set[str]:
    d = root / ".claude" / "agents"
    return {p.stem for p in d.glob("*.md")} if d.is_dir() else set()


def unstaffed_roles(plan_text: str, root: Path) -> list[str]:
    """Proposed roles with no shipped agent to dispatch (would fall back to a
    manual stand-in). Empty when every role is staffed."""
    agents = shipped_agents(root)
    if not agents:
        return []          # can't assess (no installed agents) — don't false-warn
    missing: list[str] = []
    for role in proposed_roles(plan_text):
        if not any(role == a or role.startswith(a + "-") for a in agents):
            missing.append(role)
    return missing


def advisory(feature: str, root: Path) -> str | None:
    plan = root / "design" / feature / "plan.md"
    if not plan.exists():
        return None
    missing = unstaffed_roles(plan.read_text(), root)
    if not missing:
        return None
    return ("[prusik-gate] staffing ADVISORY — the plan proposes role(s) with no "
            f"shipped agent: {', '.join(missing)}. These dispatch to a manual "
            "stand-in. Add `.claude/agents/<base>.md` (e.g. via `prusik refresh`) "
            "or rename the role to a shipped agent before the team build.")
