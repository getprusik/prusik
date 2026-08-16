"""fb-efb97d03d9d9 — prusik bounces is cumulative over the whole ledger, so a remedy
rewrite's effect drowns: one post-fix sprint's few events can't move a rate computed
over dozens of sprints (49% before a fix, 49% after). `--since DATE` windows block
events to the post-fix cohort so a rewrite is measured as a clean before/after.

moat-finding: fb-efb97d03d9d9
"""

from __future__ import annotations

from datetime import timezone

from prusik import bounce


def _blk(feature, cls_field, ts):
    # a classifiable advance_blocked event: 'missing' → unmet_exit_artifacts, etc.
    return {"event": "advance_blocked", "feature": feature, "ts": ts, **cls_field}


_PRE = "2026-08-01T10:00:00+00:00"
_POST = "2026-08-16T10:00:00+00:00"
_MISSING = {"missing": ["x"]}                 # → unmet_exit_artifacts
_INCONS = {"inconsistencies": ["y"]}          # → cross_artifact_inconsistency


def test_parse_since_accepts_date_and_rejects_version():
    dt = bounce._parse_since("2026-08-15")
    assert dt.year == 2026 and dt.month == 8 and dt.day == 15
    assert dt.tzinfo is not None                       # naive date read as UTC-aware
    assert bounce._parse_since("2026-08-15T12:30:00+00:00").tzinfo == timezone.utc
    for bad in ("0.211.0", "v0.211.0", "0.211"):
        try:
            bounce._parse_since(bad)
            assert False, f"{bad} should be rejected as version-shaped"
        except ValueError as e:
            assert "DATE, not a version" in str(e)


def test_filter_since_keeps_post_cutoff_and_drops_tsless():
    cutoff = bounce._parse_since("2026-08-15")
    events = [_blk("a", _MISSING, _PRE), _blk("b", _MISSING, _POST),
              {"event": "advance_blocked", "feature": "c", "missing": ["z"]}]  # no ts
    kept = bounce._filter_since(events, cutoff)
    assert [e["feature"] for e in kept] == ["b"]        # pre-cutoff and ts-less excluded


def test_window_isolates_the_post_fix_cohort_from_frozen_history():
    # THE POINT: a class that re-bounced heavily in HISTORY but not since the fix must
    # read clean in the window — the frozen cumulative rate hides exactly this.
    events = (
        [_blk("old1", _INCONS, _PRE) for _ in range(6)]        # cross_artifact: pre-fix noise
        + [_blk("new", _MISSING, _POST), _blk("new", _MISSING, _POST)])  # post: unmet re-bounced
    full = bounce.analyze(events)
    windowed = bounce.analyze(bounce._filter_since(events, bounce._parse_since("2026-08-15")))
    # full ledger is dominated by cross_artifact; the window shows ONLY the post-fix cohort
    assert full["total_block_events"] == 8
    assert windowed["total_block_events"] == 2
    cls = {c["gate_class"]: c for c in windowed["by_class"]}
    assert set(cls) == {"unmet_exit_artifacts"}            # cross_artifact absent post-fix
    assert cls["unmet_exit_artifacts"]["rebounces"] == 1


def test_run_since_reports_window_and_rejects_version(tmp_path, capsys):
    lp = tmp_path / "ledger.jsonl"
    import json
    lp.write_text("\n".join(json.dumps(e) for e in
                            [_blk("a", _MISSING, _PRE), _blk("b", _MISSING, _POST)]))
    assert bounce.run(ledger_path=str(lp), since="2026-08-15") == 0
    out = capsys.readouterr().out
    assert "window: since 2026-08-15" in out and "1 of 2 block events" in out
    # version-shaped --since exits 2 with the helpful message
    assert bounce.run(ledger_path=str(lp), since="0.211.0") == 2
    assert "DATE, not a version" in capsys.readouterr().out
