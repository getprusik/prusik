"""The full-suite check names the phase it guards and credits prusik's own captured
full-suite run (fb-6916f692908a)."""
# moat-finding: fb-6916f692908a

from __future__ import annotations

import io
import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import yaml

from tests._common import _mktmp_project, _write_ledger  # noqa: F401,E402
from prusik import gate, ledger, schema, shared_infra, suite_baseline

_ENTERED = "2026-06-05T12:00:00+00:00"
_IN_WINDOW = "2026-06-05T12:30:00+00:00"


def _capture(*, feature="feat", ok=True, nonempty=906, kind="tests", stale=False):
    """A capture as prusik records it: an evidence entry (fb-1f42f449f29d credits capture
    proof by the tree it ran against, not by a ledger window). `stale` stamps an older tree."""
    return {"_capture": True, "feature": feature, "ok": ok, "nonempty": nonempty,
            "kind": kind, "stale": stale}


def _write_capture(proj, c):
    d = proj / "reports" / c["feature"]
    d.mkdir(parents=True, exist_ok=True)
    wt_hash = "f" * 16 if c["stale"] else gate._worktree_substantive_hash(proj)
    schema.evidence_path_for(d, "regression").write_text(json.dumps({
        "schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": [{
            "phase": "regression", "command": "pytest -q",
            "exit_code": 0 if c["ok"] else 1,
            "nonempty_primitive": {"kind": c["kind"], "value": c["nonempty"]},
            "output_sha": "0" * 16, "worktree_hash": wt_hash, "exec_dir": ".",
            "captured_by": schema.EVIDENCE_CAPTURED_BY}]}))


def _setup(proj, *, current, events, require=False, baseline=906):
    os.environ["CLAUDE_PROJECT_DIR"] = str(proj)
    (proj / ".sprint").mkdir(exist_ok=True)
    suite_baseline.update(proj, baseline)
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "sprint-config.yaml").write_text(
        yaml.safe_dump({"require_full_suite_at_build": require}))
    shutil.rmtree(proj / "reports", ignore_errors=True)
    for c in (e for e in events if e.get("_capture")):
        _write_capture(proj, c)
    _write_ledger(proj, [{"ts": _ENTERED, "event": "phase_advance",
                          "to_phase": current, "feature": "feat"}]
                  + [e for e in events if not e.get("_capture")])


def _run(current, target, *, shared=()):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(shared_infra, "touched_shared_test_infra",
                           return_value=list(shared)), \
            redirect_stdout(out), redirect_stderr(err):
        rc = gate._full_suite_gate(current, target, "feat")
    return rc, out.getvalue() + err.getvalue()


def _with_proj(fn):
    proj = _mktmp_project()
    try:
        fn(proj)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_advisory_names_the_reviewing_phase():
    def body(proj):
        _setup(proj, current="reviewing", events=[])
        rc, text = _run("reviewing", "integrating")
        assert rc is None
        assert "this reviewing phase" in text
        assert "this building phase" not in text
    _with_proj(body)


def test_blocked_text_and_ledger_reason_name_the_phase():
    def body(proj):
        _setup(proj, current="reviewing", events=[], require=True)
        rc, text = _run("reviewing", "integrating")
        assert rc == 2
        assert "this reviewing phase" in text
        blocked = [r for r in ledger.read_all() if r.get("event") == "advance_blocked"]
        assert "this reviewing phase" in blocked[-1]["reason"]
    _with_proj(body)


def test_building_exit_still_names_building():
    def body(proj):
        _setup(proj, current="building", events=[])
        _, text = _run("building", "reviewing")
        assert "this building phase" in text
    _with_proj(body)


def test_green_full_capture_is_credited():
    def body(proj):
        _setup(proj, current="reviewing", events=[_capture()], require=True)
        rc, text = _run("reviewing", "integrating")
        assert rc is None
        assert "ADVISORY" not in text and "BLOCKED" not in text
    _with_proj(body)


def test_green_capture_silences_the_advisory():
    def body(proj):
        _setup(proj, current="reviewing", events=[_capture(nonempty=1874)],
               baseline=1874)
        rc, text = _run("reviewing", "integrating")
        assert rc is None and "ADVISORY" not in text
    _with_proj(body)


def test_weaker_captures_are_not_credited():
    cases = {
        "red": _capture(ok=False),
        "other feature": _capture(feature="other"),
        "older tree": _capture(stale=True),
        "subset": _capture(nonempty=133),
        "non-tests kind": _capture(kind="lint"),
    }
    for name, ev in cases.items():
        proj = _mktmp_project()
        try:
            _setup(proj, current="reviewing", events=[ev], require=True)
            rc, _ = _run("reviewing", "integrating")
            assert rc == 2, f"{name} capture was credited"
        finally:
            shutil.rmtree(proj, ignore_errors=True)


def test_capture_before_a_rewind_is_not_credited():
    """reviewing capture → rewind to building → rebuilt → back to reviewing: the old
    capture is of the tree before the rebuild, so it is stale."""
    def body(proj):
        _setup(proj, current="reviewing", require=True, events=[
            _capture(stale=True),
            {"ts": "2026-06-05T12:20:00+00:00", "event": "phase_advance",
             "to_phase": "building", "feature": "feat"},
            {"ts": "2026-06-05T12:40:00+00:00", "event": "phase_advance",
             "to_phase": "reviewing", "feature": "feat"},
        ])
        rc, _ = _run("reviewing", "integrating")
        assert rc == 2
    _with_proj(body)


def test_remedy_names_both_paths():
    def body(proj):
        _setup(proj, current="reviewing", events=[])
        _, advisory = _run("reviewing", "integrating")
        _setup(proj, current="reviewing", events=[], require=True)
        _, blocked = _run("reviewing", "integrating")
        for text in (advisory, blocked):
            assert "prusik prove --kind tests" in text
            assert "prusik gate capture --kind tests" in text
    _with_proj(body)


def test_shared_infra_credited_capture_proceeds_and_subset_blocks():
    shared = [{"file": "tests/conftest.py", "reason": "tier-spanning"}]

    def proceeds(proj):
        _setup(proj, current="reviewing", events=[_capture()])
        assert _run("reviewing", "integrating", shared=shared)[0] is None
    _with_proj(proceeds)

    def blocks(proj):
        _setup(proj, current="reviewing", events=[_capture(nonempty=5)])
        assert _run("reviewing", "integrating", shared=shared)[0] == 2
        blocked = [r for r in ledger.read_all() if r.get("event") == "advance_blocked"]
        assert blocked[-1]["gate_class"] == gate.gate_class.FULL_SUITE_NOT_PROVEN
    _with_proj(blocks)


def test_proven_prove_run_still_credited():
    def body(proj):
        _setup(proj, current="reviewing", require=True, events=[
            {"ts": _IN_WINDOW, "event": "prove_run", "kind": "tests",
             "proven": True, "executed": 906}])
        assert _run("reviewing", "integrating")[0] is None
    _with_proj(body)

