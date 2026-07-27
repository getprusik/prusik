"""`prusik feedback --list` rendered the legacy jsonl `status` field ('open'
forever) instead of the ticket lattice's derived state, so an adopter's list
showed 'open' on findings the loop had verified-closed — a lying scoreboard
right after `prusik update` closes their findings. The list now derives state
from the ticket when one exists; the legacy field is only a fallback for
pre-ticket records.

fb-56ee9ebcf4e6.

moat-finding: fb-56ee9ebcf4e6
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from prusik import feedback as fb
from prusik import feedback_store as fs


def _list_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("prusik.ledger.project_root", lambda: tmp_path)
    args = SimpleNamespace(title=None, list=True)
    fb.run(args)
    return capsys.readouterr().out


def test_verified_closed_ticket_shows_closed_not_open(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = fb.file_feedback(tmp_path, "bug", "gate misfires on X")
    fid = rec["id"]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_d.py").write_text("def test_x():\n    assert 1\n")
    fs.resolve(tmp_path, fid, rtype="fix",
               verify=f"{sys.executable} -m pytest tests/test_d.py -q")
    fs.verify(tmp_path, fid)
    assert fs.derive_state(fs.load(tmp_path, fid)) == "verified-closed"

    out = _list_output(tmp_path, capsys, monkeypatch)
    line = next(ln for ln in out.splitlines() if fid in ln)
    assert "verified-closed" in line and " open " not in line


def test_open_ticket_still_lists_open(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = fb.file_feedback(tmp_path, "bug", "still broken")
    out = _list_output(tmp_path, capsys, monkeypatch)
    line = next(ln for ln in out.splitlines() if rec["id"] in ln)
    assert "open" in line


def test_pre_ticket_record_falls_back_to_legacy_status(tmp_path, capsys, monkeypatch):
    # adversarial: a legacy jsonl record with NO ticket file (pre-ticket era)
    # must not crash and must keep showing its stored status
    monkeypatch.chdir(tmp_path)
    rec = fb.build_record("bug", "ancient finding", ts="2025-01-01T00:00:00")
    fb.append(tmp_path, rec)
    assert fs.load(tmp_path, rec["id"]) is None
    out = _list_output(tmp_path, capsys, monkeypatch)
    line = next(ln for ln in out.splitlines() if rec["id"] in ln)
    assert "open" in line
