"""fb-afbe9deead21 — a deferred test tier must have a destination.

Reviewing EXCLUDES `reviewing_defer_markers` tests (live-server smokes: pre-integration
the running server is still the OLD binary) and defers them to sprint-complete — but
only a brief's own criteria ever ran them there. An adopter's sprint excluded
`browser_smoke` in reviewing (correctly), its criteria were all integration tests, and
a real regression (a 500 on a write route) passed the gate, caught only by post-merge live
smoke. The deferral had no destination.

Adversarial cases are the point: an all-skip (server down) run, a run that EXCLUDES the
marker, a marker that appears only as TEXT (the engine's own fixtures — this file
included), a feature-less/historical prove run, and `-m slow` (which does NOT exclude a
`slow`+`browser_smoke` test) must never be credited.

moat-finding: fb-afbe9deead21
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from prusik import brief_lint, deferred_tier as dt, gate, gate_class, ledger, phases, schema


# ---- fixtures -----------------------------------------------------------------------

def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path, monkeypatch, files: dict[str, str]) -> Path:
    r = tmp_path
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t.test")
    _git(r, "config", "user.name", "t")
    for rel, body in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    (r / ".sprint").mkdir(exist_ok=True)
    monkeypatch.chdir(r)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    return r


SMOKE_APPLIED = (
    "import pytest\n\n"
    "@pytest.mark.browser_smoke\n"
    "def test_capture_page():\n    assert True\n"
)


def _events(root: Path, name: str) -> list[dict]:
    p = root / ".sprint" / "ledger.jsonl"
    if not p.exists():
        return []
    return [e for e in (json.loads(x) for x in p.read_text().splitlines() if x.strip())
            if e.get("event") == name]


def _enter_integrating(feature="feat"):
    ledger.append("phase_advance", from_phase="reviewing", to_phase="integrating",
                  feature=feature)


def _prove(command, *, executed=3, proven=True, feature="feat", kind="tests"):
    ledger.append("prove_run", kind=kind, exit_code=0 if proven else 1,
                  executed=executed, proven=proven, command=command,
                  feature=feature, phase="integrating")


# ---- marker presence: code, never text ------------------------------------------------

def test_decorator_applied_marker_is_present(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    assert dt.present_markers(r, ("browser_smoke",)) == ["browser_smoke"]


def test_module_pytestmark_applied_marker_is_present(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {
        "tests/test_a.py": "import pytest\npytestmark = [pytest.mark.browser_smoke]\n"
                           "def test_x():\n    pass\n"})
    assert dt.present_markers(r, ("browser_smoke",)) == ["browser_smoke"]


def test_marker_only_as_text_is_not_present(tmp_path, monkeypatch):
    # the engine's own fixtures embed the marker as STRINGS — must never block
    r = _repo(tmp_path, monkeypatch, {
        "tests/test_fixture_text.py": (
            '"""Docs: use @pytest.mark.browser_smoke for live tests."""\n'
            "# @pytest.mark.browser_smoke\n"
            "SRC = '@pytest.mark.browser_smoke\\ndef test_y(): pass'\n"
            "def test_x():\n    assert 'browser_smoke' in SRC\n")})
    assert dt.present_markers(r, ("browser_smoke",)) == []


def test_pytestmark_naming_a_different_marker_is_not_present(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {
        "tests/test_slow.py": "import pytest\npytestmark = pytest.mark.slow\n"
                              "def test_x():\n    pass\n"})
    assert dt.present_markers(r, ("browser_smoke",)) == []


def test_untracked_and_non_test_files_do_not_count(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"src/helpers.py": SMOKE_APPLIED})
    (r / "tests").mkdir()
    (r / "tests" / "test_untracked.py").write_text(SMOKE_APPLIED)   # never `git add`ed
    assert dt.present_markers(r, ("browser_smoke",)) == []


def test_non_git_project_is_scanned_not_silently_passed(tmp_path, monkeypatch, capsys):
    # integrator Mode B: no git at all — the working tree is the source, and it says so
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text(SMOKE_APPLIED)
    (tmp_path / ".sprint").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    _enter_integrating()
    assert dt.gate_check(tmp_path, {}, "feat", []) is False
    err = capsys.readouterr().err
    assert "scanned working tree" in err
    assert _events(tmp_path, "deferred_tier_scan")[-1]["present"] == ["browser_smoke"]


def test_git_failure_falls_to_a_visible_working_tree_scan(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    real_run = subprocess.run

    def broken_git(cmd, *a, **k):
        if cmd[:1] == ["git"] and "ls-files" in cmd:
            raise subprocess.TimeoutExpired(cmd, 60)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(dt.subprocess, "run", broken_git)
    _enter_integrating()
    assert dt.gate_check(r, {}, "feat", []) is False          # stricter, never quieter
    assert "git unavailable" in capsys.readouterr().err


def test_unparseable_test_file_is_judged_conservatively(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {
        "tests/test_new_syntax.py": "import pytest\n@pytest.mark.browser_smoke\n"
                                    "def test_x(:\n    pass\n"})       # unparseable
    _enter_integrating()
    assert dt.scan(r, ("browser_smoke",))["unparsed"] == ["tests/test_new_syntax.py"]
    assert dt.gate_check(r, {}, "feat", []) is False
    assert "unparseable" in capsys.readouterr().err


def test_markerless_non_git_project_stays_quiet(tmp_path, monkeypatch, capsys):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unit.py").write_text("def test_x():\n    pass\n")
    (tmp_path / ".sprint").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert dt.gate_check(tmp_path, {}, "feat", []) is True
    assert capsys.readouterr().err == ""
    assert not _events(tmp_path, "deferred_tier_scan")


def test_unreadable_test_file_is_reported_not_silently_skipped(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {"tests/test_unit.py": "def test_x():\n    pass\n"})
    (r / "tests" / "test_gone.py").symlink_to(r / "tests" / "missing_target.py")
    _git(r, "add", "tests/test_gone.py")
    assert dt.scan(r, ("browser_smoke",))["unreadable"] == ["tests/test_gone.py"]
    assert dt.gate_check(r, {}, "feat", []) is True          # holds no runnable marker
    assert "unreadable" in capsys.readouterr().err


def test_non_mapping_config_is_loud_and_stays_strict(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _enter_integrating()
    assert dt.gate_check(r, {"deferred_tier": True}, "feat", []) is False   # no crash
    assert "must be a mapping" in capsys.readouterr().err


def test_engine_repo_itself_applies_no_deferred_marker():
    root = Path(__file__).resolve().parent.parent
    assert dt.present_markers(root, schema.DEFAULT_DEFER_MARKERS) == []


# ---- the ONE -m evaluator: selects / requires / excludes ------------------------------

@pytest.mark.parametrize("cmd,expected", [
    ("pytest -m browser_smoke", True),
    ("pytest -m 'browser_smoke and not slow'", True),
    ("pytest -m 'not slow or browser_smoke'", True),
    ("python -m pytest tests/smoke -m browser_smoke", True),
    ("bash -c \"cd app && pytest tests -m 'browser_smoke'\"", True),
    ("pytest -m 'not browser_smoke'", False),
    ("pytest -m 'not (browser_smoke or live_server)'", False),
    ("python -m pytest tests", False),                         # module flag ≠ marker flag
    ("pytest -m 'slow and browser_smoke'", False),             # only-marker item: slow absent
    ("pytest -m '((browser_smoke'", False),                    # unparseable → never credited
])
def test_selects(cmd, expected):
    assert schema.command_selects_marker(cmd, "browser_smoke") is expected


def test_targets_distinguishes_aiming_from_incidental_selection():
    assert schema.command_targets_marker("pytest tests/e2e -m browser_smoke", "browser_smoke")
    # a conjunction still aims at the tier (regression-sentinel advisory g)
    assert schema.command_targets_marker("pytest -m 'browser_smoke and e2e'", "browser_smoke")
    # selects e2e items only incidentally — does NOT target the tier
    assert not schema.command_targets_marker("pytest tests/e2e -m 'not browser_smoke'", "e2e")
    assert not schema.command_targets_marker("pytest -m 'not slow or browser_smoke'",
                                             "browser_smoke")


def test_only_flags_after_pytest_are_marker_flags():
    # a path whose basename is `python` is not the interpreter (advisory h)
    assert schema.command_excludes_markers("pytest tests/e2e/python -m 'not browser_smoke'",
                                           ("browser_smoke",))
    assert schema.command_selects_marker("coverage run -m pytest tests -m browser_smoke",
                                         "browser_smoke")
    # no pytest token → wrapper args are read (HEAD semantics), but never the
    # interpreter's own module flag
    assert schema.command_selects_marker("make smoke ARGS='-m browser_smoke'", "browser_smoke")
    assert not schema.command_selects_marker("python3 -m browser_smoke", "browser_smoke")


def test_excludes_is_conservative_over_other_markers():
    assert schema.command_excludes_markers("pytest -m 'not browser_smoke'", ("browser_smoke",))
    # `-m slow` still runs a slow+browser_smoke test → NOT an exclusion
    assert not schema.command_excludes_markers("pytest tests/e2e -m slow", ("browser_smoke",))
    assert not schema.command_excludes_markers("pytest -m 'not slow or browser_smoke'",
                                               ("browser_smoke",))
    assert schema.command_excludes_markers("pytest -m 'not (browser_smoke or live_server)'",
                                           ("browser_smoke", "live_server"))


def test_brief_lint_and_schema_agree_on_selection():
    markers = ("browser_smoke",)
    for cmd in ("pytest -m 'browser_smoke and not slow'", "pytest -m browser_smoke",
                "pytest -m 'browser_smoke and e2e'"):
        assert brief_lint._is_browser_smoke_command(cmd, markers)
    assert not brief_lint._is_browser_smoke_command("pytest -m 'not browser_smoke'", markers)


def test_behaviour_dir_that_requires_the_marker_now_lints_clean(tmp_path):
    cp = tmp_path / "f.criteria.yaml"
    cp.write_text('schema_version: "1.0"\ncriteria:\n  - id: s\n    description: smoke\n'
                  '    verify_command: "uv run pytest tests/behavior -m browser_smoke"\n')
    ok, errs = schema.validate_criteria_file(cp, tmp_path)
    assert ok, errs


def test_defer_markers_single_source():
    assert schema.defer_markers({}) == schema.DEFAULT_DEFER_MARKERS
    assert schema.defer_markers({"reviewing_defer_markers": ["e2e", " "]}) == ("e2e",)


# ---- the gate -------------------------------------------------------------------------

def test_unproven_tier_blocks_with_event_and_remedy(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _enter_integrating()
    assert dt.gate_check(r, {}, "feat", []) is False
    err = capsys.readouterr().err
    assert "browser_smoke" in err and "prusik prove" in err and "BLOCKED" in err
    ev = _events(r, "deferred_tier_unproven")
    assert ev and ev[-1]["markers"] == ["browser_smoke"] and ev[-1]["require"] is True
    blk = _events(r, "advance_blocked")
    assert blk and blk[-1]["gate_class"] == gate_class.DEFERRED_TIER
    assert blk[-1]["to_phase"] == "sprint_complete"


def test_no_applied_marker_is_unaffected(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/test_unit.py": "def test_x():\n    pass\n"})
    assert dt.gate_check(r, {}, "feat", []) is True
    assert not _events(r, "deferred_tier_unproven")


def test_proven_prove_run_after_integrating_passes(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _enter_integrating()
    _prove("pytest tests/smoke -m 'browser_smoke'")
    assert dt.gate_check(r, {}, "feat", []) is True
    assert _events(r, "deferred_tier_proven")


def test_all_skip_run_is_not_proof(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _enter_integrating()
    _prove("pytest -m browser_smoke", executed=0, proven=False)    # server down: all skipped
    assert dt.gate_check(r, {}, "feat", []) is False


def test_marker_excluding_run_is_not_proof(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _enter_integrating()
    _prove("pytest -m 'not browser_smoke'", executed=2716)
    assert dt.gate_check(r, {}, "feat", []) is False


def test_prove_run_before_integrating_or_other_feature_is_not_proof(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _prove("pytest -m browser_smoke")                       # before the window
    _enter_integrating()
    _prove("pytest -m browser_smoke", feature="other")      # another feature's run
    ledger.append("prove_run", kind="tests", exit_code=0, executed=3, proven=True,
                  command="pytest -m browser_smoke")        # historical: no feature
    assert dt.gate_check(r, {}, "feat", []) is False


def test_no_integrating_window_only_criteria_can_prove(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _prove("pytest -m browser_smoke")                       # no window → never counts
    assert dt.gate_check(r, {}, "feat", []) is False
    crit = [{"id": "smoke", "passed": True, "kind": "tests", "executed": 4,
             "verify_command": "uv run pytest tests/behavior -m browser_smoke"}]
    assert dt.gate_check(r, {}, "feat", crit) is True


def test_failed_or_empty_criterion_is_not_proof(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    bad = [{"id": "a", "passed": False, "kind": "tests", "executed": 4,
            "verify_command": "pytest -m browser_smoke"},
           {"id": "b", "passed": True, "kind": "tests", "executed": 0,
            "verify_command": "pytest -m browser_smoke"}]
    assert dt.gate_check(r, {}, "feat", bad) is False


def test_each_marker_needs_its_own_proof(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {
        "tests/test_s.py": SMOKE_APPLIED,
        "tests/test_l.py": "import pytest\n@pytest.mark.live_server\ndef test_y():\n    pass\n"})
    cfg = {"reviewing_defer_markers": ["browser_smoke", "live_server"]}
    _enter_integrating()
    _prove("pytest -m browser_smoke")
    assert dt.gate_check(r, cfg, "feat", []) is False
    assert _events(r, "deferred_tier_unproven")[-1]["markers"] == ["live_server"]
    _prove("pytest -m 'live_server or browser_smoke'")
    assert dt.gate_check(r, cfg, "feat", []) is True


def test_declared_command_is_matched_normalized(tmp_path, monkeypatch):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    cfg = {"deferred_tier": {"command": "make  smoke  ARGS='-q'"}}
    _enter_integrating()
    _prove("make smoke 'ARGS=-q'")             # shlex.join form of the same argv
    assert dt.gate_check(r, cfg, "feat", []) is True


def test_require_false_is_a_loud_opt_out(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path, monkeypatch, {"tests/smoke/test_capture.py": SMOKE_APPLIED})
    _enter_integrating()
    assert dt.gate_check(r, {"deferred_tier": {"require": False}}, "feat", []) is True
    assert "ADVISORY" in capsys.readouterr().err
    ev = _events(r, "deferred_tier_unproven")
    assert ev and ev[-1]["require"] is False
    assert not _events(r, "advance_blocked")     # opt-out is not a block


def test_gate_class_registered():
    assert gate_class.DEFERRED_TIER in gate_class.GATE_CLASSES
    assert gate_class.classify({"event": "advance_blocked",
                                "reason": "deferred tier unproven: browser_smoke"}
                               ) == gate_class.DEFERRED_TIER


# ---- prove records what it ran --------------------------------------------------------

def test_prove_run_records_command_and_sprint(tmp_path, monkeypatch):
    from prusik import prove
    r = _repo(tmp_path, monkeypatch, {"README.md": "x\n"})
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    rc = prove.run(["--", "echo", "1 passed in 0.01s"], kind="tests")
    assert rc == 0
    ev = _events(r, "prove_run")[-1]
    assert ev["command"] == "echo '1 passed in 0.01s'"
    assert ev["feature"] == "feat" and ev["phase"] == "integrating"


def test_prove_run_still_recorded_when_sprint_state_unreadable(tmp_path, monkeypatch):
    from prusik import prove

    def corrupt(*a, **k):
        raise ValueError("corrupt state.json")

    r = _repo(tmp_path, monkeypatch, {"README.md": "x\n"})
    monkeypatch.setattr(phases, "current_sprint_state", corrupt)
    assert prove.run(["--", "echo", "1 passed in 0.01s"], kind="tests") == 0
    ev = _events(r, "prove_run")[-1]
    assert ev["proven"] is True and ev["feature"] is None


# ---- field replay: the escape, end to end through sprint_complete -------------------

def _field_shaped(tmp_path, monkeypatch) -> Path:
    r = _repo(tmp_path, monkeypatch, {
        "tests/smoke/test_capture_smoke.py": SMOKE_APPLIED,
        "tests/integration/test_reads.py": "def test_reads():\n    assert True\n",
        "briefs/feat.criteria.yaml": (
            'schema_version: "1.0"\ncriteria:\n'
            '  - id: a1\n    description: scoped reads\n'
            '    verify_command: "true"\n    expected_exit: 0\n'),
    })
    (r / "decisions").mkdir()
    (r / "decisions" / "feat.json").write_text(json.dumps({
        "feature": "feat", "mode": "solo", "reason": "t",
        "scope_summary": {"size": "M", "domains": ["backend"]},
        "brief_meta": {"type": "bug_fix", "priority": "P0"}}))
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    ledger.append("sprint_started", feature="feat")
    _enter_integrating()
    return r


def _complete():
    return gate.sprint_complete(argparse.Namespace(
        feature="feat", duration_min=5, tokens=1, escalated=False))


def test_field_replay_integration_only_criteria_block(tmp_path, monkeypatch):
    r = _field_shaped(tmp_path, monkeypatch)
    # reviewing ran the suite excluding the marker — exactly as designed
    _prove("pytest tests/integration -m 'not browser_smoke'", executed=1240)
    assert _complete() == 2
    assert not _events(r, "sprint_complete")


def test_field_replay_completes_once_the_tier_is_proven(tmp_path, monkeypatch):
    r = _field_shaped(tmp_path, monkeypatch)
    _prove("pytest tests/integration -m 'not browser_smoke'", executed=1240)
    _prove("pytest tests/smoke -m browser_smoke", executed=12)
    assert _complete() == 0
    assert _events(r, "sprint_complete")
