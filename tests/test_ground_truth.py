"""Phase-entry reality: ground-truth drift blocks; goal-already-met probes.

moat-finding:fb-4c542a24db7c — a security sprint's brief enumerated 7
advisories; by scoping they were fixed on main and 3 different ones were the
real blockers, so scope.md derived from stale facts.
moat-finding:fb-8637c2416504 — the same sprint sat in scoping while its
target vulns were fixed on base; nothing filed a goal-already-met signal.
One mechanism, two faces: recorded reality re-verifies at phase entry.
"""

import json

from prusik import ground_truth, watchdog

CRIT_HEADER = 'schema_version: "1.0"\ncriteria:\n'


def _crit(tmp, feature="feat", body="", gt=None):
    briefs = tmp / "briefs"
    briefs.mkdir(exist_ok=True)
    text = CRIT_HEADER + (body or
                          "  - id: c1\n    description: d\n"
                          "    verify_command: \"true\"\n")
    if gt:
        text += f"ground_truth:\n  command: {json.dumps(gt)}\n"
    p = briefs / f"{feature}.criteria.yaml"
    p.write_text(text)
    return p


def test_capture_then_check_roundtrip(tmp_path):
    src = tmp_path / "gt.txt"
    src.write_text("GHSA-1\nGHSA-2\n")
    p = _crit(tmp_path, gt=f"cat {src}")
    assert ground_truth.capture(p) == 0
    text = p.read_text()
    assert "output_sha256" in text and "captured_at" in text
    ok, msg = ground_truth.check(p)
    assert ok, msg


def test_drift_detected_with_diff(tmp_path):
    src = tmp_path / "gt.txt"
    src.write_text("GHSA-1\n")
    p = _crit(tmp_path, gt=f"cat {src}")
    ground_truth.capture(p)
    src.write_text("GHSA-999\n")                    # the world moved
    ok, msg = ground_truth.check(p)
    assert not ok
    assert "GHSA-1" in msg and "GHSA-999" in msg    # human diff, both sides


def test_absent_block_is_dormant(tmp_path):
    p = _crit(tmp_path)                              # no ground_truth
    ok, msg = ground_truth.check(p)
    assert ok and "no ground truth" in msg
    (tmp_path / ".sprint").mkdir()
    assert ground_truth.sprint_start_check("feat", tmp_path)
    assert not (tmp_path / ".sprint" / "ledger.jsonl").exists()


def test_sprint_start_blocks_on_drift_and_records(tmp_path, monkeypatch,
                                                  capsys):
    src = tmp_path / "gt.txt"
    src.write_text("A\n")
    p = _crit(tmp_path, gt=f"cat {src}")
    ground_truth.capture(p)
    src.write_text("B\n")
    (tmp_path / ".sprint").mkdir()
    monkeypatch.chdir(tmp_path)
    assert not ground_truth.sprint_start_check("feat", tmp_path)
    out = capsys.readouterr().out
    assert "ground-truth" in out and "--capture" in out   # re-baseline path
    events = [json.loads(line) for line in
              (tmp_path / ".sprint" / "ledger.jsonl").read_text().splitlines()]
    assert any(e["event"] == "ground_truth_drift" for e in events)


PROVE_RED_GREEN = ("  - id: r1\n    description: d\n"
                   "    verify_command: \"true\"\n    prove_red: true\n"
                   "  - id: r2\n    description: d\n"
                   "    verify_command: \"true\"\n    prove_red: true\n")
PROVE_RED_MIXED = ("  - id: r1\n    description: d\n"
                   "    verify_command: \"true\"\n    prove_red: true\n"
                   "  - id: r2\n    description: d\n"
                   "    verify_command: \"false\"\n    prove_red: true\n")


def test_probe_fires_when_all_prove_red_green_on_base(tmp_path):
    _crit(tmp_path, body=PROVE_RED_GREEN)
    payload = ground_truth.base_probe("feat", tmp_path, "scoping")
    assert payload and payload["green_criteria"] == ["r1", "r2"]


def test_probe_silent_when_any_prove_red_still_red(tmp_path):
    _crit(tmp_path, body=PROVE_RED_MIXED)
    assert ground_truth.base_probe("feat", tmp_path, "scoping") is None


def test_probe_only_in_early_phases_and_needs_prove_red(tmp_path):
    _crit(tmp_path, body=PROVE_RED_GREEN)
    assert ground_truth.base_probe("feat", tmp_path, "building") is None
    _crit(tmp_path, feature="plain")                  # no prove_red entries
    assert ground_truth.base_probe("plain", tmp_path, "scoping") is None


def test_watchdog_files_criteria_already_met_once(tmp_path, monkeypatch):
    _crit(tmp_path, body=PROVE_RED_GREEN)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "sprint-config.yaml").write_text("phases: {}\n")
    (tmp_path / ".sprint").mkdir()
    (tmp_path / ".sprint" / "state.json").write_text(
        json.dumps({"feature": "feat", "phase": "scoping"}))
    monkeypatch.chdir(tmp_path)
    assert watchdog.check(root=tmp_path) == 1
    inc = list((tmp_path / ".sprint" / "incidents").glob("*.json"))
    kinds = [json.loads(f.read_text())["kind"] for f in inc]
    assert kinds.count("criteria_already_met") == 1
    watchdog.check(root=tmp_path)                     # second run: no re-file
    inc2 = list((tmp_path / ".sprint" / "incidents").glob("*.json"))
    kinds2 = [json.loads(f.read_text())["kind"] for f in inc2]
    assert kinds2.count("criteria_already_met") == 1
