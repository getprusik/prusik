"""The unified fidelity SCORECARD (the v0.19 eval-suite keystone) folds three reproducible
signals — divergence-injection, corpus catch-rate, agent-control — into one version-stamped
artifact with a pass/fail FLOOR. The floor is the regression gate: a gate-weakening engine
or config change drops `floor_met` to False. This is the evidence layer the adopter trust
report renders and the cross-harness fidelity check compares against.

moat-finding: roadmap-horizon-1b-eval-keystone
"""

from __future__ import annotations

import json

from prusik import eval as kit_eval
from prusik import injection


def test_scorecard_floor_met_on_shipped_surface():
    card = kit_eval.compute_scorecard()
    assert card["prusik_version"]
    # 1. injection: every divergence caught, no control false-blocked
    assert card["injection"]["available"], "the active sprint-config must load"
    ic, it = card["injection"]["catch_rate"]
    assert ic == it and it >= 3, card["injection"]
    assert card["injection"]["misses"] == []
    assert card["injection"]["false_blocks"] == []
    # 2. corpus: every defect-class case flagged, clean stays clean
    assert card["corpus"]["cases_passed"] == card["corpus"]["cases_total"] > 0
    # 3. agent-control: prusik-on caught every case vibe-coding (=0) would ship
    assert card["agent_control"]["prusik_on_catches"] == card["corpus"]["cases_total"]
    assert card["agent_control"]["prusik_off_catches"] == 0
    assert card["floor_met"] is True


def test_floor_fails_on_injection_miss(monkeypatch):
    # ADVERSARIAL: simulate a gate weakening so a divergence slips through. The floor MUST
    # drop — that is the whole point of gating the scorecard.
    real = injection.summarize

    def fake(results):
        s = dict(real(results))
        s["misses"] = [{"id": "writable_gate", "kind": "divergence"}]
        return s

    monkeypatch.setattr(injection, "summarize", fake)
    card = kit_eval.compute_scorecard()
    assert card["floor_met"] is False
    assert "writable_gate" in card["injection"]["misses"]


def test_floor_fails_on_control_false_block(monkeypatch):
    # a control that gets BLOCKED (over-firing) must also drop the floor — fidelity is
    # catch AND discrimination, not catch alone.
    real = injection.summarize

    def fake(results):
        s = dict(real(results))
        s["false_blocks"] = [{"id": "deny_commands", "kind": "control"}]
        return s

    monkeypatch.setattr(injection, "summarize", fake)
    assert kit_eval.compute_scorecard()["floor_met"] is False


def test_scorecard_writes_versioned_artifact(tmp_path):
    out = tmp_path / "scorecard.json"
    rc = kit_eval.scorecard(json_output=True, out=str(out))
    assert rc == 0                                    # shipped surface meets the floor
    data = json.loads(out.read_text())
    assert data["floor_met"] is True
    assert data["prusik_version"]                     # version-stamped for cross-version trend
    assert "by_case" in data["corpus"]
