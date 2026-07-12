"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: test_reach.

Run: uv run python -m pytest tests/test_test_reach.py -v
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


# ---------- v0.20.0 — test-set-reach + capture --reset + manifest dedup ----------

def test_v0200_test_reach_flags_outside_touched_test():
    """A test OUTSIDE the touched set that references a touched route
    must be flagged. Reproduces the m4-suspect-skip-audit class
    (occurrence 2; threshold met)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "invoices.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix=\"/invoices\")\n"
            "@router.get(\"/clients/search\")\n"
            "async def search(): pass\n"
        )
        # Test OUTSIDE worktrees/ — i.e. the "rest of the test suite"
        (tmp / "tests").mkdir(exist_ok=True)
        (tmp / "tests" / "test_search_other.py").write_text(
            "def test_legacy_search_route():\n"
            "    # asserts on /invoices/clients/search — outside our touched set\n"
            "    assert '/invoices/clients/search' in 'some response'\n"
        )
        from prusik.test_reach import find_test_reach
        findings = find_test_reach(
            [tmp / "src" / "invoices.py"], tmp,
        )
        route_flags = [f for f in findings if f["class"] == "route"]
        assert route_flags, f"expected route flag for outside-test, got: {findings}"
        assert route_flags[0]["contract_id"] == "/invoices/clients/search"
        assert any("test_search_other.py" in r
                   for r in route_flags[0]["references"])
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0200_test_reach_skips_touched_set_tests():
    """A test INSIDE the touched set (i.e. the sprint is modifying it)
    must NOT count as 'outside-reach' — by definition it's part of the
    reviewing set."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "api.py").write_text(
            "from fastapi import APIRouter\n"
            "r = APIRouter()\n"
            "@r.get(\"/api/foo\")\n"
            "async def foo(): pass\n"
        )
        # The test IS in the touched set
        (tmp / "tests").mkdir(exist_ok=True)
        touched_test = tmp / "tests" / "test_foo.py"
        touched_test.write_text(
            "def test_foo():\n    assert '/api/foo' == '/api/foo'\n"
        )
        from prusik.test_reach import find_test_reach
        # Pass BOTH the route file AND the test as touched
        findings = find_test_reach(
            [tmp / "src" / "api.py", touched_test], tmp,
        )
        # The route is touched; the test is also touched → no out-of-set flag
        assert findings == [], \
            f"touched-set tests must not flag; got: {findings}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0200_capture_reset_clears_prior_entries():
    """v0.20.0 --reset closes the stale-exit-1 footgun: a prior failed
    entry coexists with a later passing entry and silently poisons the
    every-entry-exit-0 gate check. With --reset, the prior is cleared."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "reports" / "demo").mkdir(parents=True)
        ev_path = tmp / "reports" / "demo" / "regression.evidence.json"
        # Plant a stale exit-1 entry
        ev_path.write_text(json.dumps({
            "schema_version": "1.0",
            "entries": [{
                "phase": "regression", "command": "stale", "exit_code": 1,
                "nonempty_primitive": {"kind": "tests", "value": 0},
                "output_sha": "stale", "worktree_hash": "stale",
                "captured_by": "kit-gate-capture",
            }],
        }))
        import argparse as _ap
        with contextlib.redirect_stdout(io.StringIO()):
            rc = gate.capture(_ap.Namespace(
                feature="demo", phase="regression", kind="tests",
                command=["--", "echo", "5 passed"],
                reset=True,
                baseline_domain=None, baseline_source=None,
                baseline_known_failures=None,
            ))
        assert rc == 0
        entries = schema.load_evidence(ev_path)
        assert len(entries) == 1, "after --reset, only the new entry"
        assert entries[0]["exit_code"] == 0
        assert entries[0]["command"] != "stale"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0200_manifest_record_surface_write_dedup_within_minute():
    """v0.20.0: two record_surface_write calls within 60s with the same
    (command, version, files_changed) tuple coalesce instead of producing
    duplicate history entries. Closes the [08:29] bookkeeping noise."""
    from prusik import manifest as kit_manifest
    m = kit_manifest.new_manifest(
        version="0.20.0", files=[], directories_created=[],
        gitignore_block_added=False, detection={},
    )
    # init entry from new_manifest is already there
    initial_history_len = len(m["history"])
    files = [{"path": "a.txt", "hash": "x"}]
    kit_manifest.record_surface_write(m, command="refresh",
                                       version="0.20.0", files=files)
    after_first = len(m["history"])
    assert after_first == initial_history_len + 1, "first refresh appends"
    # Same shape, immediately after — should coalesce, NOT append
    kit_manifest.record_surface_write(m, command="refresh",
                                       version="0.20.0", files=files)
    after_dup = len(m["history"])
    assert after_dup == after_first, \
        f"same-shape duplicate within 60s must coalesce; history grew to {after_dup}"


def test_v0200_manifest_record_surface_write_appends_on_distinct():
    """A second call with DIFFERENT shape (different version, or different
    files_changed count) still appends — the dedup is for same-shape
    bursts, not all repeats."""
    from prusik import manifest as kit_manifest
    m = kit_manifest.new_manifest(
        version="0.20.0", files=[], directories_created=[],
        gitignore_block_added=False, detection={},
    )
    kit_manifest.record_surface_write(m, command="refresh",
                                       version="0.20.0", files=[])
    n1 = len(m["history"])
    # Different version → distinct event → should append
    kit_manifest.record_surface_write(m, command="refresh",
                                       version="0.21.0", files=[])
    assert len(m["history"]) == n1 + 1


