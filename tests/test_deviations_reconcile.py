"""Mid-build deviation reconciliation (v0.83.0, field finding #17): the boundary check now
surfaces real drift (#15), so a builder needs a SANCTIONED way to reconcile it —
write the deviation LOG (always-writable) and have the boundary check CREDIT it."""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import consistency, phases


def test_deviations_log_is_writable_in_building_but_scope_is_not():
    pats = phases.always_writable_patterns({}, "feat")
    assert "design/*/deviations.md" in pats        # the log is always-writable
    # the boundary artifacts are NOT (scope/plan stay controlled mid-build)
    assert not any(p.endswith("scope.md") or p.endswith("plan.md") for p in pats)
    # and is_path_writable honors it regardless of phase
    cfg = {"phases": [{"name": "building", "writable": ["worktrees/*/**"]}]}
    assert phases.is_path_writable("design/feat/deviations.md", cfg, "building", "feat")[0]
    assert not phases.is_path_writable("design/feat/scope.md", cfg, "building", "feat")[0]


def _scope(proj, mods):
    p = proj / "design" / "feat" / "scope.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"## Modules touched\n{mods}\n")


def test_boundary_credits_declared_deviation():
    proj = _mktmp_project()
    try:
        _scope(proj, "- src/")
        (proj / "design" / "feat" / "deviations.md").write_text(
            "## Deviations\n"
            "- DEV-001: `errors/AppError.ts` — shared error type needed by the guard\n"
            "- DEV-002: `migrate.ts` — schema migration for the new column\n")
        wt = proj / "worktrees" / "solo"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "x.py").write_text("x = 1\n")                  # in scope
        (wt / "errors").mkdir()
        (wt / "errors" / "AppError.ts").write_text("export {}\n")    # out-of-scope, DECLARED
        (wt / "migrate.ts").write_text("export {}\n")                # out-of-scope, DECLARED
        (wt / "rogue").mkdir()
        (wt / "rogue" / "z.py").write_text("z = 1\n")                # out-of-scope, UNDECLARED
        v = " ".join(consistency.builder_writes_within_plan(proj, "feat"))
        assert "errors/AppError.ts" not in v        # credited via deviations.md
        assert "migrate.ts" not in v                # credited
        assert "rogue/z.py" in v                    # undeclared → still flagged
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_boundary_credits_declared_dotfile(): # field finding #20.1
    """A leading-dot filename (.env.test) recorded as a deviation must be CREDITED —
    the old `.strip("./")` turned it into `env.test` and missed it."""
    proj = _mktmp_project()
    try:
        _scope(proj, "- src/")
        (proj / "design" / "feat" / "deviations.md").write_text(
            "## Deviations\n"
            "- DEV-005: `.env.test` — test fixture env required by the billing suite\n")
        wt = proj / "worktrees" / "solo"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "x.py").write_text("x = 1\n")          # in scope
        (wt / ".env.test").write_text("KEY=1\n")             # out-of-scope, DECLARED dotfile
        v = " ".join(consistency.builder_writes_within_plan(proj, "feat"))
        assert ".env.test" not in v                          # credited despite leading dot
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_boundary_ignores_lockfiles(): # field finding #20.2
    """A dependency lockfile at repo root (pnpm-lock.yaml) is machine-written, never a
    plan deliverable — must not trip the boundary check even when undeclared."""
    proj = _mktmp_project()
    try:
        _scope(proj, "- src/")
        wt = proj / "worktrees" / "solo"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "x.py").write_text("x = 1\n")
        for lock in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock",
                     "poetry.lock", "Cargo.lock", "go.sum"):
            (wt / lock).write_text("# generated\n")
        v = " ".join(consistency.builder_writes_within_plan(proj, "feat"))
        for lock in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock",
                     "poetry.lock", "Cargo.lock", "go.sum"):
            assert lock not in v
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_boundary_does_not_flag_design_dir(): # field finding #20.3
    """The deviations log itself lives under design/ — it must never be flagged as an
    out-of-boundary new file (it was flagging itself)."""
    proj = _mktmp_project()
    try:
        _scope(proj, "- src/")
        wt = proj / "worktrees" / "solo"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "x.py").write_text("x = 1\n")
        (wt / "design" / "feat").mkdir(parents=True)
        (wt / "design" / "feat" / "deviations.md").write_text("## Deviations\n")
        v = " ".join(consistency.builder_writes_within_plan(proj, "feat"))
        assert "deviations.md" not in v
        assert "design/" not in v
    finally:
        shutil.rmtree(proj, ignore_errors=True)
