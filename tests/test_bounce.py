"""Repeat-bounce report — the remedy-quality lens. Pins the bounce math (a
re-bounce is every fire of a (sprint, gate-class) pair beyond the first) and
that classification flows from gate_class (explicit key or derived history)."""

from __future__ import annotations

from prusik import bounce, gate_class as gc


def _blk(event, feature, **kw):
    return {"event": event, "feature": feature, **kw}


def test_empty_ledger_reports_nothing():
    a = bounce.analyze([])
    assert a["total_block_events"] == 0
    assert "no block events" in bounce._render(a)


def test_non_block_events_are_ignored():
    a = bounce.analyze([{"event": "phase_advance", "feature": "f"},
                        {"event": "prove_run", "feature": "f"}])
    assert a["total_block_events"] == 0


def test_rebounce_is_every_fire_beyond_the_first_per_pair():
    # feature A: writable_scope fires 3× (2 wasted) + unmet once (0 wasted).
    # feature B: writable_scope fires 1× (0 wasted). One pair re-bounced of three.
    events = [
        _blk("gate_blocked", "A", gate_class=gc.WRITABLE_SCOPE),
        _blk("gate_blocked", "A", gate_class=gc.WRITABLE_SCOPE),
        _blk("gate_blocked", "A", gate_class=gc.WRITABLE_SCOPE),
        _blk("advance_blocked", "A", gate_class=gc.UNMET_EXIT_ARTIFACTS),
        _blk("gate_blocked", "B", gate_class=gc.WRITABLE_SCOPE),
    ]
    a = bounce.analyze(events)
    assert a["total_block_events"] == 5
    assert a["sprint_gate_pairs"] == 3            # (A,writable),(A,unmet),(B,writable)
    assert a["repeat_bounce_pairs"] == 1          # only (A,writable) fired ≥2
    assert a["wasted_rebounces"] == 2             # 3 fires - 1 = 2
    # writable_scope: 4 fires across 2 sprints, 2 wasted, bounced in 1 sprint
    ws = next(r for r in a["by_class"] if r["gate_class"] == gc.WRITABLE_SCOPE)
    assert (ws["fires"], ws["rebounces"], ws["sprints"], ws["bounced_sprints"]) == (4, 2, 2, 1)
    # worst-remedy-first ordering: writable_scope (2 wasted) leads unmet (0).
    assert a["by_class"][0]["gate_class"] == gc.WRITABLE_SCOPE


def test_classification_uses_gate_class_including_derived_history():
    # A pre-field advance_blocked (no gate_class, only `missing=`) must classify
    # as unmet_exit_artifacts and bounce against an explicitly-keyed twin.
    events = [
        _blk("advance_blocked", "A", missing=["scope.md"]),          # derived
        _blk("advance_blocked", "A", gate_class=gc.UNMET_EXIT_ARTIFACTS),  # explicit
    ]
    a = bounce.analyze(events)
    assert a["repeat_bounce_pairs"] == 1          # same class → same pair → re-bounce
    assert a["wasted_rebounces"] == 1


def test_render_names_the_top_class_and_rate():
    events = [_blk("gate_blocked", "A", gate_class=gc.WRITABLE_SCOPE) for _ in range(3)]
    out = bounce._render(bounce.analyze(events))
    assert "writable_scope" in out
    assert "67%" in out                            # 2 wasted / 3 fires
