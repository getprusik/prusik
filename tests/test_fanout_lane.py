"""Fan-out fix lane (fb-7ab319116f42): a field-adding sprint's reverse-dep
files in PROJECT ROOT, which the blast-radius gate already PREDICTED pre-build, become
writable in-flow during a fix-round — so the sprint fixes its predicted fan-out instead
of dead-ending into an integrate-with-flag override.

moat-finding: fb-7ab319116f42

The set is the SYSTEM-COMPUTED prediction, never an agent claim — so the writable
boundary stays sound: a NON-predicted root file is still blocked, and a NON-predicted
out-of-plan write is still flagged. (The adversarial half is the load-bearing half.)
"""

from __future__ import annotations

import json

from prusik import consistency, fix_round, phases

_CONFIG = {"phases": [{"name": "reviewing", "writable": ["reports/{feature}/**"]}]}


def _prediction(root, feature, at_risk):
    (root / ".sprint").mkdir(parents=True, exist_ok=True)
    (root / ".sprint" / f"blast-prediction.{feature}.json").write_text(json.dumps(
        {"feature": feature, "at_risk_tests": at_risk, "symbol_leak_tests": []}))


def _active_fixround(root, feature, fan_out):
    (root / ".sprint").mkdir(parents=True, exist_ok=True)
    fix_round._marker_path(root).write_text(json.dumps(
        {"feature": feature, "round": 1, "started_at": "2026-06-07T00:00:00+00:00",
         "fan_out_files": fan_out}))


def test_load_fan_out_from_prediction(tmp_path):
    _prediction(tmp_path, "feat", ["tests/test_company.py", "app/constants.py"])
    assert fix_round._load_fan_out_files("feat", tmp_path) == [
        "app/constants.py", "tests/test_company.py"]
    assert fix_round._load_fan_out_files("nope", tmp_path) == []   # no prediction → empty


def test_predicted_file_writable_only_during_fixround(tmp_path):
    _active_fixround(tmp_path, "feat", ["tests/test_company.py", "app/constants.py"])
    ok, _ = phases.is_path_writable("tests/test_company.py", _CONFIG, "reviewing",
                                    "feat", root=tmp_path)
    assert ok                                                    # predicted → writable
    ok2, _ = phases.is_path_writable("app/constants.py", _CONFIG, "reviewing", "feat",
                                     root=tmp_path)
    assert ok2                                                   # predicted non-test too


def test_NON_predicted_root_file_stays_blocked(tmp_path):
    """ADVERSARIAL: the fan-out lane must NOT become a writable hole. A root file the
    prediction did not name is still blocked, even inside a fix-round."""
    _active_fixround(tmp_path, "feat", ["tests/test_company.py"])
    ok, why = phases.is_path_writable("app/secret_unpredicted.py", _CONFIG, "reviewing",
                                      "feat", root=tmp_path)
    assert not ok and "writable patterns" in (why or "")


def test_predicted_file_NOT_writable_without_fixround(tmp_path):
    _prediction(tmp_path, "feat", ["tests/test_company.py"])     # prediction but NO round
    ok, _ = phases.is_path_writable("tests/test_company.py", _CONFIG, "reviewing",
                                    "feat", root=tmp_path)
    assert not ok                                               # only writable in-round


def test_boundary_credits_predicted_fanout_but_flags_others(tmp_path):
    _prediction(tmp_path, "feat", ["app/constants.py"])         # constants is predicted
    (tmp_path / "design" / "feat").mkdir(parents=True)
    (tmp_path / "design" / "feat" / "scope.md").write_text(
        "## Modules touched\n- app/company.py\n")
    wt = tmp_path / "worktrees" / "backend" / "app"
    wt.mkdir(parents=True)
    (wt / "constants.py").write_text("KEYS=()\n")               # predicted reverse-dep
    (wt / "random.py").write_text("x=1\n")                      # NOT predicted, out-of-plan
    viols = consistency.builder_writes_within_plan(tmp_path, "feat")
    assert not any("constants.py" in v for v in viols)         # predicted → credited
    assert any("random.py" in v for v in viols)                # ADVERSARIAL: still flagged
