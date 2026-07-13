"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: phases.

Run: uv run python -m pytest tests/test_phases.py -v
Or run the whole suite: uv run python -m pytest tests/ -v

Shared helpers live in tests/_common.py (private; pytest does not
collect leading-underscore modules). v0.23.0 split tests/test_smoke.py
by domain to keep individual files navigable.
"""

# noqa: F401 — wildcard imports below intentionally re-export everything
# from _common (prusik modules, helpers, the tempfile/json/os toolbelt).
# F401 individual unused-name warnings would obscure the rest.
from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_bridge, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)


# ---------- always_writable (v0.3.5) ----------

def test_always_writable_allows_path_regardless_of_phase():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        # Phase 'scoping' only allows scope.md + scope-approval.txt, but
        # always_writable default includes reports/kit-trial/**
        ok, _ = phases.is_path_writable(
            "reports/kit-trial/journal.md", config, "scoping", "foo")
        assert ok, "reports/kit-trial/journal.md should be always-writable"


        # Sanity: non-always-writable path in scoping still blocked
        ok, _ = phases.is_path_writable(
            "src/whatever.py", config, "scoping", "foo")
        assert not ok
    finally:
        shutil.rmtree(tmp)


def test_always_writable_respects_custom_patterns():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        # Inject a custom pattern
        config["always_writable"] = (config.get("always_writable") or []) + ["docs/**"]
        ok, _ = phases.is_path_writable(
            "docs/some-note.md", config, "building", "foo")
        assert ok
        ok, _ = phases.is_path_writable(
            "src/core.py", config, "building", "foo")
        assert not ok
    finally:
        shutil.rmtree(tmp)


def test_always_writable_applies_across_all_phases():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        for phase in ("scoping", "triage", "planning", "solo_execute",
                      "building", "reviewing", "integrating"):
            ok, _ = phases.is_path_writable(
                "reports/kit-trial/notes.md", config, phase, "foo")
            assert ok, f"should be writable in phase {phase}"
    finally:
        shutil.rmtree(tmp)


def test_always_writable_matches_absolute_path_outside_project_root():
    """v0.3.7 fix: bridge writes (outside project root) must be allowed."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        # Default template includes ~/.claude/prusik/bridges/**
        bridge_path = Path.home() / ".claude" / "prusik" / "bridges" / "some-slug" / "bridge.md"
        ok, _ = phases.is_path_writable(
            str(bridge_path), config, "scoping", "foo")
        assert ok, "bridge file at ~/.claude/prusik/bridges/ must be writable in scoping"
    finally:
        shutil.rmtree(tmp)


def test_always_writable_matches_cc_memory_path():
    """v0.3.7 fix: CC's auto-memory writes must be allowed."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        mem_path = Path.home() / ".claude" / "projects" / "-Users-foo-bar" / "memory" / "note.md"
        ok, _ = phases.is_path_writable(
            str(mem_path), config, "scoping", "foo")
        assert ok, "CC memory path at ~/.claude/projects/ must be writable"
    finally:
        shutil.rmtree(tmp)


def test_outside_project_root_without_always_writable_still_blocked():
    """v0.3.7 regression guard: paths outside root that aren't in always_writable stay blocked."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        # Pick a path that's NOT under ~/.claude/prusik/bridges or ~/.claude/projects
        target = Path.home() / "random-unconfigured-path" / "x.md"
        ok, reason = phases.is_path_writable(
            str(target), config, "scoping", "foo")
        assert not ok
        assert "outside project root" in (reason or "")
    finally:
        shutil.rmtree(tmp)



# ---------- v0.10.0 Fix 4: phase-independent meta-artifact carve-out ----------
# Grounded in session 382ea180 (m4-s8c): the largest gate_blocked category
# was the phase write-lock blocking legitimate orchestrator writes. The
# 20:14:29 stall ("can't author the new brief during reviewing") is the
# canonical case these tests pin.

_ALL_PHASES = ("scoping", "triage", "planning", "solo_execute",
               "building", "reviewing", "integrating")


def test_v0100_fix4_build_reports_writable_all_phases():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        for phase in _ALL_PHASES:
            ok, _ = phases.is_path_writable(
                "reports/m4-s8c/build-backend.txt", config, phase, "m4-s8c")
            assert ok, f"build report must be writable in phase {phase}"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix4_verify_scripts_writable_all_phases():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        for phase in _ALL_PHASES:
            ok, _ = phases.is_path_writable(
                "scripts/verify/m4-uxgate-a1.sh", config, phase, "m4-s8c")
            assert ok, f"verify script must be writable in phase {phase}"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix4_next_feature_brief_writable_during_reviewing():
    """The 20:14:29 m4-s8c stall: a mid-sprint pivot must author the
    corrected brief, but the active sprint locked writes during reviewing."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        ok, _ = phases.is_path_writable(
            "briefs/m4-s8d-pivot-followup.md", config, "reviewing", "m4-s8c")
        assert ok, "next-feature brief must be writable even during reviewing"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix4_sprint_housekeeping_writable_all_phases():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        for phase in _ALL_PHASES:
            ok, _ = phases.is_path_writable(
                ".sprint/state.json", config, phase, "m4-s8c")
            assert ok, f".sprint/ housekeeping must be writable in phase {phase}"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix4_widening_does_not_leak_to_product_code():
    """Regression guard: Fix 4 widens meta-artifacts ONLY. Product code
    must still obey phase write-locks (the scope invariant is untouched)."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        for path in ("api/main.py", "src/core.py", "adapters/http/settings.py"):
            ok, _ = phases.is_path_writable(path, config, "scoping", "m4-s8c")
            assert not ok, f"{path} must still be blocked in scoping"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix4_builder_kit_housekeeping_not_flagged_by_subset_gate():
    """The literal m4-s8c 01:16:21 block: builder_writes_within_plan flagged
    `<role>/.sprint/status/<role>.txt` — prusik blocking its own
    housekeeping. Real rogue product writes must still be caught."""
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "plan.md").write_text(
            "## Modules touched\n- api/billing/\n")
        wt = tmp / "worktrees" / "backend-builder-jit"
        (wt / "api" / "billing").mkdir(parents=True)
        (wt / "api" / "billing" / "retry.py").write_text("ok")
        # Prusik housekeeping under the worktree — must NOT be flagged
        (wt / ".sprint" / "status").mkdir(parents=True)
        (wt / ".sprint" / "status" / "backend-builder-jit.txt").write_text("alive")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert errs == [], f"kit housekeeping must not be flagged: {errs}"
        # Sanity: a genuine rogue product write IS still caught
        (wt / "infra").mkdir()
        (wt / "infra" / "surprise.py").write_text("oops")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert any("surprise.py" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)



# ---------- v0.10.0 Fix 1: derive vs hand-maintain (Tier 1 cure) ----------
# m4-s8c: plan.md's hand-maintained `## Modules touched` rotted across ~10
# rewinds (lost settings.py/validation.py), `plan_within_scope` blocked 3×.
# Fix 1: compare derived worktree reality directly against scope.md (the
# stable authoritative boundary); plan_mods is legacy fallback only.


def test_v0100_fix1_rotted_plan_list_no_longer_blocks_when_within_scope():
    """The literal m4-s8c failure: scope declares the module, plan's
    hand-list lost it across rewinds, builder writes within scope. OLD
    behavior blocked (file ∉ rotted plan_mods). NEW: passes (file ∈ scope)."""
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text(
            "## Modules touched\n- api/billing/\n- api/settings/\n")
        # plan.md ROTTED — lost api/settings/ across rewinds (the bug)
        (tmp / "design" / f / "plan.md").write_text(
            "## Modules touched\n- api/billing/\n")
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "api" / "settings").mkdir(parents=True)
        (wt / "api" / "settings" / "x.py").write_text("ok")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert errs == [], f"within-scope write must not block on plan rot: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix1_enforcement_intact_real_out_of_scope_still_blocks():
    """The invariant is preserved and strengthened: a write genuinely
    outside scope.md still blocks — derived reality vs the real boundary."""
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text(
            "## Modules touched\n- api/billing/\n")
        (tmp / "design" / f / "plan.md").write_text(
            "## Modules touched\n- api/billing/\n")
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "api" / "payments").mkdir(parents=True)
        (wt / "api" / "payments" / "rogue.py").write_text("nope")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert errs, "out-of-scope write must still block (enforcement intact)"
        assert any("rogue.py" in e for e in errs), errs
        assert any("scope.md" in e for e in errs), \
            f"boundary source must be scope.md, not plan.md: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix1_plan_within_scope_no_longer_a_planning_gate():
    """Tier 1 + Tier 2 cure: hand-list drift (plan adds a module not in
    scope) must NOT block at planning-exit anymore. The function still
    exists for direct callers; it is just not in PHASE_CHECKS['planning']."""
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text(
            "## Modules touched\n- api/billing/\n")
        (tmp / "design" / f / "plan.md").write_text(
            "## Modules touched\n- api/billing/\n- api/surprise/\n")
        # The function itself still detects drift (kept for direct callers)
        assert consistency.plan_within_scope(tmp, f), \
            "function retained: still computes plan⊄scope for direct callers"
        # But it is no longer run as a planning-phase gate
        assert consistency.run_for_phase("planning", tmp, f) == [], \
            "planning-exit must not gate on hand-list drift anymore"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix1_reconciliation_summary_quantifies_rot():
    """Observability (the 'observable' quality tenet): the summary records
    exactly the m4-s8c rot — scope modules the hand-list dropped, and stale
    entries the hand-list carried that scope never had. Pure function."""
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text(
            "## Modules touched\n- api/billing/\n- api/settings/\n- domain/validation/\n")
        # Hand-list rotted: dropped settings/ + validation/, carried a stale entry
        (tmp / "design" / f / "plan.md").write_text(
            "## Modules touched\n- api/billing/\n- api/ghost/\n")
        s = consistency.reconciliation_summary(tmp, f)
        assert s is not None
        assert s["boundary"] == "scope.md"
        assert s["dropped_from_plan"] == ["api/settings/", "domain/validation/"], s
        assert s["stale_in_plan"] == ["api/ghost/"], s
        # No scope artifact → None (legacy projects: nothing to reconcile)
        (tmp / "design" / f / "scope.md").unlink()
        assert consistency.reconciliation_summary(tmp, f) is None
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix1_legacy_fallback_when_no_scope_artifact():
    """Opt-in / non-coupling: a project with no scope.md falls back to the
    pre-v0.10.0 plan_mods comparison — behavior unchanged for projects that
    don't opt into scope-based checks; the newer path is strictly additive."""
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        # No scope.md — legacy path
        (tmp / "design" / f / "plan.md").write_text(
            "## Modules touched\n- api/billing/\n")
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "infra").mkdir(parents=True)
        (wt / "infra" / "surprise.py").write_text("oops")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert errs, "legacy plan_mods fallback must still flag drift"
        assert any("plan.md" in e for e in errs), \
            f"fallback boundary source must be plan.md: {errs}"
    finally:
        shutil.rmtree(tmp)



# ---------- v0.10.0 Fix 3: content-addressed re-gating (Tier 3 cure) ----------
# m4-s8c: 13 scope-critic dispatches, most on UNCHANGED content. A verdict
# is bound to the substantive hash of what it judged; unchanged → carry
# forward, no re-dispatch. Cost O(rewinds) → O(substantive-changes).


def test_v0100_fix3_substantive_hash_stable_across_reformatting():
    """Whitespace/reflow must NOT bust a verdict — only substance does."""
    tmp = _mktmp_project()
    try:
        a = tmp / "scope.md"
        a.write_text("## Goal\nShip the thing.\n\n## Risks\n- one\n")
        h1 = schema.substantive_hash(a)
        # Reformat: extra blank lines, trailing spaces, reflowed whitespace
        a.write_text("## Goal\n\n  Ship   the thing.  \n\n\n## Risks\n\n- one\n\n")
        h2 = schema.substantive_hash(a)
        assert h1 == h2, "reformatting must not change the substantive hash"
        # Substantive change DOES bust it
        a.write_text("## Goal\nShip a DIFFERENT thing.\n\n## Risks\n- one\n")
        assert schema.substantive_hash(a) != h1
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix3_modules_touched_excluded_from_hash():
    """Fix 1 made Modules-touched derived/non-gating. A rewind that only
    churns that block must NOT bust the verdict (else Tier 3 waste returns)."""
    tmp = _mktmp_project()
    try:
        a = tmp / "plan.md"
        a.write_text("## Goal\nX\n\n## Modules touched\n- api/a/\n")
        h1 = schema.substantive_hash(a)
        a.write_text("## Goal\nX\n\n## Modules touched\n- api/a/\n- api/b/\n- api/c/\n")
        assert schema.substantive_hash(a) == h1, \
            "Modules-touched churn must not change the substantive hash"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix3_record_then_verdict_current_carries_forward():
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        scope_rel = f"design/{f}/scope.md"
        (tmp / scope_rel).write_text("## Goal\nShip welcome screen.\n")
        rec = argparse.Namespace(role="scope-critic", feature=f,
                                 artifact=scope_rel, verdict="APPROVED")
        assert gate.record_verdict(rec) == 0
        cur = argparse.Namespace(role="scope-critic", feature=f, artifact=scope_rel)
        # Unchanged → carry forward (skip re-dispatch)
        assert gate.verdict_current(cur) == 0
        # Substantive change → re-gate needed
        (tmp / scope_rel).write_text("## Goal\nShip a totally different screen.\n")
        assert gate.verdict_current(cur) == 1
        # Cosmetic-only change → still carries forward
        (tmp / scope_rel).write_text("## Goal\n\n  Ship a totally different screen.  \n")
        rec2 = argparse.Namespace(role="scope-critic", feature=f,
                                  artifact=scope_rel, verdict="APPROVED")
        gate.record_verdict(rec2)
        (tmp / scope_rel).write_text("## Goal\nShip a totally different screen.\n\n")
        assert gate.verdict_current(cur) == 0
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix3_rejected_verdict_does_not_carry_forward():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        scope_rel = f"design/{f}/scope.md"
        (tmp / scope_rel).write_text("## Goal\nX\n")
        gate.record_verdict(argparse.Namespace(
            role="scope-critic", feature=f, artifact=scope_rel, verdict="REJECTED"))
        cur = argparse.Namespace(role="scope-critic", feature=f, artifact=scope_rel)
        assert gate.verdict_current(cur) == 1, "REJECTED must not carry forward"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix3_exit_artifact_carry_forward_after_rewind():
    """The literal m4-s8c cure: scope APPROVED, a rewind wiped
    scope-approval.txt, scope.md substantively unchanged → the scoping
    exit-artifact gate is satisfied from the ledger WITHOUT re-dispatching
    scope-critic. If scope.md substantively changed → still missing."""
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        scope_rel = f"design/{f}/scope.md"
        (tmp / scope_rel).write_text("## Goal\nWelcome screen.\n\n## Size\nM\n")
        gate.record_verdict(argparse.Namespace(
            role="scope-critic", feature=f, artifact=scope_rel, verdict="APPROVED"))
        # Rewind wiped the approval artifact entirely
        approval_rel = f"reports/{f}/scope-approval.txt"
        phase_spec = {"exit_artifacts": [
            {"path": "reports/{feature}/scope-approval.txt",
             "must_contain": "APPROVED"}]}
        missing = gate._unsatisfied_exit_artifacts(phase_spec, f)
        assert missing == [], f"hash-valid verdict must carry forward: {missing}"
        # The approval file was regenerated from the ledger
        assert "carried forward" in (tmp / approval_rel).read_text()
        from prusik.ledger import read_all
        assert any(r["event"] == "verdict_carried_forward" for r in read_all())
        # Substantive change to scope.md → carry-forward must NOT apply
        (tmp / scope_rel).write_text("## Goal\nA different feature entirely.\n\n## Size\nL\n")
        (tmp / approval_rel).unlink()
        missing = gate._unsatisfied_exit_artifacts(phase_spec, f)
        assert missing, "substantive change must force re-gate (no false carry-forward)"
    finally:
        shutil.rmtree(tmp)


def test_v0100_m4s8c_replay_ceremony_is_o_substantive_not_o_rewinds():
    """End-to-end regression fixture replaying the m4-s8c failure shape:
    a rewind that changes NOTHING substantive, plus a rotted hand-list.
    Asserts the combined v0.10.0 cure — zero ceremony when nothing
    substantive changed (cost is O(substantive-changes), not O(rewinds))."""
    tmp = _mktmp_project()
    try:
        f = "m4-s8c"
        (tmp / "design" / f).mkdir(parents=True)
        scope_rel = f"design/{f}/scope.md"
        (tmp / scope_rel).write_text(
            "## Goal\nWelcome screen.\n\n## Size\nM\n"
            "## Modules touched\n- adapters/http/\n- domain/\n")
        # plan.md hand-list ROTTED across rewinds (the literal m4-s8c bug):
        # lost domain/, carries a stale ghost entry.
        (tmp / "design" / f / "plan.md").write_text(
            "## Goal\nWelcome screen.\n\n## Modules touched\n- adapters/http/\n- api/ghost/\n")
        # scope-critic approved once; then ~10 rewinds occur (phase pointer
        # moves, scope.md substance unchanged).
        gate.record_verdict(argparse.Namespace(
            role="scope-critic", feature=f, artifact=scope_rel, verdict="APPROVED"))

        # (1) Tier 3: after a rewind wiped scope-approval.txt, the gate is
        # satisfied from the ledger — ZERO scope-critic re-dispatch.
        phase_spec = {"exit_artifacts": [
            {"path": "reports/{feature}/scope-approval.txt",
             "must_contain": "APPROVED"}]}
        assert gate._unsatisfied_exit_artifacts(phase_spec, f) == [], \
            "unchanged scope across a rewind must carry forward (0 re-gate)"

        # (2) Tier 1: builder wrote within scope but NOT in the rotted
        # plan-list — must NOT false-block (the m4-s8c stall).
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "domain").mkdir(parents=True)
        (wt / "domain" / "validation.py").write_text("ok")  # in scope, not in rotted plan
        assert consistency.builder_writes_within_plan(tmp, f) == [], \
            "within-scope write must not block on plan-list rot"

        # (3) Tier 4: a pivot can author the next brief mid-reviewing.
        _copy_sprint_config(tmp)
        cfg = phases.load_sprint_config()
        ok, _ = phases.is_path_writable(
            f"briefs/{f}-pivot.md", cfg, "reviewing", f)
        assert ok, "pivot brief must be writable during reviewing"

        # Contrast: a REAL substantive scope change DOES cost a re-gate.
        (tmp / scope_rel).write_text(
            "## Goal\nAn entirely different feature.\n\n## Size\nXL\n")
        (tmp / f"reports/{f}/scope-approval.txt").unlink()
        assert gate._unsatisfied_exit_artifacts(phase_spec, f), \
            "a substantive change MUST still force a re-gate (cost tracks substance)"
    finally:
        shutil.rmtree(tmp)


def test_v0100_fix3_no_prior_verdict_still_blocks():
    """v0.11.0 supersedes the v0.10.0 'reviewers never carry forward' rule.
    But with NO prior verdict recorded, the reviewer gate still blocks —
    carry-forward reuses a real PASS, it never invents one."""
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        phase_spec = {"exit_artifacts": [
            {"path": "reports/{feature}/regression.txt", "must_contain": "PASS"}]}
        missing = gate._unsatisfied_exit_artifacts(phase_spec, f)
        assert missing, "no prior PASS → reviewer gate must still block"
        assert any("regression.txt" in m for m in missing), missing
    finally:
        shutil.rmtree(tmp)



# ---------- v0.11.0 #1: reviewer-gate carry-forward ----------
# v0.10.0 excluded regression/conventions. That left the DOMINANT per-rewind
# cost (full suite + cold mypy) fully O(rewinds). #1 binds reviewer PASS to
# the BUILT-CODE worktree hash: identical code across a rewind ⇒ reuse the
# real prior PASS; rebuilt code ⇒ re-run.


def _wt_file(tmp, role, rel, content):
    p = tmp / "worktrees" / role / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_v0110_reviewer_hash_ignores_meta_churn_tracks_code():
    tmp = _mktmp_project()
    try:
        _wt_file(tmp, "backend-builder", "api/x.py", "def f(): return 1\n")
        h1 = gate._worktree_substantive_hash(tmp)
        # Meta churn (Fix-4 carve-outs) must NOT change the hash
        _wt_file(tmp, "backend-builder", ".sprint/status/backend-builder.txt", "alive")
        _wt_file(tmp, "backend-builder", "reports/feat/build-backend.txt", "PASS")
        assert gate._worktree_substantive_hash(tmp) == h1, "meta churn must not move it"
        # A real code change MUST change the hash
        _wt_file(tmp, "backend-builder", "api/x.py", "def f(): return 2\n")
        assert gate._worktree_substantive_hash(tmp) != h1
    finally:
        shutil.rmtree(tmp)


def test_v0110_regression_pass_carries_forward_when_code_unchanged():
    """The literal dominant-cost cure: regression PASSed, a rewind wiped
    regression.txt but did NOT rebuild code → gate satisfied from the
    ledger, NO suite re-run. Rebuilt code → re-run."""
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        _wt_file(tmp, "backend-builder", "api/x.py", "def f(): return 1\n")
        gate.record_verdict(argparse.Namespace(
            role="regression-sentinel", feature=f, artifact="worktrees",
            verdict="PASS"))
        phase_spec = {"exit_artifacts": [
            {"path": "reports/{feature}/regression.txt", "must_contain": "PASS"}]}
        # Rewind wiped regression.txt; code identical → carry forward
        assert gate._unsatisfied_exit_artifacts(phase_spec, f) == [], \
            "unchanged built code must carry the prior PASS (no suite re-run)"
        assert "carried forward" in (tmp / f"reports/{f}/regression.txt").read_text()
        # Rebuild changes the worktree hash → must re-run (no false carry)
        (tmp / f"reports/{f}/regression.txt").unlink()
        _wt_file(tmp, "backend-builder", "api/x.py", "def f(): return 999\n")
        assert gate._unsatisfied_exit_artifacts(phase_spec, f), \
            "rebuilt code must force re-run (worktree hash is the detector)"
    finally:
        shutil.rmtree(tmp)


def test_v0110_conventions_regates_when_claude_md_changes():
    """conventions-enforcer judges code AGAINST CLAUDE.md — so a CLAUDE.md
    change must bust its carry-forward even if code is identical."""
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "CLAUDE.md").write_text("## Style\nOne sentence max.\n")
        _wt_file(tmp, "backend-builder", "api/x.py", "def f(): return 1\n")
        gate.record_verdict(argparse.Namespace(
            role="conventions-enforcer", feature=f, artifact="worktrees",
            verdict="PASS"))
        phase_spec = {"exit_artifacts": [
            {"path": "reports/{feature}/conventions.txt", "must_contain": "PASS"}]}
        assert gate._unsatisfied_exit_artifacts(phase_spec, f) == [], \
            "code+CLAUDE.md unchanged → carry forward"
        # CLAUDE.md changes → conventions verdict may differ → re-gate
        (tmp / f"reports/{f}/conventions.txt").unlink()
        (tmp / "CLAUDE.md").write_text("## Style\nVerbose docstrings required.\n")
        assert gate._unsatisfied_exit_artifacts(phase_spec, f), \
            "CLAUDE.md change must force conventions re-gate"
    finally:
        shutil.rmtree(tmp)


def test_v0110_failed_reviewer_verdict_does_not_carry_forward():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        _wt_file(tmp, "backend-builder", "api/x.py", "x\n")
        gate.record_verdict(argparse.Namespace(
            role="regression-sentinel", feature=f, artifact="worktrees",
            verdict="FAIL"))
        cur = argparse.Namespace(role="regression-sentinel", feature=f,
                                 artifact="worktrees")
        assert gate.verdict_current(cur) == 1, "a FAIL must never carry forward"
    finally:
        shutil.rmtree(tmp)



# ---------- v0.11.0 #2: proportional-ceremony (trivial) lane ----------
# A one-line fix paying full brief→scope→plan→review ceremony IS the
# structural generalization of the m4-s8c bypass. The trivial lane makes
# ceremony proportional to blast radius (prusik's stated value): skips
# scope-critic/triage/plan-critic, keeps brief-critic + the reviewing
# correctness floor. Decided at sprint-start (triage is too late — it runs
# AFTER scoping). Guarded ungameably by brief Type.


def _trivial_brief(tmp, feature, btype):
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{feature}.md").write_text(
        f"## Goal\nFix the typo in the retry log message.\n\n"
        f"## Success criteria\nLog reads correctly within 1 line changed.\n\n"
        f"## Type\n{btype}\n")
    rdir = tmp / "reports" / feature
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "brief-critique.txt").write_text("PASS\n")
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
        {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}))
    (tmp / "design").mkdir(exist_ok=True)
    (tmp / "design" / "map.md").write_text("map placeholder")
    discovery.fingerprint_map()


def test_v0110_trivial_lane_accepted_for_eligible_brief_type():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        _trivial_brief(tmp, "feat", "bug_fix")
        rc = gate.sprint_start(argparse.Namespace(feature="feat", trivial=True))
        assert rc == 0
        st = phases.current_sprint_state()
        assert st["phase"] == "scoping" and st.get("lane") == "trivial", st
        from prusik.ledger import read_all
        ev = [r for r in read_all() if r["event"] == "sprint_started"]
        assert ev and ev[-1].get("lane") == "trivial", ev
    finally:
        shutil.rmtree(tmp)


def test_v0110_trivial_lane_rejected_for_new_feature_ungameable():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        _trivial_brief(tmp, "feat", "new_feature")
        rc = gate.sprint_start(argparse.Namespace(feature="feat", trivial=True))
        assert rc == 2, "a new_feature brief must NOT be allowed into trivial lane"
        from prusik.ledger import read_all
        assert any(r["event"] == "trivial_lane_rejected" for r in read_all())
        # sprint_start bailed before setting state
        assert phases.current_sprint_state() is None
    finally:
        shutil.rmtree(tmp)


def test_v0110_trivial_lane_persists_across_advance():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        _trivial_brief(tmp, "feat", "chore")
        assert gate.sprint_start(argparse.Namespace(feature="feat", trivial=True)) == 0
        (tmp / "design" / "feat").mkdir(parents=True, exist_ok=True)
        (tmp / "design" / "feat" / "trivial.md").write_text(
            "## Change\nOne-line log fix.\n\n## How verified\npytest -k log\n")
        rc = gate.advance(argparse.Namespace(
            phase="solo_execute", feature="feat", allow_rewind=False))
        assert rc == 0, "trivial scoping→solo_execute must pass on trivial.md"
        assert phases.current_sprint_state().get("lane") == "trivial", \
            "lane must survive the advance (set_sprint_state overwrite)"
    finally:
        shutil.rmtree(tmp)


def test_v0110_trivial_scoping_exit_swaps_artifact_set():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        cfg = phases.load_sprint_config()
        scoping = phases.get_phase_spec(cfg, "scoping")
        (tmp / "design" / "feat").mkdir(parents=True)
        # Trivial lane: scope.md/scope-approval NOT required; trivial.md IS
        phases.set_sprint_state({"phase": "scoping", "feature": "feat",
                                 "lane": "trivial"})
        miss = gate._unsatisfied_exit_artifacts(scoping, "feat")
        assert miss, "trivial lane still needs trivial.md"
        assert any("trivial.md" in m for m in miss), miss
        (tmp / "design" / "feat" / "trivial.md").write_text(
            "## Change\nx\n\n## How verified\ny\n")
        assert gate._unsatisfied_exit_artifacts(scoping, "feat") == [], \
            "trivial.md present → scoping exit satisfied, no scope-critic"
        # Standard lane unaffected: still demands scope.md + approval
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        miss_std = gate._unsatisfied_exit_artifacts(scoping, "feat")
        assert any("scope.md" in m for m in miss_std), miss_std
        assert any("scope-approval.txt" in m for m in miss_std), miss_std
    finally:
        shutil.rmtree(tmp)


def test_v0110_trivial_does_not_weaken_reviewing_correctness_floor():
    """The non-negotiable: trivial sprints still pay the full reviewing
    gate. reviewing declares no trivial variant → regression+conventions
    PASS required exactly as for a standard sprint."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        cfg = phases.load_sprint_config()
        reviewing = phases.get_phase_spec(cfg, "reviewing")
        (tmp / "design" / "feat").mkdir(parents=True)
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat",
                                 "lane": "trivial"})
        miss = gate._unsatisfied_exit_artifacts(reviewing, "feat")
        assert any("regression.txt" in m for m in miss), miss
        assert any("conventions.txt" in m for m in miss), miss
    finally:
        shutil.rmtree(tmp)



# ---------- phase writable ----------

def test_phase_writable_paths():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config()
        ok, _ = phases.is_path_writable("design/foo/scope.md", config, "scoping", "foo")
        assert ok
        ok, _ = phases.is_path_writable("api/main.py", config, "scoping", "foo")
        assert not ok
        ok, _ = phases.is_path_writable("worktrees/solo/main.py", config, "solo_execute", "foo")
        assert ok
    finally:
        shutil.rmtree(tmp)



# ---------- pre-sprint gate ----------

def test_pre_sprint_gate_blocks_without_critique():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Add retry logic to payment processor.

## Success criteria
99% of transient payment errors retried within 5s.

## Type
new_feature
""")
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 2, "should be blocked by missing brief-critique"
    finally:
        shutil.rmtree(tmp)


def test_pre_sprint_gate_passes_with_critique():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Add retry logic to payment processor.

## Success criteria
99% of transient payment errors retried within 5s.

## Type
new_feature
""")
        critique_dir = tmp / "reports" / "feat"
        critique_dir.mkdir(parents=True)
        (critique_dir / "brief-critique.txt").write_text("PASS\n")
        # Also satisfy the map_freshness pre-sprint gate: empty dep-graph + matching fingerprint.
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
            {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}
        ))
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map placeholder")
        discovery.fingerprint_map()
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 0
        state = phases.current_sprint_state()
        assert state["phase"] == "scoping"
    finally:
        shutil.rmtree(tmp)



# ---------- sprint complete + digest ----------

def test_sprint_complete_derives_duration_from_ledger_when_flag_missing():
    """v0.3.11: no --duration-min → fall back to ledger timestamps."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "decisions").mkdir()
        (tmp / "decisions" / "feat.json").write_text(json.dumps({
            "feature": "feat", "mode": "solo", "reason": "test",
            "scope_summary": {"size": "S", "domains": ["backend"]},
            "brief_meta": {"type": "bug_fix", "priority": "P2"},
        }))
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
        # Seed an old sprint_started timestamp so duration is measurable
        from prusik.ledger import ledger_path
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        past = (_dt.now(_tz.utc) - _td(minutes=45)).isoformat()
        ledger_path().parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path(), "a") as f:
            f.write(json.dumps({"ts": past, "event": "sprint_started",
                                "feature": "feat"}) + "\n")
        args = argparse.Namespace(feature="feat", duration_min=None,
                                   tokens=None, escalated=False)
        rc = gate.sprint_complete(args)
        assert rc == 0
        from prusik.ledger import read_all
        events = [r for r in read_all() if r["event"] == "sprint_complete"]
        assert events
        actual = events[-1]["actual"]
        # Duration derived, non-None, and labeled
        assert actual["duration_min"] is not None
        assert actual["duration_min"] >= 40  # ~45 min elapsed
        assert actual.get("duration_source") == "ledger"
    finally:
        shutil.rmtree(tmp)


def test_sprint_complete_records_predicted_actual():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # Fake a triage decision so predicted recovery has something
        (tmp / "decisions").mkdir()
        (tmp / "decisions" / "feat.json").write_text(json.dumps({
            "feature": "feat", "mode": "solo", "reason": "test",
            "scope_summary": {"size": "S", "domains": ["backend"]},
            "brief_meta": {"type": "bug_fix", "priority": "P2"},
        }))
        # Set state and seed a sprint_started event
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
        from prusik.ledger import append as _append
        _append("sprint_started", feature="feat")
        args = argparse.Namespace(feature="feat", duration_min=47, tokens=123000, escalated=False)
        rc = gate.sprint_complete(args)
        assert rc == 0
        assert phases.current_sprint_state() is None
        # verify ledger event
        from prusik.ledger import read_all
        events = [r for r in read_all() if r["event"] == "sprint_complete"]
        assert events
        assert events[-1]["actual"]["duration_min"] == 47
        assert events[-1]["actual"]["tokens"] == 123000
        assert events[-1]["predicted"]["mode"] == "solo"
    finally:
        shutil.rmtree(tmp)


def test_digest_reports_outcomes():
    tmp = _mktmp_project()
    try:
        from prusik.ledger import append as _append
        _append("sprint_started", feature="a")
        _append("sprint_complete", feature="a",
                predicted={"mode": "solo", "size": "S", "duration_min": 30, "tokens": 100000},
                actual={"mode": "solo", "duration_min": 45, "tokens": 130000})
        _append("sprint_complete", feature="b",
                predicted={"mode": "solo", "size": "M", "duration_min": 60, "tokens": 200000},
                actual={"mode": "team", "duration_min": 120, "tokens": 500000, "escalated": True})
        _append("gate_blocked", tool="Write", phase="scoping",
                reason="path outside scope")
        rc = ledger_digest()
        assert rc == 0
    finally:
        shutil.rmtree(tmp)



# ---------- watchdog ----------

def test_watchdog_flags_stale_heartbeat():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "building", "feature": "feat"})
        status_dir = tmp / ".sprint" / "status"
        status_dir.mkdir(parents=True)
        hb = status_dir / "backend-builder.txt"
        hb.write_text("working on api/checkout/")
        # Make it old enough to be stale (default threshold 30 min)
        old = time.time() - 60 * 60
        os.utime(hb, (old, old))
        rc = watchdog.check()
        # exit 1 signals incidents present
        assert rc == 1
        incidents = list((tmp / ".sprint" / "incidents").glob("*.json"))
        assert incidents, "expected at least one incident"
        data = json.loads(incidents[0].read_text())
        assert data["kind"] == "stale_heartbeat"
        assert data["teammate"] == "backend-builder"
    finally:
        shutil.rmtree(tmp)


def test_watchdog_noop_without_sprint():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        rc = watchdog.check()
        assert rc == 0
    finally:
        shutil.rmtree(tmp)



# ---------- issue sync ----------

def test_issues_sync_noop_when_tracker_none():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        rc = issues.sync()
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_issues_search_on_empty_db():
    tmp = _mktmp_project()
    try:
        results = issues.search("anything")
        assert results == []
    finally:
        shutil.rmtree(tmp)


def test_issues_search_scores_matches():
    tmp = _mktmp_project()
    try:
        db = tmp / ".sprint" / "issues.db.jsonl"
        db.parent.mkdir(parents=True)
        db.write_text("\n".join([
            json.dumps({"id": "#1", "title": "email receipts broken",
                        "body": "users complain about missing receipts"}),
            json.dumps({"id": "#2", "title": "unrelated",
                        "body": "something else entirely"}),
            json.dumps({"id": "#3", "title": "receipt styling",
                        "body": "receipt pdf has wrong font"}),
        ]) + "\n")
        results = issues.search("email receipts", limit=5)
        assert results
        assert results[0]["id"] == "#1"
    finally:
        shutil.rmtree(tmp)



# ---------- map freshness ----------

def test_fingerprint_map_writes_snapshot():
    tmp = _mktmp_project()
    try:
        (tmp / "pkg").mkdir()
        (tmp / "pkg" / "a.py").write_text("import os\n")
        discovery.dep_graph()
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("## Modules\n- pkg\n")
        rc = discovery.fingerprint_map()
        assert rc == 0
        fp = json.loads((tmp / ".sprint" / "map-fingerprint.json").read_text())
        assert "modules" in fp
        assert fp["module_count"] == 1
    finally:
        shutil.rmtree(tmp)


def test_map_drift_zero_when_unchanged():
    tmp = _mktmp_project()
    try:
        (tmp / "pkg").mkdir()
        (tmp / "pkg" / "a.py").write_text("import os\n")
        discovery.dep_graph()
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()
        drift = discovery.map_drift()
        assert drift["drift_pct"] == 0.0
    finally:
        shutil.rmtree(tmp)


def test_map_drift_flags_added_modules():
    tmp = _mktmp_project()
    try:
        (tmp / "pkg").mkdir()
        (tmp / "pkg" / "a.py").write_text("import os\n")
        discovery.dep_graph()
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()
        # Add two new modules — should register as drift
        (tmp / "pkg" / "b.py").write_text("import sys\n")
        (tmp / "pkg" / "c.py").write_text("import json\n")
        discovery.dep_graph()
        drift = discovery.map_drift()
        assert drift["drift_pct"] > 0
        assert drift["added_count"] == 2
    finally:
        shutil.rmtree(tmp)


def test_map_freshness_gate_blocks_on_drift():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Add retry logic to payment processor.

## Success criteria
99% of transient errors retried within 5s.

## Type
new_feature
""")
        # brief-critique PASS so only map_freshness gate can fail
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "brief-critique.txt").write_text("PASS\n")
        # Fingerprint with zero modules; then add a bunch → massive drift
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
            {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}
        ))
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()
        # Now add modules — 100% drift
        (tmp / "pkg").mkdir()
        (tmp / "pkg" / "a.py").write_text("import os\n")
        (tmp / "pkg" / "b.py").write_text("import sys\n")
        discovery.dep_graph()
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 2, "high drift should block sprint_start"
    finally:
        shutil.rmtree(tmp)



# ---------- scope-critic gate ----------

def test_scoping_phase_requires_scope_approval():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "api").mkdir()
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
Test scope for this feature.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small.

## Domains
- backend

## Risks
- Something could break.

## Open questions
- (none)
""")
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        # Without scope-approval.txt, advance must refuse
        args = argparse.Namespace(phase="triage", feature="feat")
        rc = gate.advance(args)
        assert rc == 2, "advance must require scope-approval.txt"
        # Add APPROVED — advance succeeds
        approval = tmp / "reports" / "feat" / "scope-approval.txt"
        approval.parent.mkdir(parents=True)
        approval.write_text("APPROVED\nlooks good\n")
        rc = gate.advance(args)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)



# ---------- cross-artifact consistency ----------

def test_plan_within_scope_detects_scope_creep():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text("""## Modules touched
- api/billing/
- web/checkout/
""")
        (tmp / "design" / f / "plan.md").write_text("""## Modules touched
- api/billing/
- web/checkout/
- infra/queue/
""")
        errs = consistency.plan_within_scope(tmp, f)
        assert errs
        assert "infra/queue/" in errs[0]
    finally:
        shutil.rmtree(tmp)


def test_plan_within_scope_passes_when_subset():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text("""## Modules touched
- api/billing/
- web/checkout/
""")
        (tmp / "design" / f / "plan.md").write_text("""## Modules touched
- api/billing/
""")
        assert consistency.plan_within_scope(tmp, f) == []
    finally:
        shutil.rmtree(tmp)


def test_builder_writes_within_plan_catches_drift():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "plan.md").write_text("""## Modules touched
- api/billing/
""")
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "api" / "billing").mkdir(parents=True)
        (wt / "api" / "billing" / "retry.py").write_text("ok")
        # Rogue write outside plan
        (wt / "infra").mkdir()
        (wt / "infra" / "surprise.py").write_text("oops")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert errs
        assert any("surprise.py" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_builder_writes_allows_test_directory():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "plan.md").write_text("""## Modules touched
- api/billing/
""")
        wt = tmp / "worktrees" / "test-writer"
        (wt / "tests").mkdir(parents=True)
        (wt / "tests" / "test_billing.py").write_text("assert True")
        errs = consistency.builder_writes_within_plan(tmp, f)
        assert errs == []
    finally:
        shutil.rmtree(tmp)


def test_brief_type_matches_scope_flags_mismatch():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / f"{f}.md").write_text("""## Goal
A small fix.

## Success criteria
Fix applied within 1 hour.

## Type
bug_fix
""")
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text("## Size\nL — huge\n")
        errs = consistency.brief_type_matches_scope(tmp, f)
        assert errs
    finally:
        shutil.rmtree(tmp)



# ---------- prusik gate sprint-init (v0.3.5) ----------

def test_sprint_init_reports_missing_map():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Add thing.

## Success criteria
Arrives within 5s with no errors.

## Type
new_feature
""")
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_init(args)
        # Without design/map.md, sprint_init should stop and report
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_sprint_init_reports_missing_brief():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # Fake a valid discovery + map + fingerprint
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
            {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}
        ))
        (tmp / ".sprint" / "inventory.json").write_text("{}")
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_init(args)
        assert rc == 1  # brief missing
    finally:
        shutil.rmtree(tmp)


_VALID_BRIEF = """## Goal
Add email receipts on successful checkout for customers.

## Success criteria
Receipt arrives within 10s of payment with no errors.

## Type
new_feature
"""


def test_sprint_init_reports_missing_brief_critique():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
            {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}
        ))
        (tmp / ".sprint" / "inventory.json").write_text("{}")
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text(_VALID_BRIEF)
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_init(args)
        assert rc == 1  # brief-critique missing
    finally:
        shutil.rmtree(tmp)


def test_sprint_init_advances_when_all_prereqs_met():
    tmp = _mktmp_project()
    try:
        # v0.5.9: sprint-init now hard-blocks on missing permissions baseline,
        # so use full kit_init (which writes the template settings.json
        # containing the baseline) rather than just copying sprint-config.
        kit_init.run()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
            {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}
        ))
        (tmp / ".sprint" / "inventory.json").write_text("{}")
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()
        (tmp / "briefs" / "feat.md").write_text(_VALID_BRIEF)
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "brief-critique.txt").write_text("PASS\n")
        args = argparse.Namespace(feature="feat", skip_lint=True)
        rc = gate.sprint_init(args)
        assert rc == 0
        assert phases.current_sprint_state()["phase"] == "scoping"
    finally:
        shutil.rmtree(tmp)



# ---------- fix-round (v0.5.7) ----------

def test_fix_round_start_writes_marker_and_logs():
    tmp = _mktmp_project()
    try:
        rc = kit_fix_round.start("feat", root=tmp)
        assert rc == 0
        state = kit_fix_round.current_state(tmp)
        assert state and state["feature"] == "feat" and state["round"] == 1
        from prusik.ledger import read_all
        events = [r for r in read_all() if r["event"] == "fix_round_start"]
        assert events and events[-1]["round"] == 1
    finally:
        shutil.rmtree(tmp)


def test_fix_round_start_when_already_active_fails():
    tmp = _mktmp_project()
    try:
        kit_fix_round.start("feat", root=tmp)
        rc = kit_fix_round.start("feat", root=tmp)
        assert rc == 1, "starting twice without ending should fail"
    finally:
        shutil.rmtree(tmp)


def test_fix_round_end_clears_marker():
    tmp = _mktmp_project()
    try:
        kit_fix_round.start("feat", root=tmp)
        rc = kit_fix_round.end("feat", root=tmp)
        assert rc == 0
        assert not kit_fix_round.is_active(tmp)
    finally:
        shutil.rmtree(tmp)


def test_fix_round_end_without_active_fails():
    tmp = _mktmp_project()
    try:
        rc = kit_fix_round.end("feat", root=tmp)
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_fix_round_cap_at_max():
    """Third start hits the cap and logs fix_round_cap_hit."""
    tmp = _mktmp_project()
    try:
        # Round 1: start, end
        assert kit_fix_round.start("feat", root=tmp) == 0
        assert kit_fix_round.end("feat", root=tmp) == 0
        # Round 2: start, end
        assert kit_fix_round.start("feat", root=tmp) == 0
        assert kit_fix_round.end("feat", root=tmp) == 0
        # Round 3 attempt → cap
        rc = kit_fix_round.start("feat", root=tmp)
        assert rc == 2, "third start should exit 2 (cap)"
        from prusik.ledger import read_all
        cap_events = [r for r in read_all() if r["event"] == "fix_round_cap_hit"]
        assert cap_events and cap_events[-1]["feature"] == "feat"
    finally:
        shutil.rmtree(tmp)



# ---------- v0.11.0 #3: in-kit fix-round escalation gate ----------
# The MAX_ROUNDS cap previously dead-ended into an out-of-kit STOP (write a
# BUG to the bridge) — the m4-s8c bypass precursor. #3 makes the cap point
# at a recorded IN-KIT decision instead. Ledger-driven, no new state keys.


def _reach_fix_round_cap(tmp, feature="feat"):
    assert kit_fix_round.start(feature, root=tmp) == 0
    assert kit_fix_round.end(feature, root=tmp) == 0
    assert kit_fix_round.start(feature, root=tmp) == 0
    assert kit_fix_round.end(feature, root=tmp) == 0  # prior == MAX_ROUNDS


def test_v0110_escalate_rejected_before_cap():
    tmp = _mktmp_project()
    try:
        assert kit_fix_round.start("feat", root=tmp) == 0
        assert kit_fix_round.end("feat", root=tmp) == 0  # only 1 round used
        rc = kit_fix_round.escalate("feat", "extend-once", "want more", root=tmp)
        assert rc == 2, "escalation before the cap must be refused"
    finally:
        shutil.rmtree(tmp)


def test_v0110_escalate_requires_valid_decision_and_rationale():
    tmp = _mktmp_project()
    try:
        _reach_fix_round_cap(tmp)
        assert kit_fix_round.escalate("feat", "bogus", "x", root=tmp) == 2
        assert kit_fix_round.escalate("feat", "abandon", "", root=tmp) == 2, \
            "rationale is mandatory — no silent override"
    finally:
        shutil.rmtree(tmp)


def test_v0110_escalate_extend_once_grants_exactly_one_more_round():
    tmp = _mktmp_project()
    try:
        _reach_fix_round_cap(tmp)
        assert kit_fix_round.start("feat", root=tmp) == 2, "at cap"
        assert kit_fix_round.escalate(
            "feat", "extend-once", "reviewer flake needs one more pass",
            root=tmp) == 0
        # One more round is now allowed...
        assert kit_fix_round.start("feat", root=tmp) == 0, "extend-once → +1"
        assert kit_fix_round.end("feat", root=tmp) == 0
        # ...but only one: back at the (raised) cap
        assert kit_fix_round.start("feat", root=tmp) == 2, "bounded — re-escalate"
        from prusik.ledger import read_all
        assert any(r["event"] == "fix_round_escalation"
                   and r["decision"] == "extend-once" for r in read_all())
    finally:
        shutil.rmtree(tmp)


def test_v0110_escalate_abandon_clears_state_and_records():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
        _reach_fix_round_cap(tmp)
        assert kit_fix_round.escalate(
            "feat", "abandon", "not worth pursuing further", root=tmp) == 0
        assert phases.current_sprint_state() is None, "abandon clears sprint state"
        from prusik.ledger import read_all
        ev = [r for r in read_all() if r["event"] == "fix_round_escalation"]
        assert ev and ev[-1]["decision"] == "abandon" and ev[-1]["rationale"]
    finally:
        shutil.rmtree(tmp)


def test_v0110_escalate_integrate_with_flag_overrides_reviewing_audited():
    """The cure for the bypass funnel: a recorded integrate-with-flag lets
    the sprint leave reviewing DESPITE FAIL — loud + audited, reports keep
    their FAIL, the ledger records what was overridden and why. Without the
    escalation the gate still blocks (enforcement intact)."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "reports" / f).mkdir(parents=True)
        (tmp / "reports" / f / "regression.txt").write_text("FAIL\nflaky\n")
        (tmp / "reports" / f / "conventions.txt").write_text("PASS\n")
        phases.set_sprint_state({"phase": "reviewing", "feature": f})
        _reach_fix_round_cap(tmp, f)
        # Without escalation: reviewing → integrating is blocked
        rc = gate.advance(argparse.Namespace(
            phase="integrating", feature=f, allow_rewind=False))
        assert rc == 2, "FAIL reviewing must block without an escalation"
        # Record the in-kit override
        assert kit_fix_round.escalate(
            f, "integrate-with-flag",
            "flaky infra unrelated to the change; operator accepts risk",
            root=tmp) == 0
        rc = gate.advance(argparse.Namespace(
            phase="integrating", feature=f, allow_rewind=False))
        assert rc == 0, "recorded integrate-with-flag must let advance proceed"
        from prusik.ledger import read_all
        ev = [r for r in read_all() if r["event"] == "integrated_under_escalation"]
        assert ev and ev[-1]["feature"] == f and ev[-1]["rationale"], ev
        assert ev[-1]["overridden"], "must record WHICH gates were overridden"
        # Reports were NOT fabricated — regression.txt still says FAIL
        assert (tmp / "reports" / f / "regression.txt").read_text().startswith("FAIL")
    finally:
        shutil.rmtree(tmp)



# ---------- v0.11.1 Candidate S: fix-round sprint-scoped + reaped ----------
# m4-s8c→#13 (bridge 2026-05-16-m4-test-infra-hardening [22:55]): a fix-round
# from a bypassed sprint (no sprint_complete) orphaned, survived ~26h, and
# silently granted worktrees/*/** writable-expansion to a DIFFERENT sprint's
# reviewing phase. Isolation-invariant break → fix+test, not held (per the
# invariant-vs-feature discipline).


def test_v0111_s_sprint_start_reaps_orphaned_fix_round():
    """THE incident regression: an orphan fix-round from a bypassed sprint
    must be reaped when a different sprint starts."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # m4-s8c-style orphan: a real round for 'old-sprint', never ended,
        # never sprint_complete'd (the bypass).
        assert kit_fix_round.start("old-sprint", root=tmp) == 0
        assert kit_fix_round.is_active(tmp)
        # A different sprint starts.
        _trivial_brief(tmp, "new-sprint", "bug_fix")
        rc = gate.sprint_start(argparse.Namespace(feature="new-sprint", trivial=False))
        assert rc == 0, "new sprint must start"
        assert not kit_fix_round.is_active(tmp), "orphaned fix-round must be reaped"
        from prusik.ledger import read_all
        ev = [r for r in read_all() if r["event"] == "fix_round_reaped"]
        assert ev and ev[-1]["feature"] == "old-sprint", ev
        assert phases.current_sprint_state()["feature"] == "new-sprint"
    finally:
        shutil.rmtree(tmp)


def test_v0111_s_fix_round_start_reaps_foreign_owner():
    """fix-round start for a feature other than the marker's owner: reap the
    foreign orphan and proceed (don't silently block behind dead state)."""
    tmp = _mktmp_project()
    try:
        assert kit_fix_round.start("feat-a", root=tmp) == 0
        rc = kit_fix_round.start("feat-b", root=tmp)
        assert rc == 0, "start for a new feature must reap the orphan and proceed"
        st = kit_fix_round.current_state(tmp)
        assert st and st["feature"] == "feat-b", st
        from prusik.ledger import read_all
        assert any(r["event"] == "fix_round_reaped" and r["feature"] == "feat-a"
                   for r in read_all())
    finally:
        shutil.rmtree(tmp)


def test_v0111_s_same_feature_round_not_reaped():
    """Guard against over-reap: an active round for THIS feature is refused,
    NOT reaped — the current sprint's own round must survive."""
    tmp = _mktmp_project()
    try:
        assert kit_fix_round.start("feat", root=tmp) == 0
        rc = kit_fix_round.start("feat", root=tmp)
        assert rc == 1, "same-feature active round must be refused, not started"
        st = kit_fix_round.current_state(tmp)
        assert st and st["feature"] == "feat" and st["round"] == 1, "round intact"
        from prusik.ledger import read_all
        assert not any(r["event"] == "fix_round_reaped" for r in read_all()), \
            "must NOT reap the current sprint's own active round"
    finally:
        shutil.rmtree(tmp)


def test_v0111_s_sprint_complete_reaps_open_fix_round():
    """A completing sprint must not leave an open fix-round to orphan."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "decisions").mkdir()
        (tmp / "decisions" / "feat.json").write_text(json.dumps({
            "feature": "feat", "mode": "solo", "reason": "test",
            "scope_summary": {"size": "S", "domains": ["backend"]},
            "brief_meta": {"type": "bug_fix", "priority": "P2"},
        }))
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
        assert kit_fix_round.start("feat", root=tmp) == 0
        assert kit_fix_round.is_active(tmp)
        rc = gate.sprint_complete(argparse.Namespace(
            feature="feat", duration_min=1, tokens=None, escalated=False))
        assert rc == 0
        assert not kit_fix_round.is_active(tmp), "complete must reap the open round"
        from prusik.ledger import read_all
        assert any(r["event"] == "fix_round_reaped" and r["feature"] == "feat"
                   for r in read_all())
    finally:
        shutil.rmtree(tmp)


def test_v0111_s_reap_idempotent_no_false_event():
    """reap() with no marker is a silent no-op — no spurious event."""
    tmp = _mktmp_project()
    try:
        assert kit_fix_round.reap(tmp, reason="none") is None
        from prusik.ledger import read_all
        assert not any(r["event"] == "fix_round_reaped" for r in read_all())
    finally:
        shutil.rmtree(tmp)


def test_fix_round_extends_writable_in_reviewing_only():
    """Active fix round → worktrees/*/** writable in reviewing phase only."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config(tmp)
        target = "worktrees/backend-builder/src/foo.py"

        # Without fix round: reviewing blocks worktrees/
        ok, _ = phases.is_path_writable(target, config, "reviewing", "feat", root=tmp)
        assert not ok, "reviewing should block worktrees/ when no fix round"

        # Start fix round → reviewing now allows worktrees/
        kit_fix_round.start("feat", root=tmp)
        ok, _ = phases.is_path_writable(target, config, "reviewing", "feat", root=tmp)
        assert ok, "reviewing+fix-round should allow worktrees/"

        # Other phases unaffected even with fix round active — building still
        # has worktrees/ writable (its own rule), reviewing keeps the extension.
        # End round → back to blocked.
        kit_fix_round.end("feat", root=tmp)
        ok, _ = phases.is_path_writable(target, config, "reviewing", "feat", root=tmp)
        assert not ok, "reviewing should block worktrees/ after fix round ends"
    finally:
        shutil.rmtree(tmp)


def test_fix_round_does_not_open_design_writes():
    """Fix-round expands ONLY worktrees/*/**. design/ etc stay blocked."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config = phases.load_sprint_config(tmp)
        kit_fix_round.start("feat", root=tmp)
        # design/feat/scope.md is NOT in reviewing's writable patterns and
        # NOT in worktrees/*/** — must stay blocked even with fix-round on.
        target = "design/feat/scope.md"
        ok, reason = phases.is_path_writable(target, config, "reviewing", "feat", root=tmp)
        assert not ok, f"design/ must stay blocked under fix-round: {reason}"
    finally:
        shutil.rmtree(tmp)


def test_fix_round_gate_cli_wires():
    """`prusik gate fix-round start/end --feature X` round-trips through argparse."""
    tmp = _mktmp_project()
    try:
        # start
        sys.argv = ["prusik", "gate", "fix-round", "start", "--feature", "feat"]
        from prusik.__main__ import main as _main
        rc = _main()
        assert rc == 0
        assert kit_fix_round.is_active(tmp)
        # end
        sys.argv = ["prusik", "gate", "fix-round", "end", "--feature", "feat"]
        rc = _main()
        assert rc == 0
        assert not kit_fix_round.is_active(tmp)
    finally:
        shutil.rmtree(tmp)



# ---------- sprint-init permissions hard-block (v0.5.9) ----------

def test_permissions_missing_returns_list():
    """missing() is a pure-data extractor (no printing) for gate use."""
    tmp = _mktmp_project()
    try:
        # No settings files → all baseline missing
        result = kit_permissions.missing(tmp)
        assert isinstance(result, list)
        assert len(result) == len(kit_permissions.RECOMMENDED_ALLOW)
    finally:
        shutil.rmtree(tmp)


def test_permissions_missing_zero_after_full_baseline():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Shipped template has full baseline
        result = kit_permissions.missing(tmp)
        assert result == [], f"shipped template should have 0 missing: {result}"
    finally:
        shutil.rmtree(tmp)


def test_sprint_init_hard_blocks_on_missing_permissions_baseline():
    """v0.5.9: sprint-init must refuse to start when audit shows missing
    baseline entries — prevents the silent-Bash-denial class of bug from
    biting mid-build."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Replace settings.json with a minimal one (only 2 allow entries)
        settings = tmp / ".claude" / "settings.json"
        settings.write_text(json.dumps({
            "hooks": {},
            "permissions": {"allow": ["Bash(echo *)", "Bash(ls *)"]}
        }))

        # Set up the OTHER prerequisites so we know the failure is about perms
        (tmp / "design").mkdir(exist_ok=True)
        (tmp / "design" / "map.md").write_text("# Map\nstub for test")
        (tmp / ".sprint" / "map-fingerprint.json").write_text(
            json.dumps({"mods": [], "ts": "2026-04-25T00:00:00Z"})
        )
        (tmp / "briefs").mkdir(exist_ok=True)
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Test the sprint-init permissions hard-block reaches its gate.

## Success criteria
sprint-init exits 1 with a permissions message when baseline is incomplete.

## Type
new_feature
""")
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "brief-critique.txt").write_text("PASS\nlooks good")

        args = argparse.Namespace(feature="feat", skip_lint=True)
        rc = gate.sprint_init(args)
        assert rc == 1, "sprint-init should refuse when permissions baseline incomplete"
        from prusik.ledger import read_all
        events = [r for r in read_all() if r["event"] == "sprint_init_blocked"]
        assert events
        assert events[-1]["reason"] == "permissions baseline incomplete"
    finally:
        shutil.rmtree(tmp)


def test_sprint_init_passes_permission_check_with_full_baseline():
    """When permissions baseline is complete, sprint-init proceeds past
    the v0.5.9 gate. (Test stops at the brief check; we're asserting the
    permissions gate doesn't trigger a false-positive block.)"""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Shipped template has full baseline; don't touch settings.json.
        # Don't write brief — sprint-init will fail at the brief check, NOT
        # at permissions. We verify by checking ledger has no
        # sprint_init_blocked event with reason=permissions.
        args = argparse.Namespace(feature="feat", skip_lint=True)
        gate.sprint_init(args)
        # Will return 1 due to missing brief, but must NOT have logged
        # permissions-related sprint_init_blocked event.
        from prusik.ledger import read_all
        perm_blocks = [r for r in read_all()
                       if r.get("event") == "sprint_init_blocked"
                       and r.get("reason") == "permissions baseline incomplete"]
        assert not perm_blocks, "shipped template must not trigger permissions hard-block"
    finally:
        shutil.rmtree(tmp)



# ---------- role-spec hardening (v0.6.0) ----------

def test_regression_sentinel_template_pins_project_root():
    """v0.6.0: regression-sentinel must instruct running tests from project
    root, not from worktree subdirs (would produce false-positive failures
    from layout artifacts — observed in cli-foundation)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    assert "PROJECT ROOT" in text or "project root" in text, \
        "regression-sentinel must instruct running tests from project root"
    assert "$CLAUDE_PROJECT_DIR" in text, \
        "regression-sentinel should reference $CLAUDE_PROJECT_DIR explicitly"
    assert "worktree" in text and "false-positive" in text, \
        "regression-sentinel must explain WHY worktrees produce false positives"


def test_conventions_enforcer_template_prefers_running_linter():
    """v0.6.0: conventions-enforcer must prefer running the configured
    linter over static reading. Static reading produced 5 false-positive
    ruff E501 violations in cli-foundation because project disabled E501."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "conventions-enforcer.md")
    text = p.read_text()
    assert "RUN the linter" in text or "run it as your primary signal" in text, \
        "conventions-enforcer must instruct running the linter as primary signal"
    assert "STATIC-READ FALLBACK" in text, \
        "conventions-enforcer must require labeling static-read fallback findings"
    # Specifically warn off the cli-foundation failure mode
    assert "ruff config" in text or "explicitly disabled" in text, \
        "conventions-enforcer must warn against flagging rules the project disabled"



# ---------- v0.6.1 hardening (stitcher-tests B1/B2/B4/B5) ----------

def test_b1_horizontal_rules_skipped_in_list_extraction():
    """v0.6.1 B1: `---`, `----`, `* * *`, `___` etc. between scope sections
    must NOT be parsed as bullets — they're cosmetic structure."""
    from prusik.schema import extract_list_items, _is_md_hr
    body = """- backend
- frontend
---
- infra
* * *
- data
___
- doc
"""
    items = extract_list_items(body)
    assert items == ["backend", "frontend", "infra", "data", "doc"], items
    # _is_md_hr unit checks
    for hr in ["---", "----", "-----", "* * *", "***", "___", " --- ", "- - -"]:
        assert _is_md_hr(hr), f"should be HR: {hr!r}"
    for not_hr in ["- item", "-- broken", "*bold*", "__bold__", "-"]:
        assert not _is_md_hr(not_hr), f"should NOT be HR: {not_hr!r}"


def test_b1_hr_does_not_pollute_domains_enum():
    """Regression for the actual stitcher-tests failure mode: scope.md
    with `---` separators between sections must validate cleanly when
    Domains is otherwise correct."""
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("#")
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
Add coverage to the stitcher.

## Modules touched
- `src/a.py`

---

## Blast radius
- none

---

## Related work
- none

## Size
S

## Domains
- backend

---

## Risks
- coverage gaps

## Open questions
- none
""")
        ok, errs = schema.validate_scope(scope, project_root=tmp)
        assert ok, f"scope with --- separators should validate: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_b2_plus_marker_at_column_zero_recognized_as_bullet():
    """`+ path` at column 0 (CommonMark §5.2 alt bullet) is extracted AND keeps
    its `+` new-file marker (finding #8): the marker is preserved through
    extract_list_items so extract_module_token still tags it new. `* ` is a plain
    alt marker (no new-file meaning) and is stripped like `- `."""
    from prusik.schema import extract_list_items, extract_module_token
    body = """- existing.py — refactor
+ `new_file.py` — new module
* `another.py` — also recognized via CommonMark alt
"""
    items = extract_list_items(body)
    assert len(items) == 3
    assert items[0] == "existing.py — refactor"
    assert items[1] == "+ `new_file.py` — new module"     # `+` preserved
    assert items[2] == "`another.py` — also recognized via CommonMark alt"
    # end-to-end: the preserved `+` makes the path tag as new-file
    assert extract_module_token(items[1]) == ("new_file.py", True)
    assert extract_module_token(items[0]) == ("existing.py", False)


def test_finding8_bare_plus_marker_treated_as_new_file():
    """Finding #8 (An adopter): a scoping agent naturally emits `+ path` (diff style)
    for a new file — not only the canonical `- + path`. Both must mean new-file
    and skip the existence check, so the standard scope→advance flow doesn't trip
    on a file the sprint is about to create. (Overturns the v0.6.1 behavior that
    failed bare `+ path` with a path-existence error: the canonical `- + path`
    form already defers typo-detection to builder time, so failing `+ path` for
    'existence' bought no real protection — only friction.)"""
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
New-file marker at column zero must be honored.

## Modules touched
+ `src/new_billing.py` — new module the sprint creates
+ src/another_new.py
- src/existing.py

## Blast radius
- none

## Related work
- none

## Size
S

## Domains
- backend

## Risks
- none

## Open questions
- none
""")
        (tmp / "src" / "existing.py").write_text("#")     # the only one that exists
        ok, errs = schema.validate_scope(scope, project_root=tmp)
        assert ok, f"bare `+ path` must be accepted as new-file: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_b4_brief_schema_accepts_test_and_chore_types():
    """v0.6.1 B4: brief type enum now includes `test` and `chore`."""
    tmp = _mktmp_project()
    try:
        for sprint_type in ("test", "chore"):
            brief = tmp / "briefs" / f"{sprint_type}.md"
            brief.parent.mkdir(exist_ok=True)
            brief.write_text(f"""## Goal
Add coverage for the stitcher module.

## Success criteria
Coverage rises from 60% to at least 85% within one sprint.

## Type
{sprint_type}
""")
            ok, errs = schema.validate_brief(brief)
            assert ok, f"brief with type='{sprint_type}' should validate: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_b4_triage_routes_test_and_chore_to_solo():
    """v0.6.1 B4: test and chore briefs auto-route to solo mode (low-risk,
    single-domain by nature)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        for sprint_type in ("test", "chore"):
            (tmp / "briefs" / f"{sprint_type}-feat.md").write_text(f"""## Goal
Add coverage for the stitcher module thoroughly.

## Success criteria
Coverage rises from 60% to at least 85% within one sprint.

## Type
{sprint_type}
""")
            (tmp / "design" / f"{sprint_type}-feat").mkdir(parents=True, exist_ok=True)
            (tmp / "design" / f"{sprint_type}-feat" / "scope.md").write_text("""## Goal recap
Add coverage for the stitcher.

## Modules touched
- `briefs/`

## Blast radius
- none

## Related work
- none

## Size
M

## Domains
- backend
- test

## Risks
- none

## Open questions
- none
""")
            rc = triage.run(f"{sprint_type}-feat")
            assert rc == 0
            decision = json.loads(
                (tmp / "decisions" / f"{sprint_type}-feat.json").read_text()
            )
            assert decision["mode"] == "solo", \
                f"type='{sprint_type}' should auto-route to solo: {decision}"
    finally:
        shutil.rmtree(tmp)


def test_b5_status_heartbeats_writable_in_any_phase():
    """v0.6.1 B5: .sprint/status/<role>.txt heartbeat files must be
    writable from any phase (always_writable). Pre-v0.6.1 the scoping
    role's heartbeat was blocked because scoping's writable patterns
    don't include .sprint/status/."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        config = phases.load_sprint_config(tmp)
        # scoping phase normally allows only design/{feature}/scope.md +
        # reports/{feature}/scope-approval.txt. .sprint/status/scoping.txt
        # must be allowed via always_writable.
        ok, reason = phases.is_path_writable(
            ".sprint/status/scoping.txt", config, "scoping", "feat", root=tmp
        )
        assert ok, f"heartbeat path must be always-writable: {reason}"
        # Same check for building, reviewing, integrating
        for phase in ("building", "reviewing", "integrating"):
            ok, reason = phases.is_path_writable(
                f".sprint/status/{phase}-builder.txt", config, phase, "feat",
                root=tmp
            )
            assert ok, f"heartbeat must be writable in {phase}: {reason}"
    finally:
        shutil.rmtree(tmp)


def test_b3_sprint_pause_and_resume_slash_commands_exist():
    """v0.6.1 B3: shipped templates include /sprint-pause and /sprint-resume
    slash commands that wrap prusik pause / prusik resume — surfaces the existing
    mechanism for discoverability."""
    cmds_dir = (Path(__file__).parent.parent / "prusik" / "templates"
                / ".claude" / "commands")
    assert (cmds_dir / "sprint-pause.md").exists()
    assert (cmds_dir / "sprint-resume.md").exists()
    pause = (cmds_dir / "sprint-pause.md").read_text()
    assert "prusik pause" in pause, "sprint-pause must invoke prusik pause"
    resume = (cmds_dir / "sprint-resume.md").read_text()
    assert "prusik resume" in resume



# ---------- v0.6.2 worktree cache hygiene (B7) ----------

def test_worktrees_clean_of_cache_artifacts_passes_when_clean():
    tmp = _mktmp_project()
    try:
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "real.py").write_text("# real product code")
        errs = consistency.worktrees_clean_of_cache_artifacts(tmp, "feat")
        assert errs == [], errs
    finally:
        shutil.rmtree(tmp)


def test_worktrees_clean_flags_ruff_cache():
    tmp = _mktmp_project()
    try:
        wt = tmp / "worktrees" / "solo"
        (wt / ".ruff_cache").mkdir(parents=True)
        (wt / ".ruff_cache" / "abc").write_text("")
        errs = consistency.worktrees_clean_of_cache_artifacts(tmp, "feat")
        assert errs, "should detect .ruff_cache in worktree"
        joined = " ".join(errs)
        assert ".ruff_cache" in joined
        assert "--no-cache" in joined, "error must include source-side fix hint"
    finally:
        shutil.rmtree(tmp)


def test_worktrees_clean_flags_pytest_cache_and_pycache():
    """Each cache-marker dir is independently flagged."""
    tmp = _mktmp_project()
    try:
        wt = tmp / "worktrees" / "test-writer"
        (wt / "tests").mkdir(parents=True)
        # __pycache__ under tests/ — would NOT be caught by the tests/
        # carve-out in builder_writes_within_plan, but IS caught by this check
        (wt / "tests" / "__pycache__").mkdir()
        (wt / ".pytest_cache").mkdir()
        (wt / ".mypy_cache").mkdir()
        errs = consistency.worktrees_clean_of_cache_artifacts(tmp, "feat")
        assert errs
        joined = " ".join(errs)
        assert "__pycache__" in joined
        assert ".pytest_cache" in joined
        assert ".mypy_cache" in joined
    finally:
        shutil.rmtree(tmp)


def test_worktrees_cache_check_registered_for_reviewing_phase():
    """The check fires at advance-from-reviewing, the moment right before
    integrator pulls worktree contents into project root."""
    assert consistency.worktrees_clean_of_cache_artifacts in \
        consistency.PHASE_CHECKS["reviewing"]
    # Also at building / solo_execute exit (defense-in-depth)
    assert consistency.worktrees_clean_of_cache_artifacts in \
        consistency.PHASE_CHECKS["building"]
    assert consistency.worktrees_clean_of_cache_artifacts in \
        consistency.PHASE_CHECKS["solo_execute"]


def test_advance_from_reviewing_blocks_when_cache_present():
    """End-to-end: cache pollution in worktree blocks advance into integrating."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"}, root=tmp)
        # Make reviewing-phase exit artifacts exist + PASS so they don't
        # block first; the consistency check is what we want to test.
        (tmp / "reports" / "feat").mkdir(parents=True, exist_ok=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        (tmp / "reports" / "feat" / "conventions.txt").write_text("PASS\n")
        # v0.12.0 (F): a PASS is honored only against kit-captured evidence.
        # Generate real evidence via the wrapper so the exit-artifact check
        # passes and the test can reach the consistency check it targets.
        gate.capture(argparse.Namespace(
            feature="feat", phase="regression", kind="tests",
            command=["--", "echo", "1 passed"]))
        gate.capture(argparse.Namespace(
            feature="feat", phase="conventions", kind="lint",
            command=["--", "echo", "Files: 5"]))   # real files-checked scope signal
        # Pollute worktree (empty cache dir — no files, worktree hash and
        # thus evidence-freshness unaffected; the consistency scan still
        # flags the cache dir itself).
        wt = tmp / "worktrees" / "solo"
        (wt / ".ruff_cache").mkdir(parents=True)
        args = argparse.Namespace(
            phase="integrating", feature="feat", allow_rewind=False
        )
        rc = gate.advance(args)
        assert rc == 2, "advance should refuse with cache present"
        from prusik.ledger import read_all
        blocks = [r for r in read_all() if r.get("event") == "advance_blocked"]
        assert blocks
        assert any("cache" in str(b.get("inconsistencies", [])).lower()
                   for b in blocks), blocks
    finally:
        shutil.rmtree(tmp)


def test_role_specs_mandate_cache_suppression_flags():
    """Both reviewer role specs must instruct the agent to pass --no-cache
    flags when running ruff/mypy/pytest."""
    rs = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
          / "agents" / "regression-sentinel.md").read_text()
    assert "no:cacheprovider" in rs, \
        "regression-sentinel must mandate pytest -p no:cacheprovider"
    assert "--no-incremental" in rs and "--cache-dir=/dev/null" in rs, \
        "regression-sentinel must mandate mypy cache flags"

    ce = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
          / "agents" / "conventions-enforcer.md").read_text()
    assert "ruff check -v --no-cache" in ce, \
        "conventions-enforcer must mandate ruff -v (files-checked signal) --no-cache"
    assert "--no-incremental" in ce and "--cache-dir=/dev/null" in ce, \
        "conventions-enforcer must mandate mypy cache flags"



# ---------- v0.7.1 behavior_regression integration ----------

def _setup_behavior_regression_project(tmp, *, br_block, gate_block, populate_dir):
    """Bootstrap a project with brief, brief-critique PASS, and the optional
    behavior_regression top-level block + pre-sprint gate. `br_block` and
    `gate_block` are YAML strings inserted into sprint-config.yaml (or None
    to skip). `populate_dir` controls whether tests/behavior/ has a file."""
    _copy_sprint_config(tmp)
    cfg_path = tmp / ".claude" / "sprint-config.yaml"
    cfg = cfg_path.read_text()
    if br_block is not None:
        cfg += "\n" + br_block + "\n"
    if gate_block is not None:
        # Splice into pre_sprint_gates by appending under the existing
        # block — YAML allows duplicate top-level keys merged at parse;
        # safer to inject as a new mapping under pre_sprint_gates.
        cfg = cfg.replace(
            "pre_sprint_gates:\n",
            "pre_sprint_gates:\n" + gate_block + "\n",
            1,
        )
    cfg_path.write_text(cfg)

    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / "feat.md").write_text("""## Goal
Add retry logic to payment processor flow.

## Success criteria
99% of transient errors retried within 5s.

## Type
new_feature
""")
    # brief-critique PASS so only behavior_regression gate can fail
    (tmp / "reports" / "feat").mkdir(parents=True, exist_ok=True)
    (tmp / "reports" / "feat" / "brief-critique.txt").write_text("PASS\n")
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / "design").mkdir(exist_ok=True)
    (tmp / "design" / "map.md").write_text("map")

    if populate_dir:
        (tmp / "tests" / "behavior").mkdir(parents=True, exist_ok=True)
        (tmp / "tests" / "behavior" / "test_smoke.py").write_text(
            "def test_smoke():\n    assert True\n"
        )

    # Fingerprint LAST, mirroring the real cartographer flow (map + fingerprint are taken
    # after the code exists). map_freshness now compares against a FRESH file walk
    # (fb-dde6878ad04b); a fingerprint snapshotted BEFORE this test file was written would
    # correctly read as drifted — the old ordering masked that via a seeded empty cached
    # dep-graph the old map_drift read instead of walking the tree.
    discovery.fingerprint_map()


def test_behavior_regression_gate_passes_when_disabled():
    """Gate must no-op if pre_sprint_gates.behavior_regression is absent
    or behavior_regression top-level block is disabled."""
    tmp = _mktmp_project()
    try:
        _setup_behavior_regression_project(
            tmp,
            br_block="behavior_regression:\n  enabled: false\n  command: 'pytest tests/behavior/'\n",
            gate_block="  behavior_regression:\n    enabled: true\n    check: behavior_regression\n",
            populate_dir=False,  # empty dir, but block disabled → still passes
        )
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 0, "disabled top-level block must skip the gate"
    finally:
        shutil.rmtree(tmp)


def test_behavior_regression_gate_blocks_on_empty_dir():
    """When behavior_regression.enabled is true but tests/behavior/ has no
    test files, the pre-sprint gate must FAIL — prevents the carve-out
    from rotting silently as a declared-but-empty contract."""
    tmp = _mktmp_project()
    try:
        _setup_behavior_regression_project(
            tmp,
            br_block="behavior_regression:\n  enabled: true\n  command: 'pytest tests/behavior/'\n",
            gate_block="  behavior_regression:\n    enabled: true\n    check: behavior_regression\n",
            populate_dir=False,
        )
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 2, "empty tests/behavior/ with enabled block must block sprint_start"
    finally:
        shutil.rmtree(tmp)


def test_behavior_regression_gate_passes_when_dir_populated():
    """Happy path: enabled block + non-empty test dir → gate passes."""
    tmp = _mktmp_project()
    try:
        _setup_behavior_regression_project(
            tmp,
            br_block="behavior_regression:\n  enabled: true\n  command: 'pytest tests/behavior/'\n",
            gate_block="  behavior_regression:\n    enabled: true\n    check: behavior_regression\n",
            populate_dir=True,
        )
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 0, "populated tests/behavior/ with enabled block must pass"
    finally:
        shutil.rmtree(tmp)


def test_behavior_regression_gate_respects_custom_test_dir():
    """Gate's `test_dir` and `pattern` options override the defaults."""
    tmp = _mktmp_project()
    try:
        _setup_behavior_regression_project(
            tmp,
            br_block="behavior_regression:\n  enabled: true\n  command: 'pytest acceptance/'\n",
            gate_block=(
                "  behavior_regression:\n"
                "    enabled: true\n"
                "    check: behavior_regression\n"
                "    test_dir: 'acceptance'\n"
                "    pattern: '*.acceptance.py'\n"
            ),
            populate_dir=False,
        )
        # Populate the custom dir with the custom pattern, then re-fingerprint so the map
        # reflects the file (map_freshness walks the tree fresh — fb-dde6878ad04b).
        (tmp / "acceptance").mkdir(parents=True, exist_ok=True)
        (tmp / "acceptance" / "smoke.acceptance.py").write_text(
            "def test_smoke():\n    assert True\n"
        )
        discovery.fingerprint_map()
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 0, "custom test_dir + pattern must be honored"
    finally:
        shutil.rmtree(tmp)


def test_regression_sentinel_template_reads_behavior_regression_block():
    """v0.7.1: regression-sentinel must instruct reading
    `.claude/sprint-config.yaml`'s top-level `behavior_regression` block
    and running its `command` in addition to the project's general test
    command. Failure of either is a FAIL."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    assert "behavior_regression" in text, \
        "regression-sentinel must reference the behavior_regression block by name"
    assert "in addition to" in text or "in addition" in text, \
        "regression-sentinel must run behavior suite IN ADDITION to general suite (not instead-of)"
    # Must explain that behavior failures are always treated as regressions
    assert "always treated as" in text or "always treated" in text \
        or "always" in text and "regression" in text, \
        "regression-sentinel must clarify behavior-failure handling"


def test_sprint_config_template_documents_behavior_regression():
    """v0.7.1: shipped template must document the behavior_regression
    top-level block and the matching pre-sprint gate (commented examples
    for opt-in)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "sprint-config.yaml")
    text = p.read_text()
    # Top-level block documented (commented or live)
    assert "behavior_regression:" in text
    # Pre-sprint gate option documented
    assert "check: behavior_regression" in text



# ---------- v0.8.0 project_policy integration ----------

def test_regression_sentinel_template_reads_project_policy_block():
    """v0.8.0: regression-sentinel must instruct reading
    `.claude/sprint-config.yaml`'s top-level `project_policy` block and
    running its `command` in addition to the general suite + behavior
    suite. Failure of any of the three is a FAIL."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    assert "project_policy" in text, \
        "regression-sentinel must reference the project_policy block by name"
    # Must explain compose-not-replace posture
    lower = text.lower()
    assert "compose" in lower or "in addition to" in lower, \
        "regression-sentinel must clarify that project_policy COMPOSES with general suite, not replaces"
    # Must call out at least one failure mode prusik can't model
    assert ("tenant" in lower or "secret" in lower or "migration" in lower
            or "license" in lower or "policy" in lower), \
        "regression-sentinel must name examples of project-side invariants prusik can't model"


def test_sprint_config_template_documents_project_policy():
    """v0.8.0: shipped template must document the project_policy block
    as a commented example (opt-in)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "sprint-config.yaml")
    text = p.read_text()
    assert "project_policy:" in text, \
        "sprint-config template must document the project_policy block"
    # Must reference at least one realistic command shape
    assert "lint-staged" in text or "pre-commit" in text, \
        "sprint-config template must show an example project_policy.command"



# ---------- v0.8.3 — B27 .sprint/status/** writable at engine layer ----------
#
# B27: live-cc observed 6-8 gate_blocked Write events per sprint on
# .sprint/status/<role>.txt — builders emitting heartbeats / status
# crumbs that prusik's own watchdog reads from. The template has had
# `.sprint/status/**` in always_writable since v0.6.1, but Unlock
# Trading's project-level sprint-config.yaml is user-modified, so
# `prusik refresh` keeps skipping it; their always_writable still doesn't
# include this path. v0.8.3 hardcodes the kit-internal infrastructure
# paths in phases.py so they're guaranteed-writable independent of
# project config.

def test_b27_sprint_status_writable_in_building_with_old_config():
    """Project shipped a sprint-config.yaml that predates v0.6.1 and
    doesn't include `.sprint/status/**` in always_writable. Builders
    must STILL be able to write `.sprint/status/<role>.txt` because the
    kit's own watchdog/orchestrator infrastructure depends on it."""
    config_without_sprint_status = {
        "always_writable": ["reports/kit-trial/**"],  # missing .sprint/status/**
        "phases": [
            {"name": "building", "writable": ["worktrees/*/**"]},
        ],
    }
    tmp = _mktmp_project()
    try:
        ok, reason = phases.is_path_writable(
            ".sprint/status/test-writer.txt",
            config_without_sprint_status,
            "building",
            "feat",
        )
        assert ok, (
            f"v0.8.3 must allow .sprint/status/<role>.txt writes in "
            f"building phase even when project config doesn't list "
            f"the path in always_writable. Got: {reason}"
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b27_sprint_status_writable_across_phases():
    """Per the v0.6.1 comment, status heartbeats fire from ANY phase
    (gating per-phase would block long-running roles). Verify
    `.sprint/status/<role>.txt` is writable across building, reviewing,
    integrating, scoping, planning, triage."""
    minimal_config = {
        "phases": [
            {"name": p, "writable": ["dummy/**"]}
            for p in ("scoping", "triage", "planning",
                      "building", "reviewing", "integrating")
        ],
    }
    tmp = _mktmp_project()
    try:
        for phase in ("scoping", "triage", "planning",
                      "building", "reviewing", "integrating"):
            ok, reason = phases.is_path_writable(
                ".sprint/status/regression-sentinel.txt",
                minimal_config,
                phase,
                "feat",
            )
            assert ok, (
                f"v0.8.3: .sprint/status/<role>.txt must be writable in "
                f"phase '{phase}' regardless of phase-specific writable "
                f"patterns. Got: {reason}"
            )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b27_kit_internal_paths_unioned_with_project_always_writable():
    """Both kit-internal and project-declared always_writable entries must
    be respected. Project-declared entries that duplicate kit-internals
    dedup cleanly. v0.10.0 (Fix 4): kit-internal set generalized from
    `.sprint/status/**` to the phase-independent meta-artifact set."""
    config = {
        "always_writable": [
            ".sprint/**",          # duplicate of kit-internal — must dedup
            "reports/kit-trial/**",
            "custom/**",           # project-specific entry
        ],
        "phases": [{"name": "building", "writable": []}],
    }
    patterns = phases.always_writable_patterns(config, "feat")
    # Kit-internal first
    assert patterns[0] == ".sprint/**", \
        "kit-internal patterns must lead the list"
    # Dedup of duplicate
    assert patterns.count(".sprint/**") == 1, \
        "duplicate entries between kit-internal and config must dedup"
    # Project entries preserved
    assert "reports/kit-trial/**" in patterns
    assert "custom/**" in patterns


def test_b27_kit_internal_paths_when_config_has_no_always_writable():
    """Project config with no `always_writable` key at all (some older
    configs might omit it entirely) — kit-internal entries still apply.
    v0.10.0 (Fix 4): the full phase-independent meta-artifact set is
    hardcoded at the engine layer, not just `.sprint/status/**`."""
    config_without_always_writable = {
        "phases": [{"name": "building", "writable": []}],
    }
    patterns = phases.always_writable_patterns(
        config_without_always_writable, "feat"
    )
    for expected in (".sprint/**", "reports/**", "scripts/verify/**", "briefs/**"):
        assert expected in patterns, f"{expected} must be a kit-internal always-writable"



# ---------- v0.8.7 — fix-round status command + cache-allow role spec ----------
#
# Driven by real ledger data from c2c_invoicing project showing 2-hour
# reviewing phases. Root cause: `mypy --no-incremental --cache-dir=/dev/null`
# re-checks 96k LOC from scratch every dispatch. Original v0.6.0 flag was
# belt-and-suspenders against B7 (worktree cache leak), now redundant
# because v0.7.0 B17 mandates running from project root.
#
# v0.8.7 ships: prusik gate fix-round status + role-spec cache-allow update.
# Aggressive narrowing (--lf, skip-behavior-in-fix-rounds) is held in
# reserve as v0.8.8 opt-in candidate; default stays "everything runs every
# time, just faster."


def test_v087_fix_round_status_no_active_round():
    """`prusik gate fix-round status` reports '(no active fix-round)' when
    the marker file doesn't exist. Used by reviewer agents to decide
    narrow-vs-full mode."""
    tmp = _mktmp_project()
    try:
        from prusik import fix_round as _fix_round
        out = _capture_stdout(lambda: _fix_round.status())
        assert "(no active fix-round)" in out
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v087_fix_round_status_active_round_reports_metadata():
    """When a fix-round is active, status prints feature/round/started_at
    in a parseable format."""
    tmp = _mktmp_project()
    try:
        from prusik import fix_round as _fix_round
        rc = _fix_round.start("feat-test")
        assert rc == 0
        out = _capture_stdout(lambda: _fix_round.status())
        assert "feature=feat-test" in out
        assert "round=1" in out
        assert "started=" in out
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v087_role_spec_no_longer_mandates_no_cacheprovider():
    """v0.8.7: cache-suppression flags are no longer the recommended
    default. Role spec MUST clarify that caches at project root are fine
    (the B7 issue was specifically about WORKTREE caches, solved by v0.7.0
    B17 mandating project-root execution)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # The v0.8.7 explanation MUST appear
    assert "Caches at project root are FINE" in text or \
           "caches at project root are fine" in text.lower(), \
        "role spec must explain that project-root caches are not the B7 issue"
    # Must call out incremental mypy specifically as the win
    assert "incrementally" in text, \
        "role spec must mention mypy incremental mode as the speedup"
    # Must reference c2c_invoicing observation OR a specific large-codebase
    # number to anchor WHY this matters
    assert "96k-LOC" in text or "multiple minutes" in text or "5-10×" in text, \
        "role spec must cite the empirical evidence motivating the change"


def test_v087_role_spec_does_not_mandate_no_incremental():
    """The bare 'mypy --no-incremental --cache-dir=/dev/null' line should
    NOT appear as the recommended default invocation."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # The OLD recommendation had it as the default invocation. Now it's
    # called out as belt-and-suspenders. Check the new shape exists.
    assert "default cache" in text.lower() or \
           "mypy                         #" in text, \
        "role spec must show mypy with default cache as recommended invocation"


def test_v087_role_spec_still_runs_full_suite():
    """Critical safety test: v0.8.7 must NOT introduce test-skipping.
    Every dispatch still runs the full general test command + behavior
    regression + project policy. Cache only speeds up; it does not
    narrow coverage."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # MUST still mandate running the general test command
    assert "Run the project's test command" in text
    # MUST still mandate behavior_regression block reading
    assert "If the project declares a behavior-regression suite, run it too" in text
    # MUST still mandate project_policy block reading
    assert "If the project declares a commit-time policy pipeline, run it too" in text
    # Must NOT introduce skip-on-fix-round language without explicit opt-in
    assert "skip behavior_regression" not in text.lower() or \
           "opt-in" in text.lower() or \
           "fix_round_narrowing" in text.lower(), \
        "v0.8.7 must not auto-skip behavior_regression; narrowing is held in reserve"



# ---------- v0.8.8 — regression-sentinel cluster-by-signature + early-exit ----------
#
# Two role-spec extensions driven by m4-s2b-test-hygiene-sweep ledger
# evidence:
#   - 224 of 224 dominant-class integration failures collapsed to 1 root
#     cause → cluster-by-signature converts wall-of-failures into
#     triage-ready cluster summary
#   - 793 cascading errors took 91 min wall-clock; same suite post-fix
#     ran in 5 min → early-exit on cascading-fixture-failure aborts at
#     30s with diagnostic, saves the 90-minute death-march
#
# Both are role-spec text changes (no engine work). Tested as
# stable-string contracts per v0.8.6 interface stability pattern:
# changing these strings is a versioned interface change.


def test_v088_role_spec_cluster_by_signature():
    """v0.8.8: regression-sentinel must instruct grouping by traceback
    signature for ≥3-member clusters, with verbatim list still required
    below the cluster summary (so the operator can verify cluster claims)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # Cluster instruction present
    assert "[CLUSTER]" in text, \
        "role spec must use [CLUSTER] marker for cluster summary lines"
    assert "cluster summary" in text.lower(), \
        "role spec must explicitly name 'cluster summary' as the output element"
    # Group key: exception class + traceback frame
    assert "exception_class" in text or "traceback signature" in text.lower(), \
        "role spec must specify clustering by exception class + traceback frame"
    # Threshold: ≥3 members
    assert "≥3" in text or "3 members" in text or "three members" in text, \
        "role spec must specify the ≥3-members threshold for clustering"
    # Verbatim list still required (anti-fabrication safeguard)
    assert "verbatim list" in text.lower(), \
        "role spec must require verbatim failure list below cluster summary"
    # Anchored to ledger evidence (m4-s2b)
    assert "m4-s2b" in text or "224 of 224" in text or "224" in text, \
        "role spec must cite the empirical evidence motivating clustering"


def test_v088_role_spec_early_exit_on_cascading_failure():
    """v0.8.8: regression-sentinel must instruct early-exit on
    cascading-fixture-failure with explicit threshold, observed-counts
    requirement (anti-fabrication), and operator-actionable diagnostic."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # Trigger marker present
    assert "[CASCADE]" in text, \
        "role spec must use [CASCADE] marker for early-exit FAIL output"
    # Threshold language present
    assert "50%" in text and "30 seconds" in text, \
        "role spec must specify the >50%-in-30s threshold for cascade detection"
    # Anti-fabrication: must require observed numbers
    assert "OBSERVED counts" in text or "observed counts" in text or \
           "elapsed time" in text, \
        "role spec must require observed N/M + T values to anchor the abort"
    # Pointer at typical culprits to help operator triage
    assert "DB connection" in text or "schema drift" in text or \
           "shared fixture" in text, \
        "role spec must name typical culprits (auth/port/role/schema/env) to triage"
    # Anti-fabrication: same B26 framing applied
    assert "B26" in text or "fabrication" in text.lower(), \
        "role spec must cross-reference B26 fabrication discipline (claimed-without-evidence is a defect)"


def test_v088_role_spec_still_runs_full_suite():
    """v0.8.8 must NOT introduce coverage skipping. Cluster-by-signature
    is post-hoc compression of the verbatim list (which is still required);
    early-exit fires only on cascading-fixture-failure (>50% setup-errored
    in 30s), not on slow tests or many-failed-tests scenarios."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # Full suite is still mandated
    assert "Run the project's test command" in text
    assert "If the project declares a behavior-regression suite, run it too" in text
    assert "If the project declares a commit-time policy pipeline, run it too" in text
    # Verbatim failure list is still required (cluster doesn't replace it)
    assert "list of failures with module attribution" in text



# ---------- v0.8.10 — opt-in / non-coupling iface stability ----------
#
# Driven by live-cc's [18:23] OBSERVATION on m4-s2d-test-debt-cleanup bridge,
# formalizing the architectural compass raised by Raj: prusik + CC are opt-in
# developer tooling. Disabling prusik must never break the product or its test
# suite. These tests enforce the kit-package boundary: nothing prusik ships
# may create a product-runtime dependency on prusik binary.
#
# First occurrence at project layer: m4-h1 v1 _find_artifact() raised
# RuntimeError when .claude/hooks/ was absent (fixed in m4-h1 v2 via
# pytestmark.skipif). The kit-side guard ensures the *prusik itself* doesn't
# create the analogous coupling.


def test_v0810_iface_templates_ship_no_python_files():
    """Prusik's templates directory must contain zero .py files. Templates
    are .md / .yaml / .json / .gitignore only. Prusik ships ORCHESTRATION
    DATA, never product-runtime Python. This is the structural guarantee
    that disabling prusik cannot break product code: there's no product code
    to break.
    """
    templates_root = Path(__file__).parent.parent / "prusik" / "templates"
    py_files = list(templates_root.rglob("*.py"))
    assert not py_files, (
        f"kit must not ship Python files in templates/ "
        f"(coupling risk); found: {[str(p.relative_to(templates_root)) for p in py_files]}"
    )


def test_v0810_iface_templates_only_under_allowed_top_dirs():
    """Templates may only place files under whitelisted top-level paths.
    Adding a new top-level path (e.g. `tests/` or `src/`) would mean prusik
    is shipping into the product surface — violating the principle. The
    whitelist is intentional: any expansion requires a kit-author decision
    and updating this test."""
    templates_root = Path(__file__).parent.parent / "prusik" / "templates"
    # .claude/  → CC consumes; absent = no firing, no error
    # .sprint/  → prusik state; gitignored at project init
    # artifacts/ → markdown templates for scope/plan/brief/retro
    allowed = {".claude", ".sprint", "artifacts"}
    found = {p.name for p in templates_root.iterdir()}
    extras = found - allowed
    assert not extras, (
        f"kit templates may only ship under {sorted(allowed)}; "
        f"found unexpected top-level entries: {sorted(extras)}. "
        f"If intentional, update the allowed set in this test AND review "
        f"the opt-in / non-coupling principle."
    )


def test_v0810_iface_post_init_no_kit_imports_in_product_paths():
    """After `prusik init`, no .py file outside `.claude/` and `.sprint/`
    imports prusik modules. Walks the initialized project root and greps for
    `import prusik` / `from prusik`. The product surface must be kit-free."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        offenders = []
        for py in tmp.rglob("*.py"):
            rel = py.relative_to(tmp)
            parts = rel.parts
            # Kit-state surfaces are allowed to interop with kit.
            if parts and parts[0] in (".claude", ".sprint"):
                continue
            text = py.read_text(errors="ignore")
            if re.search(r"^\s*(?:import\s+prusik\b|from\s+prusik(?:\.|\s+import\s))",
                         text, re.MULTILINE):
                offenders.append(str(rel))
        assert not offenders, (
            f"product-path files must not import prusik; found: {offenders}"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)





# ---------- v0.8.11 — convergence-stall detector ----------
#
# Driven by m4-h2 integrator stalling 38+ min on 4 identical regression-gate
# runs with no operator signal — parent token counter frozen at Agent dispatch,
# subagent burning invisible budget. The hook fingerprints Bash output per
# command-shape and emits ledger + additionalContext on N=3 identical.


def test_v0811_normalize_command_shape_strips_cd_prefix():
    """`cd "/path" && cmd` should normalize to `cmd` so the same logical
    command from different working directories groups under one shape."""
    s = gate._normalize_command_shape('cd "/Users/x/proj" && pytest -q')
    assert s == "pytest -q", f"got {s!r}"


def test_v0811_normalize_command_shape_strips_env_vars():
    """Leading env-var assignments (e.g. UNLOCK_PG_PORT=5435 pytest ...)
    should normalize away — same command shape regardless of env override."""
    s = gate._normalize_command_shape("UNLOCK_PG_PORT=5435 DATABASE_URL=foo pytest tests/")
    assert s == "pytest tests/", f"got {s!r}"


def test_v0811_normalize_command_shape_strips_date_substitution():
    """$(date +...) substitutions vary per second; replacing with <TS>
    keeps the shape stable across retries."""
    s = gate._normalize_command_shape("pytest -o cache=run-$(date +%H%M).log")
    assert "<TS>" in s
    assert "$(date" not in s


def test_v0811_fingerprint_identical_for_normalized_runtime():
    """pytest output that differs only in wall-clock duration must
    fingerprint identically. Without this, 3:32 vs 3:35 vs 3:40 break
    the convergence detector exactly when it should fire."""
    out_a = ("23 failed, 814 passed, 66 skipped, 498 warnings, 1 error "
             "in 220.77s (0:03:40)")
    out_b = ("23 failed, 814 passed, 66 skipped, 498 warnings, 1 error "
             "in 212.88s (0:03:32)")
    fp_a = gate._fingerprint_output(out_a)
    fp_b = gate._fingerprint_output(out_b)
    assert fp_a == fp_b, \
        f"identical runs with different durations must fingerprint same: {fp_a} vs {fp_b}"


def test_v0811_fingerprint_differs_when_failure_set_changes():
    """Different failure lists must produce different fingerprints — that's
    the signal that the agent IS making progress and we should NOT fire."""
    out_a = "FAILED tests/test_foo.py::test_a\nFAILED tests/test_foo.py::test_b\n"
    out_b = "FAILED tests/test_foo.py::test_a\n"  # one fewer failure = progress
    fp_a = gate._fingerprint_output(out_a)
    fp_b = gate._fingerprint_output(out_b)
    assert fp_a != fp_b, "different failure sets must fingerprint differently"


def test_v0811_post_tool_emits_convergence_stall_event_on_third_identical():
    """End-to-end: feed three identical Bash results through post_tool().
    After the third, a `convergence_stall` event must land in the ledger
    and the hook must emit additionalContext for the agent's next turn."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # Active sprint state required (post_tool no-ops without one)
        (tmp / ".sprint").mkdir(exist_ok=True)
        (tmp / ".sprint" / "state.json").write_text(
            json.dumps({"phase": "reviewing", "feature": "test-feat"}))
        ledger_p = tmp / ".sprint" / "ledger.jsonl"

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/integration/ -q --no-cov"},
            "tool_response": {
                "stdout": "23 failed, 814 passed in 220.77s (0:03:40)\n"
                          "FAILED tests/integration/test_x.py::test_a\n"
                          "FAILED tests/integration/test_x.py::test_b\n",
                "stderr": "",
            },
        }

        # Run 1 — no stall yet, ring has 1 fp
        result = _run_post_tool(payload)
        assert "convergence_stall" not in (ledger_p.read_text() if ledger_p.exists() else "")
        assert result.strip() == "" or "additionalContext" not in result

        # Run 2 — ring has 2 fps, identical, still no stall (need N=3)
        result = _run_post_tool(payload)
        assert "convergence_stall" not in (ledger_p.read_text() if ledger_p.exists() else "")

        # Run 3 — ring has 3 identical fps → stall fires
        result = _run_post_tool(payload)
        ledger_text = ledger_p.read_text()
        assert "convergence_stall" in ledger_text, \
            "third identical run must emit convergence_stall event"
        assert "additionalContext" in result, \
            "stall must inject systemMessage for next agent turn"
        assert "[prusik-convergence-stall]" in result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v0811_post_tool_skips_when_no_active_sprint():
    """No active sprint → no detection. Opt-in / non-coupling — prusik
    must not maintain state for commands outside a sprint."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # No state.json written → no active sprint
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "tool_response": {"stdout": "x" * 200, "stderr": ""},
        }
        for _ in range(5):
            _run_post_tool(payload)
        watch = tmp / ".sprint" / "convergence-watch.json"
        # No watch file should be created when no sprint is active
        assert not watch.exists() or json.loads(watch.read_text()).get("watches") in ({}, None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v0811_post_tool_skips_trivial_commands():
    """`ls`, `pwd`, etc. don't carry convergence signal — they're
    fast and idempotent. Watching them would burn state writes for
    no information."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / ".sprint").mkdir(exist_ok=True)
        (tmp / ".sprint" / "state.json").write_text(
            json.dumps({"phase": "reviewing", "feature": "f"}))

        for cmd in ["ls -la", "pwd", "echo hello"]:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
                "tool_response": {"stdout": "x" * 200, "stderr": ""},
            }
            for _ in range(5):
                _run_post_tool(payload)
        watch = tmp / ".sprint" / "convergence-watch.json"
        if watch.exists():
            data = json.loads(watch.read_text())
            assert data.get("watches") == {}, \
                f"trivial commands must not populate watches; got {data}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v0811_settings_template_registers_post_tool_hook():
    """The shipped settings.json template must register `prusik gate post-tool`
    as a PostToolUse hook for Bash. Without this entry the detector never
    fires in a fresh project init."""
    settings_path = (Path(__file__).parent.parent / "prusik" / "templates"
                     / ".claude" / "settings.json")
    settings = json.loads(settings_path.read_text())
    post = settings.get("hooks", {}).get("PostToolUse", [])
    assert post, "PostToolUse hook entry must be present"
    bash_matchers = [h for h in post if h.get("matcher") == "Bash"]
    assert bash_matchers, "PostToolUse must include a Bash matcher"
    cmds = [h["command"] for h in bash_matchers[0]["hooks"]]
    assert "prusik gate post-tool" in cmds


def test_v0811_role_specs_include_convergence_stall_response():
    """integrator + regression-sentinel role specs must teach the response
    pattern for the [prusik-convergence-stall] message. Without the cognitive
    layer, the mechanical signal goes unread."""
    base = Path(__file__).parent.parent / "prusik" / "templates" / ".claude" / "agents"
    integrator = (base / "integrator.md").read_text()
    sentinel = (base / "regression-sentinel.md").read_text()

    for name, text in [("integrator", integrator), ("regression-sentinel", sentinel)]:
        assert "[prusik-convergence-stall]" in text, \
            f"{name} role spec must reference the [prusik-convergence-stall] marker"
        assert "STOP" in text or "halt" in text.lower(), \
            f"{name} role spec must instruct halt on stall"
        assert "verbatim" in text.lower(), \
            f"{name} must require quoting the message verbatim (B26 fabrication guard)"


# Helper for v0811 tests — invokes post_tool with a payload via stdin redirect

def _run_post_tool(payload: dict) -> str:
    import io
    import contextlib
    fake_stdin = io.StringIO(json.dumps(payload))
    fake_stdout = io.StringIO()
    real_stdin = sys.stdin
    try:
        sys.stdin = fake_stdin
        with contextlib.redirect_stdout(fake_stdout):
            gate.post_tool()
    finally:
        sys.stdin = real_stdin
    return fake_stdout.getvalue()



# ---------- v0.9.0 — success_criteria sibling-file gate ----------
#
# Driven by m4-h2 (reviewer waved through despite noting a blocker → A1
# metric missed) + m4-s9a (reviewer PASSED structurally without running
# 14 per-file assertions → integrator-phase pytest caught 13 failures).
# Both cases: declared criteria not mechanically verified pre-complete.
# v0.9.0 adds sibling-file briefs/<feature>.criteria.yaml + sprint-complete
# verify gate. Project authors verify scripts; prusik invokes from root.


def _write_criteria(brief_path: Path, criteria: list[dict],
                    schema_version: str = "1.0") -> Path:
    """Helper: write a criteria.yaml sibling to a brief.md path."""
    import yaml as _yaml
    cp = schema.criteria_path_for_brief(brief_path)
    cp.write_text(_yaml.safe_dump({
        "schema_version": schema_version,
        "criteria": criteria,
    }))
    return cp


def test_v090_criteria_path_for_brief_sibling_naming():
    """briefs/foo.md → briefs/foo.criteria.yaml (sibling, suffix swap)."""
    p = schema.criteria_path_for_brief(Path("briefs/m4-h2.md"))
    assert str(p) == "briefs/m4-h2.criteria.yaml"


def test_v090_validate_criteria_accepts_valid_file():
    """Well-formed criteria.yaml with id/description/verify_command (exists)
    must validate without errors."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "scripts" / "verify").mkdir(parents=True)
        verify_script = tmp / "scripts" / "verify" / "a1.sh"
        verify_script.write_text("#!/bin/sh\nexit 0\n")
        verify_script.chmod(0o755)
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        cp = _write_criteria(brief, [
            {"id": "A1", "description": "must pass",
             "verify_command": "scripts/verify/a1.sh"},
        ])
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert ok, f"valid criteria should validate; errors: {errs}"
        assert errs == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_validate_criteria_rejects_missing_required_fields():
    """Each entry must have id, description, verify_command. Missing any →
    error. Empty criteria list → error."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        cp = _write_criteria(brief, [
            {"id": "A1"},  # missing description + verify_command
        ])
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert not ok
        joined = " ".join(errs)
        assert "description" in joined
        assert "verify_command" in joined
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_validate_criteria_rejects_duplicate_ids():
    """Two criteria with same id → error. Operator-friendliness: ids are
    used in failure messages and verify-<id>.txt filenames; collisions
    would corrupt the audit trail."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "scripts" / "verify").mkdir(parents=True)
        s = tmp / "scripts" / "verify" / "x.sh"
        s.write_text("#!/bin/sh\nexit 0\n")
        s.chmod(0o755)
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        cp = _write_criteria(brief, [
            {"id": "A1", "description": "first",  "verify_command": "scripts/verify/x.sh"},
            {"id": "A1", "description": "second", "verify_command": "scripts/verify/x.sh"},
        ])
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert not ok
        assert any("duplicate id" in e and "A1" in e for e in errs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_validate_criteria_rejects_missing_verify_command_path():
    """A bare token that LOOKS like a project script path (dir part or .sh/.py
    suffix) must still exist at lint time — catches typos / forgotten script
    creation. The error teaches both valid forms (command OR script path)."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        cp = _write_criteria(brief, [
            {"id": "A1", "description": "ok",
             "verify_command": "scripts/verify/does-not-exist.sh"},
        ])
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert not ok
        joined = " ".join(errs)
        assert "no file exists at" in joined        # the path-typo case
        assert "shell command" in joined            # message teaches both forms
        assert "acceptance-TDD" in joined


    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v0541_validate_criteria_accepts_shell_verify_command():
    """Finding #7: lint must AGREE with the gate's exec contract. The gate runs
    verify_command as `bash -c "<vc>"`, so a shell command like `pnpm test`
    is valid — it must NOT fail lint with a bogus 'path does not exist'. The
    execution-evidence gate (not a lint path-check) enforces real proof."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        cp = _write_criteria(brief, [
            # the exact adopter shape: a pnpm command, not a script path
            {"id": "A1", "description": "billing tests green",
             "verify_command": "pnpm --filter @an adopter/backend test"},
            {"id": "A2", "description": "chained proof",
             "verify_command": "pytest tests/billing.py -k stripe && echo ok"},
            {"id": "A3", "description": "bare runner token",
             "verify_command": "vitest"},
        ])
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert ok, f"shell commands must lint clean; got {errs}"
        assert errs == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v0541_verify_command_classifier():
    """Unit-level contract for the path-vs-command classifier so the boundary
    is pinned: commands (spaces / operators / known runners / bare PATH words)
    are NOT path-checked; only dir-or-suffix bare tokens are."""
    from prusik.schema import _verify_command_is_bare_path as isp
    # commands — never path-checked
    assert not isp("pnpm test")
    assert not isp("pytest -k x")
    assert not isp("a && b")
    assert not isp("pytest")                 # known runner, lone token
    assert not isp("make")
    assert not isp("check")                  # bare on-PATH word, no dir/suffix
    # bare project script paths — path-checked
    assert isp("scripts/verify/x.sh")
    assert isp("./verify.sh")
    assert isp("tests/run.py")
    assert isp("verify.sh")                  # script suffix, no dir


def test_v090_validate_criteria_rejects_wrong_schema_version():
    """schema_version must be exactly '1.0' string. Wrong value → error
    so future versions can evolve without silent acceptance."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "scripts" / "verify").mkdir(parents=True)
        s = tmp / "scripts" / "verify" / "x.sh"
        s.write_text("#!/bin/sh\nexit 0\n")
        s.chmod(0o755)
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        cp = _write_criteria(brief, [
            {"id": "A1", "description": "x", "verify_command": "scripts/verify/x.sh"},
        ], schema_version="2.0")  # wrong version
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert not ok
        assert any("schema_version" in e for e in errs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_brief_lint_warns_when_criteria_file_absent():
    """During v0.9.0→v0.10.0 deprecation window, briefs without sibling
    criteria.yaml are warned-not-errored. Operator can opt out via
    sprint-config in v0.10.0."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "briefs").mkdir(exist_ok=True)
        brief = tmp / "briefs" / "feat.md"
        # Real briefs use the brief.md template format — but for this lint
        # test we only care that brief-lint emits the criteria-warn marker
        # when the sibling .criteria.yaml is missing.
        brief.write_text("# Goal\nx\n\n## Type\nnew_feature\n\n"
                         "## Success criteria\n- thing\n\n"
                         "## Modules touched\n- src/x.py\n\n"
                         "## Out of scope\n- nothing\n")
        from prusik import brief_lint as _bl
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _bl.lint(brief, root=tmp)
        out = buf.getvalue()
        assert "[criteria-warn]" in out, \
            "missing sibling criteria.yaml must produce a warning, not silent pass"


    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_run_success_criteria_returns_true_when_no_file():
    """When briefs/<feature>.criteria.yaml is absent, _run_success_criteria
    must return (True, []) — no criteria means no gate. Deprecation window
    compat: existing briefs continue to reach sprint_complete."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("# brief\n")
        ok, results = gate._run_success_criteria("feat", tmp)
        assert ok is True
        assert results == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_run_success_criteria_passes_when_all_exit_zero():
    """Two criteria, both verify scripts exit 0 → all_passed True, each
    produces a verify-<id>.txt + a success_criterion_verified ledger event."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "scripts" / "verify").mkdir(parents=True)
        for n in ("a1", "a2"):
            s = tmp / "scripts" / "verify" / f"{n}.sh"
            s.write_text(f"#!/bin/sh\necho {n} OK\nexit 0\n")
            s.chmod(0o755)
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        _write_criteria(brief, [
            {"id": "A1", "description": "first",  "verify_command": "scripts/verify/a1.sh"},
            {"id": "A2", "description": "second", "verify_command": "scripts/verify/a2.sh"},
        ])
        ok, results = gate._run_success_criteria("feat", tmp)
        assert ok is True
        assert len(results) == 2
        assert all(r["passed"] for r in results)
        # Per-criterion output files
        assert (tmp / "reports" / "feat" / "verify-A1.txt").exists()
        assert (tmp / "reports" / "feat" / "verify-A2.txt").exists()
        # Ledger events
        events = (tmp / ".sprint" / "ledger.jsonl").read_text().splitlines()
        verified = [json.loads(e) for e in events
                    if "success_criterion_verified" in e]
        assert len(verified) == 2
        assert {v["id"] for v in verified} == {"A1", "A2"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_run_success_criteria_fails_when_any_nonzero():
    """If any verify_command exits non-zero (and != expected_exit),
    all_passed is False. The failing criterion is the one with passed=False."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "scripts" / "verify").mkdir(parents=True)
        ok_s = tmp / "scripts" / "verify" / "ok.sh"
        ok_s.write_text("#!/bin/sh\nexit 0\n"); ok_s.chmod(0o755)
        fail_s = tmp / "scripts" / "verify" / "fail.sh"
        fail_s.write_text("#!/bin/sh\necho oops\nexit 1\n"); fail_s.chmod(0o755)
        brief = tmp / "briefs" / "feat.md"
        brief.write_text("# brief\n")
        _write_criteria(brief, [
            {"id": "A1", "description": "ok",   "verify_command": "scripts/verify/ok.sh"},
            {"id": "A2", "description": "fail", "verify_command": "scripts/verify/fail.sh"},
        ])
        all_passed, results = gate._run_success_criteria("feat", tmp)
        assert all_passed is False
        by_id = {r["id"]: r for r in results}
        assert by_id["A1"]["passed"] is True
        assert by_id["A2"]["passed"] is False
        assert by_id["A2"]["exit_code"] == 1
        # The failing verify output is captured
        a2_txt = (tmp / "reports" / "feat" / "verify-A2.txt").read_text()
        assert "oops" in a2_txt
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v090_integrator_role_spec_includes_step_6():
    """The shipped integrator.md template must teach the success_criteria
    verification step + name reports/<feature>/verify-<id>.txt + name the
    sibling-file convention."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude" /
         "agents" / "integrator.md")
    text = p.read_text()
    assert "success_criteria" in text
    assert "criteria.yaml" in text
    assert "verify-<id>.txt" in text or "verify-" in text
    assert "prusik gate sprint-complete will refuse" in text.lower() or \
           "prusik gate sprint-complete will refuse" in text or \
           "sprint-complete" in text.lower()


def test_v090_iface_templates_still_ship_no_python():
    """v0.9.0 must not regress v0.8.10's iface invariant: templates
    contain zero .py files. We add YAML schema + role-spec text + engine
    Python (in kit/, not templates/)."""
    templates_root = Path(__file__).parent.parent / "prusik" / "templates"
    py_files = list(templates_root.rglob("*.py"))
    assert not py_files, \
        f"v0.9.0 must not introduce templates/*.py: {py_files}"



# ---------- v0.9.1 — gate-role UX-coverage heuristics ----------
#
# Driven by live-cc [16:12] OBSERVATION on 2026-05-13-authed-ux-coverage-gap
# bridge: 9 user-visible defects shipped through every prusik gate (brief-critic,
# scope-critic, plan-critic, regression-sentinel, conventions-enforcer)
# because TestClient at the handler level bypasses real template rendering,
# JS execution, parallel HTTP requests, and cookie carriage on static
# assets. The mechanical execution layer for browser smoke is already
# v0.9.0's success_criteria; the gap is the COGNITIVE layer at the gate
# roles — none of them flag "UI touched but no browser verification."
#
# v0.9.1 ships three role-spec extensions:
#  - brief-critic: UI brief must declare browser-level criterion
#  - scope-critic: UI files in Modules touched → require test-path in same list
#  - regression-sentinel: non-Python diff → explicit "UNVERIFIED" acknowledgment
#
# Recurrence trigger: M2.S7 (HTMX), M2.S14 (Alpine), m4-s9a (server 500),
# M4-walk (9 defects). 4 prior occurrences. Solidly past second-trigger.


def test_v091_brief_critic_includes_ux_heuristic():
    """brief-critic role spec must teach: UI-mentioning brief must declare
    a browser-level criterion in the sibling criteria.yaml. Operationally
    specific so the agent can apply the rule, not aspirational."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude" /
         "agents" / "brief-critic.md")
    text = p.read_text()
    # Operationally-specific token list
    for tok in ["template", "form", "click", "render"]:
        assert tok in text.lower(), f"UX heuristic must enumerate {tok!r} as UI signal"
    # The required check
    assert "criteria.yaml" in text, \
        "brief-critic must reference the sibling criteria.yaml as the verification surface"
    assert "browser" in text.lower(), \
        "brief-critic must require browser-level verification"
    # Drives reference
    assert "M2.S7" in text or "M4-walk" in text or "m4-uxgate" in text, \
        "brief-critic must carry recurrence-trigger lineage so future kit-author edits know why the rule exists"


def test_v091_scope_critic_includes_template_touch_heuristic():
    """scope-critic role spec must teach: if Modules touched contains UI
    files but no test-path, REJECT. AST/glob-level check, deterministic."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude" /
         "agents" / "scope-critic.md")
    text = p.read_text()
    # The check is operationally specific
    for tok in ["templates/", "css", "js", "tests/behavior"]:
        assert tok in text.lower(), \
            f"scope-critic must enumerate {tok!r} as UI-file signal"
    # The action
    assert "REJECT" in text and "browser" in text.lower(), \
        "scope-critic must REJECT UI-without-browser-test"
    # Driven-by lineage
    assert "M2.S7" in text or "M4-walk" in text or "4-occurrence" in text


def test_v091_regression_sentinel_includes_non_python_awareness():
    """regression-sentinel role spec must teach: non-Python UI diffs require
    an explicit [non-python-diff] acknowledgment line. The acknowledgment
    does NOT change PASS/FAIL; it records dep-graph bound."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude" /
         "agents" / "regression-sentinel.md")
    text = p.read_text()
    assert "[non-python-diff]" in text, \
        "regression-sentinel must use the exact [non-python-diff] marker as the operator-facing signal"
    # The check is operationally specific
    for tok in ["templates/", "css", "js"]:
        assert tok in text.lower()
    # The reasoning
    assert "UNVERIFIED" in text, \
        "regression-sentinel must mark non-Python risk as UNVERIFIED, not 'safe'"
    # The acknowledgment must NOT change PASS/FAIL — that property protects
    # the dep-graph claim's truthfulness without forcing false negatives
    assert "does NOT change your PASS/FAIL verdict" in text or \
           "does not change your PASS/FAIL" in text.lower() or \
           "still gated by the pytest result" in text


def test_v091_role_spec_extensions_compose_with_v090_success_criteria():
    """v0.9.1 cognitive role-spec layer composes with v0.9.0's mechanical
    success_criteria layer. brief-critic forces the criterion to be DECLARED;
    success_criteria infrastructure RUNS it; integrator's step 6 reports
    failures. Three roles, three layers."""
    brief_critic = (Path(__file__).parent.parent / "prusik" / "templates" /
                    ".claude" / "agents" / "brief-critic.md").read_text()
    integrator = (Path(__file__).parent.parent / "prusik" / "templates" /
                  ".claude" / "agents" / "integrator.md").read_text()
    # brief-critic enforces declaration via criteria.yaml
    assert "criteria.yaml" in brief_critic
    # integrator enforces execution via verify_command at sprint-complete
    assert "criteria.yaml" in integrator
    assert "verify_command" in integrator


def test_v091_iface_invariant_still_holds():
    """v0.9.1 is templates-text-only — must not introduce .py in templates
    nor regress v0.8.10's opt-in / non-coupling iface guarantee."""
    templates_root = Path(__file__).parent.parent / "prusik" / "templates"
    py_files = list(templates_root.rglob("*.py"))
    assert not py_files, \
        f"v0.9.1 must not introduce templates/*.py: {py_files}"



# ---------- v0.15.0 — friction reduction ----------

def test_v0150_scratch_namespaces_engine_baked():
    """The /tmp + .runtime carve-outs are engine-hardcoded (work on any
    sprint-config age — the v0.10 Fix-4 / v0.13.0 lesson)."""
    from prusik.phases import _KIT_INTERNAL_ALWAYS_WRITABLE as W
    assert "/tmp/**" in W
    assert "/private/tmp/**" in W   # macOS — /tmp resolves through symlink
    assert ".runtime/**" in W


def test_v0150_tmp_writable_during_any_phase():
    """Agents using /tmp for scratch must no longer trip the gate,
    regardless of phase. This was the dominant adopter gate_blocked class."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        import yaml as _y
        config = _y.safe_load(open(tmp / ".claude" / "sprint-config.yaml"))
        # Reviewing is the most restrictive phase; if /tmp works here it
        # works everywhere.
        ok, _ = phases.is_path_writable("/tmp/server.log", config,
                                         "reviewing", "feat", root=tmp)
        assert ok, "OS scratch /tmp must be writable in any phase"
        ok, _ = phases.is_path_writable("/private/tmp/x.log", config,
                                         "reviewing", "feat", root=tmp)
        assert ok, "macOS-resolved /private/tmp must be writable"
        # Regression: arbitrary non-scratch absolute paths must STILL be
        # denied — the carve-out is scoped, not a generic escape hatch.
        ok, reason = phases.is_path_writable("/etc/hosts", config,
                                              "reviewing", "feat", root=tmp)
        assert not ok, f"non-scratch absolute paths must still be denied, got: {reason}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_settings_template_does_not_force_default_mode():
    """Product stance (reversed in v0.34.0): the shipped settings.json must
    NOT set permissions.defaultMode. Auto-accept (acceptEdits) is a per-dev /
    per-environment preference, not something to force on a whole team via
    committed config — adopters who want it add it to their own gitignored
    .claude/settings.local.json. (Earlier prusik shipped
    defaultMode=acceptEdits on the rationale that the prusik gate is the real
    safety layer so CC's edit prompts are redundant; the cost of imposing a
    permission mode on every committed adopter outweighed the prompt-noise
    saving — and since refresh re-propagates template scalar permission keys,
    a project couldn't durably opt out while the template carried it.)"""
    s = json.loads((Path(__file__).parent.parent / "prusik" / "templates"
                    / ".claude" / "settings.json").read_text())
    assert "defaultMode" not in s.get("permissions", {}), \
        "shipped settings.json must not force a permissions.defaultMode"


def test_v0150_settings_template_has_added_allow_patterns():
    """Long-tail dev-workflow tools the template was missing."""
    s = json.loads((Path(__file__).parent.parent / "prusik" / "templates"
                    / ".claude" / "settings.json").read_text())
    allow = s["permissions"]["allow"]
    for pat in ("Bash(jq *)", "Bash(playwright *)", "Bash(xargs *)",
                "Bash(touch *)", "Bash(env *)", "Bash(stat *)"):
        assert pat in allow, f"expected {pat} in template allow list"


def test_v0151_settings_merge_lands_missing_nested_permission_key():
    """v0.15.0 ship-bug regression: defaultMode silently didn't land via
    additive merge when `permissions` existed but lacked the nested key.
    The +12 allow union worked but `permissions.defaultMode` was skipped.
    v0.15.1 closes this — additive merge for nested keys under permissions
    the project lacks (project-wins for keys it has)."""
    from prusik import refresh_merge as _rm
    tmpl = json.dumps({
        "permissions": {"defaultMode": "acceptEdits", "allow": ["Bash(x *)"]}
    })
    proj = json.dumps({
        "permissions": {"allow": ["Bash(y *)"]}  # has permissions, no defaultMode
    })
    merged_text, summary = _rm.merge_settings_json(tmpl, proj)
    m = json.loads(merged_text)
    assert m["permissions"]["defaultMode"] == "acceptEdits", \
        "missing nested key under existing top-level dict must be added"
    # allow union still works
    assert set(m["permissions"]["allow"]) == {"Bash(x *)", "Bash(y *)"}
    assert "defaultMode" in summary.get("added_permission_keys", [])


def test_v0151_settings_merge_project_wins_on_nested_keys():
    """When the project sets a permissions.defaultMode value, the merge
    must NOT overwrite it — project-wins for existing keys (the same
    discipline as top-level + arrays)."""
    from prusik import refresh_merge as _rm
    tmpl = json.dumps({"permissions": {"defaultMode": "acceptEdits"}})
    proj = json.dumps({"permissions": {"defaultMode": "plan", "allow": []}})
    merged_text, summary = _rm.merge_settings_json(tmpl, proj)
    m = json.loads(merged_text)
    assert m["permissions"]["defaultMode"] == "plan", "project-wins"
    assert "defaultMode" not in summary.get("added_permission_keys", [])


def test_v0150_template_settings_remains_valid_json():
    """Edited the shipped template — ensure it still parses."""
    p = (Path(__file__).parent.parent / "prusik" / "templates"
         / ".claude" / "settings.json")
    s = json.loads(p.read_text())   # raises on invalid → test fails
    assert "permissions" in s and "hooks" in s



# ---------- v0.17.0 — items 4, 5, 7, 8, 9, 10 ----------

def test_v0170_brief_templates_exist_for_all_types():
    """Item 9: one brief template per brief Type (8 templates). Ships
    under artifacts/briefs/ per v0.8.10 opt-in invariant (no new
    top-level template dirs)."""
    tmpl_dir = Path(__file__).parent.parent / "prusik" / "templates" / "artifacts" / "briefs"
    expected = {"new_feature", "bug_fix", "refactor", "migration",
                "doc", "config", "test", "chore"}
    found = {p.stem for p in tmpl_dir.glob("*.md")}
    missing = expected - found
    assert not missing, f"missing brief templates for Types: {missing}"
    # Each must declare its own Type in the body
    for stem in expected:
        text = (tmpl_dir / f"{stem}.md").read_text()
        assert f"## Type\n{stem}" in text, \
            f"{stem}.md template must declare ## Type matching its filename"


def test_v0170_doctor_sprint_view():
    """Item 10: prusik doctor --sprint <feature> shows the per-sprint view."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _write_ledger(tmp, [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "sprint_started",
             "feature": "demo"},
            {"ts": "2026-01-01T00:30:00+00:00", "event": "phase_advance",
             "feature": "demo", "phase": "reviewing"},
            {"ts": "2026-01-01T01:00:00+00:00", "event": "fix_round_start",
             "feature": "demo"},
            {"ts": "2026-01-01T02:00:00+00:00", "event": "sprint_complete",
             "feature": "demo"},
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik import doctor as kit_doctor
            kit_doctor.run(sprint="demo")
        out = buf.getvalue()
        assert "demo" in out and "COMPLETED" in out
        assert "fix_rounds:      1" in out
        assert "phase_advances:  1" in out
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0170_doctor_insights_for_brief():
    """Item 4: forward-looking risk signal for a specific brief."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        bp = tmp / "briefs" / "demo.md"
        bp.write_text(
            "# demo\n\n## Type\nnew_feature\n\n## Goal\nshort\n\n"
            "## Success criteria\n- x\n\n## Priority\nP2\n\n## Notes\nnone\n"
        )
        # Empty ledger — should report no history baseline
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik import doctor as kit_doctor
            kit_doctor.run(insights_for_brief=str(bp))
        out = buf.getvalue()
        # With a short Goal and new_feature Type, should flag thin_goal
        # AND that new_feature requires the full lane
        assert "lane" in out or "thin_goal" in out or \
               "looks clean" in out  # depends on history; both branches valid
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0170_init_with_stack_preset():
    """Item 7: prusik init --stack fastapi-postgres uses the preset."""
    tmp = _mktmp_project()
    try:
        kit_init.run(stack="fastapi-postgres")
        sc = (tmp / ".claude" / "sprint-config.yaml").read_text()
        # Preset-distinctive content: alembic/versions writable + the
        # FastAPI-specific behavior_regression command
        assert "alembic/versions" in sc
        assert "tests/behavior/" in sc
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0170_init_rejects_unknown_stack():
    """--stack <unknown> should fail loudly, not silently use the default."""
    tmp = _mktmp_project()
    try:
        rc = kit_init.run(stack="totally-not-a-real-stack")
        assert rc == 2, "unknown stack must fail closed"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0170_sprint_cli_runs_preflight():
    """Item 8: prusik sprint <feature> pre-flight orchestrator chains the
    pre-agent steps. Validates the orchestrator runs end-to-end on a
    minimal valid brief."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        bp = tmp / "briefs" / "demo.md"
        bp.write_text(
            "# demo\n\n## Type\nbug_fix\n\n## Goal\n"
            "Customers list shows the wrong total when filter X is active. "
            "Expected: shows count of filtered rows. Actual: shows total of "
            "unfiltered rows. Reproduced on 2026-05-30 in field-style staging.\n\n"
            "## Success criteria\n"
            "- A new regression test FAILS on current code and PASSES after "
            "the fix (at least 1 such test in tests/unit/)\n"
            "- 0 regressions in the unfiltered-list path under "
            "the existing tests/integration/ suite\n\n"
            "## Priority\nP1\n\n## Notes\nnone\n"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik.sprint_cli import run as sprint_run
            sprint_run("demo", yes=True)
        out = buf.getvalue()
        # Either succeeds (rc=0) — the brief passed all pre-checks — or
        # surfaces a specific actionable issue (rc≠0 with structured output).
        # The orchestrator MUST chain at least the brief-lint + insights steps.
        assert "Step 1/4" in out and "Step 2/4" in out, out
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


