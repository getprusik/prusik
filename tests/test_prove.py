"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: prove (prusik/prove.py + prusik/evidence.py) — the standalone
anti-fabrication gate. No FSM, no init.

Run: uv run python -m pytest tests/test_prove.py -v
"""

from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)

from prusik import evidence, prove

# moat-finding markers — findings this file's ruff-clean evidence tests lock in (C7):
#   moat-finding: fb-c72887f762a4  — clean lint captured as "nothing executed" (v0.91.0)
#   moat-finding: fb-30365316abd5  — execution-evidence lint false-negatives on clean code (v0.67.0)


def _run(cmd, **kw):
    """Run prove.run capturing stdout; return (rc, out)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = prove.run(cmd, **kw)
    return rc, buf.getvalue()


def _sh(line):
    """argv for a shell line, as the CLI would pass `prusik prove -- bash -c '<line>'`."""
    return ["bash", "-c", line]


# ---------- evidence.executed_count (the ungameable primitive) ----------

def test_executed_count_tests_sums_passed_and_failed():
    assert evidence.executed_count("tests", "3 passed, 2 failed in 0.4s") == 5


def test_executed_count_excludes_skipped_and_collected():
    # skip-only / collection-only must NOT count as executed
    assert evidence.executed_count("tests", "collected 9 items\n5 skipped in 0.1s") == 0


def test_executed_count_lint_ruff_clean_counts_but_silent_and_empty_scope_do_not():
    # field bridge #2 (refines v0.53.0): a clean non-verbose `ruff check` is a real
    # success that was unrepresentable. The discriminator is the COMMAND — a bare
    # marker is indistinguishable from `echo 'All checks passed!'` by output alone
    # (the v0.53.0 gaming case), so the marker counts ONLY when the run invoked
    # ruff. echo is not ruff; tsc/eslint stay silent-on-empty → still need a count.
    assert evidence.executed_count("lint", "All checks passed!") == 0   # no command
    assert evidence.executed_count(
        "lint", "All checks passed!", "ruff check src/") == 1           # real ruff
    assert evidence.executed_count(
        "lint", "All checks passed!", "echo 'All checks passed!'") == 0  # echo ≠ ruff
    # empty scope: ruff says so → still 0 (the wrong-glob false-clean stays caught)
    assert evidence.executed_count(
        "lint", "warning: No Python files found under the given path(s)\n",
        "ruff check src/") == 0
    # unrecognizable + silent-tool markers still 0
    assert evidence.executed_count("lint", "nothing recognizable", "ruff check") == 0
    assert evidence.executed_count("types", "Files:  2614") == 2614          # tsc
    assert evidence.executed_count("lint", '[{"filePath":"/a.ts"}]') == 1    # eslint
    # the adopter pain is gone: a real clean ruff run now PROVES
    assert evidence.prove_verdict("lint", 0,
        evidence.executed_count("lint", "All checks passed!", "ruff check src/"))[0]


def test_executed_count_lint_ruff_verbose_files_checked():
    # field BUG [12:51]: ruff's clean default ("All checks passed!") has NO count,
    # so a legitimately-clean lint phase false-blocked. `ruff check -v` emits the
    # real, ungameable scope signal — "Checked N files in: …" — and now proves.
    out = ("[DEBUG] Identified files to lint in: 1.7ms\n"
           "[DEBUG] Checked 62 files in: 22.1µs\n"
           "All checks passed!\n")
    assert evidence.executed_count("lint", out) == 62
    ok, msg = evidence.prove_verdict("lint", 0, evidence.executed_count("lint", out))
    assert ok and "62 checked" in msg
    # multiple invocations (monorepo) sum, consistent with tests/tsc
    assert evidence.executed_count("lint", "Checked 5 files in: 1ms\n"
                                           "Checked 8 files in: 2ms\n") == 13
    # the teaching error still names ruff as a path to a scope signal
    _, why = evidence.prove_verdict("lint", 0, 0)
    assert "ruff" in why


def test_executed_count_types_mypy_source_files():
    assert evidence.executed_count("types", "Success: no issues found in 12 source files") == 12


# ---------- evidence.prove_verdict ----------

def test_verdict_exit0_with_tests_is_proven():
    ok, reason = evidence.prove_verdict("tests", 0, 7)
    assert ok and "7 test(s) executed" in reason


def test_verdict_exit0_zero_executed_is_false_clean():
    ok, reason = evidence.prove_verdict("tests", 0, 0)
    assert not ok and "false-clean" in reason


def test_verdict_nonzero_exit_not_proven():
    ok, reason = evidence.prove_verdict("tests", 1, 50)
    assert not ok and "non-zero" in reason


# ---------- prove.run (end-to-end via shell) ----------

def test_prove_passes_when_tests_really_ran():
    rc, out = _run(_sh("echo '3 passed in 0.1s'"))
    assert rc == 0
    assert "✓ PROVEN" in out


def test_prove_catches_exit0_but_nothing_ran():
    """THE fabrication case: command exits 0 but no tests executed."""
    rc, out = _run(_sh("echo 'collected 0 items'; exit 0"))
    assert rc == 1, "exit 0 with zero executed must NOT pass"
    assert "✗ NOT PROVEN" in out
    assert "exit 0 alone does not prove" in out


def test_prove_catches_skip_only_run():
    rc, out = _run(_sh("echo '5 skipped in 0.1s'; exit 0"))
    assert rc == 1
    assert "NOT PROVEN" in out


def test_prove_fails_on_nonzero_exit():
    rc, out = _run(_sh("echo '1 failed in 0.1s'; exit 1"))
    assert rc == 1
    assert "non-zero" in out


def test_prove_min_executed_threshold():
    rc, _ = _run(_sh("echo '2 passed in 0.1s'"), min_executed=5)
    assert rc == 1, "executed below --min must fail"
    rc2, _ = _run(_sh("echo '6 passed in 0.1s'"), min_executed=5)
    assert rc2 == 0


def test_prove_lint_echoed_marker_is_not_proven():
    # Echoing a marker must NOT prove — that's exactly the fabrication prove
    # exists to catch. A real files-checked signal (tsc Files:) does prove.
    rc, _ = _run(_sh("echo 'All checks passed!'"), kind="lint")
    assert rc == 1
    rc2, out2 = _run(_sh("echo 'Files:  523'"), kind="types")
    assert rc2 == 0 and "NOT PROVEN" not in out2


def test_prove_strips_leading_dashdash():
    rc, _ = _run(["--", "echo", "4 passed in 0.1s"])
    assert rc == 0


def test_prove_preserves_arg_quoting():
    """shlex.join must not mangle a quoted arg with spaces (real CLI need:
    `prusik prove -- pytest -k 'name with spaces'`)."""
    rc, out = _run(["printf", "%s\\n", "7 passed in 0.1s"])
    assert rc == 0 and "PROVEN" in out


def test_prove_no_command_is_usage_error():
    rc, _ = _run([])
    assert rc == 2


def test_prove_json_output_shape():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = prove.run(_sh("echo '3 passed in 0.1s'"), json_output=True)
    out = buf.getvalue()
    payload = json.loads(out[out.index("{"):])
    assert rc == 0
    assert payload["proven"] is True
    assert payload["executed"] == 3
    assert payload["exit_code"] == 0
    assert payload["kind"] == "tests"


def test_prove_needs_no_init():
    """prove must work with zero ceremony — no .claude/, no .sprint/, nothing."""
    with tempfile.TemporaryDirectory() as td:
        cwd = os.getcwd()
        os.chdir(td)
        try:
            rc, out = _run(_sh("echo '1 passed in 0.1s'"))
            assert rc == 0 and "PROVEN" in out
            assert not (Path(td) / ".sprint").exists(), "prove must not create state"
            assert not (Path(td) / ".claude").exists()
        finally:
            os.chdir(cwd)


# ---------- shared core: gate.capture still uses the same primitive ----------

def test_gate_capture_alias_points_at_shared_primitive():
    from prusik import gate as _gate
    assert _gate._parse_nonempty_primitive is evidence.executed_count


def test_prove_emits_prove_run_ledger_event(tmp_path, monkeypatch):
    # v0.80.0 (finding #14): a prove run is recorded so a builder's full-suite
    # proof is measurable + gate-checkable.
    (tmp_path / ".sprint").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    from prusik import prove as _prove
    rc = _prove.run(["--", "echo", "3 passed"], kind="tests")
    assert rc == 0
    ledger = (tmp_path / ".sprint" / "ledger.jsonl").read_text()
    assert "prove_run" in ledger and '"kind": "tests"' in ledger
    assert '"proven": true' in ledger
