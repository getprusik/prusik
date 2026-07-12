"""`prusik doctor` — harness self-assessment + drift detection (v0.8.5).

Phase 1.1 of the "drop-in 360 harness" plan. Five-subsystem 1-5 scoring
adopted from learn-harness-engineering's framework, applied to prusik's
specific implementation:

    Instructions     ←→ .claude/agents/, .claude/commands/, CLAUDE.md
    State            ←→ briefs/, design/, reports/, decisions/, ledger
    Verification     ←→ test command, pre-commit, project_policy block,
                        behavior_regression block, CI presence
    Scope            ←→ briefs/ schema discipline, scope.md presence,
                        triage decisions, plan files
    Session Lifecycle ←→ hooks wired in settings.json, phase FSM in use,
                        worktrees model, healthy phase transitions

Scoring discipline:
- **Monotone**: improving the harness always improves the score.
- **Observable**: computable from `.claude/`, `.sprint/`, briefs/, design/.
- **Ungameable**: each check requires actual structural improvement;
  none can be satisfied by writing a string of the right shape.
- **Smooth**: scores accumulate from independent sub-checks rather than
  cliff transitions, so small improvements produce small score changes.

Drift detection compares current `detect_project()` against the
detection snapshot recorded at install time (manifest's `detection`
key, written by v0.8.4's `prusik init`). Differences surface as
informational warnings — operators see when project state has diverged
from the harness's understanding of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from prusik import detect, ledger, phases


# ---------- Public entry point ----------

def run(json_output: bool = False, suggest_permissions: bool = False,
        suggest_apply: bool = False,
        insights: bool = False, insights_for_brief: str | None = None,
        sprint: str | None = None) -> int:
    """Main entry. Always exit 0 — informational, not blocking.

    v0.16.0: two new modes that turn the ledger into self-improving
    adoption hygiene — prusik's own derive-from-evidence thesis applied
    to itself.

    - `suggest_permissions`: mine gate_blocked events for recurring noise
      classes and propose exact additive patches (permissions.allow lines,
      writable patterns). Prusik tells the operator how to reduce its own
      friction from observed-recurrence, not generic guidance.
    - `insights`: pattern detection across past sprints — rewind clusters,
      fix-round spikes, calendar drift — surface as recommendations the
      operator can act on. Heuristic, no ML; the data is in the ledger
      already, the mechanism just makes it visible.
    """
    root = ledger.project_root()
    claude_dir = root / ".claude"

    if not claude_dir.exists():
        print(f"[prusik-doctor] No .claude/ directory at {root}.\n"
              f"            Run `prusik init` first.", file=sys.stderr)
        return 1

    if suggest_permissions:
        return _suggest_permissions(root, json_output=json_output,
                                    apply=suggest_apply)
    if insights:
        return _compute_insights(root, json_output=json_output)
    if insights_for_brief:
        return _insights_for_brief(root, insights_for_brief,
                                    json_output=json_output)
    if sprint:
        return _sprint_stats(root, sprint, json_output=json_output)

    # v0.13.0: load through the manifest module — it migrates the in-memory
    # view (so a pre-v0.13.0 manifest reports its true surface version) but
    # does NOT persist. doctor is read-only; it must not mutate the manifest
    # as a side effect of being run (read/write separation).
    from prusik import manifest as _manifest
    manifest_path = _manifest.find_manifest(claude_dir)
    manifest: dict | None = _manifest.load(manifest_path) if manifest_path else None

    current_detection = detect.detect_project(root)

    scores = {
        "instructions": _score_instructions(root, claude_dir),
        "state": _score_state(root),
        "verification": _score_verification(root, current_detection),
        "scope": _score_scope(root),
        "session_lifecycle": _score_session_lifecycle(root, claude_dir),
    }

    drift = _detect_drift(manifest, current_detection)

    if json_output:
        from prusik import manifest as _manifest
        out = {
            "kit_version": _read_kit_version(),
            "manifest_kit_version": _manifest.surface_version(manifest),
            "manifest_created_with": _manifest.created_with(manifest),
            "scores": {k: {"score": v[0], "evidence": v[1]} for k, v in scores.items()},
            "lowest": _lowest_subsystem(scores),
            "drift": drift,
        }
        print(json.dumps(out, indent=2))
        return 0

    _print_text_report(root, scores, drift, manifest)
    return 0


# ---------- Subsystem scorers ----------

def _score_instructions(root: Path, claude_dir: Path) -> tuple[int, list[str]]:
    """Score the Instructions subsystem 0-5.

    Sub-checks (each 1.0 unless noted):
      - CLAUDE.md OR AGENTS.md exists at project root
      - .claude/agents/ has ≥3 role files
      - .claude/agents/ has ≥10 role files (full library)
      - .claude/commands/ has ≥3 slash commands
      - .claude/schemas/ exists
    """
    points = 0.0
    evidence: list[str] = []

    claude_md = root / "CLAUDE.md"
    agents_md = root / "AGENTS.md"
    if claude_md.exists():
        size = claude_md.stat().st_size
        points += 1.0
        evidence.append(f"✓ CLAUDE.md present ({size} bytes)")
    elif agents_md.exists():
        size = agents_md.stat().st_size
        points += 1.0
        evidence.append(f"✓ AGENTS.md present ({size} bytes)")
    else:
        evidence.append("⚠ No CLAUDE.md or AGENTS.md at project root")

    agents_dir = claude_dir / "agents"
    if agents_dir.is_dir():
        role_files = list(agents_dir.glob("*.md"))
        if len(role_files) >= 10:
            points += 2.0
            evidence.append(f"✓ .claude/agents/ has full role library ({len(role_files)} files)")
        elif len(role_files) >= 3:
            points += 1.0
            evidence.append(f"✓ .claude/agents/ has {len(role_files)} role files (partial)")
        elif role_files:
            points += 0.5
            evidence.append(f"· .claude/agents/ has only {len(role_files)} role files")
        else:
            evidence.append("⚠ .claude/agents/ exists but is empty")
    else:
        evidence.append("⚠ .claude/agents/ missing")

    commands_dir = claude_dir / "commands"
    if commands_dir.is_dir():
        cmd_files = list(commands_dir.glob("*.md"))
        if len(cmd_files) >= 3:
            points += 1.0
            evidence.append(f"✓ .claude/commands/ has {len(cmd_files)} slash commands")
        elif cmd_files:
            points += 0.5
            evidence.append(f"· .claude/commands/ has {len(cmd_files)} slash commands (minimal)")
        else:
            evidence.append("⚠ .claude/commands/ exists but is empty")
    else:
        evidence.append("⚠ .claude/commands/ missing")

    schemas_dir = claude_dir / "schemas"
    if schemas_dir.is_dir() and any(schemas_dir.iterdir()):
        points += 1.0
        evidence.append("✓ .claude/schemas/ present")
    else:
        evidence.append("· .claude/schemas/ missing or empty")

    return _round_to_5(points), evidence


def _score_state(root: Path) -> tuple[int, list[str]]:
    """Score the State subsystem 0-5.

    Sub-checks:
      - briefs/, design/, reports/, decisions/ directories present (1.0 each, capped 2.0)
      - .sprint/ exists (1.0)
      - .sprint/ledger.jsonl has ≥10 entries (1.0); ≥100 entries (additional 0.5)
      - design/ has ≥1 feature directory (0.5)
      - design/ has ≥5 feature directories (additional 0.5)
    """
    points = 0.0
    evidence: list[str] = []

    work_dirs = ("briefs", "design", "reports", "decisions")
    present = [d for d in work_dirs if (root / d).is_dir()]
    points += min(2.0, len(present) * 0.5)
    if len(present) == len(work_dirs):
        evidence.append(f"✓ All working dirs present: {', '.join(work_dirs)}")
    elif present:
        missing = [d for d in work_dirs if d not in present]
        evidence.append(f"· Working dirs present: {', '.join(present)} (missing: {', '.join(missing)})")
    else:
        evidence.append("⚠ No working directories (briefs/, design/, reports/, decisions/)")

    sprint_dir = root / ".sprint"
    if sprint_dir.is_dir():
        points += 1.0
        evidence.append("✓ .sprint/ present")
    else:
        evidence.append("⚠ .sprint/ missing — phase state not tracked")

    ledger_path = sprint_dir / "ledger.jsonl"
    if ledger_path.exists():
        try:
            count = sum(1 for line in ledger_path.read_text().splitlines() if line.strip())
        except OSError:
            count = 0
        if count >= 100:
            points += 1.5
            evidence.append(f"✓ Ledger has {count} events (mature usage)")
        elif count >= 10:
            points += 1.0
            evidence.append(f"✓ Ledger has {count} events")
        elif count > 0:
            points += 0.5
            evidence.append(f"· Ledger has {count} events (early usage)")
        else:
            evidence.append("⚠ Ledger file exists but empty")
    else:
        evidence.append("· No ledger yet (no sprints have run)")

    design_dir = root / "design"
    if design_dir.is_dir():
        feat_dirs = [d for d in design_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if len(feat_dirs) >= 5:
            points += 1.0
            evidence.append(f"✓ {len(feat_dirs)} feature directories in design/")
        elif feat_dirs:
            points += 0.5
            evidence.append(f"· {len(feat_dirs)} feature directories in design/")
        else:
            evidence.append("· design/ has no feature directories yet")

    return _round_to_5(points), evidence


def _score_verification(root: Path, detection: dict) -> tuple[int, list[str]]:
    """Score the Verification subsystem 0-5.

    Sub-checks:
      - General test command discovered (1.0)
      - Pre-commit pipeline present in project (1.0)
      - project_policy block configured + enabled (1.0)
      - behavior_regression block configured + enabled (1.0)
      - CI present (.github/workflows/ etc.) (1.0)
    """
    points = 0.0
    evidence: list[str] = []

    tc = detection.get("test_commands") or {}
    if tc.get("general"):
        points += 1.0
        evidence.append(f"✓ Test command: {tc['general']}")
    else:
        evidence.append("⚠ No test command discovered")

    pc = detection.get("pre_commit") or {}
    if pc.get("type"):
        points += 1.0
        evidence.append(f"✓ Pre-commit pipeline: {pc['type']}")
    else:
        evidence.append("· No pre-commit pipeline detected")

    config = phases.load_sprint_config(root) or {}
    pp = config.get("project_policy") or {}
    if pp.get("enabled"):
        points += 1.0
        evidence.append(f"✓ project_policy block enabled "
                        f"(command: {pp.get('command', '<not set>')!r})")
    else:
        if pc.get("type"):
            evidence.append("⚠ project_policy NOT enabled but pre-commit exists — "
                            "reviewer is missing this signal")
        else:
            evidence.append("· project_policy not enabled (no pre-commit to wire)")

    br = config.get("behavior_regression") or {}
    if br.get("enabled"):
        points += 1.0
        evidence.append(f"✓ behavior_regression block enabled "
                        f"(command: {br.get('command', '<not set>')!r})")
    else:
        bt = detection.get("behavior_tests") or {}
        if bt.get("dir"):
            evidence.append(f"⚠ behavior_regression NOT enabled but tests/behavior/ "
                            f"is populated ({bt['test_count']} files)")
        else:
            evidence.append("· behavior_regression not enabled (no tests/behavior/)")

    ci = detection.get("ci") or {}
    if ci.get("present"):
        points += 1.0
        evidence.append(f"✓ CI: {', '.join(ci.get('providers', []))}")
    else:
        evidence.append("· No CI detected")

    return _round_to_5(points), evidence


def _score_scope(root: Path) -> tuple[int, list[str]]:
    """Score the Scope subsystem 0-5.

    Sub-checks:
      - briefs/ has ≥1 file (1.0); ≥5 files (additional 1.0)
      - design/ has scope.md files (1.0)
      - design/ has plan.md files (1.0)
      - decisions/ has triage decisions (1.0)
    """
    points = 0.0
    evidence: list[str] = []

    briefs_dir = root / "briefs"
    if briefs_dir.is_dir():
        brief_files = list(briefs_dir.glob("*.md"))
        if len(brief_files) >= 5:
            points += 2.0
            evidence.append(f"✓ briefs/ has {len(brief_files)} briefs (mature)")
        elif brief_files:
            points += 1.0
            evidence.append(f"· briefs/ has {len(brief_files)} brief(s)")
        else:
            evidence.append("⚠ briefs/ is empty — no scope discipline yet")
    else:
        evidence.append("⚠ briefs/ missing")

    design_dir = root / "design"
    scope_count = 0
    plan_count = 0
    if design_dir.is_dir():
        for d in design_dir.iterdir():
            if d.is_dir():
                if (d / "scope.md").exists():
                    scope_count += 1
                if (d / "plan.md").exists():
                    plan_count += 1
    if scope_count > 0:
        points += 1.0
        evidence.append(f"✓ {scope_count} feature(s) with scope.md")
    else:
        evidence.append("· No scope.md files yet")

    if plan_count > 0:
        points += 1.0
        evidence.append(f"✓ {plan_count} feature(s) with plan.md")
    else:
        evidence.append("· No plan.md files yet")

    decisions_dir = root / "decisions"
    if decisions_dir.is_dir():
        decisions = list(decisions_dir.glob("*.json"))
        if decisions:
            points += 1.0
            evidence.append(f"✓ {len(decisions)} triage decision(s)")
        else:
            evidence.append("· decisions/ has no triage records yet")

    return _round_to_5(points), evidence


def _score_session_lifecycle(root: Path, claude_dir: Path) -> tuple[int, list[str]]:
    """Score the Session Lifecycle subsystem 0-5.

    Sub-checks:
      - .claude/settings.json has prusik gate hooks wired (PreToolUse + Stop +
        SessionStart) (2.0)
      - sprint-config.yaml present (1.0)
      - .sprint/state.json mechanism in use OR ledger has phase events (1.0)
      - Worktrees model used (worktrees/ dir + ledger has phase_advance to
        building) (1.0)
    """
    points = 0.0
    evidence: list[str] = []
    gate_hooks_wired = False

    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            wired = []
            for hook_name in ("PreToolUse", "Stop", "SessionStart"):
                hook_specs = hooks.get(hook_name, [])
                for spec in hook_specs:
                    for h in spec.get("hooks", []):
                        if "prusik gate" in h.get("command", ""):
                            wired.append(hook_name)
                            break
            wired = list(set(wired))
            gate_hooks_wired = bool(wired)
            if len(wired) == 3:
                points += 2.0
                evidence.append("✓ All three prusik gate hooks wired (PreToolUse + Stop + SessionStart)")
            elif wired:
                points += 1.0
                evidence.append(f"· prusik gate hooks wired: {', '.join(wired)} (missing some)")
            else:
                evidence.append("⚠ settings.json present but no prusik gate hooks wired")
        except (json.JSONDecodeError, OSError):
            evidence.append("⚠ settings.json present but unreadable")
    else:
        evidence.append("⚠ .claude/settings.json missing — hooks not wired")

    sprint_config = claude_dir / "sprint-config.yaml"
    if sprint_config.exists():
        points += 1.0
        evidence.append("✓ sprint-config.yaml present (phase FSM defined)")
    else:
        evidence.append("⚠ sprint-config.yaml missing — phase FSM undefined")

    # v0.53.4 (finding #5): the INERT-HARNESS check. Scaffold installed but no
    # gate hooks = the FSM enforces NOTHING, even though it reads as set up —
    # the exact state a hooks-present repo lands in without `init --merge-hooks`.
    if sprint_config.exists() and not gate_hooks_wired:
        evidence.append(
            "⚠⚠ INERT HARNESS — sprint-config present but NO prusik gate hooks in "
            "settings.json: the FSM enforces nothing. If settings.json already "
            "had a hooks block, re-run `prusik init --merge-hooks` to wire prusik's "
            "gates alongside it.")

    ledger_path = root / ".sprint" / "ledger.jsonl"
    has_phase_events = False
    if ledger_path.exists():
        try:
            for line in ledger_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("event") in ("phase_advance", "sprint_started"):
                    has_phase_events = True
                    break
        except OSError:
            pass
    if has_phase_events:
        points += 1.0
        evidence.append("✓ Ledger shows phase transitions (lifecycle in use)")
    else:
        evidence.append("· No phase transitions in ledger yet")

    worktrees_dir = root / "worktrees"
    if worktrees_dir.is_dir():
        # Has the project actually used worktrees in a sprint?
        used_worktrees = False
        if has_phase_events and ledger_path.exists():
            try:
                for line in ledger_path.read_text().splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("event") == "phase_advance" \
                            and entry.get("to_phase") in ("building", "solo_execute"):
                        used_worktrees = True
                        break
            except OSError:
                pass
        if used_worktrees:
            points += 1.0
            evidence.append("✓ Worktrees model in active use")
        else:
            evidence.append("· worktrees/ exists but not yet used by a sprint")
    else:
        evidence.append("· worktrees/ missing")

    return _round_to_5(points), evidence


# ---------- Drift detection ----------

def _detect_drift(manifest: dict | None, current: dict) -> dict:
    """Compare manifest's recorded detection against current detection.

    Returns a dict {axis: change_description} for axes that drifted.
    Empty dict = no drift.
    """
    drift: dict = {}
    if not manifest:
        return {"_meta": "no_manifest_to_compare"}

    recorded = manifest.get("detection")
    if not recorded:
        # Pre-v0.8.4 manifest doesn't have detection.
        return {"_meta": "manifest_predates_detection"}

    # Compare each axis
    for axis in ("stacks", "linters"):
        rec = set(recorded.get(axis, []) or [])
        cur = set(current.get(axis, []) or [])
        added = sorted(cur - rec)
        removed = sorted(rec - cur)
        if added or removed:
            parts = []
            if added:
                parts.append(f"+{', +'.join(added)}")
            if removed:
                parts.append(f"-{', -'.join(removed)}")
            drift[axis] = " ".join(parts)

    # Test commands
    rec_tc = (recorded.get("test_commands") or {}).get("general")
    cur_tc = (current.get("test_commands") or {}).get("general")
    if rec_tc != cur_tc:
        drift["test_command"] = f"{rec_tc!r} → {cur_tc!r}"

    # Pre-commit type
    rec_pc = (recorded.get("pre_commit") or {}).get("type")
    cur_pc = (current.get("pre_commit") or {}).get("type")
    if rec_pc != cur_pc:
        drift["pre_commit"] = f"{rec_pc!r} → {cur_pc!r}"

    # Behavior tests
    rec_bt = (recorded.get("behavior_tests") or {}).get("test_count", 0)
    cur_bt = (current.get("behavior_tests") or {}).get("test_count", 0)
    if rec_bt != cur_bt:
        drift["behavior_tests"] = f"{rec_bt} → {cur_bt} test files"

    return drift


# ---------- Helpers ----------

def _round_to_5(points: float) -> int:
    """Round float points to a 0-5 integer. Cap at 5."""
    return min(5, max(0, round(points)))


def _lowest_subsystem(scores: dict) -> str:
    """Find the subsystem with the lowest score. Tie-broken by axis name
    (alphabetical) for determinism."""
    items = sorted(scores.items(), key=lambda kv: (kv[1][0], kv[0]))
    return items[0][0]


def _read_kit_version() -> str:
    from prusik import __version__
    return __version__


# ---------- Output formatting ----------

_SUBSYSTEM_LABELS = {
    "instructions": "Instructions",
    "state": "State",
    "verification": "Verification",
    "scope": "Scope",
    "session_lifecycle": "Session Lifecycle",
}


_NEXT_STEP_HINTS = {
    "instructions": (
        "Add a CLAUDE.md to project root if missing. "
        "Run `prusik init` to install the role library if .claude/agents/ is empty."
    ),
    "state": (
        "Run a sprint (prusik gate sprint-start <feature>) to populate "
        "briefs/, design/, reports/, decisions/, and the ledger."
    ),
    "verification": (
        "If you have a pre-commit pipeline, declare it as `project_policy.command` "
        "in .claude/sprint-config.yaml. If you have tests/behavior/, declare it as "
        "`behavior_regression.command`. Run `prusik init` for copy-paste snippets."
    ),
    "scope": (
        "Write briefs/<feature>.md and run `prusik gate brief briefs/<feature>.md` "
        "to start a sprint. Scope discipline emerges from the FSM."
    ),
    "session_lifecycle": (
        "Run `prusik init` to wire the PreToolUse + Stop + SessionStart hooks. "
        "If hooks are wired but unused, run a sprint to activate the lifecycle."
    ),
}


def _print_text_report(root: Path, scores: dict, drift: dict,
                        manifest: dict | None) -> None:
    from prusik import manifest as _manifest
    kit_v = _read_kit_version()
    manifest_v = _manifest.surface_version(manifest)  # true deployed surface
    created_v = _manifest.created_with(manifest)

    print(f"[prusik-doctor] Harness scorecard for {root}")
    scaffolded = (f"  (scaffolded v{created_v})"
                  if created_v not in ("unknown", manifest_v) else "")
    print(f"            prusik binary: v{kit_v}    "
          f"manifest: v{manifest_v}{scaffolded}")
    print()

    for axis, (score, evidence) in scores.items():
        label = _SUBSYSTEM_LABELS[axis]
        print(f"  {label}:{' ' * (20 - len(label))}{score}/5")
        for e in evidence:
            print(f"    {e}")
        print()

    lowest = _lowest_subsystem(scores)
    lowest_score = scores[lowest][0]
    print(f"[prusik-doctor] Lowest subsystem: {_SUBSYSTEM_LABELS[lowest]} "
          f"({lowest_score}/5).")
    print("            Suggested next step:")
    hint = _NEXT_STEP_HINTS[lowest]
    for line in _wrap(hint, 76):
        print(f"              {line}")
    print()

    if drift:
        meta = drift.get("_meta")
        if meta == "no_manifest_to_compare":
            print("[prusik-doctor] No manifest detected — skipping drift check.")
        elif meta == "manifest_predates_detection":
            print("[prusik-doctor] No drift baseline (manifest predates v0.8.4 "
                  "detection). This self-heals on the next `prusik refresh`, "
                  "which establishes the baseline from current state — "
                  "`prusik init` re-scaffold is NOT required.")
        else:
            print(f"[prusik-doctor] Drift since install ({len(drift)} change(s)):")
            for axis, change in drift.items():
                if axis == "_meta":
                    continue
                print(f"  · {axis}: {change}")
    else:
        print("[prusik-doctor] No drift detected since install.")

    _print_version_staleness()


def _print_version_staleness() -> None:
    """Best-effort: is a newer prusik release available? Read-only, short timeout,
    silent on any failure (offline / rate-limited). No phone-home (see
    version_check). PULL — it only NOTIFIES; the operator runs `prusik update`."""
    try:
        from prusik import version_check
        installed, latest, newer = version_check.check(timeout=2.0)
        if latest is None:
            return                      # couldn't check — say nothing
        if newer:
            print(f"[prusik-doctor] ↑ prusik {latest} is available (you're on "
                  f"{installed}) — `prusik update` to upgrade + refresh templates.")
        else:
            print(f"[prusik-doctor] prusik {installed} is the latest release.")
    except Exception:  # noqa: BLE001 — a version check must never break doctor
        pass


def _wrap(text: str, width: int) -> list[str]:
    """Simple word-wrap helper."""
    words = text.split()
    out: list[str] = []
    line = ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w) if line else w
    if line:
        out.append(line)
    return out


# ============================================================
# v0.16.0 — `prusik doctor --suggest-permissions` (Tier 1 #3)
# ============================================================
#
# Mine the gate_blocked log for recurring deny patterns; propose exact
# additive patches the operator can apply to reduce future friction. The
# prusik's own derive-from-evidence thesis turned on its own adoption
# hygiene: every project accumulates its own deny patterns over time;
# rather than make adopters hand-tune permissions for months, surface the
# patterns and let them apply once.
#
# Heuristic: cluster gate_blocked events by (tool, reason-pattern);
# anything with N≥2 occurrences crosses the recurrence-trigger threshold
# (prusik's own discipline) and gets a proposed patch. Skip patterns
# already templated in v0.15.0+ (e.g. /tmp scratch) — those are now
# engine-baked, no patch needed.

import re as _re
from collections import Counter as _Counter, defaultdict as _dd


# Patterns prusik already handles in v0.15.0+ — don't re-suggest them.
_V015_ENGINE_BAKED = (
    "/tmp/", "/private/tmp/", ".runtime/",
)


def _suggest_permissions(root: Path, json_output: bool = False,
                          apply: bool = False) -> int:
    ledger_path = root / ".sprint" / "ledger.jsonl"
    if not ledger_path.exists():
        print(f"[prusik-doctor] No ledger yet at {ledger_path} — nothing to "
              f"mine. Run a sprint first.", file=sys.stderr)
        return 0

    events = []
    for line in ledger_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    gb = [e for e in events if e.get("event") == "gate_blocked"]
    if not gb:
        if json_output:
            print(json.dumps({"suggestions": [], "rationale":
                              "no gate_blocked events in ledger"}))
        else:
            print("[prusik-doctor] No gate_blocked events — nothing to suggest.")
        return 0

    # Group by tool + first token of command (for Bash) or path prefix
    # (for Write/Edit). Recurrence-trigger: only suggest when N≥2.
    bash_first_tokens: _Counter = _Counter()
    bash_redirect_paths: _Counter = _Counter()
    write_path_prefixes: _Counter = _Counter()

    # Treat top-level files (no subdir) as files, not dirs, in suggestions.
    _TOP_LEVEL_FILES_NOT_DIRS = ("CLAUDE.md", "AGENTS.md", ".gitignore",
                                  "pyproject.toml", "package.json", "Makefile")

    for e in gb:
        tool = e.get("tool", "")
        reason = e.get("reason") or ""
        if tool == "Bash":
            # Two sub-classes: redirect-target denies vs command denies
            m = _re.search(r"bash redirect to unwriteable path: (\S+)", reason)
            if m:
                target = m.group(1)
                if any(target.startswith(p) for p in _V015_ENGINE_BAKED):
                    continue  # v0.15.0+ handles these; don't re-suggest
                # Skip absolute paths under project root — suggesting the
                # entire project tree as always-writable is wrong shape.
                try:
                    rel = str(Path(target).relative_to(root))
                    parent = str(Path(rel).parent) or "."
                    if parent == ".":
                        continue  # bare-filename redirect; not phase-config-fixable
                except ValueError:
                    # Outside project root and not /tmp; legitimately need
                    # the operator's intent, don't auto-suggest.
                    continue
                bash_redirect_paths[parent] += 1
            else:
                cmd = e.get("command", "")
                first = cmd.strip().split()[0] if cmd.strip() else ""
                if first and first not in ("prusik", "kit"):  # own binary already allowed (kit = pre-rename ledger entries)
                    bash_first_tokens[first] += 1
        elif tool in ("Write", "Edit", "NotebookEdit"):
            target = e.get("target", "")
            try:
                rel = str(Path(target).relative_to(root))
            except ValueError:
                continue
            # Top-level file → suggest the file, not '/**' a non-directory
            if "/" not in rel:
                if rel in _TOP_LEVEL_FILES_NOT_DIRS:
                    write_path_prefixes[rel] += 1   # exact file
                # Otherwise it's something unexpected; skip rather than mislead
                continue
            top = rel.split("/", 1)[0]
            write_path_prefixes[f"{top}/"] += 1

    # Build suggestions — recurrence-trigger filter (≥2)
    suggestions = []
    for cmd, n in bash_first_tokens.most_common():
        if n < 2: continue
        suggestions.append({
            "kind": "permissions.allow",
            "patch": f"Bash({cmd} *)",
            "occurrences": n,
            "rationale": f"Bash '{cmd}' command denied {n}× in ledger — "
                         f"pre-allow in .claude/settings.json permissions.allow",
        })
    for path, n in bash_redirect_paths.most_common():
        if n < 2: continue
        suggestions.append({
            "kind": "writable",
            "patch": f"{path}/**",
            "occurrences": n,
            "rationale": f"Bash redirect to '{path}/' denied {n}× — add to "
                         f".claude/sprint-config.yaml always_writable list "
                         f"(or the appropriate phase's writable list)",
        })
    for path, n in write_path_prefixes.most_common():
        if n < 2: continue
        # path is either "topdir/" → suggest "topdir/**", or a top-level
        # file → suggest the file directly (not "/**" appended to a file).
        patch = f"{path}**" if path.endswith("/") else path
        suggestions.append({
            "kind": "writable",
            "patch": patch,
            "occurrences": n,
            "rationale": f"Write/Edit to '{path}' denied {n}× — likely a "
                         f"phase writable-pattern gap; add to the phase's "
                         f"writable list (or always_writable if cross-phase)",
        })

    if apply:
        # v0.17.0 — write the suggestions into settings.json + sprint-config
        # via the existing additive merge. Asks confirmation once.
        return _apply_suggestions(root, suggestions)

    if json_output:
        print(json.dumps({"suggestions": suggestions}, indent=2))
        return 0

    if not suggestions:
        print("[prusik-doctor] No recurring deny patterns (N≥2) to suggest — "
              "ledger is clean.")
        return 0

    print(f"[prusik-doctor] Suggested patches based on {len(gb)} gate_blocked "
          f"events in ledger:\n")
    by_kind: dict = _dd(list)
    for s in suggestions:
        by_kind[s["kind"]].append(s)
    if by_kind.get("permissions.allow"):
        print("  ── Add to .claude/settings.json `permissions.allow`: ──")
        for s in by_kind["permissions.allow"]:
            print(f"    \"{s['patch']}\"   # {s['occurrences']}× denied — "
                  f"{s['rationale']}")
        print()
    if by_kind.get("writable"):
        print("  ── Add to .claude/sprint-config.yaml writable patterns: ──")
        for s in by_kind["writable"]:
            print(f"    - \"{s['patch']}\"   # {s['occurrences']}× — "
                  f"{s['rationale']}")
        print()
    print("  (Apply only after reviewing — these are SUGGESTIONS, not "
          "auto-applied. Some denies may be legitimate phase discipline.)")
    return 0


# ============================================================
# v0.16.0 — `prusik doctor --insights` (Tier 3 #8 lite)
# ============================================================
#
# Pattern detection across past sprints in the ledger. Heuristic-based,
# no ML — the data is already there; this just makes it visible. Goal: the
# prusik gets smarter about its own adoption over time, per-project, without
# needing a central service. Each insight comes with a suggested action,
# not a gate-block.

def _compute_insights(root: Path, json_output: bool = False) -> int:
    ledger_path = root / ".sprint" / "ledger.jsonl"
    if not ledger_path.exists():
        print(f"[prusik-doctor] No ledger yet at {ledger_path} — nothing to "
              f"analyze. Run a sprint first.", file=sys.stderr)
        return 0

    events = []
    for line in ledger_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Per-sprint aggregation
    from datetime import datetime as _dt
    per_sprint: dict = _dd(lambda: {
        "starts": [], "completes": [],
        "rewinds": 0, "fix_rounds": 0, "advance_blocked": 0,
        "evidence_events": 0, "gate_blocked": 0,
    })
    for e in events:
        f = e.get("feature")
        if not f: continue
        ev = e["event"]
        d = per_sprint[f]
        if ev == "sprint_started":
            d["starts"].append(e["ts"])
        elif ev == "sprint_complete":
            d["completes"].append(e["ts"])
        elif ev == "phase_rewind":     d["rewinds"] += 1
        elif ev == "fix_round_start":  d["fix_rounds"] += 1
        elif ev == "advance_blocked":  d["advance_blocked"] += 1
        elif ev == "reviewer_execution_verified": d["evidence_events"] += 1
        elif ev == "gate_blocked":     d["gate_blocked"] += 1

    insights: list[dict[str, Any]] = []

    # Insight 1: sprints with >1 phase_rewind — brief or scope likely vague
    rewind_heavy = [f for f, d in per_sprint.items() if d["rewinds"] > 1]
    if rewind_heavy:
        insights.append({
            "kind": "brief_clarity",
            "severity": "medium",
            "finding": f"{len(rewind_heavy)} sprint(s) had >1 phase_rewind",
            "examples": rewind_heavy[:3],
            "action": "Briefs for rewind-heavy sprints likely under-specified "
                     "scope or success criteria. Tighten brief before sprint-"
                     "start (run `prusik gate brief <path>`); consider scope "
                     "split when feature crosses multiple modules.",
        })

    # Insight 2: sprints with >2 fix-rounds — scope likely too aggressive
    fix_heavy = [f for f, d in per_sprint.items() if d["fix_rounds"] > 2]
    if fix_heavy:
        insights.append({
            "kind": "scope_aggression",
            "severity": "medium",
            "finding": f"{len(fix_heavy)} sprint(s) used >2 fix-rounds",
            "examples": fix_heavy[:3],
            "action": "Fix-round count tracks reviewer rejections per sprint. "
                     ">2 suggests scope too aggressive for one sprint or "
                     "convention drift in builders. Consider trivial-lane "
                     "(`sprint-start --trivial`) for bounded changes; widen "
                     "scope splits for larger work.",
        })

    # Insight 3: wall-clock vs active time per sprint (idle calendar drift)
    def _t(s): return _dt.fromisoformat(s.replace("Z","+00:00"))
    drifty = []
    for f, d in per_sprint.items():
        if not d["starts"] or not d["completes"]: continue
        # Compute active time: sum of gaps <30min between this sprint's events
        feat_events = sorted(
            [e for e in events if e.get("feature")==f],
            key=lambda e: _t(e["ts"])
        )
        if len(feat_events) < 2: continue
        start = _t(feat_events[0]["ts"]); end = _t(feat_events[-1]["ts"])
        wall = (end-start).total_seconds()
        active = 0.0
        for a, b in zip(feat_events[:-1], feat_events[1:]):
            g = (_t(b["ts"])-_t(a["ts"])).total_seconds()
            if g < 1800: active += g
        if wall < 3600: continue  # skip short sprints
        idle_pct = 1 - active / wall
        if idle_pct > 0.90:
            drifty.append((f, wall/3600, active/3600, int(idle_pct*100)))
    if drifty:
        insights.append({
            "kind": "calendar_drift",
            "severity": "low",
            "finding": f"{len(drifty)} sprint(s) had >90% idle wall-clock",
            "examples": [
                f"{f} ({w:.1f}h wall / {a:.1f}h active / {i}% idle)"
                for f, w, a, i in drifty[:3]
            ],
            "action": "Sprints sat idle most of their wall-clock duration. "
                     "Engineering throughput is fine; calendar pacing is the "
                     "bottleneck. Block contiguous 2-3h sessions per sprint "
                     "rather than partial sessions across days. Honor the "
                     "one-sprint-per-session discipline.",
        })

    # Insight 4: F evidence adoption rate (post v0.12.0)
    sprints_with_evidence = sum(1 for d in per_sprint.values()
                                 if d["evidence_events"] > 0)
    sprints_recent = [d for d in per_sprint.values() if d["completes"]]
    if len(sprints_recent) >= 3:
        ratio = sprints_with_evidence / len(sprints_recent)
        if ratio < 0.3:
            insights.append({
                "kind": "f_evidence_uptake",
                "severity": "low",
                "finding": f"F evidence captured in only {sprints_with_evidence}"
                           f"/{len(sprints_recent)} completed sprints",
                "examples": [],
                "action": "Candidate-F (`prusik gate capture`) is shipped but "
                          "rarely invoked. Ensure reviewer role-specs are "
                          "post-v0.12.0 (Step 0 + capture wrapper). Check "
                          "`.claude/agents/regression-sentinel.md` and "
                          "`conventions-enforcer.md` carry the capture-wrapper "
                          "instructions; re-run `prusik refresh` if not.",
            })

    if json_output:
        print(json.dumps({"insights": insights,
                          "sprints_analyzed": len(per_sprint)}, indent=2))
        return 0

    if not insights:
        print(f"[prusik-doctor] {len(per_sprint)} sprints analyzed — no "
              f"actionable patterns detected. Discipline holding clean.")
        return 0

    print(f"[prusik-doctor] Insights from {len(per_sprint)} past sprints:\n")
    for ins in insights:
        sev_marker = {"high":"!!","medium":"!","low":"·"}.get(ins["severity"],"·")
        print(f"  [{sev_marker}] {ins['finding']}  ({ins['kind']})")
        if ins.get("examples"):
            for ex in ins["examples"]:
                print(f"        - {ex}")
        for line in _wrap(ins["action"], 70):
            print(f"        → {line}")
        print()
    return 0


# ============================================================
# v0.17.0 — `prusik doctor --suggest-permissions --apply` (Item 5)
# ============================================================
#
# Close the suggest-permissions loop: with --apply, after the operator
# confirms, write the suggestions directly to .claude/settings.json (for
# Bash allow patterns) and .claude/sprint-config.yaml (for writable
# patterns) via the same additive merge prusik ships everywhere else.
# Never overwrites user values; only adds missing ones.

def _apply_suggestions(root: Path, suggestions: list) -> int:
    """Apply suggest-permissions output to settings.json + sprint-config.yaml
    using the existing additive-merge machinery. Asks operator confirmation
    once (single y/N), then writes both files."""
    if not suggestions:
        print("[prusik-doctor] No suggestions to apply.")
        return 0
    print("[prusik-doctor] About to apply these additions:")
    for s in suggestions:
        print(f"  + {s['kind']}: {s['patch']}   ({s['occurrences']}× denied)")
    resp = input("Apply? [y/N] ").strip().lower()
    if resp != "y":
        print("[prusik-doctor] Cancelled.")
        return 0

    # Build a synthetic 'template' carrying only the additions; merge into
    # the project's current state. The additive merge is exactly the
    # discipline we ship; reuse it rather than hand-roll write logic.
    settings_p = root / ".claude" / "settings.json"
    sprint_p = root / ".claude" / "sprint-config.yaml"
    allow_adds = [s["patch"] for s in suggestions if s["kind"] == "permissions.allow"]
    writable_adds = [s["patch"] for s in suggestions if s["kind"] == "writable"]

    applied = 0
    if allow_adds and settings_p.exists():
        from prusik import refresh_merge
        proj_text = settings_p.read_text()
        # Synthetic template: just the new permissions.allow patterns
        tmpl_text = json.dumps({"permissions": {"allow": allow_adds}}, indent=2)
        merged_text, summary = refresh_merge.merge_settings_json(tmpl_text, proj_text)
        if merged_text != proj_text:
            settings_p.write_text(merged_text)
            n_added = summary.get("permission_additions", {}).get("allow", 0)
            print(f"  ✓ settings.json: +{n_added} allow patterns")
            applied += n_added
        else:
            print("  · settings.json: no-op (patterns already present)")
    if writable_adds and sprint_p.exists():
        # For sprint-config, add to always_writable (cross-phase scope)
        from prusik import refresh_merge
        proj_text = sprint_p.read_text()
        tmpl_text = ("always_writable:\n" +
                     "\n".join(f"  - \"{p}\"" for p in writable_adds) + "\n")
        try:
            merged_text, summary = refresh_merge.merge_sprint_config_yaml(
                tmpl_text, proj_text)
            if merged_text != proj_text:
                sprint_p.write_text(merged_text)
                n_added = len(summary.get("added_top_level_keys", [])) + \
                          sum(summary.get("list_additions", {}).values())
                print(f"  ✓ sprint-config.yaml: +{n_added} writable patterns")
                applied += n_added
            else:
                print("  · sprint-config.yaml: no-op")
        except Exception as e:
            print(f"  ✗ sprint-config.yaml: merge failed ({e}); skipped",
                  file=sys.stderr)
    print(f"\n[prusik-doctor] Applied {applied} additive patches. Re-run "
          f"`prusik doctor --suggest-permissions` to see the new (possibly empty) "
          f"residual.")
    return 0


# ============================================================
# v0.17.0 — `prusik doctor --insights-for-brief` (Item 4)
# ============================================================
#
# Forward-looking risk signal: take a brief file path, look at past sprints
# of similar shape (Type, module overlap), surface predicted risk. The
# insights mechanism currently looks BACKWARD; this surfaces it FORWARD,
# pre-sprint, so the operator can tighten the brief before sprint-start.

def _insights_for_brief(root: Path, brief_path: str,
                         json_output: bool = False) -> int:
    bp = Path(brief_path)
    if not bp.is_absolute():
        bp = root / bp
    if not bp.exists():
        print(f"[prusik-doctor] Brief not found: {brief_path}", file=sys.stderr)
        return 1

    from prusik import schema as _schema
    sections = _schema.parse_sections(bp.read_text())
    btype = sections.get("## Type", "").strip()
    goal = sections.get("## Goal", "")

    # Content-only signals work regardless of ledger history; load events
    # when present (empty list when no ledger yet — content checks still
    # run, history-baseline checks gracefully default to zero).
    ledger_path = root / ".sprint" / "ledger.jsonl"
    events = []
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Per-sprint history (same shape as _compute_insights)
    from collections import defaultdict as _dd
    per_sprint: dict = _dd(lambda: {
        "rewinds": 0, "fix_rounds": 0, "advance_blocked": 0,
        "completed": False,
    })
    for e in events:
        f = e.get("feature")
        if not f: continue
        ev = e["event"]
        d = per_sprint[f]
        if ev == "phase_rewind":     d["rewinds"] += 1
        elif ev == "fix_round_start":  d["fix_rounds"] += 1
        elif ev == "advance_blocked":  d["advance_blocked"] += 1
        elif ev == "sprint_complete":  d["completed"] = True

    # Heuristic — sprints that completed share the brief Type metadata in
    # their brief; we don't have a direct btype-per-sprint index, so use
    # all-sprints + per-feature averages as the baseline.
    # Content-checks (run regardless of ledger history)
    risks = []
    if btype in ("new_feature", "refactor", "migration"):
        risks.append({
            "kind": "lane",
            "severity": "info",
            "msg": f"Type={btype} requires the full lane (trivial-lane "
                   f"REJECTS this Type by brief-Type guard). Plan for "
                   f"scope-critic + plan-critic review steps.",
        })

    # History-baseline checks (only if ledger has data — content checks above
    # run regardless; an empty-ledger first-sprint still gets brief-content
    # signals)
    n = max(1, len(per_sprint))
    avg_rewinds = sum(d["rewinds"] for d in per_sprint.values()) / n if per_sprint else 0
    avg_fix = sum(d["fix_rounds"] for d in per_sprint.values()) / n if per_sprint else 0
    rewind_heavy = sum(1 for d in per_sprint.values() if d["rewinds"] > 1)

    if avg_rewinds > 0.5:
        risks.append({
            "kind": "rewind_baseline",
            "severity": "medium",
            "msg": f"Historical avg {avg_rewinds:.1f} phase_rewinds/sprint "
                   f"({rewind_heavy} sprints had >1). Most rewinds trace to "
                   f"under-specified scope or success criteria. Run "
                   f"`prusik brief-lint <path>` and `prusik gate brief <path>` "
                   f"first; consider scope split if Goal touches >2 modules.",
        })
    if avg_fix > 1.0:
        risks.append({
            "kind": "fix_round_baseline",
            "severity": "medium",
            "msg": f"Historical avg {avg_fix:.1f} fix-rounds/sprint. Common "
                   f"causes: scope too aggressive, conventions drift in "
                   f"builders. For this brief: tighten Success criteria so "
                   f"reviewers have unambiguous accept conditions.",
        })
    # Heuristic on the brief itself
    if len(goal.strip()) < 100:
        risks.append({
            "kind": "thin_goal",
            "severity": "medium",
            "msg": f"## Goal section is short ({len(goal.strip())} chars). "
                   f"Thin goals are the leading cause of rewinds — the "
                   f"scope-critic + plan-critic both depend on a clear goal. "
                   f"Expand before sprint-start.",
        })

    if json_output:
        print(json.dumps({"brief": str(bp), "type": btype, "risks": risks},
                        indent=2))
        return 0

    if not risks:
        print(f"[prusik-doctor] Brief looks clean for sprint-start "
              f"(Type={btype}, history baseline OK).")
        return 0
    print(f"[prusik-doctor] Risk signals for {bp.name} (Type={btype}):\n")
    for r in risks:
        sev = {"high":"!!","medium":"!","low":"·","info":"i"}.get(r["severity"],"·")
        print(f"  [{sev}] ({r['kind']})")
        for line in _wrap(r["msg"], 70):
            print(f"        {line}")
        print()
    return 0


# ============================================================
# v0.17.0 — `prusik doctor --sprint <feature>` (Item 10)
# ============================================================
#
# Single-sprint retrospective view: chronological event trail with
# durations + classifications. The ledger has the data; this is a focused
# display. Useful for retro authoring + post-mortem investigations.

def _sprint_stats(root: Path, feature: str, json_output: bool = False) -> int:
    ledger_path = root / ".sprint" / "ledger.jsonl"
    if not ledger_path.exists():
        print(f"[prusik-doctor] No ledger at {ledger_path}.", file=sys.stderr)
        return 1
    from datetime import datetime as _dt
    def _t(s): return _dt.fromisoformat(s.replace("Z","+00:00"))

    events = []
    for line in ledger_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            if ev.get("feature") == feature:
                events.append(ev)
        except json.JSONDecodeError:
            continue
    if not events:
        print(f"[prusik-doctor] No events for feature {feature!r}.",
              file=sys.stderr)
        return 1
    events.sort(key=lambda e: _t(e["ts"]))

    start = _t(events[0]["ts"])
    end = _t(events[-1]["ts"])
    wall_h = (end - start).total_seconds() / 3600
    active_s = 0.0
    for a, b in zip(events[:-1], events[1:]):
        g = (_t(b["ts"]) - _t(a["ts"])).total_seconds()
        if g < 1800: active_s += g
    active_h = active_s / 3600

    summary = {
        "feature": feature,
        "started": events[0]["ts"],
        "ended": events[-1]["ts"],
        "completed": any(e["event"] == "sprint_complete" for e in events),
        "wall_hours": round(wall_h, 2),
        "active_hours": round(active_h, 2),
        "idle_pct": int(100 * (1 - active_h / wall_h)) if wall_h else 0,
        "events_total": len(events),
        "phase_advances": sum(1 for e in events if e["event"] == "phase_advance"),
        "phase_rewinds": sum(1 for e in events if e["event"] == "phase_rewind"),
        "fix_rounds": sum(1 for e in events if e["event"] == "fix_round_start"),
        "advance_blocked": sum(1 for e in events if e["event"] == "advance_blocked"),
        "gate_blocked": sum(1 for e in events if e["event"] == "gate_blocked"),
        "evidence_events": sum(1 for e in events
                               if e["event"] == "reviewer_execution_verified"),
        "critic_verdicts": sum(1 for e in events
                               if e["event"] == "critic_verdict"),
    }
    if json_output:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"[prusik-doctor] Sprint: {feature}")
    print(f"  status:          {'COMPLETED' if summary['completed'] else 'INCOMPLETE'}")
    print(f"  started:         {summary['started']}")
    print(f"  ended:           {summary['ended']}")
    print(f"  wall:            {summary['wall_hours']}h")
    print(f"  active:          {summary['active_hours']}h ({summary['idle_pct']}% idle)")
    print(f"  events:          {summary['events_total']}")
    print(f"  phase_advances:  {summary['phase_advances']}")
    print(f"  phase_rewinds:   {summary['phase_rewinds']}")
    print(f"  fix_rounds:      {summary['fix_rounds']}")
    print(f"  advance_blocked: {summary['advance_blocked']}")
    print(f"  gate_blocked:    {summary['gate_blocked']}")
    print(f"  evidence_events: {summary['evidence_events']}")
    print(f"  critic_verdicts: {summary['critic_verdicts']}")
    if summary['phase_rewinds'] > 1 or summary['fix_rounds'] > 2 or \
       summary['idle_pct'] > 90:
        print()
        print("  Retro signals to consider:")
        if summary['phase_rewinds'] > 1:
            print(f"    - {summary['phase_rewinds']} phase_rewinds — was the brief specific enough?")
        if summary['fix_rounds'] > 2:
            print(f"    - {summary['fix_rounds']} fix_rounds — was scope too aggressive?")
        if summary['idle_pct'] > 90:
            print(f"    - {summary['idle_pct']}% idle wall-clock — calendar pacing not throughput")
    return 0
