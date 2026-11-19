"""A builder can stop and hand back, and a builder that built nothing never advances.

fb-1e4da436fcfc (first half): drive spawned the builder, proved the suite and advanced. A
builder bound by its plan to STOP and escalate had no channel to do so, and a builder that
stopped with nothing built left the suite green on the old code, so the sprint advanced to
reviewing on an empty build. Two signals now stop drive:

- `reports/<f>/stop-<role>.txt`, written by the agent: `builder_stopped`, its first line the
  reason. Cleared before every spawn, so only that spawn's stop counts.
- the builder's branch tip still equal to its recorded fork point, computed by drive:
  `empty_build`, whatever the agent says.

moat-finding: fb-1e4da436fcfc
moat-finding: fb-4a170f4332ec
"""

from __future__ import annotations

from pathlib import Path

from prusik import ledger, phases
from tests.test_sprint_drive import (  # noqa: F401
    F, _committing_builder, _git_project, proj, rig, set_phase,
)
from tests.test_tiered_solo_builder import _builds, gproj  # noqa: F401

_AGENTS = Path(__file__).resolve().parent.parent / "prusik" / "templates" / ".claude" / "agents"


def _advanced(rig) -> bool:
    return any(e[:3] == ("gate", "advance", "reviewing") for e in rig.events)


def _done(role: str) -> list:
    return [r for r in ledger.read_all() if r.get("event") == "drive_builder_done"
            and r.get("role") == role]


# ── empty build: system-computed ──────────────────────────────────────────────────────────

def test_a_team_builder_that_commits_nothing_escalates_empty_build(rig, proj):
    _git_project(proj)
    assert rig.run() == 3                       # the suite would prove green: rig default
    esc = rig.escalation()
    assert esc["kind"] == "empty_build" and esc["role"] == "backend-builder"
    assert esc["phase"] == "building"
    assert not _advanced(rig) and _done("backend-builder") == []
    assert phases.current_sprint_state(proj)["phase"] == "building"


def test_a_solo_builder_that_commits_nothing_escalates_empty_build(rig, gproj):
    ledger.append("phase_advance", from_phase="triage", to_phase="solo_execute", feature=F)
    set_phase(gproj, "solo_execute")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "empty_build" and esc["role"] == "solo-builder"
    assert not _advanced(rig) and _done("solo-builder") == []


def test_positive_control_a_committing_builder_advances(rig, proj):
    _git_project(proj)
    rig.adapter.scripts["backend-builder"] = _committing_builder(proj)
    rig.rcs["advance"] = [0, 1]                 # building -> reviewing, then stop
    rig.run()
    assert _advanced(rig) and len(_done("backend-builder")) == 1


def test_without_git_there_is_no_branch_to_measure_and_no_empty_build(rig, proj):
    rig.rcs["advance"] = [0, 1]
    rig.run()
    assert _advanced(rig)
    assert all(e.get("kind") != "empty_build" for e in ledger.read_all()
               if e.get("event") == "drive_escalated")


# ── stop: the agent's channel ─────────────────────────────────────────────────────────────

def _stopping(proj, role: str, reason: str, *, commit: bool):
    build = _committing_builder(proj, role) if commit else None

    def script(req):
        if build:
            build(req)
        (proj / "reports" / F / f"stop-{role}.txt").write_text(f"{reason}\nmore detail\n")
    return script


def test_a_builder_stop_escalates_builder_stopped_with_its_reason(rig, proj):
    _git_project(proj)
    rig.adapter.scripts["backend-builder"] = _stopping(
        proj, "backend-builder", "PostToolUse does not fire on a non-zero exit", commit=True)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "builder_stopped" and esc["role"] == "backend-builder"
    assert "PostToolUse does not fire on a non-zero exit" in esc["reason"]
    assert "more detail" not in esc["reason"]
    assert any(e.endswith("stop-backend-builder.txt") for e in esc["evidence"])
    assert not _advanced(rig) and _done("backend-builder") == []


def test_a_stop_wins_over_a_green_suite_and_a_real_commit(rig, proj):
    """Adversarial: everything else says done. The stop still halts drive."""
    _git_project(proj)
    rig.adapter.scripts["backend-builder"] = _stopping(proj, "backend-builder", "r",
                                                       commit=True)
    rig.rcs["prove"] = [0, 0, 0]
    assert rig.run() == 3 and rig.escalation()["kind"] == "builder_stopped"
    assert not any(e[0] == "prove" for e in rig.events), "nothing proven past a stop"


def test_a_stop_from_an_earlier_run_does_not_halt_the_next_spawn(rig, proj):
    """The stop file is evidence for the person until drive spawns that role again; a
    re-spawn clears it first, so a resolved stop never re-escalates."""
    _git_project(proj)
    stale = proj / "reports" / F / "stop-backend-builder.txt"
    stale.write_text("resolved last time\n")
    rig.adapter.scripts["backend-builder"] = _committing_builder(proj)
    rig.rcs["advance"] = [0, 1]
    rig.run()
    assert not stale.exists()
    assert _advanced(rig)


def test_another_roles_stop_file_is_left_alone(rig, proj):
    _git_project(proj)
    other = proj / "reports" / F / "stop-test-writer.txt"
    other.write_text("keep me\n")
    rig.adapter.scripts["backend-builder"] = _committing_builder(proj)
    rig.rcs["advance"] = [0, 1]
    rig.run()
    assert other.read_text() == "keep me\n"


def test_every_builder_spec_names_the_stop_channel():
    for role in ("backend-builder", "frontend-builder", "test-writer", "solo-builder"):
        text = (_AGENTS / f"{role}.md").read_text()
        assert f"reports/{{feature}}/stop-{role}.txt" in text, role


# ── fb-4a170f4332ec: builders spend context deliberately ──────────────────────────────────

def test_every_builder_spec_carries_the_same_context_spend_rule():
    """moat-finding: fb-4a170f4332ec — a builder woke on its own background test runs and
    read gate.py (>4000 lines) whole, 86 turns at ~139K context each."""
    rules = set()
    for role in ("backend-builder", "frontend-builder", "test-writer", "solo-builder"):
        line = next(ln for ln in (_AGENTS / f"{role}.md").read_text().splitlines()
                    if ln.startswith("- **Spend context like it is billed"))
        assert "foreground" in line and "offset/limit" in line and "one turn" in line
        rules.add(line)
    assert len(rules) == 1, "one rule, worded once"
