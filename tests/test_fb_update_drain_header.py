"""`prusik update`'s closure drain printed "closing the loop on N finding(s)
… (verifying in this repo)" on EVERY run, counting shipped∩local regardless of
state — so a repo whose findings all closed long ago read as if work were
happening on each update, with no outcome line following. The header now
counts only actionable (not-yet-closed) findings and says plainly when the
loop is already closed.

fb-ff4271c46232.

moat-finding: fb-ff4271c46232
"""

from __future__ import annotations

import sys

from prusik import feedback_store as fs
from prusik.update_cmd import _close_shipped_findings


def _wire(tmp_path, monkeypatch, shipped_ids):
    monkeypatch.setattr("prusik.ledger.project_root", lambda: tmp_path)
    monkeypatch.setattr("prusik.changelog.installed_closed_ids",
                        lambda: set(shipped_ids))
    monkeypatch.setattr("prusik.changelog.installed_moat_closures", lambda: {})


def test_all_closed_says_loop_already_closed(tmp_path, capsys, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_d.py").write_text("def test_x():\n    assert 1\n")
    fs.create(tmp_path, fb_id="fb-done", kind="bug", title="t", content_hash="h")
    fs.resolve(tmp_path, "fb-done", rtype="fix",
               verify=f"{sys.executable} -m pytest tests/test_d.py -q")
    fs.verify(tmp_path, "fb-done")

    _wire(tmp_path, monkeypatch, {"fb-done"})
    _close_shipped_findings(timeout=3.0)
    out = capsys.readouterr().out
    assert "loop already closed" in out
    assert "closing the loop on" not in out


def test_open_shipped_finding_still_announces_the_actionable_count(
        tmp_path, capsys, monkeypatch):
    # adversarial: one closed + one open shipped finding — the header must
    # count 1 (the actionable one), never 2
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_d.py").write_text("def test_x():\n    assert 1\n")
    fs.create(tmp_path, fb_id="fb-done", kind="bug", title="t", content_hash="h1")
    fs.resolve(tmp_path, "fb-done", rtype="fix",
               verify=f"{sys.executable} -m pytest tests/test_d.py -q")
    fs.verify(tmp_path, "fb-done")
    fs.create(tmp_path, fb_id="fb-open", kind="bug", title="u", content_hash="h2")

    _wire(tmp_path, monkeypatch, {"fb-done", "fb-open"})
    _close_shipped_findings(timeout=3.0)
    out = capsys.readouterr().out
    assert "closing the loop on 1 finding(s)" in out
