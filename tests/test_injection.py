"""Divergence-injection harness — catch-rate, discrimination, gap detection,
and the public deny helper (v0.50.0)."""

from __future__ import annotations

import json

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _copy_sprint_config,
    _mktmp_project,
    gate,
    phases,
)
from prusik import injection


def _config():
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    return phases.load_sprint_config(), tmp


# ---------- the public deny helper ----------

def test_is_command_denied_uses_phase_deny_list():
    spec = {"deny_commands": ["git push", "git merge"]}
    assert gate.is_command_denied("git push origin main", spec) is True
    assert gate.is_command_denied("git status", spec) is False
    assert gate.is_command_denied("anything", {"deny_commands": []}) is False


# ---------- catch-rate on the default config ----------

def test_default_config_catches_every_divergence(tmp_path):
    config, _ = _config()
    results = injection.run_cases(config, tmp_path)
    s = injection.summarize(results)
    caught, total = s["catch_rate"]
    assert total == 3                       # scope-drift, premature-push, fabricated-done
    assert caught == total, s["misses"]     # all caught on the shipped config
    assert s["misses"] == []


def test_controls_are_not_flagged(tmp_path):
    config, _ = _config()
    results = injection.run_cases(config, tmp_path)
    s = injection.summarize(results)
    dok, dtot = s["discrimination"]
    assert dok == dtot == 3                  # legit actions all pass — gate discriminates
    assert s["false_blocks"] == []


def test_each_failure_mode_is_represented(tmp_path):
    config, _ = _config()
    gates = {r["gate"] for r in injection.run_cases(config, tmp_path)
             if r["kind"] == "divergence"}
    assert gates == {"writable_gate", "deny_commands", "execution_evidence"}


def test_divergence_catalog_is_immutable_a_defect_cannot_become_a_feature():
    """A planted DEFECT must never be silently reclassified into accepted behavior
    (a 'feature'). Injection cannot do that on its own — it only READS config and
    writes to a temp dir — so the one path by which a defect could become a feature
    is a visible edit to this catalog (drop a case, or flip its kind
    divergence→control). Pin the exact catalog so that edit fails loudly: adding a
    real divergence is then a deliberate act that updates this test, never a quiet
    downgrade of a guardrail."""
    by_kind: dict[str, set] = {}
    for c in injection._cases():
        by_kind.setdefault(c["kind"], set()).add(c["id"])
    assert by_kind["divergence"] == {
        "scope-drift-write", "premature-push", "fabricated-done"}, \
        "a divergence was dropped or reclassified — a defect must not become a feature"
    assert by_kind["control"] == {
        "in-lane-write", "benign-status", "genuine-evidence"}
    # every divergence targets a real enforced gate (no orphan/no-op 'defect')
    for c in injection._cases():
        if c["kind"] == "divergence":
            assert c["gate"] in {"writable_gate", "deny_commands",
                                 "execution_evidence"}


def test_earliness_rank_orders_evidence_after_build(tmp_path):
    config, _ = _config()
    ranks = {r["gate"]: r["rank"] for r in injection.run_cases(config, tmp_path)}
    assert ranks["execution_evidence"] > ranks["writable_gate"]   # review later than build


# ---------- gap detection: a weakened config MISSES ----------

def test_weakened_config_is_caught_as_a_miss(tmp_path):
    # A config whose building phase drops deny_commands AND opens writable to **
    # must FAIL the harness — proving it detects real guardrail gaps, not a
    # rubber stamp.
    config, _ = _config()
    for p in config["phases"]:
        if p["name"] == "building":
            p["deny_commands"] = []
            p["writable"] = ["**"]
    results = injection.run_cases(config, tmp_path)
    s = injection.summarize(results)
    miss_ids = {m["id"] for m in s["misses"]}
    assert "scope-drift-write" in miss_ids      # ** allows the out-of-lane write
    assert "premature-push" in miss_ids         # no deny_commands → push not caught


# ---------- fabricated evidence specifically ----------

def test_fabricated_evidence_rejected_genuine_accepted(tmp_path):
    assert injection._evidence_ok(tmp_path, captured_by="agent-narrated") is False
    from prusik import schema
    assert injection._evidence_ok(
        tmp_path, captured_by=schema.EVIDENCE_CAPTURED_BY) is True


# ---------- CLI ----------

def test_cli_passes_on_default_config_rc0():
    _config()
    out = _capture_stdout(lambda: _run_and_capture_rc())
    assert "3/3 known defects caught" in out


def test_cli_rc_is_zero_on_clean_config():
    _config()
    assert injection.run() == 0


def test_cli_json_shape_and_rc():
    _config()
    out = _capture_stdout(lambda: injection.run(json_output=True))
    data = json.loads(out)
    assert data["catch_rate"] == [3, 3]
    assert data["misses"] == [] and data["false_blocks"] == []


_RC = {}


def _run_and_capture_rc():
    _RC["rc"] = injection.run()
