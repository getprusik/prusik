"""A sprint closes only from its last phase, with that phase's exit artifacts.

moat-finding: fb-a959d1f48f12
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest
import yaml

from prusik import catch_quality as cq
from prusik import fix_round, gate, gate_class as gc, ledger, main_writer, pause, phases

FEATURE = "feat"
RETRO = "design/feat/retro.md"
FULL_RETRO = "## What happened\nx\n## What surprised\ny\n## Updates to CLAUDE.md\nnone\n"
TEMPLATE = Path(gate.__file__).parent / "templates" / ".claude" / "sprint-config.yaml"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".sprint").mkdir()
    shutil.copy(TEMPLATE, tmp_path / ".claude" / "sprint-config.yaml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRUSIK_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def criteria_runs(monkeypatch):
    calls: list[str] = []

    def run(feature, root):
        calls.append(feature)
        return True, []

    monkeypatch.setattr(gate, "_run_success_criteria", run)
    return calls


def _at(phase, **extra):
    phases.set_sprint_state({"phase": phase, "feature": FEATURE, **extra})
    ledger.append("sprint_started", feature=FEATURE)


def _close():
    return gate.sprint_complete(argparse.Namespace(
        feature=FEATURE, duration_min=1, tokens=None, escalated=False))


def _retro(proj, text=FULL_RETRO):
    path = proj / RETRO
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _events(name):
    return [r for r in ledger.read_all() if r["event"] == name]


def _assert_still_open(phase):
    state = phases.current_sprint_state()
    assert state and state["feature"] == FEATURE and state["phase"] == phase
    assert not _events("sprint_complete")


def test_missing_retro_refuses_and_keeps_the_sprint_open(proj, criteria_runs, capsys):
    _at("integrating")
    before = (proj / ".sprint" / "state.json").read_bytes()

    assert _close() == 2

    assert RETRO in capsys.readouterr().err
    _assert_still_open("integrating")
    assert (proj / ".sprint" / "state.json").read_bytes() == before


def test_refusal_keeps_the_open_fix_round_and_the_writer_lease(proj, criteria_runs, monkeypatch):
    _at("integrating")
    assert fix_round.start(FEATURE, root=proj) == 0
    released: list[Path] = []
    monkeypatch.setattr(main_writer, "release", lambda root: released.append(root) or True)

    assert _close() == 2

    assert fix_round.is_active(proj)
    assert not _events("fix_round_reaped")
    assert released == []
    assert not _events("writer_lease_released")


@pytest.mark.parametrize("section", ["## What happened", "## What surprised",
                                     "## Updates to CLAUDE.md"])
def test_retro_missing_a_section_is_refused_and_names_it(proj, criteria_runs, capsys, section):
    _at("integrating")
    _retro(proj, FULL_RETRO.replace(section, "## Other"))

    assert _close() == 2

    assert section in capsys.readouterr().err
    _assert_still_open("integrating")


def test_the_artifact_rule_is_the_one_advance_uses(proj, criteria_runs, monkeypatch):
    _at("integrating")
    _retro(proj)
    seen: list[dict] = []

    def judged(spec, feature):
        seen.append(spec)
        return ["from the shared rule"]

    monkeypatch.setattr(gate, "_unsatisfied_exit_artifacts", judged)

    assert _close() == 2
    assert [s["name"] for s in seen] == ["integrating"]


def test_trivial_lane_artifacts_on_the_last_phase_apply(proj, criteria_runs, capsys):
    path = proj / ".claude" / "sprint-config.yaml"
    config = yaml.safe_load(path.read_text())
    config["phases"][-1]["trivial_exit_artifacts"] = [{"path": "design/{feature}/lite.md"}]
    path.write_text(yaml.safe_dump(config))
    _at("integrating", lane="trivial")
    _retro(proj)   # the full lane's artifact does not satisfy the trivial lane's

    assert _close() == 2
    assert "design/feat/lite.md" in capsys.readouterr().err

    (proj / "design" / "feat" / "lite.md").write_text("done\n")
    assert _close() == 0


@pytest.mark.parametrize("phase", ["scoping", "triage", "planning", "solo_execute",
                                   "building", "reviewing"])
def test_a_close_from_an_earlier_phase_is_refused(proj, criteria_runs, capsys, phase):
    _at(phase)
    _retro(proj)     # even with the retro on disk, the last phase was never reached

    assert _close() == 2

    err = capsys.readouterr().err
    assert f"'{phase}'" in err and "'integrating'" in err
    _assert_still_open(phase)
    assert criteria_runs == []


def test_reviewing_with_every_artifact_satisfied_still_cannot_close(proj, criteria_runs,
                                                                    monkeypatch):
    # adversarial: judging the CURRENT phase's artifacts would pass here and lose the retro
    _at("reviewing")
    monkeypatch.setattr(gate, "_unsatisfied_exit_artifacts", lambda spec, feature: [])

    assert _close() == 2
    _assert_still_open("reviewing")


def test_the_last_phase_is_read_from_the_config(proj, criteria_runs):
    path = proj / ".claude" / "sprint-config.yaml"
    config = yaml.safe_load(path.read_text())
    config["phases"].append({"name": "shipping", "writable": ["**"]})
    path.write_text(yaml.safe_dump(config))

    _at("integrating")
    _retro(proj)
    assert _close() == 2
    _assert_still_open("integrating")

    phases.set_sprint_state({"phase": "shipping", "feature": FEATURE})
    assert _close() == 0


@pytest.mark.parametrize("body", [None, "phases: []\n"])
def test_no_phases_in_config_refuses_the_close(proj, criteria_runs, capsys, body):
    _at("integrating")
    path = proj / ".claude" / "sprint-config.yaml"
    if body is None:
        path.unlink()
    else:
        path.write_text(body)

    assert _close() == 1

    assert "prusik init" in capsys.readouterr().err
    _assert_still_open("integrating")


def test_a_refused_close_runs_no_success_criteria(proj, criteria_runs):
    _at("integrating")
    assert _close() == 2
    assert criteria_runs == []


def test_a_refusal_for_another_feature_is_not_this_gate(proj, criteria_runs):
    _at("integrating")
    assert gate.sprint_complete(argparse.Namespace(
        feature="other", duration_min=1, tokens=None, escalated=False)) == 1
    assert not _events("advance_blocked")


def test_missing_artifact_refusal_is_recorded_and_classified(proj, criteria_runs):
    _at("integrating")
    assert _close() == 2

    block = _events("advance_blocked")[-1]
    assert block["feature"] == FEATURE
    assert block["from_phase"] == "integrating"
    assert block["to_phase"] == "sprint_complete"
    assert gc.classify(block) == gc.UNMET_EXIT_ARTIFACTS
    assert RETRO in block["missing"][0]


def test_early_close_refusal_is_recorded_and_classified(proj, criteria_runs):
    _at("reviewing")
    assert _close() == 2

    block = _events("advance_blocked")[-1]
    assert block["to_phase"] == "sprint_complete"
    assert gc.classify(block) == gc.CLOSE_BEFORE_LAST_PHASE
    derived = {k: v for k, v in block.items() if k != "gate_class"}
    assert gc.classify(derived) == gc.CLOSE_BEFORE_LAST_PHASE


def test_a_later_close_enforces_the_block(proj, criteria_runs):
    _at("integrating")
    assert _close() == 2
    _retro(proj)
    assert _close() == 0

    records = ledger.read_all()
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    closes = [c for c in catches if c["gate"] == "phase_gate"]
    assert closes and closes[0]["verdict"] == cq.TRUE_CATCH


def test_a_block_with_no_later_close_stays_unresolved(proj, criteria_runs):
    _at("integrating")
    assert _close() == 2

    records = ledger.read_all()
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    closes = [c for c in catches if c["gate"] == "phase_gate"]
    assert closes and closes[0]["verdict"] == cq.UNRESOLVED


def test_another_features_close_does_not_enforce_the_block():
    records = [
        {"ts": "2026-06-01T00:00:01+00:00", "event": "advance_blocked", "feature": "a",
         "to_phase": "sprint_complete", "missing": ["x"]},
        {"ts": "2026-06-01T00:01:00+00:00", "event": "sprint_complete", "feature": "b"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    assert [c["verdict"] for c in catches] == [cq.UNRESOLVED]


def test_a_later_close_enforces_a_deferred_tier_block():
    records = [
        {"ts": "2026-06-01T00:00:01+00:00", "event": "advance_blocked", "feature": "a",
         "to_phase": "sprint_complete", "gate_class": gc.DEFERRED_TIER,
         "reason": "deferred tier unproven: browser_smoke"},
        {"ts": "2026-06-01T00:01:00+00:00", "event": "sprint_complete", "feature": "a"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    assert [c["verdict"] for c in catches] == [cq.TRUE_CATCH]


def test_a_satisfied_close_behaves_as_before(proj, criteria_runs):
    _at("integrating")
    _retro(proj)

    assert _close() == 0

    assert phases.current_sprint_state() is None
    assert len(_events("sprint_complete")) == 1
    assert criteria_runs == [FEATURE]
    assert not _events("advance_blocked")


def test_a_paused_sprint_is_checked_the_same(proj, criteria_runs):
    _at("integrating")
    pause.pause(reason="checkpoint")
    assert pause.is_paused()

    assert _close() == 2
    _assert_still_open("integrating")

    _retro(proj)
    assert _close() == 0
    assert phases.current_sprint_state() is None
