"""drive is run from a plain shell and every stop is a hand-off a fresh session can pick up.

moat-finding: fb-d50f2764de21

A shell-hosted drive held its lease as a random `drive-<uuid>` that outlived the process, so the
next run (or a fresh session writing the fix) was `writer_busy` for up to 30 minutes; and each
escalation's remedy lived only in /sprint-run prose. Adversarial cases: a lease drive does not
hold (taken mid-run, or held before drive started) must survive drive's exit untouched.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from prusik import drive, gate, ledger, main_writer, phases
from tests.test_sprint_drive import (  # noqa: F401
    F, _measuring, _usage, proj, rig, set_config,
)

_SPRINT_RUN = (Path(drive.__file__).parent / "templates" / ".claude" / "commands"
               / "sprint-run.md")


def _shell(rig):
    rig.adapter.current_session_id = lambda: None


def _released():
    return [e for e in ledger.read_all() if e["event"] == "writer_lease_released"]


# ── the lease is released on every exit, and only drive's own ──────────────────────────────

def test_a_shell_run_that_escalates_leaves_no_lease_and_the_next_run_proceeds(rig, proj):
    _shell(rig)
    rig.rcs["prove"] = [1]
    assert rig.run() == 3 and rig.escalation()["kind"] == "suite_failed"
    assert main_writer.load(proj) is None
    rel = _released()
    assert len(rel) == 1 and rel[0]["reason"] == "drive exit"
    assert rel[0]["prior_holder"].startswith("drive-")
    assert rig.run() == 0, rig.out                     # a new drive-<uuid>: not writer_busy
    assert main_writer.load(proj) is None


def test_a_completed_drive_leaves_no_lease(rig, proj):
    assert rig.run() == 0
    assert main_writer.load(proj) is None
    assert [e["prior_holder"] for e in _released()] == ["holder-1"]


def test_a_lease_another_session_took_mid_run_survives_drives_exit(rig, proj):
    def steal(req):
        now = datetime.now(timezone.utc).isoformat()
        main_writer._write(proj, {"session": "other-live", "feature": "x", "phase": "p",
                                  "acquired_at": now, "refreshed_at": now})
    rig.adapter.probe = steal
    rig.run()
    lease = main_writer.load(proj)
    assert lease is not None and lease["session"] == "other-live"
    assert _released() == []


def test_writer_busy_at_start_leaves_the_holders_lease_byte_identical(rig, proj):
    main_writer.check_and_touch(proj, "somebody-else", feature="x", phase="building")
    path = proj / ".sprint" / "main-writer.json"
    before = path.read_bytes()
    assert rig.run() == 3 and rig.escalation()["kind"] == "writer_busy"
    assert path.read_bytes() == before and _released() == []


def test_release_if_held_touches_only_its_own_lease(proj):
    main_writer.check_and_touch(proj, "a", feature=F, phase="building")
    path = proj / ".sprint" / "main-writer.json"
    before = path.read_bytes()
    assert main_writer.release_if_held(proj, "b") is None
    assert path.read_bytes() == before
    assert main_writer.release_if_held(proj, "a")["session"] == "a"
    assert not path.exists()
    assert main_writer.release_if_held(proj, "a") is None          # nothing held: no-op


# ── the escalation file is self-contained ─────────────────────────────────────────────────

def test_every_kind_has_one_remedy():
    assert set(drive._REMEDIES) == set(drive.ESCALATION_KINDS)
    for kind, (text, resume) in drive._REMEDIES.items():
        assert text.strip() and isinstance(resume, bool), kind


def test_a_real_escalation_carries_next_and_resume(rig, proj):
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "suite_failed"
    assert esc["next"] == drive._REMEDIES["suite_failed"][0].replace("<f>", F)
    assert esc["resume"] == "prusik drive feat"
    assert f"next: {esc['next']}" in rig.out and "resume: prusik drive feat" in rig.out


def test_a_user_only_kind_has_no_resume(rig, proj):
    main_writer.check_and_touch(proj, "somebody-else", feature="x", phase="building")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "writer_busy" and esc["resume"] is None
    assert "resume: not until the user decides" in rig.out


def test_the_session_cap_resumes_and_the_sprint_cap_does_not(rig, proj, monkeypatch):
    set_config(proj, suite="true", session_budget_usd=1)
    _measuring(monkeypatch, _usage(3, session={"holder-1": 2}))
    assert rig.run() == 3
    assert rig.escalation()["resume"] == "prusik drive feat"
    _measuring(monkeypatch, _usage(25))
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "budget_exceeded" and "drive.budget_usd" in esc["reason"]
    assert esc["resume"] is None


# ── a fresh session is told ───────────────────────────────────────────────────────────────

def _banner(capsys) -> str:
    assert gate.session_start() == 0
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


def _drive_lines(ctx: str) -> list[str]:
    return [ln for ln in ctx.splitlines() if "drive stopped" in ln]


def test_the_banner_names_a_pending_escalation(rig, proj, capsys):
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    lines = _drive_lines(_banner(capsys))
    assert lines == ["[prusik] drive stopped: suite_failed in building — read "
                     "reports/feat/drive-escalation.json (next, resume)."]


def test_the_banner_is_unchanged_without_one(proj, capsys):
    ctx = _banner(capsys)
    assert _drive_lines(ctx) == [] and ctx.splitlines()[0].startswith("[prusik] Active sprint")


def test_a_malformed_escalation_leaves_the_banner_unchanged(proj, capsys):
    clean = _banner(capsys)
    for text in ("{not json", "[1, 2]", '{"phase": "x"}'):
        (proj / "reports/feat/drive-escalation.json").write_text(text)
        assert _banner(capsys) == clean


def test_an_escalation_of_another_feature_is_not_shown(rig, proj, capsys):
    (proj / "reports/other").mkdir()
    (proj / "reports/other/drive-escalation.json").write_text(
        json.dumps({"kind": "suite_failed", "phase": "building"}))
    assert _drive_lines(_banner(capsys)) == []
    phases.set_sprint_state({"feature": "other", "phase": "building"}, proj)
    assert len(_drive_lines(_banner(capsys))) == 1


# ── /sprint-run hands drive to a plain terminal; the remedy text has one source ───────────

def _step3() -> str:
    text = _SPRINT_RUN.read_text(encoding="utf-8")
    return text[text.index("## Step 3"):text.index("## Manual / not driven")]


def test_step3_hands_drive_to_a_plain_terminal():
    s = _step3()
    assert "prusik drive $ARGUMENTS" in s and "plain terminal" in s
    assert "run_in_background" not in s
    assert "`next`" in s and "`resume`" in s


def test_step3_restates_no_remedy():
    s = _step3()
    named = [k for k in drive.ESCALATION_KINDS if re.search(rf"\b{k}\b", s)]
    assert named == []
    for text, _ in drive._REMEDIES.values():
        assert text.split(".")[0] not in s

