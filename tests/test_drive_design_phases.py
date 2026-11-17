"""`prusik drive` runs scoping, triage and planning (fb-f358daf6e022). criterion:
drive-design-loop."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest
import yaml

from prusik import drive, phases
from tests.test_sprint_drive import F, PLAN, escalated_events, proj, rig, set_phase  # noqa: F401

SCOPE = """# Scope

## Goal recap
Drive the design phases in code for this feature.

## Modules touched
- `briefs/feat.md`

## Blast radius
- none

## Related work
- none

## Size
S

## Domains
- backend

## Risks
- none

## Open questions
- none
"""
GOOD_PLAN = """# Plan

## Goal recap
Drive the design phases in code for this feature.

## Modules touched
- `briefs/feat.md`

## Build order
1. do it

## Interfaces
none

## Test plan
- t1
- t2
- t3

## Risks
- none

## Out of scope
- nothing

## Proposed roles
- **backend-builder** owns worktrees/backend-builder/.
"""
SENTINEL = "ZZ-SENTINEL-CRITIQUE-9431"


def _writer(proj, rel, text):
    def script(req):
        (proj / rel).write_text(text)
    return script


def _seq_writer(proj, rel, texts):
    queue = list(texts)

    def script(req):
        (proj / rel).write_text(queue.pop(0) if len(queue) > 1 else queue[0])
    return script


def _report_seq(proj, name, verdicts, extra=""):
    queue = list(verdicts)

    def script(req):
        v = queue.pop(0) if len(queue) > 1 else queue[0]
        (proj / "reports/feat" / name).write_text(f"{v}\n{extra}")
    return script


def _rounds(proj, n):
    p = proj / ".claude/sprint-config.yaml"
    cfg = yaml.safe_load(p.read_text())
    cfg["drive"]["design_rounds"] = n
    p.write_text(yaml.safe_dump(cfg))


def _backdate(path, seconds=3600):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _real_design_gates(monkeypatch):
    """scope and plan run the real gate; the rest stays on the Rig's recorder."""
    fake = drive._Drive._gate

    def gate(self, *a):
        if a[0] in ("scope", "plan"):
            return self._cli([sys.executable, "-m", "prusik", "gate", *a])
        return fake(self, *a)
    monkeypatch.setattr(drive._Drive, "_gate", gate)


def _fake_triage(monkeypatch, proj, decision=None, rc=0, raw=None):
    prev = drive._Drive._prusik

    def prusik(self, *a, cwd=None):
        if a[0] == "triage":
            (proj / "decisions").mkdir(exist_ok=True)
            p = proj / "decisions" / f"{F}.json"
            if raw is not None:
                p.write_text(raw)
            elif decision is not None:
                p.write_text(json.dumps(decision))
            return rc, "triage out"
        return prev(self, *a, cwd=cwd)
    monkeypatch.setattr(drive._Drive, "_prusik", prusik)


def _spawns(rig):
    return [r.role for r in rig.adapter.requests]


def _advances(rig):
    return [e[2] for e in rig.events if e[:2] == ("gate", "advance")]


def test_full_run_scoping_to_building(rig, proj, monkeypatch):
    set_phase(proj, "scoping")
    (proj / "design/feat/plan.md").unlink()
    _real_design_gates(monkeypatch)
    _fake_triage(monkeypatch, proj, {"mode": "team", "reason": "r"})
    rig.adapter.scripts["scoping"] = _writer(proj, "design/feat/scope.md", SCOPE)
    rig.adapter.scripts["scope-critic"] = _report_seq(
        proj, "scope-approval.txt", ["REJECTED", "APPROVED"])
    rig.adapter.scripts["feature-planner"] = _seq_writer(
        proj, "design/feat/plan.md", ["# not a plan\n", GOOD_PLAN])
    assert rig.run() == 0, rig.out
    assert _spawns(rig)[:7] == ["scoping", "scope-critic", "scoping", "scope-critic",
                                "feature-planner", "feature-planner", "plan-critic"]
    assert _advances(rig)[:3] == ["triage", "planning", "building"]
    assert not (proj / "reports/feat/drive-escalation.json").exists()


def test_author_prompt_never_contains_critic_text(rig, proj):
    set_phase(proj, "scoping")
    rig.adapter.scripts["scoping"] = _writer(proj, "design/feat/scope.md", SCOPE)
    rig.adapter.scripts["scope-critic"] = _report_seq(
        proj, "scope-approval.txt", ["REJECTED", "APPROVED"], extra=SENTINEL + "\n")
    rig.run()
    authors = [r.prompt for r in rig.adapter.requests if r.role == "scoping"]
    assert len(authors) == 2
    assert all(SENTINEL not in p for p in authors)
    assert "reports/feat/scope-approval.txt" in authors[1]


def test_gate_failure_passes_error_lines_to_author(rig, proj):
    set_phase(proj, "planning")
    rig.rcs["plan"] = [1]
    rig.tails["plan"] = "E-ONE bad section\nE-TWO worse"
    rig.adapter.scripts["feature-planner"] = _writer(proj, "design/feat/plan.md", PLAN)
    rig.run()
    prompt = next(r.prompt for r in rig.adapter.requests if r.role == "feature-planner")
    assert "E-ONE bad section" in prompt and "E-TWO worse" in prompt


def test_round_cap_escalates_plan_rejected(rig, proj):
    set_phase(proj, "planning")
    _rounds(proj, 2)
    rig.adapter.scripts["plan-critic"] = _report_seq(proj, "plan-approval.txt", ["REJECTED"])
    rig.adapter.scripts["feature-planner"] = _writer(proj, "design/feat/plan.md", PLAN)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "plan_rejected" and esc["role"] == "plan-critic"
    assert any(e.endswith("plan-approval.txt") for e in esc["evidence"])
    assert _spawns(rig).count("feature-planner") == 2
    assert "building" not in _advances(rig)


def test_round_cap_escalates_scope_rejected(rig, proj):
    set_phase(proj, "scoping")
    _rounds(proj, 2)
    rig.adapter.scripts["scope-critic"] = _report_seq(proj, "scope-approval.txt", ["REJECTED"])
    rig.adapter.scripts["scoping"] = _writer(proj, "design/feat/scope.md", SCOPE)
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "scope_rejected"
    assert _spawns(rig).count("scoping") == 2
    assert "triage" not in _advances(rig)


def test_stale_author_fails_closed(rig, proj):
    set_phase(proj, "planning")
    rig.rcs["plan"] = [1]
    _backdate(proj / "design/feat/plan.md")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "spawn_failed" and esc["role"] == "feature-planner"
    assert "plan-critic" not in _spawns(rig)


@pytest.mark.parametrize("old_report", [False, True])
def test_stale_critic_fails_closed(rig, proj, old_report):
    set_phase(proj, "scoping")
    (proj / "design/feat/scope.md").write_text(SCOPE)
    if old_report:
        rep = proj / "reports/feat/scope-approval.txt"
        rep.write_text("APPROVED\n")
        _backdate(rep)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "spawn_failed" and esc["role"] == "scope-critic"
    assert _advances(rig) == []


def test_triage_team_advances_to_planning(rig, proj, monkeypatch):
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, {"mode": "team", "reason": "r"})
    rig.run()
    assert _advances(rig)[0] == "planning"


def test_triage_solo_continues_into_solo_execute_and_spawns_solo_builder(rig, proj,
                                                                          monkeypatch):
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, {"mode": "solo", "reason": "r"})
    rig.run()
    assert _advances(rig)[:2] == ["solo_execute", "reviewing"]
    assert _spawns(rig)[0] == "solo-builder"
    assert escalated_events() == [], "the build is not handed back"


@pytest.mark.parametrize("kw,needle", [
    ({"decision": {"mode": "reject", "reason": "too vague"}}, "too vague"),
    ({}, "decision missing"),
    ({"raw": "{garbage"}, "decision unreadable"),
    ({"raw": "[1]"}, "unknown mode"),
    ({"decision": {"mode": "weird"}}, "unknown mode weird"),
    ({"decision": {"mode": "team"}, "rc": 1}, "triage rc 1"),
])
def test_triage_reject_and_unreadable_escalate(rig, proj, monkeypatch, kw, needle):
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, **kw)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "triage_rejected" and needle in esc["reason"]
    assert _advances(rig) == []


def test_trivial_lane_at_scoping_is_refused(rig, proj):
    phases.set_sprint_state({"feature": F, "phase": "scoping", "lane": "trivial"}, proj)
    assert rig.run() == 2
    for needle in ("trivial.md", "worktrees/solo/", "reviewing"):
        assert needle in rig.out
    assert rig.adapter.requests == []
    assert not (proj / "reports/feat/drive-escalation.json").exists()


@pytest.mark.parametrize("bad", [0, -1, "3", True, 1.5])
def test_invalid_design_rounds_is_refused(rig, proj, bad):
    set_phase(proj, "scoping")
    _rounds(proj, bad)
    assert rig.run() == 2
    assert "drive.design_rounds must be a positive integer" in rig.out
    assert rig.adapter.requests == []
