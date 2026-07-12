"""fb-dde6878ad04b — `discovery fingerprint-map` snapshotted the CACHED dep-graph.json,
which could be stale (a file merged in a prior sprint wasn't in the graph yet). The
map-freshness gate, however, compares against a FRESH file walk (`current_modules`, the
fb-76ff51b273de fix) — so a fingerprint run right after the cartographer wrote map.md
snapshotted a module set MISSING the merged file, and the gate read the just-written
fingerprint as already-drifted. Re-running the cartographer couldn't clear it (same stale
cache); only a manual `prusik discovery all` rebuilt the graph.

fingerprint-map now auto-refreshes the dep-graph and snapshots the module set from the
SAME fresh walk the gate uses — so a freshly-run fingerprint is drift-free by construction,
and re-running the cartographer always clears the gate (its whole purpose). Genuine drift
AFTER the fingerprint is still caught (the gate isn't blinded).

moat-finding: fb-dde6878ad04b
"""

from __future__ import annotations

import json

from prusik import discovery


def _project(tmp_path):
    (tmp_path / "design").mkdir(parents=True, exist_ok=True)
    (tmp_path / "design" / "map.md").write_text("# map\n")
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "core.py").write_text("x = 1\n")
    return src


def test_fingerprint_captures_file_present_at_run_time_without_discovery_all(tmp_path):
    src = _project(tmp_path)
    discovery.dep_graph(tmp_path)                       # cached graph: core.py only (stale-analog)
    (src / "billing.py").write_text("import os\n")      # merged AFTER the graph → cache is stale

    # The cartographer runs fingerprint-map ONLY — not `prusik discovery all`.
    assert discovery.fingerprint_map(tmp_path) == 0

    fp = json.loads((tmp_path / ".sprint" / "map-fingerprint.json").read_text())
    assert "src/billing.py" in fp["modules"]            # auto-refreshed — the file IS captured
    # adversarial: the freshness gate sees NO spurious drift for the billing subsystem,
    # so re-running the cartographer alone clears sprint-init (the operator's escape was
    # a manual `discovery all` — now unnecessary).
    assert discovery.feature_scoped_drift(tmp_path, ["billing"]) == []
    assert discovery.map_drift(tmp_path)["drift_pct"] == 0.0


def test_genuine_drift_after_fingerprint_is_still_caught(tmp_path):
    """Positive control — the fix must not blind the gate: a file merged AFTER the
    fingerprint is real staleness and must still register."""
    src = _project(tmp_path)
    assert discovery.fingerprint_map(tmp_path) == 0     # snapshot: core.py only
    (src / "billing.py").write_text("import os\n")       # merged AFTER the fingerprint → REAL drift

    assert discovery.feature_scoped_drift(tmp_path, ["billing"]) == ["src/billing.py"]
    assert discovery.map_drift(tmp_path)["added_count"] == 1


def test_fingerprint_requires_map_but_not_a_prebuilt_graph(tmp_path):
    """fingerprint-map no longer needs a pre-existing dep-graph.json — it builds one.
    It still requires the cartographer's map.md."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "core.py").write_text("x = 1\n")
    assert discovery.fingerprint_map(tmp_path) == 1     # no design/map.md yet
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "map.md").write_text("# map\n")
    assert discovery.fingerprint_map(tmp_path) == 0     # builds the graph itself
    assert (tmp_path / ".sprint" / "dep-graph.json").exists()
