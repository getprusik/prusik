"""`prusik gate capture --reset` with NO command is a CLEAR — it discards the phase's
prior evidence and records nothing, so an agent never fakes a clear with a no-op
`-- echo reset` that appended a tests=0 entry tripping the false-clean guard at every
advance (fb-9a095c7674f2).

moat-finding: fb-9a095c7674f2
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from prusik import gate, schema


def _seed_evidence(tmp_path, entries):
    rd = tmp_path / "reports" / "feat"
    rd.mkdir(parents=True, exist_ok=True)
    ev = schema.evidence_path_for(rd, "regression")
    ev.write_text(json.dumps(
        {"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": entries}))
    return ev


def test_reset_without_command_clears_all_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    ev = _seed_evidence(tmp_path, [
        {"phase": "regression", "exit_code": 0,
         "nonempty_primitive": {"kind": "tests", "value": 0}}])   # the poisoning entry
    rc = gate.capture(SimpleNamespace(command=[], reset=True,
                                      feature="feat", phase="regression", kind="tests"))
    assert rc == 0
    assert schema.load_evidence(ev) == []        # cleared — no tests=0 entry left behind


def test_no_command_without_reset_still_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rc = gate.capture(SimpleNamespace(command=[], reset=False,
                                      feature="feat", phase="regression", kind="tests"))
    assert rc == 2                                # a capture with nothing to run is misuse


def test_reset_clear_on_absent_file_is_a_noop_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rc = gate.capture(SimpleNamespace(command=[], reset=True,
                                      feature="feat", phase="regression", kind="tests"))
    assert rc == 0                                # clearing nothing is fine, not an error
