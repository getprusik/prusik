"""Product report — the composed per-product health snapshot (v0.54.0)."""

from __future__ import annotations

import json

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _mktmp_project,
    _write_ledger,
)
from prusik import report
from prusik import catch_quality as cq


def _seed():
    # two journeys (one done, one open), an auto-true evidence catch, a stall
    recs = [{"ts": f"2026-06-01T0{i}:00:00+00:00", **e}
            for i, e in enumerate([
                {"event": "sprint_started", "feature": "a"},
                {"event": "phase_advance", "from_phase": "scoping",
                 "to_phase": "building", "feature": "a"},
                {"event": "sprint_complete", "feature": "a"},
                {"event": "sprint_started", "feature": "b"},
                {"event": "phase_advance", "from_phase": "scoping",
                 "to_phase": "building", "feature": "b"},
            ])]
    recs.append({"ts": "2026-06-01T09:00:00+00:00",
                 "event": "reviewer_execution_verified", "ok": False,
                 "feature": "a"})                        # auto-true evidence catch
    recs.append({"ts": "2026-06-01T09:30:00+00:00",
                 "event": "convergence_stall", "kind": "phase_rewind",
                 "feature": "b"})
    return recs


# ---------- composition ----------

def test_build_composes_trust_value_health():
    r = report.build(_seed())
    assert r["journeys"] == 2
    assert r["completed"] == 1 and r["completion_pct"] == 50
    assert r["open_features"] == ["b"]
    assert r["convergence_stalls"] == 1
    # evidence catch auto-resolves true → trust reflects it
    assert r["trust"]["evidence_gate"][cq.TRUE_CATCH] == 1


# ---------- CLI ----------

def test_cli_empty_ledger():
    _mktmp_project()
    out = _capture_stdout(lambda: report.run())
    assert "ledger is empty" in out


def test_cli_text_has_all_sections():
    tmp = _mktmp_project()
    _write_ledger(tmp, _seed())
    out = _capture_stdout(lambda: report.run())
    for section in ("TRUST", "VALUE", "IMPROVEMENT", "value chain"):
        assert section in out
    assert "phones home" not in out or "never phones home" in out  # the no-telemetry note


def test_cli_json_shape():
    tmp = _mktmp_project()
    _write_ledger(tmp, _seed())
    out = _capture_stdout(lambda: report.run(json_output=True))
    d = json.loads(out)
    assert d["journeys"] == 2 and d["completion_pct"] == 50
    assert "trust" in d and "phases" in d


# ---------- opt-in export (v0.55.0) ----------

# a deliberately distinctive feature name — the leak canary for the
# anonymization contract. If it ever appears in the export, privacy is broken.
_CANARY = "field-stripe-billing-x9z"


def _seed_named():
    recs = _seed()
    for r in recs:
        if r.get("feature") == "b":
            r["feature"] = _CANARY
    return recs


def test_export_payload_shape_and_provenance():
    from pathlib import Path
    p = report.export_payload(_seed_named(), "An adopter", Path("/tmp/whatever"))
    assert p["schema_version"] == report.EXPORT_SCHEMA_VERSION
    assert p["product"] == "An adopter"
    assert len(p["product_hash"]) == 12 and all(c in "0123456789abcdef"
                                                for c in p["product_hash"])
    # as_of / window derive from ledger timestamps, not wall-clock
    assert p["as_of"] == "2026-06-01T09:30:00+00:00"
    assert p["window"]["first_event"] == "2026-06-01T00:00:00+00:00"
    m = p["metrics"]
    assert m["journeys"] == 2 and m["completed"] == 1
    assert m["open_feature_count"] == 1          # COUNT, not names
    assert "open_features" not in m              # names never exported
    assert "trust" in m and "phases" in m


def test_export_is_anonymized_no_feature_names_leak():
    from pathlib import Path
    p = report.export_payload(_seed_named(), "An adopter", Path("/tmp/whatever"))
    blob = json.dumps(p)
    assert _CANARY not in blob, "feature name leaked into the export artifact"


def test_export_carries_anonymized_feedback():
    """C3: filed findings ride the export to HQ — authored title + metadata kept,
    but `feature` (product intent) and `detail` (verbatim → paths/secrets) DROPPED."""
    import shutil

    from prusik import feedback
    tmp = _mktmp_project()
    try:
        feedback.file_feedback(
            tmp, "bug", "scoped coverage false-fails",
            detail=f"verbatim: /Users/secret/{_CANARY}/path errored", severity="high")
        p = report.export_payload(_seed(), "x", tmp)
        assert p["metrics"]["feedback_count"] == 1
        fb = p["feedback"][0]
        assert fb["kind"] == "bug" and fb["severity"] == "high"
        assert fb["title"] == "scoped coverage false-fails"
        assert fb["has_detail"] is True            # repro exists locally
        assert "detail" not in fb and "feature" not in fb
        assert fb["content_hash"] and fb["id"].startswith("fb-")
        # the verbatim detail (path + canary) never leaves the machine
        blob = json.dumps(p)
        assert "/Users/secret/" not in blob and _CANARY not in blob
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_full_detail_carries_everything_no_anonymization():
    """HQ-internal `--full`: anonymization is a PUBLIC-prusik concern, so for a
    product HQ owns the export carries the real feature, verbatim detail, repro,
    and open-feature NAMES — none of which the default (shareable) export leaks."""
    import shutil

    from prusik import feedback
    tmp = _mktmp_project()
    try:
        feedback.file_feedback(
            tmp, "bug", "a finding",
            detail="verbatim /Users/secret/repro.log errored", severity="high")
        # default = anonymized: feature + open-feature names dropped
        anon = report.export_payload(_seed_named(), "x", tmp)
        assert "feature" not in anon["feedback"][0]
        assert "open_features" not in anon["metrics"]
        assert "verbatim /Users/secret" not in json.dumps(anon)
        # --full = HQ-internal: everything carried
        full = report.export_payload(_seed_named(), "x", tmp, full_detail=True)
        fb = full["feedback"][0]
        assert "feature" in fb and "repro" in fb and fb.get("detail")
        assert "open_features" in full["metrics"]
        assert "verbatim /Users/secret/repro.log" in json.dumps(full)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_feedback_empty_when_none_filed():
    from pathlib import Path
    p = report.export_payload(_seed_named(), "An adopter", Path("/tmp/whatever"))
    assert p["feedback"] == [] and p["metrics"]["feedback_count"] == 0


def test_product_hash_is_stable_and_one_way():
    from pathlib import Path
    root = Path("/some/private/repo/path")
    a = report.export_payload(_seed(), "x", root)["product_hash"]
    b = report.export_payload(_seed(), "x", root)["product_hash"]
    assert a == b                                 # stable per repo
    assert "private" not in a and str(root) not in json.dumps(
        report.export_payload(_seed(), "x", root))  # path not leaked


def test_cli_export_writes_default_file():
    tmp = _mktmp_project()
    _write_ledger(tmp, _seed_named())
    out = _capture_stdout(lambda: report.run(export_artifact=True, product="An adopter"))
    assert "wrote anonymized export" in out
    f = tmp / ".sprint" / "report-export.json"
    assert f.exists()
    d = json.loads(f.read_text())
    assert d["product"] == "An adopter" and d["metrics"]["journeys"] == 2
    assert _CANARY not in f.read_text()           # canary on the written file too


def test_cli_export_stdout():
    tmp = _mktmp_project()
    _write_ledger(tmp, _seed_named())
    out = _capture_stdout(lambda: report.run(export_artifact=True,
                                             to_stdout=True))
    d = json.loads(out)
    assert d["product"] == "unnamed-product"      # default label
    assert not (tmp / ".sprint" / "report-export.json").exists()  # nothing written


def test_cli_export_empty_ledger():
    _mktmp_project()
    out = _capture_stdout(lambda: report.run(export_artifact=True))
    assert "nothing to export" in out


# ---------- A1: time-series (the snapshot→trends upgrade) ----------

def test_timeseries_buckets_by_month():
    recs = [
        {"ts": "2026-04-03T10:00:00+00:00", "event": "sprint_started", "feature": "a"},
        {"ts": "2026-04-20T10:00:00+00:00", "event": "sprint_complete", "feature": "a"},
        {"ts": "2026-05-02T10:00:00+00:00", "event": "sprint_started", "feature": "b"},
        {"ts": "2026-05-09T10:00:00+00:00", "event": "phase_advance", "feature": "b"},
    ]
    ts = report.timeseries(recs)
    assert [r["period"] for r in ts] == ["2026-04", "2026-05"]
    apr, may = ts
    assert apr["started"] == 1 and apr["completed"] == 1 and apr["events"] == 2
    assert may["started"] == 1 and may["completed"] == 0 and may["events"] == 2


def test_export_carries_timeseries():
    from pathlib import Path
    p = report.export_payload(_seed(), "x", Path("/tmp/whatever"))
    assert "timeseries" in p and isinstance(p["timeseries"], list)
    assert all("period" in b and "started" in b for b in p["timeseries"])
