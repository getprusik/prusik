"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: verify_loop (kit/verify_loop.py, v0.28.0 — closed-loop verification).

The substantiation move for v0.26+v0.27: prove the loop actually closes.
"""

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


def _make_buggy_project(tmp: Path) -> None:
    """Create a minimal project with a known binding-mismatch bug.
    Mirrors case-001 shape (Python+FastAPI fetch_url class)."""
    (tmp / "src").mkdir()
    (tmp / "templates").mkdir()
    (tmp / "src" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "r = APIRouter(prefix='/v1')\n"
        "@r.get('/items')\n"
        "def items(): return []\n"
    )
    (tmp / "templates" / "page.html").write_text(
        "<button hx-get='/items'>Go</button>\n"  # missing prefix
    )


def _fix_binding(tmp: Path) -> None:
    """Apply the binding fix — template fetches the prefixed URL."""
    (tmp / "templates" / "page.html").write_text(
        "<button hx-get='/v1/items'>Go</button>\n"
    )


def _apply_suggested_test(tmp: Path, test_name: str) -> None:
    """Add prusik's suggested test to the project's test suite."""
    (tmp / "tests").mkdir(exist_ok=True)
    (tmp / "tests" / "test_routes.py").write_text(
        f"def {test_name}():\n"
        f"    # Agent applied prusik's suggested test\n"
        f"    assert True\n"
    )


def test_v0280_record_creates_checkpoint():
    """record() writes a checkpoint at .sprint/verify-loop/<feature>.json
    with the current findings."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _capture_stdout(lambda: verify_loop.record(feature="x"))
        cp = tmp / ".sprint" / "verify-loop" / "x.json"
        assert cp.exists()
        data = json.loads(cp.read_text())
        assert data["schema_version"] == "1.0"
        assert data["feature"] == "x"
        assert data["stats"]["count"] >= 1, \
            "buggy project should produce ≥1 finding at T0"
        # Every finding has a suggested_test attached
        for f in data["findings"]:
            assert f.get("suggested_test"), \
                "T0 snapshot must include the suggested_test scaffold"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_record_returns_nonzero_when_clean():
    """If T0 has NO findings, record returns rc=1 (caller may want to
    skip verify-loop for this sprint — nothing to verify)."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "noop.py").write_text("x = 1\n")
        rc = verify_loop.record(feature="clean")
    finally:
        os.chdir("/"); shutil.rmtree(tmp)
    assert rc == 1, "no findings at T0 → rc=1 (nothing to verify)"


def test_v0280_check_without_checkpoint_fails_loudly():
    """check() without a prior record() returns rc=2 (vs. silently
    treating it as 'all clear' — that'd be a silent-fallback)."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        rc = verify_loop.check(feature="x", json_output=True)
    finally:
        os.chdir("/"); shutil.rmtree(tmp)
    assert rc == 2, \
        "missing checkpoint must rc=2 (explicit error, not silent pass)"


def test_v0280_check_still_present_when_bug_unchanged():
    """If the binding wasn't fixed, T1 should report still-present
    on every T0 finding."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        # No fix applied — bug persists
        out = _capture_stdout(lambda: verify_loop.check(
            feature="x", json_output=True))
        data = json.loads(out)
        for r in data["per_finding"]:
            assert r["status"] == "still-present", \
                f"unchanged bug must show still-present; got {r['status']}"
        assert data["aggregate"]["loop_closed"] is False
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_check_fixed_but_no_test_when_only_fix_applied():
    """Agent fixed the binding but didn't add the suggested test —
    status should be fixed-but-no-test, loop NOT closed."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        _fix_binding(tmp)
        # No suggested test applied
        out = _capture_stdout(lambda: verify_loop.check(
            feature="x", json_output=True))
        data = json.loads(out)
        # Every finding should report fixed-but-no-test
        for r in data["per_finding"]:
            assert r["status"] == "fixed-but-no-test", \
                (f"fix without test should show fixed-but-no-test; "
                 f"got {r['status']}")
        assert data["aggregate"]["loop_closed"] is False
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_check_loop_closed_when_fix_and_test_applied():
    """Full closure: bug fixed AND suggested test in suite → loop closed.
    rc=0. This is the v0.28.0 falsifiable claim — when the loop closes
    end-to-end, prusik's evidence backs up the assertion-depth-gap
    structural-closure story."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        # Read the suggested test name from the checkpoint
        cp = json.loads((tmp / ".sprint" / "verify-loop" / "x.json").read_text())
        test_name = cp["findings"][0]["suggested_test"]["name"]
        # Apply fix + apply test
        _fix_binding(tmp)
        _apply_suggested_test(tmp, test_name)
        out = _capture_stdout(lambda: verify_loop.check(
            feature="x", json_output=True))
        data = json.loads(out)
        for r in data["per_finding"]:
            assert r["status"] == "resolved", \
                f"closed loop should show resolved; got {r['status']}"
            assert r["test_in_suite_files"], \
                "resolved status must show the test was found in the suite"
        assert data["aggregate"]["loop_closed"] is True, \
            "loop_closed must be True when all T0 findings resolved"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_check_returns_zero_when_loop_closed():
    """rc=0 only when ALL T0 findings resolved. Partial = rc=1."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        cp = json.loads((tmp / ".sprint" / "verify-loop" / "x.json").read_text())
        test_name = cp["findings"][0]["suggested_test"]["name"]
        _fix_binding(tmp)
        _apply_suggested_test(tmp, test_name)
        rc = verify_loop.check(feature="x", json_output=True)
        assert rc == 0, \
            "rc=0 only when loop closes end-to-end (this is the contract)"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_check_partial_returns_nonzero():
    """Partial closure (fix applied, no test) → rc=1. CI consumes this."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        _fix_binding(tmp)  # only fix, no test
        rc = verify_loop.check(feature="x", json_output=True)
        assert rc == 1, "partial closure → rc=1"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_finding_key_stable_across_record_and_check():
    """The id used at T0 must equal the id computed at T1 for the SAME
    finding shape. Otherwise check can't match T0 findings against T1's
    'still-present' set."""
    from prusik import verify_loop
    finding = {
        "class": "fetch_url",
        "template": "templates/page.html",
        "url": "/items",
        "expected": ["/v1/items"],
    }
    k1 = verify_loop._finding_key(finding)
    # Same finding shape, different attribute order — id must be identical
    finding2 = {
        "url": "/items",
        "class": "fetch_url",
        "template": "templates/page.html",
        "expected": ["/v1/items"],
    }
    k2 = verify_loop._finding_key(finding2)
    assert k1 == k2, "finding_key must be order-independent"


def test_v0280_ledger_records_verify_loop_events():
    """record/check both append ledger events for traceability —
    `prusik doctor --insights` can later aggregate 'how often does the
    loop close?'"""
    from prusik import verify_loop
    from prusik import ledger as kit_ledger
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        _ = _capture_stdout(lambda: verify_loop.check(
            feature="x", json_output=True))
        events = kit_ledger.read_all()
        kinds = [e.get("event") for e in events]
        assert "verify_loop_recorded" in kinds
        assert "verify_loop_checked" in kinds
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0280_find_test_in_suite_grep_works():
    """_find_test_in_suite finds Python AND JS test definitions by
    name. Critical: this is what distinguishes 'resolved' from
    'fixed-but-no-test'."""
    from prusik import verify_loop
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Python test
        (root / "tests").mkdir()
        (root / "tests" / "test_x.py").write_text(
            "def test_my_thing():\n    assert True\n")
        # JS test
        (root / "src").mkdir()
        (root / "src" / "x.test.ts").write_text(
            "test('my js thing', () => { expect(1).toBe(1); });\n")
        # Python match
        found = verify_loop._find_test_in_suite("test_my_thing", root)
        assert any("test_x.py" in f for f in found)
        # JS match
        found = verify_loop._find_test_in_suite("my js thing", root)
        assert any("x.test.ts" in f for f in found)
        # No match for nonexistent
        found = verify_loop._find_test_in_suite("test_nothing_here", root)
        assert found == []


def test_v0280_json_output_schema_versioned():
    """check() --json output carries schema_version. v1.0 is stable;
    silent bumps break CI consumers."""
    from prusik import verify_loop
    tmp = _mktmp_project()
    try:
        _make_buggy_project(tmp)
        _ = _capture_stdout(lambda: verify_loop.record(feature="x"))
        out = _capture_stdout(lambda: verify_loop.check(
            feature="x", json_output=True))
        data = json.loads(out)
        assert data["schema_version"] == "1.0"
        assert "aggregate" in data
        assert "per_finding" in data
        # aggregate has the falsifiable fields
        assert "t0_findings" in data["aggregate"]
        assert "resolved" in data["aggregate"]
        assert "loop_closed" in data["aggregate"]
    finally:
        os.chdir("/"); shutil.rmtree(tmp)
