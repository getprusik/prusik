"""Acceptance suite for `prusik overhead` — the cost side of overhead-to-catch.

moat-finding:fb-e7fe8177cc8c — operator-felt sprint slowness had no receipt:
`effort` (v0.47.0) reports time-per-phase but nothing reported per-gate
block→retry cost, idle-vs-active honesty, or the mechanical hook tax, and the
empty state exited 0 (nothing-to-measure must never read as zero overhead).
"""

import json

from prusik import overhead


def _ln(ts: str, event: str, **fields) -> str:
    return json.dumps({"ts": ts, "event": event, **fields})


def _write_ledger(root, lines):
    d = root / ".sprint"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ledger.jsonl").write_text("\n".join(lines) + "\n")
    return d / "ledger.jsonl"


# One sprint: scoping 120s → building (block at +8m, retried 90s later;
# advance_blocked at +18m, resolved 60s later) → reviewing → complete.
BASE = [
    _ln("2026-08-01T10:00:00+00:00", "sprint_started", feature="demo"),
    _ln("2026-08-01T10:02:00+00:00", "phase_advance",
        from_phase="scoping", to_phase="building", feature="demo"),
    _ln("2026-08-01T10:10:00+00:00", "gate_blocked", tool="Write",
        phase="building", feature="demo", reason="outside writable scope"),
    _ln("2026-08-01T10:11:30+00:00", "prove_run", kind="tests",
        exit_code=0, executed=5, proven=True),
    _ln("2026-08-01T10:20:00+00:00", "advance_blocked", from_phase="building",
        to_phase="reviewing", feature="demo", missing=["x"]),
    _ln("2026-08-01T10:21:00+00:00", "phase_advance",
        from_phase="building", to_phase="reviewing", feature="demo"),
    _ln("2026-08-01T10:30:00+00:00", "sprint_complete", feature="demo"),
]


def _analysis(lines=BASE, **kw):
    events, skipped = overhead.read_events_text("\n".join(lines))
    return overhead.analyze(events, skipped_lines=skipped, **kw)


def test_phase_breakdown_matches_hand_computed():
    a = _analysis()
    by = {p["phase"]: p for p in a["phases"]}
    assert by["scoping"]["seconds"] == 120
    assert by["building"]["seconds"] == 19 * 60
    assert by["reviewing"]["seconds"] == 9 * 60
    assert a["totals"]["elapsed_seconds"] == 30 * 60


def test_gate_retry_cost_grouped_per_gate():
    a = _analysis()
    by = {g["gate"]: g for g in a["gates"]}
    wr = by["gate_blocked: outside writable scope"]
    assert (wr["blocks"], wr["retry_seconds"]) == (1, 90)
    adv = by["advance_blocked: reviewing"]
    assert (adv["blocks"], adv["retry_seconds"]) == (1, 60)
    assert a["totals"]["gate_overhead_seconds"] == 150


def test_idle_gap_split_out_of_phase_seconds():
    lines = [
        _ln("2026-08-01T10:00:00+00:00", "sprint_started", feature="demo"),
        _ln("2026-08-01T10:05:00+00:00", "prove_run", kind="tests",
            exit_code=0, executed=1, proven=True),
        # 2h silence — human left; must not read as scoping work
        _ln("2026-08-01T12:05:00+00:00", "phase_advance",
            from_phase="scoping", to_phase="building", feature="demo"),
        _ln("2026-08-01T12:06:00+00:00", "sprint_complete", feature="demo"),
    ]
    a = _analysis(lines, idle_min=30)
    sc = {p["phase"]: p for p in a["phases"]}["scoping"]
    assert sc["idle_seconds"] == 2 * 3600
    assert sc["seconds"] == 5 * 60          # active only
    assert a["totals"]["idle_seconds"] == 2 * 3600
    assert a["totals"]["elapsed_seconds"] == 2 * 3600 + 6 * 60


def test_abandoned_block_retry_capped_at_idle_threshold():
    lines = [
        _ln("2026-08-01T10:00:00+00:00", "sprint_started", feature="demo"),
        _ln("2026-08-01T10:10:00+00:00", "gate_blocked", tool="Write",
            phase="scoping", feature="demo", reason="outside writable scope"),
        # session abandoned; next activity a day later — idle, not retry cost
        _ln("2026-08-02T10:10:00+00:00", "sprint_complete", feature="demo"),
    ]
    a = _analysis(lines, idle_min=30)
    (g,) = a["gates"]
    assert g["retry_seconds"] == 30 * 60


def test_fix_round_loop_cost():
    lines = BASE[:-1] + [
        _ln("2026-08-01T10:22:00+00:00", "fix_round_start", feature="demo"),
        _ln("2026-08-01T10:25:00+00:00", "fix_round_end", feature="demo"),
        BASE[-1],
    ]
    a = _analysis(lines)
    assert a["fix_rounds"] == {"rounds": 1, "seconds": 180}


def test_json_and_text_agree(tmp_path, monkeypatch, capsys):
    _write_ledger(tmp_path, BASE)
    monkeypatch.chdir(tmp_path)
    assert overhead.run(json_output=True) == 0
    d = json.loads(capsys.readouterr().out)
    assert set(d) >= {"phases", "gates", "fix_rounds", "totals"}
    assert overhead.run() == 0
    text = capsys.readouterr().out
    assert "building" in text and "19m" in text


def test_read_only(tmp_path, monkeypatch, capsys):
    p = _write_ledger(tmp_path, BASE)
    monkeypatch.chdir(tmp_path)
    before = p.read_bytes()
    listing = sorted(x.name for x in (tmp_path / ".sprint").iterdir())
    assert overhead.run() == 0
    assert p.read_bytes() == before
    assert sorted(x.name for x in (tmp_path / ".sprint").iterdir()) == listing


def test_nothing_to_measure_is_not_zero_overhead(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert overhead.run() == 1
    assert "nothing to measure" in capsys.readouterr().out.lower()
    assert overhead.run(json_output=True) == 1


def test_tolerates_unknown_and_malformed_lines(tmp_path, monkeypatch, capsys):
    lines = BASE + [_ln("2026-08-01T10:31:00+00:00", "future_event_type",
                        payload="?"), "{not json"]
    _write_ledger(tmp_path, lines)
    monkeypatch.chdir(tmp_path)
    assert overhead.run(json_output=True) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["skipped_lines"] == 1              # surfaced, never silent


def test_explicit_ledger_path_backtest(tmp_path, capsys):
    p = _write_ledger(tmp_path, BASE)
    copy = tmp_path / "field-ledger-copy.jsonl"
    copy.write_bytes(p.read_bytes())
    assert overhead.run(json_output=True, ledger_path=str(copy)) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["elapsed_seconds"] == 1800


def test_hook_bench_measures_real_entrypoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                 # no sprint-config: benign no-op
    r = overhead.hook_bench(n=3)
    assert set(r) == {"median_ms", "p90_ms", "n"}
    assert r["n"] == 3 and r["median_ms"] > 0
