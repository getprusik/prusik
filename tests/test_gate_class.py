"""Stable gate-class labels — the key that makes repeat-bounce measurable.

Two invariants pinned here: (1) `classify` returns a KNOWN class for every block
shape, from the explicit emitted key AND (for pre-field history) from the event's
own fields; (2) EVERY block-event emission site in gate.py declares a
`gate_class=` — the forcing function (analog of capture_diagnose's KNOWN_MODES
parity) that stops a new block site from going un-labeled and silently blinding
the instrument.
"""

from __future__ import annotations

import re
from pathlib import Path

from prusik import gate_class as gc


# ---- registry integrity ----------------------------------------------------

def test_known_classes_unique_and_unclassified_present():
    assert len(set(gc.GATE_CLASSES)) == len(gc.GATE_CLASSES)
    assert gc.UNCLASSIFIED in gc.GATE_CLASSES


def test_all_module_class_constants_are_registered():
    # Every UPPER_SNAKE str constant that looks like a class value must be in
    # GATE_CLASSES — a constant can't be added without registering its name.
    consts = {v for k, v in vars(gc).items()
              if k.isupper() and isinstance(v, str) and k != "BLOCK_EVENTS"}
    assert consts == set(gc.GATE_CLASSES)


# ---- explicit emitted key is authoritative ---------------------------------

def test_explicit_gate_class_wins():
    e = {"event": "advance_blocked", "gate_class": gc.PUSH_OR_PARK,
         "reason": "full-suite not proven: x"}   # reason would derive differently
    assert gc.classify(e) == gc.PUSH_OR_PARK      # explicit key beats the reason


def test_unknown_explicit_class_falls_back_to_derivation():
    # A bogus/renamed explicit value must not leak through — fail toward the
    # known set by deriving from fields instead.
    e = {"event": "advance_blocked", "gate_class": "bogus", "missing": ["x"]}
    assert gc.classify(e) == gc.UNMET_EXIT_ARTIFACTS


# ---- derivation from historical fields (no gate_class present) --------------

def test_derives_every_class_from_pre_field_history():
    cases = [
        ({"event": "gate_blocked",
          "reason": "shared-tree writer lease held by another session"}, gc.WRITER_LEASE),
        ({"event": "gate_blocked",
          "reason": "'reports/x.md' not in writable patterns for phase scoping"},
         gc.WRITABLE_SCOPE),
        ({"event": "gate_blocked",
          "reason": "bash redirect to unwriteable path: /tmp/x (/tmp/x not …)"},
         gc.WRITABLE_SCOPE),
        ({"event": "gate_blocked", "reason": "deny command: git push"}, gc.DENY_COMMAND),
        ({"event": "gate_blocked", "reason": "deny pattern: rm -rf"}, gc.DENY_COMMAND),
        # the reason-LESS advance sites are recovered via structured fields:
        ({"event": "advance_blocked", "missing": ["design/x/scope.md"]},
         gc.UNMET_EXIT_ARTIFACTS),
        ({"event": "advance_blocked", "inconsistencies": ["a", "b"]},
         gc.CROSS_ARTIFACT_INCONSISTENCY),
        ({"event": "advance_blocked", "reason": "full-suite not proven: subset only"},
         gc.FULL_SUITE_NOT_PROVEN),
        ({"event": "advance_blocked", "reason": "rewind without --allow-rewind"},
         gc.REWIND_GUARD),
        ({"event": "advance_blocked", "reason": "push_or_park require: unpushed sprint work"},
         gc.PUSH_OR_PARK),
        ({"event": "sprint_start_blocked", "unmet": ["map_freshness"]}, gc.SPRINT_START_GATE),
    ]
    for e, expected in cases:
        assert gc.classify(e) == expected, e


def test_unrecoverable_history_is_unclassified_never_guessed():
    # A legacy block that recorded nothing identifying stays honest — a guess
    # would pollute the very signal the instrument measures.
    assert gc.classify({"event": "advance_blocked"}) == gc.UNCLASSIFIED
    assert gc.classify({"event": "gate_blocked", "reason": "something novel"}) == gc.UNCLASSIFIED


def test_non_block_event_is_unclassified():
    assert gc.classify({"event": "phase_advance"}) == gc.UNCLASSIFIED


# ---- the forcing function: every emission site is labeled -------------------

def test_every_block_emission_site_declares_a_valid_gate_class():
    src = (Path(__file__).resolve().parent.parent / "prusik" / "gate.py").read_text()
    lines = src.splitlines()
    valid = {f"gate_class.{k}" for k, v in vars(gc).items()
             if k.isupper() and isinstance(v, str) and v in gc.GATE_CLASSES}
    sites = 0
    for i, ln in enumerate(lines):
        m = re.search(r'ledger\.append\("(gate_blocked|advance_blocked|sprint_start_blocked)"', ln)
        if not m:
            continue
        sites += 1
        window = "\n".join(lines[i:i + 7])
        assert "gate_class=gate_class." in window, \
            f"{m.group(1)} at line {i+1} has no gate_class= label"
        assert any(v in window for v in valid), \
            f"{m.group(1)} at line {i+1} labels a class outside GATE_CLASSES"
    assert sites >= 12, f"expected the known block sites, found {sites}"
