"""The Stop hook reminds once per state of the phase's exit artifacts.

moat-finding: fb-6e133097e7b1
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stdout

import pytest

from tests._common import _copy_sprint_config, _mktmp_project, gate, phases


@pytest.fixture
def project():
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
    yield tmp
    shutil.rmtree(tmp)


def _stop() -> int:
    saved = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"stop_hook_active": False}))
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            rc = gate.stop()
    finally:
        sys.stdin = saved
    if rc == 2:
        assert json.loads(out.getvalue())["decision"] == "block"
    else:
        assert '"block"' not in out.getvalue()
    return rc


def _scope(tmp, text: str) -> None:
    path = tmp / "design" / "feat" / "scope.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_the_same_state_is_reported_once(project):
    assert _stop() == 2
    assert _stop() == 0
    assert _stop() == 0


def test_a_rewritten_artifact_reminds_again(project):
    _scope(project, "draft one\n")
    assert _stop() == 2
    assert _stop() == 0
    _scope(project, "draft two\n")
    assert _stop() == 2


def test_identical_bytes_do_not_remind_again(project):
    _scope(project, "draft\n")
    assert _stop() == 2
    _scope(project, "draft\n")
    assert _stop() == 0


def test_a_removed_artifact_reminds_again(project):
    _scope(project, "draft\n")
    assert _stop() == 2
    (project / "design" / "feat" / "scope.md").unlink()
    assert _stop() == 2


@pytest.mark.parametrize("state", [{"phase": "scoping", "feature": "other"},
                                   {"phase": "planning", "feature": "feat"}])
def test_a_new_feature_or_phase_reminds_again(project, state):
    assert _stop() == 2
    phases.set_sprint_state(state)
    assert _stop() == 2


def test_a_satisfied_phase_passes_and_clears_the_record(project, monkeypatch):
    assert _stop() == 2
    record = project / ".sprint" / "stop-reported.json"
    assert record.exists()
    monkeypatch.setattr(gate, "_unsatisfied_exit_artifacts", lambda spec, feature: [])
    assert _stop() == 0
    assert not record.exists()
    monkeypatch.undo()
    assert _stop() == 2  # the same unsatisfied state is news again after it was satisfied


@pytest.mark.parametrize("damage", ["not json", "[]", '{"key": 7}'])
def test_an_unreadable_record_reminds(project, damage):
    assert _stop() == 2
    (project / ".sprint" / "stop-reported.json").write_text(damage)
    assert _stop() == 2
    assert _stop() == 0
