"""Affected-test selection (v0.69.0, field finding #5) — fail-fast subset; the full suite
at green stays the non-negotiable gate (this only orders work)."""

from __future__ import annotations

import shutil

from tests._common import _capture_stdout, _mktmp_project  # noqa: F401,E402
from prusik import affected


def _scope(tmp, feature, modules_block):
    p = tmp / "design" / feature / "scope.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"## Modules touched\n{modules_block}\n")


def test_name_matched_tests_selected():
    tmp = _mktmp_project()
    try:
        (tmp / "api").mkdir()
        (tmp / "api" / "clients.py").write_text("def clients_list():\n    return {}\n")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_clients_routes.py").write_text("def t(): pass\n")
        (tmp / "tests" / "test_unrelated.py").write_text("def t(): pass\n")
        _scope(tmp, "feat", "- `api/clients.py` — change it")
        r = affected.affected_tests("feat", tmp)
        assert "tests/test_clients_routes.py" in r["affected"]   # name-matched
        assert "tests/test_unrelated.py" not in r["affected"]
        assert r["full_suite_required"] is True                  # invariant
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_worktree_test_files_selected():
    tmp = _mktmp_project()
    try:
        wt = tmp / "worktrees" / "test-writer" / "tests"
        wt.mkdir(parents=True)
        (wt / "test_new_thing.py").write_text("def t(): pass\n")
        _scope(tmp, "feat", "- `x.py`")
        (tmp / "x.py").write_text("def x(): pass\n")
        r = affected.affected_tests("feat", tmp)
        assert "tests/test_new_thing.py" in r["affected"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sharp_reach_tests_selected_via_plan():
    """Reach reuses the SHARPENED plan-reach (named-handler-filtered), so it
    needs plan.md (the real sentinel-time artifact for team sprints) and a NAMED
    handler — keeping the subset tight, not every route in the file."""
    tmp = _mktmp_project()
    try:
        (tmp / "api").mkdir()
        (tmp / "api" / "team.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/team')\n"
            "@router.post('/invite')\n"
            "def invite():\n    return {}\n")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_routes_x.py").write_text(
            "def t(): client.post('/team/invite')\n")   # references touched route
        _scope(tmp, "feat", "- `api/team.py` — guard")
        plan = tmp / "design" / "feat" / "plan.md"
        plan.write_text("## Modules touched\n- `api/team.py`\n\n"
                        "## Build order\n- Fix `invite`: add authz guard.\n")
        r = affected.affected_tests("feat", tmp)
        assert "tests/test_routes_x.py" in r["affected"]   # via sharp reach
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_full_suite_required_always_true_and_cli_says_so():
    tmp = _mktmp_project()
    try:
        (tmp / "api").mkdir()
        (tmp / "api" / "clients.py").write_text("def f(): pass\n")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_clients.py").write_text("def t(): pass\n")
        _scope(tmp, "feat", "- `api/clients.py`")
        out = _capture_stdout(lambda: affected.run("feat", root=tmp))
        assert "FULL SUITE still required" in out
        assert "fail-fast only" in out
        # ledger event emitted
        assert "affected_tests" in (tmp / ".sprint" / "ledger.jsonl").read_text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_short_base_not_overmatched():
    """A 3-char module base (e.g. 'app') must not name-match every test."""
    tmp = _mktmp_project()
    try:
        (tmp / "app.py").write_text("def f(): pass\n")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_apple_pie.py").write_text("def t(): pass\n")
        _scope(tmp, "feat", "- `app.py`")
        r = affected.affected_tests("feat", tmp)
        assert "tests/test_apple_pie.py" not in r["affected"]  # 'app' too short
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
