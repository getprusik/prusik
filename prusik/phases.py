"""Phase FSM engine.

Reads .claude/sprint-config.yaml (project-level declaration of phases + rules)
and .sprint/state.json (current phase for the active sprint).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

from prusik.ledger import project_root


def load_sprint_config(root: Path | None = None) -> dict | None:
    root = root or project_root()
    config_path = root / ".claude" / "sprint-config.yaml"
    if not config_path.exists():
        return None
    with open(config_path) as f:
        return yaml.safe_load(f)


def current_sprint_state(root: Path | None = None) -> dict | None:
    root = root or project_root()
    path = root / ".sprint" / "state.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def set_sprint_state(state: dict, root: Path | None = None) -> None:
    """Write the sprint state. NOTE: this OVERWRITES the whole state dict —
    it does not merge. Any caller that advances/transitions must carry
    forward keys that should persist (e.g. v0.11.0 `lane`); `gate.advance`
    does this explicitly. Footgun confirmed *lower* severity in v0.11.0 #3:
    the clean idiom is to keep persistent decisions in the LEDGER (the
    audit trail, derive-don't-store) rather than add sprint-state keys —
    prefer that over growing this dict. Documented, not changed: many
    callers depend on the replace semantics (sprint_start sets fresh
    state by design)."""
    root = root or project_root()
    path = root / ".sprint" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def clear_sprint_state(root: Path | None = None) -> None:
    root = root or project_root()
    path = root / ".sprint" / "state.json"
    if path.exists():
        path.unlink()


def get_phase_spec(config: dict, phase_name: str | None) -> dict | None:
    for p in config.get("phases", []):
        if p["name"] == phase_name:
            return p
    return None


# Canonical forward order. If config defines phases in this order, we can
# detect rewinds (requests to advance BACKWARDS). Unknown phases fall off
# the ordered list and bypass the rewind check.
#
# v0.11.0 #5: THIS LIST IS THE SINGLE SOURCE OF TRUTH for the phase model.
# Prose descriptions of the FSM in the docs must match this list and should
# cite it explicitly. Historical drift: a doc once omitted `solo_execute`
# from its prose — if you change this list, grep the docs.
_PHASE_ORDER = [
    "scoping",
    "triage",
    "planning",
    "solo_execute",
    "building",
    "reviewing",
    "integrating",
]


def phase_index(phase_name: str) -> int:
    """Return the index of phase_name in the canonical order, or -1 if unknown."""
    try:
        return _PHASE_ORDER.index(phase_name)
    except ValueError:
        return -1


def is_rewind(current: str | None, target: str) -> bool:
    """True if advancing from `current` to `target` goes BACKWARDS in the
    canonical phase order. Unknown phases (not in _PHASE_ORDER) never
    trigger rewind detection — safer to let them through than to
    false-positive on an extension phase."""
    if current is None:
        return False
    ci = phase_index(current)
    ti = phase_index(target)
    if ci < 0 or ti < 0:
        return False
    return ti < ci


def resolve_path(template: str, feature: str | None = None, teammate: str | None = None) -> str:
    out = template
    if feature is not None:
        out = out.replace("{feature}", feature)
    if teammate is not None:
        out = out.replace("{teammate}", teammate)
    return out


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a simple glob (** and *) to a regex."""
    parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and i + 1 < len(pattern) and pattern[i + 1] == "*":
            parts.append(".*")
            i += 2
        elif c == "*":
            parts.append("[^/]*")
            i += 1
        elif c in r".^$+?()[]{}|\\":
            parts.append(re.escape(c))
            i += 1
        else:
            parts.append(c)
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def writable_patterns(config: dict, phase_name: str, feature: str | None) -> list[str]:
    spec = get_phase_spec(config, phase_name)
    if not spec:
        return []
    # Resolve per-teammate wildcards loosely: worktrees/{teammate}/** stays as worktrees/*/**
    return [resolve_path(p, feature=feature, teammate="*") for p in spec.get("writable", [])]


# Prusik-internal paths that are ALWAYS writable, independent of project config
# (v0.8.3, B27). These are infrastructure prusik itself owns: heartbeats
# that watchdog reads, status crumbs that roles emit for orchestrator
# visibility. Hardcoded at the engine layer — not just template layer — so
# projects with pre-v0.6.1 sprint-config.yaml that `prusik refresh` keeps
# skipping (because user-modified) still get the right behavior. Without
# this, builder-emitted status writes generate ~6-8 false-positive
# `gate_blocked` events per sprint (live-cc's m2-s5/s6/s7/app-container
# observation, 4-sprint recurrence trigger met).
#
# v0.10.0 (Fix 4, design-passes/v0.10.0-derive-and-delta-regate.md):
# generalized from `.sprint/status/**` to the full phase-independent
# meta-artifact set. Mining of session 382ea180 (m4-s8c) showed the
# LARGEST gate_blocked category was the phase write-lock blocking
# legitimate orchestrator writes — build reports, verify scripts, and
# fatally the new brief needed to express a mid-sprint pivot (20:14:29:
# "the active m4-s8c sprint locks writes to its own reports during
# `reviewing` — I can't author the new brief"). The allowlist is keyed
# to phase POSITION, but a correct pivot is non-linear: it must author
# upstream-phase artifacts from a downstream phase. None of these are
# product code, so widening cannot threaten the scope invariant
# (monotone, ungameable). A pivot's first act is authoring the corrected
# brief — never block describing the next thing.
_KIT_INTERNAL_ALWAYS_WRITABLE: tuple[str, ...] = (
    ".sprint/**",          # all prusik-owned sprint housekeeping (status, state, fix-round)
    "reports/**",          # orchestrator meta-artifacts: build reports + reviewer rationale
    "scripts/verify/**",   # v0.9.0 success-criteria verify harness — phase-independent
    "briefs/**",           # next-feature briefs — a pivot must author the corrected brief
    # v0.83.0 (field finding #17): the deviation LOG only — the sanctioned record of
    # legitimate mid-build drift that the builder roles are told to write but the
    # building write-lock was blocking (scope.md/plan.md stay controlled — the
    # boundary is not amendable mid-build; the honest log of departures from it
    # is). The boundary check credits files recorded here.
    "design/*/deviations.md",
    # v0.15.0: OS-scratch namespaces. /tmp and project-local .runtime/ are
    # transient, gitignored everywhere, and the natural place agents reach
    # for scratch. Pre-v0.15.0 prusik was fighting agents over OS scratch
    # (5+ gate_blocked events on an adopter just in the recent window: redirects to
    # /tmp/server.log, /tmp/orig_errors, .runtime/regression-*, etc.). The
    # writable-pattern invariant is about *project state*; OS scratch isn't
    # project state. Engine-hardcoded so it works on any sprint-config age.
    "/tmp/**",             # POSIX scratch (Linux native)
    "/private/tmp/**",     # macOS — /tmp resolves through symlink to here
    ".runtime/**",         # project-local transient-state convention
)


def always_writable_patterns(config: dict, feature: str | None) -> list[str]:
    """Globs that are always-writable, regardless of phase.

    Two sources, unioned:
    - Prusik-internal paths (heartbeats, status crumbs) — hardcoded so the
      prusik's own infrastructure functions even on older project configs.
    - Project-declared `always_writable` from sprint-config.yaml — for
      meta-artifacts the project wants to span phases (trial journals,
      bridge files, project-level docs).
    """
    combined: list[str] = list(_KIT_INTERNAL_ALWAYS_WRITABLE)
    raw_proj = config.get("always_writable", []) or []
    combined.extend(raw_proj)
    # Dedup while preserving order so prusik-internals appear first in any
    # debug/error message but project entries that duplicate them collapse.
    seen: set[str] = set()
    deduped: list[str] = []
    for pat in combined:
        if pat not in seen:
            seen.add(pat)
            deduped.append(pat)
    return [resolve_path(p, feature=feature, teammate="*") for p in deduped]


def _expand_user_and_env(pat: str) -> str:
    """Expand ~ and $VAR in a glob pattern so always_writable can reference
    paths outside the project tree (e.g. `~/.claude/prusik/bridges/**`)."""
    return os.path.expanduser(os.path.expandvars(pat))


def is_path_writable(target: str, config: dict, phase_name: str,
                     feature: str | None, root: Path | None = None) -> tuple[bool, str | None]:
    root = root or project_root()
    t = Path(target)
    if not t.is_absolute():
        t = root / t
    try:
        t_resolved = t.resolve()
    except OSError:
        return False, f"cannot resolve path: {target}"

    root_resolved = root.resolve()

    # Is the target inside the project tree? (None ⇒ out-of-tree.) Computed once;
    # it governs which always-writable matches may apply.
    try:
        rel: Path | None = t_resolved.relative_to(root_resolved)
    except ValueError:
        rel = None

    # Global escape hatch FIRST — checked before the phase containment rules.
    # ABSOLUTE always-writable patterns (~/.claude/… for the bridge + CC memory,
    # /tmp scratch) exist for paths OUTSIDE the project tree, ON PURPOSE. They
    # must NOT disable the writable gate for the project's OWN files when the
    # project itself lives under such a path — e.g. a /tmp checkout in CI. The
    # /tmp→/private/tmp symlink made this blindspot easy to miss (both forms are
    # allowlisted, so any /tmp-rooted project had its scope-drift gate silently
    # off). So the absolute match applies ONLY out-of-tree (rel is None);
    # in-tree paths are governed by the RELATIVE patterns below + phase rules.
    for pat in always_writable_patterns(config, feature):
        expanded = _expand_user_and_env(pat)
        if rel is None and _glob_to_regex(expanded).match(str(t_resolved)):
            return True, None
        # Relative form, for in-tree entries like reports/kit-trial/** or
        # .sprint/status/** — these are intentional in-tree escape hatches.
        if rel is not None and _glob_to_regex(pat).match(str(rel)):
            return True, None

    # Project-root containment check for phase-specific writable patterns.
    if rel is None:
        return False, f"path outside project root: {target}"
    rel_str = str(rel)

    patterns = writable_patterns(config, phase_name, feature)
    # v0.5.7: fix-round extension. When a fix round is active AND current
    # phase is `reviewing`, builders need to patch in worktrees/. Extend
    # writable for the duration of the round. Strictly scoped to reviewing —
    # other phases are unaffected.
    if phase_name == "reviewing":
        from prusik import fix_round as _fix_round
        if _fix_round.is_active(root):
            patterns = list(patterns) + ["worktrees/*/**"]
            # FAN-OUT LANE (fb-7ab319116f42): the blast-radius gate PREDICTED
            # these reverse-dep files pre-build; make exactly that system-computed set
            # writable in project root this round, so a field-adding sprint fixes its
            # predicted fan-out in-flow rather than overriding the gate. The set is
            # the prediction, never an agent claim — a non-predicted root file stays
            # blocked. Added as exact paths (they match rel_str directly).
            patterns = patterns + _fix_round.fan_out_files(root)
    if not patterns:
        return True, None  # phase declares no writable restrictions → allow
    for pat in patterns:
        if _glob_to_regex(pat).match(rel_str):
            return True, None
    return False, (f"'{rel_str}' not in writable patterns for phase '{phase_name}': {patterns}")


def print_status() -> int:
    state = current_sprint_state()
    if not state:
        print("No active sprint. Write briefs/<feature>.md and run /sprint-start <feature>.")
        return 0
    config = load_sprint_config()
    phase = state.get("phase")
    feature = state.get("feature")
    print(f"Sprint:   {feature}")
    print(f"Phase:    {phase}")
    # v0.6.3 (B8): surface pause state and reason if active. Useful when an
    # operator returns to a stale CC session and is wondering why the Stop
    # hook isn't firing on phase exits.
    from prusik import pause as _pause
    pause_state = _pause._read_marker()
    if pause_state is not None:
        reason = pause_state.get("reason")
        if reason:
            print(f"Paused:   yes — {reason}")
        else:
            print("Paused:   yes")
    if config:
        spec = get_phase_spec(config, phase)
        if spec:
            print(f"Writable: {spec.get('writable', [])}")
            if "budget_tokens" in spec:
                print(f"Budget:   {spec['budget_tokens']:,} tokens")
            if "exit_artifacts" in spec:
                print("Exit artifacts required:")
                for art in spec["exit_artifacts"]:
                    path = resolve_path(art["path"], feature=feature)
                    exists = (project_root() / path).exists()
                    mark = "✓" if exists else "·"
                    print(f"  {mark} {path}")
    return 0
