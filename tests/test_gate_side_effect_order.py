"""A refused sprint-start changes nothing, pre-sprint artifacts stay writable under another
sprint, and markdown can't change a size or type verdict.

moat-finding: fb-3adb150c6991
moat-finding: fb-5bf7db8c67a1
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest

from prusik import consistency, ground_truth, triage
from tests._common import (
    _copy_sprint_config,
    _mktmp_project,
    _wt_file,
    discovery,
    gate,
    kit_fix_round,
    kit_pause,
    phases,
    schema,
)


@pytest.fixture
def project():
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    yield tmp
    shutil.rmtree(tmp)


def _ready_brief(tmp: Path, feature: str, btype: str = "bug_fix") -> Path:
    (tmp / "briefs").mkdir(exist_ok=True)
    brief = tmp / "briefs" / f"{feature}.md"
    brief.write_text(f"## Goal\nFix the typo in the retry log message.\n\n"
                     f"## Success criteria\nLog reads correctly within 1 line changed.\n\n"
                     f"## Type\n{btype}\n")
    report = tmp / "reports" / feature / "brief-critique.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("PASS\n")
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
        {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}))
    (tmp / "design").mkdir(exist_ok=True)
    (tmp / "design" / "map.md").write_text("map placeholder")
    discovery.fingerprint_map()
    return brief


def _paused_sprint_with_open_round(tmp: Path) -> None:
    _wt_file(tmp, "solo", "svc.py", "print('A work')")
    phases.set_sprint_state({"feature": "sprint-a", "phase": "reviewing"}, root=tmp)
    assert kit_fix_round.start("sprint-a", tmp) == 0
    assert kit_pause.pause() == 0


def _snapshot(tmp: Path) -> dict[str, bytes]:
    """Every byte a refusal must leave alone. The ledger may grow: recording a refusal is
    the point of it, and it changes no sprint's state."""
    files = [*(tmp / ".sprint").rglob("*"), *(tmp / "worktrees").rglob("*")]
    return {str(f.relative_to(tmp)): f.read_bytes() for f in files
            if f.is_file() and f.name != "ledger.jsonl"}


def _no_brief(tmp, monkeypatch):
    (tmp / "briefs" / "sprint-b.md").unlink()


def _invalid_brief(tmp, monkeypatch):
    (tmp / "briefs" / "sprint-b.md").write_text("## Goal\nx\n")


def _unmet_pre_sprint_gate(tmp, monkeypatch):
    (tmp / "reports" / "sprint-b" / "brief-critique.txt").unlink()


def _ground_truth_drift(tmp, monkeypatch):
    monkeypatch.setattr(ground_truth, "sprint_start_check", lambda feature, root: False)


def _trivial_ineligible_type(tmp, monkeypatch):
    _ready_brief(tmp, "sprint-b", "new_feature")


@pytest.mark.parametrize("break_start", [
    _no_brief, _invalid_brief, _unmet_pre_sprint_gate, _ground_truth_drift,
    _trivial_ineligible_type])
def test_a_refused_force_clean_changes_nothing(project, monkeypatch, capsys, break_start):
    _ready_brief(project, "sprint-b")
    _paused_sprint_with_open_round(project)
    break_start(project, monkeypatch)
    before = _snapshot(project)
    capsys.readouterr()

    rc = gate.sprint_start(argparse.Namespace(feature="sprint-b", trivial=True, force_clean=True))

    assert rc == 2
    assert _snapshot(project) == before
    assert kit_pause.is_paused(project)
    assert "discarding" not in capsys.readouterr().out


def test_a_rejected_trivial_keeps_worktrees_and_the_fix_round(project):
    _ready_brief(project, "sprint-b", "new_feature")
    _wt_file(project, "solo", "svc.py", "stale but not ours to delete on a refusal")
    assert kit_fix_round.start("sprint-a", project) == 0
    before = _snapshot(project)

    assert gate.sprint_start(argparse.Namespace(feature="sprint-b", trivial=True)) == 2

    assert _snapshot(project) == before
    assert kit_fix_round.is_active(project)


def test_a_met_force_clean_still_discards(project, capsys):
    _ready_brief(project, "sprint-b")
    _paused_sprint_with_open_round(project)
    capsys.readouterr()

    rc = gate.sprint_start(argparse.Namespace(feature="sprint-b", trivial=True, force_clean=True))

    assert rc == 0
    assert "discarding sprint 'sprint-a' and its 1 worktree(s)" in capsys.readouterr().out
    assert not (project / "worktrees" / "solo").exists()
    assert not kit_pause.is_paused(project)
    assert not kit_fix_round.is_active(project)
    assert phases.current_sprint_state()["feature"] == "sprint-b"


def test_force_clean_is_still_required_to_discard(project):
    _ready_brief(project, "sprint-b")
    _paused_sprint_with_open_round(project)
    before = _snapshot(project)
    assert gate.sprint_start(argparse.Namespace(feature="sprint-b", trivial=True)) == 2
    assert _snapshot(project) == before


@pytest.mark.parametrize("phase", [
    "scoping", "triage", "planning", "building", "solo_execute", "reviewing", "integrating"])
def test_a_second_feature_can_prepare_under_any_active_phase(project, phase):
    config = phases.load_sprint_config(project)
    for target in ("design/sprint-b/product-fit.md", "briefs/sprint-b.md",
                   "reports/sprint-b/brief-critique.txt",
                   "reports/sprint-b/product-fit-critique.txt"):
        assert phases.is_path_writable(target, config, phase, "sprint-a", project)[0], target


@pytest.mark.parametrize("phase", ["building", "reviewing"])
def test_only_pre_sprint_artifacts_open_up(project, phase):
    # The adversarial twin: the carve-out must not reach another feature's scope or the code.
    config = phases.load_sprint_config(project)
    for target in ("design/sprint-b/scope.md", "design/sprint-b/plan.md", "prusik/gate.py"):
        assert not phases.is_path_writable(target, config, phase, "sprint-a", project)[0], target


@pytest.mark.parametrize("written", ["L", "**L**", "**L.**", "`L`", "L,", "*L*", "_L_", "**L**,",
                                     "L — every page"])
def test_every_markdown_form_reads_the_same(project, written):
    assert schema.extract_enum_value(written) == "L"
    _ready_brief(project, "f")
    scope = project / "design" / "f" / "scope.md"
    scope.parent.mkdir(parents=True)
    scope.write_text(f"## Size\n{written}\n")
    assert triage.parse_scope(scope)["size"] == "L"
    assert "'L'" in consistency.brief_type_matches_scope(project, "f")[0]


def test_trivial_lane_reads_a_bold_type(project):
    _ready_brief(project, "f", "**bug_fix**")
    assert gate.sprint_start(argparse.Namespace(feature="f", trivial=True)) == 0


def test_no_private_first_token_copy_survives():
    for module in (consistency, triage):
        assert "_first_token" not in Path(module.__file__).read_text()
    assert ".strip(\"`*_\")" not in Path(gate.__file__).read_text()


def _advance_out_of_scoping(tmp: Path, btype: str, size: str) -> int:
    _ready_brief(tmp, "f", btype)
    assert gate.sprint_start(argparse.Namespace(feature="f", trivial=True)) == 0
    design = tmp / "design" / "f"
    design.mkdir(parents=True)
    (design / "trivial.md").write_text("## Change\nx\n\n## How verified\ny\n")
    (design / "scope.md").write_text(f"## Size\n{size}\n")
    return gate.advance(argparse.Namespace(phase="solo_execute", feature="f",
                                           allow_rewind=False))


def _flags(tmp: Path) -> list[dict]:
    lines = (tmp / ".sprint" / "ledger.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e.get("event") == "size_type_flag"]


@pytest.mark.parametrize("btype, size", [("bug_fix", "L"), ("doc", "**XL**"), ("config", "L")])
def test_a_size_conflict_flags_without_blocking(project, capsys, btype, size):
    assert _advance_out_of_scoping(project, btype, size) == 0
    plain = size.strip("*")
    assert f"Type '{btype}' conflicts with size '{plain}'" in capsys.readouterr().out
    assert len(_flags(project)) == 1


@pytest.mark.parametrize("btype, size", [("bug_fix", "S"), ("chore", "L")])
def test_a_matching_type_and_size_records_nothing(project, btype, size):
    assert _advance_out_of_scoping(project, btype, size) == 0
    assert _flags(project) == []


def test_the_size_rule_is_not_a_blocking_check():
    assert consistency.brief_type_matches_scope not in consistency.PHASE_CHECKS["scoping"]
