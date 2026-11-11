"""A capture that counted nothing is refused, not recorded.

An exit-0 run with a count of 0 used to be appended to the phase's evidence file, where the
advance gate rejected the whole file for it. A re-capture only appends, so the one way out was
`--reset` and re-running every suite. It is refused at capture and the file is left as it was.
Each case runs the real `gate.capture` on a real command in a temp project.

moat-finding: fb-5d7b7cae0f30
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from prusik import capture_diagnose as cd
from prusik import gate, schema

FEATURE = "feat"
REL = f"reports/{FEATURE}/regression.evidence.json"


def _capture(command, kind="tests", reset=False):
    return gate.capture(SimpleNamespace(
        command=[command], reset=reset, feature=FEATURE, phase="regression", kind=kind))


def _ev(tmp):
    return schema.evidence_path_for(tmp / "reports" / FEATURE, "regression")


def _non_evidence_modes(tmp):
    led = tmp / ".sprint" / "ledger.jsonl"
    if not led.exists():
        return []
    rows = [json.loads(line) for line in led.read_text().splitlines() if line.strip()]
    return [r.get("mode") for r in rows if r.get("event") == "capture_non_evidence"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("kind,command", [
    ("tests", "echo 'policy ok'"),               # a policy script under --kind tests
    ("tests", "echo 'no tests ran in 0.01s'"),   # a wrong path
    ("lint", "echo 'audit: 0 vulnerabilities'"),  # an audit under --kind lint
    ("lint", "true"),                            # silent and green
    ("types", "true"),                           # silent tsc
])
def test_zero_count_green_run_is_refused_and_writes_nothing(project, capsys, kind, command):
    rc = _capture(command, kind=kind)
    err = capsys.readouterr().err
    assert rc != 0
    assert not _ev(project).exists()
    assert _non_evidence_modes(project) == ["nothing_counted"]
    assert cd.zero_count_hint(kind) in err       # the same remedy text the advance gate uses
    assert "outside capture" in err


def test_evidence_already_in_the_file_survives_and_still_advances(project):
    assert _capture("echo '3 passed'") == 0
    before = _ev(project).read_bytes()
    assert gate._evidence_unsatisfied(REL, FEATURE, project) is None

    assert _capture("echo 'policy ok'") != 0     # the capture that used to poison the file

    assert _ev(project).read_bytes() == before   # byte-identical
    assert gate._evidence_unsatisfied(REL, FEATURE, project) is None
    entries = schema.load_evidence(_ev(project))
    assert [e["nonempty_primitive"]["value"] for e in entries] == [3]


def test_reset_with_a_zero_count_command_does_not_discard_evidence(project):
    assert _capture("echo '3 passed'") == 0
    before = _ev(project).read_bytes()
    assert _capture("echo 'policy ok'", reset=True) != 0
    assert _ev(project).read_bytes() == before


def test_nothing_is_loosened_when_no_valid_entry_exists(project):
    assert _capture("echo 'policy ok'") != 0
    reason = gate._evidence_unsatisfied(REL, FEATURE, project)
    assert reason and "no execution-evidence" in reason


def test_an_older_file_holding_a_zero_count_entry_is_still_refused_at_advance(project):
    role = gate._PHASE_ROLE.get("regression", "")
    wt = gate._worktree_substantive_hash(project, gate._REVIEWER_INPUTS.get(role, ()))
    ev = _ev(project)
    ev.parent.mkdir(parents=True)
    ev.write_text(json.dumps({"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": [
        {"phase": "regression", "command": "pytest -q", "exit_code": 0,
         "nonempty_primitive": {"kind": "tests", "value": 0},
         "output_sha": "abc", "worktree_hash": wt,
         "captured_by": schema.EVIDENCE_CAPTURED_BY}]}))
    reason = gate._evidence_unsatisfied(REL, FEATURE, project)
    assert reason and "false-clean" in reason
    assert cd.zero_count_hint("tests") in reason


def test_a_real_failure_with_a_zero_count_is_still_recorded(project):
    rc = _capture("echo 'collection error'; exit 2")
    assert rc == 2
    entries = schema.load_evidence(_ev(project))
    assert len(entries) == 1 and entries[0]["exit_code"] == 2
    assert _non_evidence_modes(project) == []


def test_more_specific_modes_still_win(project, capsys):
    assert _capture("echo '>>> FULL TURBO'") == 1
    assert _non_evidence_modes(project) == ["cache_replay"]
    assert cd._DETECTORS[-1] is cd._nothing_counted
    assert cd.KNOWN_MODES[-1] == "nothing_counted"


def test_a_counted_green_run_is_still_recorded(project):
    assert _capture("echo '5 passed'") == 0
    assert schema.load_evidence(_ev(project))[0]["nonempty_primitive"]["value"] == 5
    assert _non_evidence_modes(project) == []


# ---- the project's policy command has its own kind -------------------------------------

def _declare_policy(tmp, command="echo policy-clean", enabled=True):
    (tmp / ".claude").mkdir(exist_ok=True)
    (tmp / ".claude" / "sprint-config.yaml").write_text(
        "project_policy:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        f"  command: \"{command}\"\n")


def _policy(command=None, reset=False):
    return gate.capture(SimpleNamespace(
        command=[] if command is None else ["--", command], reset=reset,
        feature=FEATURE, phase="regression", kind="policy"))


def test_policy_kind_runs_the_declared_command_and_records_it(project, capfd):
    _declare_policy(project, "echo policy-clean")
    assert _policy() == 0
    assert "policy-clean" in capfd.readouterr().out          # the declared command really ran
    entry = schema.load_evidence(_ev(project))[0]
    assert entry["command"] == "echo policy-clean"
    assert entry["exit_code"] == 0
    assert entry["nonempty_primitive"] == {"kind": "policy", "value": 1}
    assert gate._evidence_unsatisfied(REL, FEATURE, project) is None


def test_policy_entry_sits_beside_a_tests_entry(project):
    _declare_policy(project)
    assert _capture("echo '3 passed'") == 0
    assert _policy() == 0
    kinds = [e["nonempty_primitive"]["kind"] for e in schema.load_evidence(_ev(project))]
    assert kinds == ["tests", "policy"]
    assert gate._evidence_unsatisfied(REL, FEATURE, project) is None


def test_policy_kind_accepts_the_declared_command_spelled_out(project):
    _declare_policy(project, "echo policy-clean")
    assert _policy("echo policy-clean") == 0
    assert len(schema.load_evidence(_ev(project))) == 1


@pytest.mark.parametrize("given", ["true", "echo policy-clean && true", "echo other"])
def test_policy_kind_refuses_any_other_command(project, capsys, tmp_path, given):
    """The adversarial case: `--kind policy -- true` must not record a pass."""
    marker = tmp_path / "ran"
    _declare_policy(project, f"touch {marker}")
    assert _policy(given) == 2
    assert not marker.exists()                               # nothing was run
    assert not _ev(project).exists()                         # nothing was recorded
    assert "project_policy.command" in capsys.readouterr().err


@pytest.mark.parametrize("setup", ["absent", "disabled", "no_command"])
def test_policy_kind_refuses_when_nothing_is_declared(project, capsys, setup):
    if setup == "disabled":
        _declare_policy(project, enabled=False)
    elif setup == "no_command":
        (project / ".claude").mkdir()
        (project / ".claude" / "sprint-config.yaml").write_text(
            "project_policy:\n  enabled: true\n")
    assert _policy() == 2
    assert _policy("true") == 2
    assert not _ev(project).exists()
    assert "project_policy" in capsys.readouterr().err


def test_failing_policy_run_is_recorded_and_refused_at_advance(project):
    _declare_policy(project, "echo 'secret found'; exit 3")
    assert _policy() == 3
    entry = schema.load_evidence(_ev(project))[0]
    assert entry["exit_code"] == 3
    reason = gate._evidence_unsatisfied(REL, FEATURE, project)
    assert reason and "exit_code=3" in reason


def test_bare_reset_under_policy_kind_still_only_clears(project, tmp_path):
    marker = tmp_path / "ran"
    _declare_policy(project, f"touch {marker}")
    assert _capture("echo '3 passed'") == 0
    assert _policy(reset=True) == 0
    assert not marker.exists()                               # cleared, ran nothing
    assert schema.load_evidence(_ev(project)) == []


def test_the_role_file_sends_policy_through_its_own_kind():
    from pathlib import Path
    import prusik
    role = (Path(prusik.__file__).parent / "templates" / ".claude" / "agents"
            / "regression-sentinel.md").read_text()
    assert "--phase regression --kind policy" in role
    assert "step 6 project-policy) so prusik captures" not in role
