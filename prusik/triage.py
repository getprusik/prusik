"""Deterministic triage: route feature to solo or team mode.

Pure-code, zero LLM tokens. Reads scope.md (derived) and brief.md (intent meta),
applies rules declared in sprint-config.yaml, writes decisions/<feature>.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from prusik import phases, schema
from prusik.ledger import project_root, append


def _first_token(s: str) -> str | None:
    s = s.strip()
    if not s:
        return None
    return s.split()[0].rstrip(",.")


def parse_scope(scope_path: Path) -> dict:
    # v0.6.8: route through schema.extract_list_items so triage parsing
    # inherits HR skipping (v0.6.1), `+ ` / `* ` alt markers (v0.6.1),
    # and nested-bullet skipping (v0.6.7). Pre-v0.6.8 triage had its own
    # `_bullets` duplicate that was missing all three — a latent twin of
    # the consistency._bullet_items duplicate that v0.6.7 closed.
    sections = schema.parse_sections(scope_path.read_text())
    return {
        "modules": [schema.extract_path_token(b)
                    for b in schema.extract_list_items(
                        sections.get("## Modules touched", ""))
                    if schema.extract_path_token(b)],
        "domains": [d.split()[0] for d in schema.extract_list_items(
            sections.get("## Domains", ""))],
        "size": _first_token(sections.get("## Size", "")),
        "milestone": _first_token(sections.get("## Milestone", "")),
        "related_work": schema.extract_list_items(
            sections.get("## Related work", "")),
        "risks": schema.extract_list_items(sections.get("## Risks", "")),
    }


def parse_brief_meta(brief_path: Path) -> dict:
    sections = schema.parse_sections(brief_path.read_text())
    return {
        "type": _first_token(sections.get("## Type", "")),
        "priority": _first_token(sections.get("## Priority", "")) or "P2",
    }


def _match_rule(rule: dict, ctx: dict) -> bool:
    """A rule is a dict; every key must match the corresponding value in ctx.

    Value can be:
      - scalar: equality check
      - list: membership check
      - string ">=N": numeric comparison
    """
    for key, expected in rule.items():
        actual = ctx.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif isinstance(expected, str) and expected.startswith(">="):
            try:
                threshold = int(expected[2:])
            except ValueError:
                return False
            if actual is None or actual < threshold:
                return False
        elif isinstance(expected, str) and expected.startswith("<="):
            try:
                threshold = int(expected[2:])
            except ValueError:
                return False
            if actual is None or actual > threshold:
                return False
        else:
            if actual != expected:
                return False
    return True


def decide(scope: dict, brief_meta: dict, config: dict) -> tuple[str, str]:
    rules = config.get("triage", {}).get("heuristics", {})
    ctx = {
        "type": brief_meta.get("type"),
        "size": scope.get("size"),
        "domains_count": len(scope.get("domains", [])),
        "modules_count": len(scope.get("modules", [])),
        "priority": brief_meta.get("priority"),
    }
    for rule in rules.get("auto_solo_if", []):
        if _match_rule(rule, ctx):
            reason = f"matched auto_solo rule: {rule}"
            # Size-gate flag (fb-9d0bc5d0d58e): a rule with no size/domain
            # constraint — e.g. `{type: test}` — fires on type alone, so a size-L/XL or
            # multi-domain sprint can auto-route SOLO and be under-resourced. Solo may
            # still be right (a cohesive system, not parallelizable lanes), so we don't
            # override the route — we FLAG it for operator confirmation rather than
            # silently auto-soloing a large sprint.
            size = str(ctx.get("size") or "").upper()
            domains_count = int(ctx.get("domains_count") or 0)
            unconstrained = "size" not in rule and "domains_count" not in rule
            if unconstrained and (size in ("L", "XL") or domains_count >= 2):
                reason += (f" — ⚠ size={ctx.get('size')} "
                           f"domains={ctx.get('domains_count')}: auto-soloed on type "
                           f"alone; confirm this is cohesive (not parallelizable lanes)")
            return "solo", reason
    for rule in rules.get("auto_team_if", []):
        if _match_rule(rule, ctx):
            return "team", f"matched auto_team rule: {rule}"
    default = rules.get("else", "solo")
    return default, f"fell through to default: {default}"


def run(feature: str) -> int:
    root = project_root()
    config = phases.load_sprint_config()
    if not config:
        print("No sprint-config.yaml; run 'prusik init' first.")
        return 1
    scope_path = root / "design" / feature / "scope.md"
    brief_path = root / "briefs" / f"{feature}.md"
    if not scope_path.exists():
        print(f"Scope not found: {scope_path}")
        return 1
    if not brief_path.exists():
        print(f"Brief not found: {brief_path}")
        return 1
    scope = parse_scope(scope_path)
    brief_meta = parse_brief_meta(brief_path)
    mode, reason = decide(scope, brief_meta, config)
    decision = {
        "feature": feature,
        "mode": mode,
        "reason": reason,
        "scope_summary": scope,
        "brief_meta": brief_meta,
    }
    out_dir = root / "decisions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{feature}.json"
    out.write_text(json.dumps(decision, indent=2))
    append("triage_decision", feature=feature, mode=mode, reason=reason,
           domains=scope.get("domains"), size=scope.get("size"))
    print(f"Triage: {mode}")
    print(f"  Reason: {reason}")
    print(f"  Written: {out.relative_to(root)}")
    return 0
