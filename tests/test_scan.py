"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: scan (kit/scan.py, v0.24.0 — day-1 adoption-funnel close).

Run: uv run python -m pytest tests/test_scan.py -v
Or run the whole suite: uv run python -m pytest tests/ -v
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


def test_v0240_scan_no_findings_on_clean_repo():
    """A repo with no bindings should produce rc=0 + no findings."""
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "src").mkdir()
        (td_path / "src" / "noop.py").write_text("def f(): return 1\n")
        rc = _capture_stdout_with_rc(
            lambda: kit_scan.scan(root=td_path, json_output=False))
    assert rc == 0, "clean repo must exit 0"


def _capture_stdout_with_rc(fn):
    """Run fn() (which returns rc) while suppressing its stdout. Return rc."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn()
    return rc


def test_v0240_scan_flags_known_bug_in_initial_repo():
    """Run scan against case-001's initial-repo (bug PRESENT). Must
    flag the fetch_url mismatch. This is the day-1 substantiation
    signal: prusik scan works against a real defect class without needing
    sprints / FSM / worktrees."""
    from prusik import scan as kit_scan
    benchmarks = Path(__file__).parent.parent / "benchmarks" / "cases"
    case_root = benchmarks / "case-001-dev1-fetch-url-route-path" / "initial-repo"
    assert case_root.exists(), f"case-001 missing at {case_root}"

    out = _capture_stdout(lambda: kit_scan.scan(root=case_root,
                                                  json_output=False))
    assert "/clients/search" in out, \
        f"scan output must mention the buggy URL; got: {out[:500]}"
    assert "/invoices/clients/search" in out, \
        "scan output must include the suggested-fix path"


def test_v0240_scan_no_findings_on_clean_variant():
    """Case-001 clean/ variant (bug FIXED) should produce no findings.
    FP control via scan-mode."""
    from prusik import scan as kit_scan
    benchmarks = Path(__file__).parent.parent / "benchmarks" / "cases"
    clean_root = benchmarks / "case-001-dev1-fetch-url-route-path" / "clean"
    assert clean_root.exists()
    rc = _capture_stdout_with_rc(
        lambda: kit_scan.scan(root=clean_root, json_output=False))
    assert rc == 0, "scan against clean variant must rc=0 (no findings)"


def test_v0240_scan_skips_default_noise_dirs():
    """node_modules / .venv / __pycache__ / .git etc. are skipped by
    default. Otherwise scan on a real repo with vendored deps would be
    a noise generator."""
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "app.py").write_text("def f(): return 1\n")
        for noise in ("node_modules", ".venv", "__pycache__", "dist"):
            (td_path / noise).mkdir()
            (td_path / noise / "ignored.py").write_text("ignored = True\n")
        files, stats = kit_scan._collect_files(td_path, file_limit=5000,
                                                  include_test_reach=False)
    file_strs = [str(f) for f in files]
    assert any("app.py" in f for f in file_strs)
    for noise in ("node_modules", ".venv", "__pycache__", "dist"):
        assert not any(noise in f for f in file_strs), \
            f"scan must skip {noise}/ by default"


def test_v0240_scan_respects_file_limit_truncation():
    """When the repo exceeds --limit, scan reports truncation rather
    than silently scanning less than the user expects (no-silent-fallback
    discipline)."""
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for i in range(10):
            (td_path / f"f{i}.py").write_text("x = 1\n")
        files, stats = kit_scan._collect_files(td_path, file_limit=3,
                                                  include_test_reach=False)
    assert len(files) == 3
    assert stats["truncated"] is True


def test_v0240_scan_json_mode_emits_machine_readable():
    """--json mode emits parseable JSON with the expected keys."""
    from prusik import scan as kit_scan
    benchmarks = Path(__file__).parent.parent / "benchmarks" / "cases"
    case_root = benchmarks / "case-001-dev1-fetch-url-route-path" / "initial-repo"

    out = _capture_stdout(lambda: kit_scan.scan(root=case_root,
                                                  json_output=True))
    data = json.loads(out)
    assert "root" in data
    assert "stats" in data
    assert "findings" in data and "detectors" in data
    assert "total" in data
    assert data["total"] >= 1, "case-001 initial-repo must have ≥1 binding finding"
    binding = [f for f in data["findings"] if f["detector"] == "binding"]
    assert binding, "expected a binding finding"
    for f in data["findings"]:
        for k, v in f.items():
            assert not isinstance(v, Path), \
                f"finding[{k}] leaked a Path object — must serialize as str"


def test_v0240_scan_returns_nonzero_when_findings_present():
    """rc=1 when findings exist (signal for CI / scripts). rc=0 only
    when truly nothing flags. This is the falsifiable contract."""
    from prusik import scan as kit_scan
    benchmarks = Path(__file__).parent.parent / "benchmarks" / "cases"
    case_root = benchmarks / "case-001-dev1-fetch-url-route-path" / "initial-repo"
    rc = _capture_stdout_with_rc(
        lambda: kit_scan.scan(root=case_root, json_output=False))
    assert rc == 1, "scan with findings must rc=1 (falsifiable signal)"


def test_v0240_scan_handles_nonexistent_path():
    """A bad --path arg fails fast with rc=2 (vs. silently scanning cwd
    which would be a confusing-fallback — the no-silent-fallback
    discipline applies here too)."""
    from prusik import scan as kit_scan
    rc = kit_scan.scan(root=Path("/definitely/not/a/real/path/xyz"),
                        json_output=False)
    assert rc == 2, "nonexistent root must fail fast (rc=2)"


def test_v0240_scan_no_ledger_writes():
    """scan-mode is READ-ONLY by design — no .sprint/ created, no
    ledger writes, no manifest mutations. An adopter can run it
    BEFORE installing the kit."""
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "app.py").write_text("def f(): return 1\n")
        sprint_dir = td_path / ".sprint"
        before = sprint_dir.exists()
        _ = _capture_stdout(lambda: kit_scan.scan(root=td_path,
                                                    json_output=False))
        after = sprint_dir.exists()
    assert before == after, \
        "prusik scan must NOT create .sprint/ (read-only invariant)"
    assert not after, "scan against a clean tmp must leave it clean"


def test_v0240_scan_cross_stack_picks_up_jsx_findings():
    """case-004 is a JS-stack case. scan must flag it the same way it
    flags Python-stack case-001 — substantiates v0.22.0 cross-stack."""
    from prusik import scan as kit_scan
    benchmarks = Path(__file__).parent.parent / "benchmarks" / "cases"
    case_root = benchmarks / "case-004-express-fetch-url-mismatch" / "initial-repo"
    assert case_root.exists()
    out = _capture_stdout(lambda: kit_scan.scan(root=case_root,
                                                  json_output=True))
    data = json.loads(out)
    assert data["total"] >= 1, \
        "scan must catch the JS-stack case-004 too (cross-stack substantiation)"
