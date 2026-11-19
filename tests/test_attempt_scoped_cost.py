"""A sprint's cost counts only the current attempt, not every attempt under the feature name.

`usage.measure(feature)` summed every phase window the feature ever had, so a re-started
feature was charged for its dead attempt: the observed-writes redo escalated `budget_exceeded`
in scoping at $31.34 against $19, about $29 of it the attempt abandoned 2026-11-16. An attempt
is one sprint, split at `sprint_abandoned` / `sprint_complete`; caps and reports measure it
alone, earlier attempts are reported beside it, and `--all-attempts` totals them all.

Real ledgers and transcripts throughout (no measure stub on the asserted path).

moat-finding: fb-cb855f56b857
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from prusik import effort, ledger, usage
from tests import test_measured_token_usage as mtu
from tests.test_cost_budget import _cost_cli, cproj  # noqa: F401 — fixture
from tests.test_sprint_drive import F, proj, rig  # noqa: F401 — fixtures: `rig` needs `proj`

_ROW = mtu._ROW_COST


def _ev(ts, event, feature="feat", **kw):
    return {"ts": f"2026-10-26T{ts}:00+00:00", "event": event, "feature": feature, **kw}


def _adv(ts, a, b):
    return _ev(ts, "phase_advance", from_phase=a, to_phase=b)


# attempt 0: 10:00-10:20, abandoned · gap · attempt 1: briefing 10:25, sprint 10:30, open
_ABANDONED_THEN_RESTARTED = [
    _ev("10:00", "sprint_started"), _adv("10:10", "scoping", "building"),
    _ev("10:20", "sprint_abandoned"),
    _ev("10:25", "briefing_started"), _ev("10:30", "sprint_started"),
    _adv("10:40", "scoping", "building"),
]
# rows: two in attempt 0, one between attempts (no window), one in the new briefing, two after
_ROWS = [("a1", "10:05"), ("a2", "10:15"), ("gap", "10:22"),
         ("b1", "10:27"), ("b2", "10:35"), ("b3", "10:45")]


def _real_measure(monkeypatch, pinned=None):
    """usage.measure without the fixtures' stub, its `now` pinned when given."""
    def measure(f, phase=None, now=None, session=None, attempts="current"):
        return usage._measure(f, phase, pinned or now, session, attempts)
    monkeypatch.setattr(usage, "measure", measure)


def _setup(cproj, tmp_path, events, rows=_ROWS):
    mtu._ledger(cproj, events)
    t = tmp_path / "cc" / "s1.jsonl"
    mtu._write_jsonl(t, [mtu._row(mid, f"2026-10-26T{ts}:00Z") for mid, ts in rows])
    mtu._register("s1", t)


# ---- one definition of an attempt ----------------------------------------------------------

def test_the_walk_splits_attempts_at_abandon_and_the_briefing_joins_the_new_attempt():
    ws = effort.phase_windows(_ABANDONED_THEN_RESTARTED, "feat")
    assert [(w["phase"], w["attempt"]) for w in ws] == [
        ("scoping", 0), ("building", 0), ("briefing", 1), ("scoping", 1), ("building", 1)]
    cur = effort.phase_windows(_ABANDONED_THEN_RESTARTED, "feat", attempts="current")
    assert [w["phase"] for w in cur] == ["briefing", "scoping", "building"]
    assert cur[0]["start"].startswith("2026-10-26T10:25")       # the new briefing, unclipped
    prior = effort.phase_windows(_ABANDONED_THEN_RESTARTED, "feat", attempts="prior")
    assert [w["phase"] for w in prior] == ["scoping", "building"]
    assert effort.prior_attempts(_ABANDONED_THEN_RESTARTED, "feat") == 1


def test_a_re_start_without_abandon_stays_one_attempt():
    evs = [_ev("10:00", "sprint_started"), _ev("10:05", "sprint_started"),
           _adv("10:10", "scoping", "building")]
    assert {w["attempt"] for w in effort.phase_windows(evs, "feat")} == {0}
    assert effort.prior_attempts(evs, "feat") == 0


def test_an_attempt_scope_outside_the_three_is_refused():
    with pytest.raises(ValueError):
        effort.phase_windows(_ABANDONED_THEN_RESTARTED, "feat", attempts="latest")


def test_effort_rollup_still_counts_every_attempt():
    feats = effort.summarize_features(_ABANDONED_THEN_RESTARTED)
    assert feats["feat"]["phases"]["building"] == 10 * 60     # attempt 0's closed building
    assert feats["feat"]["phases"]["scoping"] == 20 * 60      # both attempts' scoping


# ---- measure: current by default, all on request ----------------------------------------

def test_measure_charges_only_the_current_attempt_and_all_attempts_sums_both(cproj, tmp_path):
    _setup(cproj, tmp_path, _ABANDONED_THEN_RESTARTED)
    cur = usage.measure("feat", now=mtu._NOW)
    prior = usage.measure("feat", now=mtu._NOW, attempts="prior")
    every = usage.measure("feat", now=mtu._NOW, attempts="all")
    assert cur.measured and prior.measured and every.measured
    assert cur.cost_usd == pytest.approx(3 * _ROW)                    # b1, b2, b3
    assert cur.cost_by_phase == pytest.approx({"briefing": _ROW, "scoping": _ROW,
                                               "building": _ROW})
    assert prior.cost_usd == pytest.approx(2 * _ROW)                  # a1, a2
    assert every.cost_usd == pytest.approx(cur.cost_usd + prior.cost_usd)   # the gap row: none
    assert every.tokens == cur.tokens + prior.tokens == 5 * mtu._UNIT


def test_a_completed_then_re_run_feature_splits_the_same_way(cproj, tmp_path):
    evs = [e if e["event"] != "sprint_abandoned" else {**e, "event": "sprint_complete"}
           for e in _ABANDONED_THEN_RESTARTED]
    _setup(cproj, tmp_path, evs)
    assert usage.measure("feat", now=mtu._NOW).cost_usd == pytest.approx(3 * _ROW)
    assert usage.measure("feat", now=mtu._NOW, attempts="all").cost_usd == pytest.approx(5 * _ROW)


def test_a_single_attempt_feature_measures_exactly_what_it_did(cproj, tmp_path):
    """mtu._journey: one attempt, scoping 10:00 → reviewing open. Same numbers both scopes."""
    rows = [("x", "10:05"), ("y", "10:15"), ("z", "10:45")]
    _setup(cproj, tmp_path, mtu._journey(), rows)
    cur = usage.measure("feat", now=mtu._NOW)
    every = usage.measure("feat", now=mtu._NOW, attempts="all")
    assert cur.cost_usd == pytest.approx(3 * _ROW) and cur.tokens == 3 * mtu._UNIT
    assert usage.spend_data(cur) == usage.spend_data(every)
    assert usage.prior_attempts_line("feat", now=mtu._NOW) is None


def test_a_feature_with_no_window_is_unmeasured_never_zero(cproj, tmp_path):
    _setup(cproj, tmp_path, _ABANDONED_THEN_RESTARTED)
    u = usage.measure("other", now=mtu._NOW)
    assert not u.measured and "'other'" in u.reason


def test_the_finished_attempt_is_the_current_one_after_it_ends(cproj, tmp_path):
    """After abandon with no restart, the attempt that just ended is the current one."""
    _setup(cproj, tmp_path, _ABANDONED_THEN_RESTARTED[:3])
    u = usage.measure("feat", now=mtu._NOW)
    assert u.measured and u.cost_usd == pytest.approx(2 * _ROW)


# ---- prusik cost -------------------------------------------------------------------------

def test_prusik_cost_shows_the_new_attempt_plus_a_prior_attempts_line(cproj, tmp_path, capsys,
                                                                      monkeypatch):
    _setup(cproj, tmp_path, _ABANDONED_THEN_RESTARTED)
    _real_measure(monkeypatch, pinned=mtu._NOW)
    capsys.readouterr()
    assert _cost_cli(["feat", "--json"]) == 0
    cur = json.loads(capsys.readouterr().out)
    assert cur["attempts"] == "current" and cur["cost_usd"] == pytest.approx(3 * _ROW, abs=1e-6)
    assert _cost_cli(["feat", "--all-attempts", "--json"]) == 0
    every = json.loads(capsys.readouterr().out)
    assert every["attempts"] == "all" and every["cost_usd"] == pytest.approx(5 * _ROW, abs=1e-6)
    assert _cost_cli(["feat"]) == 0
    out = capsys.readouterr().out
    assert "earlier attempts: 1" in out and "not counted against the cap" in out
    assert effort.fmt_usd(2 * _ROW) in out.split("earlier attempts")[1]
    assert _cost_cli(["feat", "--all-attempts"]) == 0
    out = capsys.readouterr().out
    assert "(every attempt)" in out and "earlier attempts" not in out


# ---- drive: the dead attempt is not charged, the cap is not loosened ----------------------

def _with_dead_attempt(proj, rows_now):
    """Prepend an attempt abandoned an hour ago that spent $20 (over the $19 cap), and a
    transcript with that spend plus `rows_now` (output tokens) in the current attempt."""
    recs = ledger.read_all()
    start = recs[-1]["ts"]                                       # the rig's sprint_started
    t0 = datetime.fromisoformat(start) - timedelta(hours=1)
    iso = lambda d: d.astimezone(timezone.utc).isoformat()       # noqa: E731
    dead = [{"ts": iso(t0), "event": "sprint_started", "feature": F},
            {"ts": iso(t0 + timedelta(minutes=5)), "event": "phase_advance",
             "from_phase": "scoping", "to_phase": "building", "feature": F},
            {"ts": iso(t0 + timedelta(minutes=30)), "event": "sprint_abandoned", "feature": F}]
    with open(ledger.ledger_path(), "w") as f:
        for e in dead + recs:
            f.write(json.dumps(e) + "\n")
    t = proj / "cc" / "s.jsonl"
    old = iso(t0 + timedelta(minutes=10)).replace("+00:00", "Z")
    mtu._write_jsonl(t, [mtu._row("dead", old, out=1_000_000)]
                     + [mtu._row(f"n{i}", start, out=o) for i, o in enumerate(rows_now)])
    usage.registry_path().write_text(json.dumps({"s": {
        "transcript_path": str(t), "first_seen": iso(t0), "last_seen": start}}))


def test_drive_does_not_charge_the_abandoned_attempt(rig, proj, monkeypatch):
    _real_measure(monkeypatch)
    _with_dead_attempt(proj, rows_now=[10])
    assert usage.measure(F, attempts="prior").cost_usd > 19         # the dead spend is real
    assert rig.run() == 0, rig.escalation()
    assert [r.role for r in rig.adapter.requests]


def test_spend_in_the_new_attempt_over_the_cap_still_escalates(rig, proj, monkeypatch):
    _real_measure(monkeypatch)
    _with_dead_attempt(proj, rows_now=[1_000_000])                   # $20 in THIS attempt
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "budget_exceeded" and "drive.budget_usd" in esc["reason"]
    assert "$19.00" in esc["reason"]
    prior = [e for e in esc["evidence"] if e.startswith("earlier attempts: 1")]
    assert prior and "not counted against the cap" in prior[0]
    assert rig.adapter.requests == []
