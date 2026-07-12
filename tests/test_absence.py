"""Absence detector — the out-of-diff "planned deliverable silently not produced"
class (field escape #1). Critics review what's present; this reconciles what the
plan PROMISED against what the worktree CONTAINS. High-precision by design: a false
flag erodes the gate (the --skip-lint habituation trap), so a declared file created
under a slightly different path, and unfilled template placeholders, must NOT flag.
"""

from __future__ import annotations

from pathlib import Path

from prusik import absence

_PLAN = """## Goal recap
Detail view.

## Modules touched
- + `packages/web/src/DetailView.tsx` — new component
- `packages/web/src/api.ts` — extend

## Build order
1. Add `packages/web/src/DetailView.tsx`
2. Wire `packages/web/e2e/detail-view.e2e.ts`

## Test plan
- happy path renders the detail view
- failure mode shows 404

## Risks
- none
"""

_PLAN_PLACEHOLDER_TESTS = """## Goal recap
x

## Modules touched
- `packages/web/src/api.ts` — extend

## Test plan
- <happy path>
- <failure mode>
"""


def _project(tmp: Path, plan: str = _PLAN) -> Path:
    root = tmp / "proj"
    (root / "design" / "feat").mkdir(parents=True)
    (root / "design" / "feat" / "plan.md").write_text(plan)
    return root


def _wt_file(root: Path, rel: str, body: str = "x\n") -> None:
    p = root / "worktrees" / "builder" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_declared_files_collects_new_and_backtick_paths(tmp_path):
    root = _project(tmp_path)
    declared = absence.declared_files("feat", root)
    assert "packages/web/src/DetailView.tsx" in declared      # + new + backtick
    assert "packages/web/e2e/detail-view.e2e.ts" in declared  # backtick in build order
    # an EXISTING (non-+) module is not a promised deliverable
    assert "packages/web/src/api.ts" not in declared


def test_missing_deliverables_are_flagged(tmp_path):
    root = _project(tmp_path)            # nothing produced, no worktree visibility
    rep = absence.absence_check("feat", root)
    assert not rep["clean"]
    assert "packages/web/src/DetailView.tsx" in rep["missing_files"]
    assert "packages/web/e2e/detail-view.e2e.ts" in rep["missing_files"]
    # no worktree diff to see → test_plan heuristic can't claim a miss; the absent
    # files carry the flag instead (no false confidence either way)
    assert rep["test_plan_unmet"] is False


def test_no_diff_visibility_does_not_fire_test_plan_heuristic(tmp_path):
    """ADVERSARIAL: a SHIPPED feature (worktrees gone) with tests living at root
    must NOT be flagged test_plan_unmet — we can't see the diff, so we don't claim
    a miss (the post-integration false-positive guard)."""
    root = _project(tmp_path)
    # files assembled at root (shipped), no worktrees/
    (root / "packages" / "web" / "src").mkdir(parents=True)
    (root / "packages" / "web" / "src" / "DetailView.tsx").write_text("x\n")
    (root / "packages" / "web" / "e2e").mkdir(parents=True)
    (root / "packages" / "web" / "e2e" / "detail-view.e2e.ts").write_text("x\n")
    rep = absence.absence_check("feat", root)
    assert rep["test_plan_unmet"] is False
    assert rep["clean"] is True


def test_all_present_is_clean(tmp_path):
    root = _project(tmp_path)
    _wt_file(root, "packages/web/src/DetailView.tsx")
    _wt_file(root, "packages/web/e2e/detail-view.e2e.ts")   # also satisfies test plan
    rep = absence.absence_check("feat", root)
    assert rep["clean"] is True
    assert rep["missing_files"] == []
    assert rep["test_plan_unmet"] is False


def test_same_basename_different_path_is_not_falsely_flagged(tmp_path):
    """ADVERSARIAL: the builder created the e2e under tests/ not e2e/ — same
    basename, real file. Must NOT flag (precision over recall: a false absence
    erodes the gate)."""
    root = _project(tmp_path)
    _wt_file(root, "packages/web/src/DetailView.tsx")
    _wt_file(root, "packages/web/tests/detail-view.e2e.ts")   # different dir, same name
    rep = absence.absence_check("feat", root)
    assert "packages/web/e2e/detail-view.e2e.ts" not in rep["missing_files"]
    assert rep["test_plan_unmet"] is False    # a test file WAS produced


def test_test_plan_unmet_when_only_non_test_files_change(tmp_path):
    """An adopter's exact escape: component shipped, the promised e2e absent — the test
    plan committed tests but the diff added none."""
    root = _project(tmp_path)
    _wt_file(root, "packages/web/src/DetailView.tsx")     # only the component
    rep = absence.absence_check("feat", root)
    assert rep["test_plan_unmet"] is True
    assert "packages/web/e2e/detail-view.e2e.ts" in rep["missing_files"]


def test_template_placeholder_test_plan_does_not_flag(tmp_path):
    """An unfilled `<happy path>` test plan is not a commitment → no false flag."""
    root = _project(tmp_path, _PLAN_PLACEHOLDER_TESTS)
    rep = absence.absence_check("feat", root)
    assert rep["test_plan_unmet"] is False


def test_directory_token_without_extension_is_not_flagged(tmp_path):
    """Only file-like tokens count; a declared directory is too low-precision."""
    root = _project(tmp_path, """## Modules touched
- + `packages/web/newdir/` — a new dir

## Test plan
- <happy path>
""")
    assert absence.declared_files("feat", root) == set()
    assert absence.absence_check("feat", root)["clean"] is True


def test_no_plan_is_clean(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert absence.absence_check("feat", root)["clean"] is True
