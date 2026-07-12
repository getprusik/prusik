"""The false-clean guard already BLOCKS a tests=0/types=0 reviewer evidence (it can't
launder a no-op as clean) — but the diagnosis was flat, so a genuinely-clean run that
produced no count (silent `tsc`, wrong test path) read identically to a no-op with no
hint to fix it (fb-b587d8d9b71c). The message is now kind-aware: it steers to the real
remediation instead of loosening the gate. Non-kit (agent-written) manifests stay
rejected by `captured_by` (schema), unchanged.

moat-finding: fb-b587d8d9b71c
"""

from __future__ import annotations

import json

from prusik import gate, schema


def _evidence(tmp, kind, value):
    rd = tmp / "reports" / "feat"
    rd.mkdir(parents=True, exist_ok=True)
    role = gate._PHASE_ROLE.get("regression", "")
    wt = gate._worktree_substantive_hash(tmp, gate._REVIEWER_INPUTS.get(role, ()))
    ev = schema.evidence_path_for(rd, "regression")
    ev.write_text(json.dumps({"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": [
        {"phase": "regression", "command": "pytest -q", "exit_code": 0,
         "nonempty_primitive": {"kind": kind, "value": value},
         "output_sha": "abc", "worktree_hash": wt,
         "captured_by": schema.EVIDENCE_CAPTURED_BY}]}))
    return "reports/feat/regression.evidence.json"


def test_types_zero_steers_to_extended_diagnostics(tmp_path):
    rel = _evidence(tmp_path, "types", 0)
    msg = gate._evidence_unsatisfied(rel, "feat", tmp_path)
    assert msg and "extendedDiagnostics" in msg          # actionable, not just "nothing ran"
    assert "false-clean" in msg                          # still named as the false-clean class


def test_tests_zero_steers_to_real_tests(tmp_path):
    rel = _evidence(tmp_path, "tests", 0)
    msg = gate._evidence_unsatisfied(rel, "feat", tmp_path)
    assert msg and "0 tests executed" in msg


def test_real_count_satisfies(tmp_path):
    rel = _evidence(tmp_path, "tests", 5)
    assert gate._evidence_unsatisfied(rel, "feat", tmp_path) is None   # real work → clean


def test_agent_written_manifest_is_rejected(tmp_path):
    """A hand-written manifest (captured_by ≠ kit) is rejected by the schema — the gate
    never trusts evidence it didn't capture (fb-b587 part b)."""
    rd = tmp_path / "reports" / "feat"
    rd.mkdir(parents=True)
    ev = schema.evidence_path_for(rd, "regression")
    ev.write_text(json.dumps({"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": [
        {"phase": "regression", "command": "pytest", "exit_code": 0,
         "nonempty_primitive": {"kind": "tests", "value": 9},
         "output_sha": "x", "worktree_hash": "y", "captured_by": "agent-hand-written"}]}))
    msg = gate._evidence_unsatisfied("reports/feat/regression.evidence.json", "feat", tmp_path)
    assert msg and "captured_by" in msg
