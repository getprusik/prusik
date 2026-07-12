"""Effort-telemetry lens — span derivation, per-feature cost, CLI (v0.47.0)."""

from __future__ import annotations

import json

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _mktmp_project,
    _write_ledger,
)
from prusik import effort


def _journey(feature="feat", *, complete=True, rewind=False):
    """A minimal full journey: started → through phases → (optionally) complete.
    One minute per phase so durations are exact and easy to assert."""
    ev = [
        {"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
         "feature": feature},
        {"ts": "2026-06-01T00:01:00+00:00", "event": "phase_advance",
         "from_phase": "scoping", "to_phase": "planning", "feature": feature},
        {"ts": "2026-06-01T00:02:00+00:00", "event": "phase_advance",
         "from_phase": "planning", "to_phase": "building", "feature": feature},
    ]
    if rewind:
        ev.append({"ts": "2026-06-01T00:03:00+00:00", "event": "phase_rewind",
                   "from_phase": "building", "to_phase": "planning",
                   "feature": feature})
        ev.append({"ts": "2026-06-01T00:04:00+00:00", "event": "phase_advance",
                   "from_phase": "planning", "to_phase": "building",
                   "feature": feature})
    if complete:
        last = "00:05:00" if rewind else "00:03:00"
        ev.append({"ts": f"2026-06-01T{last}+00:00", "event": "sprint_complete",
                   "feature": feature, "actual": {"tokens": 12345,
                                                  "duration_min": 5}})
    return ev


# ---------- span derivation ----------

def test_spans_tile_the_timeline_by_phase():
    spans = effort.extract_spans(_journey())
    # scoping(0→1m), planning(1→2m), building(2→3m) = 3 spans of 60s each
    assert [s["phase"] for s in spans] == ["scoping", "planning", "building"]
    assert all(s["seconds"] == 60 for s in spans)


def test_first_span_starts_at_sprint_started_not_an_advance():
    spans = effort.extract_spans(_journey())
    assert spans[0]["phase"] == "scoping"
    assert spans[0]["start"] == "2026-06-01T00:00:00+00:00"


def test_rewind_reenters_phase_and_accumulates_time():
    feats = effort.summarize_features(_journey(rewind=True))
    d = feats["feat"]
    # building visited twice (2→3m, then 4→5m) = 120s total
    assert d["phases"]["building"] == 120
    assert d["phases"]["planning"] == 120   # 1→2m and 3→4m
    assert d["rewinds"] == 1


def test_unparseable_ts_keeps_span_untimed_not_crashing():
    # valid start, unparseable end → span is formed but stays untimed (no crash).
    # ("2026…" sorts before "zzz" so ordering is preserved deterministically.)
    spans = effort.extract_spans([
        {"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
         "feature": "x"},
        {"ts": "zzz-not-a-date", "event": "phase_advance",
         "from_phase": "scoping", "to_phase": "planning", "feature": "x"},
    ])
    assert spans[0]["phase"] == "scoping"
    assert spans[0]["seconds"] is None


# ---------- per-feature summary ----------

def test_wall_clock_and_completion():
    feats = effort.summarize_features(_journey())
    d = feats["feat"]
    assert d["wall_clock_sec"] == 180        # 0 → 3m
    assert d["complete"] is True
    assert d["tokens"] == 12345
    assert d["recorded_duration_min"] == 5


def test_open_journey_uses_last_event_as_end():
    feats = effort.summarize_features(_journey(complete=False))
    d = feats["feat"]
    assert d["complete"] is False
    assert d["wall_clock_sec"] == 120        # 0 → last advance at 2m


def test_friction_counts_blocks_and_fix_rounds():
    ev = _journey()
    ev.append({"ts": "2026-06-01T00:02:30+00:00", "event": "gate_blocked",
               "feature": "feat", "phase": "building", "reason": "x"})
    ev.append({"ts": "2026-06-01T00:02:40+00:00", "event": "fix_round_start",
               "feature": "feat", "round": 1})
    feats = effort.summarize_features(ev)
    assert feats["feat"]["blocks"] == 1
    assert feats["feat"]["fix_rounds"] == 1


def test_non_journey_events_without_feature_are_ignored():
    ev = _journey() + [
        {"ts": "2026-06-01T09:00:00+00:00", "event": "serve_brief_authored",
         "slug": "x"},
    ]
    feats = effort.summarize_features(ev)
    assert set(feats) == {"feat"}            # no None-keyed entry


# ---------- per-phase aggregate ----------

def test_phase_aggregate_sums_across_journeys():
    ev = _journey("a") + _journey("b")
    agg = effort.summarize_phases(ev)
    assert agg["scoping"]["visits"] == 2
    assert agg["scoping"]["total_sec"] == 120
    assert agg["scoping"]["mean_sec"] == 60


# ---------- CLI ----------

def test_cli_text_empty_ledger():
    _mktmp_project()
    out = _capture_stdout(lambda: effort.run())
    assert "no sprint journeys" in out


def test_cli_json_shape():
    tmp = _mktmp_project()
    _write_ledger(tmp, _journey())
    out = _capture_stdout(lambda: effort.run(json_output=True))
    data = json.loads(out)
    assert "features" in data and "by_phase" in data
    assert data["by_phase"]["scoping"]["visits"] == 1


def test_cli_text_lists_the_feature():
    tmp = _mktmp_project()
    _write_ledger(tmp, _journey())
    out = _capture_stdout(lambda: effort.run())
    assert "feat" in out
    assert "where the effort goes" in out
