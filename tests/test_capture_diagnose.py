"""The capture-result classifier — the single extensible surface that ended the
recurring evidence-capture finding cluster (exit-127 fb-53f161606abc, turbo
cache-replay fb-b587d8d9b71c, …). A new non-evidence mode is a registered
detector + a name in KNOWN_MODES + a test here — never a new inline branch in
gate.capture(). The completeness test is the forcing function that keeps it that way.

moat-finding: fb-b587d8d9b71c
"""

from __future__ import annotations

from types import SimpleNamespace

from prusik import capture_diagnose as cd
from prusik import gate, schema


def _res(**kw):
    base = dict(kind="tests", exit_code=0, value=5, output="5 passed", command="pytest")
    base.update(kw)
    return cd.CaptureResult(**base)


# ---- registry integrity (the anti-recurrence forcing function) -------------------

def test_known_modes_match_detector_count_and_are_unique():
    # A detector can't be added without a registered name (and therefore without the
    # observability + documented contract), nor a name left orphaned.
    assert len(cd._DETECTORS) == len(cd.KNOWN_MODES)
    assert len(set(cd.KNOWN_MODES)) == len(cd.KNOWN_MODES)


def test_every_emitted_verdict_uses_a_known_mode():
    # Drive each detector to fire and confirm its mode is registered.
    for r in (_res(exit_code=127),
              _res(value=0, output=">>> FULL TURBO")):
        v = cd.diagnose(r)
        assert v is not None and v.mode in cd.KNOWN_MODES


# ---- real execution evidence passes through (no false refusal) -------------------

def test_clean_run_is_evidence():
    assert cd.diagnose(_res()) is None                       # exit 0, value 5 → record it


def test_zero_count_without_a_known_mode_is_not_refused_here():
    # A plain tests=0 (wrong path / auto-skip) is NOT a capture-time non-evidence mode —
    # it's recorded and diagnosed kind-aware at the ADVANCE gate. The classifier must not
    # swallow it (that would change where the diagnosis lives).
    assert cd.diagnose(_res(value=0, output="no tests ran")) is None


# ---- per-mode detectors ----------------------------------------------------------

def test_command_not_found_fires_on_127_only():
    v = cd.diagnose(_res(exit_code=127, value=0, output="bash: pnpm: command not found"))
    assert v.mode == "command_not_found" and v.exit_code == 127
    assert cd.diagnose(_res(exit_code=1)) is None            # a real failure is NOT 127


def test_cache_replay_fires_only_on_zero_count_replay():
    hit = cd.diagnose(_res(value=0, exit_code=0, output=">>> FULL TURBO"))
    assert hit.mode == "cache_replay" and hit.exit_code == 1
    assert "force" in hit.remedy.lower()                     # names the remedy
    # adversarial: a replay whose cached logs carried a REAL count stands (value>0)
    assert cd.diagnose(_res(value=730, output=">>> FULL TURBO\n730 passed")) is None
    # adversarial: a non-zero exit replay is a real failure, not a 0-executed false-clean
    assert cd.diagnose(_res(value=0, exit_code=1, output=">>> FULL TURBO")) is None


# ---- end-to-end through gate.capture: refusal is logged for measurability ---------

def test_capture_refusal_records_a_ledger_event(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rc = gate.capture(SimpleNamespace(
        command=["echo '>>> FULL TURBO'"], reset=False,
        feature="feat", phase="regression", kind="tests"))
    assert rc == 1
    ev = schema.evidence_path_for(tmp_path / "reports" / "feat", "regression")
    assert not ev.exists()                                   # refused, not recorded
    from prusik import ledger
    modes = [r.get("mode") for r in ledger.read_all() if r.get("event") == "capture_non_evidence"]
    assert modes == ["cache_replay"]                         # recurrence is now measurable
