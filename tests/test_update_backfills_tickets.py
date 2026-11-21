"""The adopter feedback loop: update closes ticketless shipped findings, and no message
claims a delivery that did not happen.

fb-44c3b9054134 — `.sprint/feedback.jsonl` records from before per-finding tickets have no
`findings/fb-<id>.json`. `prusik update` closed shipped fixes by iterating tickets only, so
those findings could never close: one adopter carried 5, every one fixed in its pinned engine.
The closer now backfills missing tickets first (the same idempotent create `prusik feedback
migrate` uses).

fb-3f863869d9bb — the session banner said filed feedback "rides to the maintainers" and
filing said it "rides the next export to HQ". Nothing leaves the repo without an export or a
configured `feedback_sink`, so the messages now say where the finding is.

Adversarial: an existing ticket with a thread and a resolution is byte-for-byte unchanged by
the backfill, and a ticketless record whose fix has not shipped gets a ticket but stays open.

moat-finding: fb-44c3b9054134
moat-finding: fb-3f863869d9bb
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from prusik import feedback, feedback_store as fs, gate
from prusik.update_cmd import _close_shipped_findings

_TEMPLATE_CFG = (Path(__file__).resolve().parent.parent / "prusik" / "templates" / ".claude"
                 / "sprint-config.yaml")


def _project(tmp_path, monkeypatch, *, sink: Path | None = None) -> Path:
    (tmp_path / ".claude").mkdir()
    cfg = _TEMPLATE_CFG.read_text(encoding="utf-8")
    if sink is not None:
        cfg += f'\nfeedback_sink: "{sink}"\n'
    (tmp_path / ".claude" / "sprint-config.yaml").write_text(cfg, encoding="utf-8")
    (tmp_path / ".sprint").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr("prusik.ledger.project_root", lambda: tmp_path)
    return tmp_path


def _legacy_record(root: Path, fid: str, title: str) -> None:
    """A jsonl-only record, as filed before tickets existed: no findings/ file."""
    rec = {"id": fid, "ts": "2026-06-06T00:00:00+00:00", "kind": "bug", "severity": None,
           "title": title, "detail": "d", "prusik_version": "0.121.0", "phase": None,
           "feature": None, "content_hash": fid[3:], "status": "open"}
    with open(root / ".sprint" / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _ship(monkeypatch, moat: dict[str, str]) -> None:
    """The installed engine's closure manifest, and a version-floor verify that passes
    without depending on which `prusik` is on PATH."""
    monkeypatch.setattr("prusik.changelog.installed_closed_ids", lambda: set(moat))
    monkeypatch.setattr("prusik.changelog.installed_moat_closures", lambda: dict(moat))
    monkeypatch.setattr(fs, "_version_floor_verify",
                        lambda fid, v: f'echo "1 passed: {fid} >= {v}"')


def test_ticketless_shipped_finding_closes_on_update(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    _legacy_record(root, "fb-aaaaaaaaaaaa", "legacy shipped finding")
    assert fs.load(root, "fb-aaaaaaaaaaaa") is None
    _ship(monkeypatch, {"fb-aaaaaaaaaaaa": "0.0.1"})

    _close_shipped_findings(timeout=0.0)
    out = capsys.readouterr().out

    rec = fs.load(root, "fb-aaaaaaaaaaaa")
    assert rec is not None, "the closer must backfill the missing ticket"
    assert fs.derive_state(rec) == "verified-closed"
    assert "fb-aaaaaaaaaaaa" in out


def test_backfill_count_is_reported(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    _legacy_record(root, "fb-aaaaaaaaaaaa", "one")
    _legacy_record(root, "fb-bbbbbbbbbbbb", "two")
    _ship(monkeypatch, {"fb-aaaaaaaaaaaa": "0.0.1"})

    _close_shipped_findings(timeout=0.0)
    out = capsys.readouterr().out
    assert "2 ticket(s)" in out, out


def test_no_backfill_line_when_nothing_is_created(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    fs.create(root, fb_id="fb-cccccccccccc", kind="bug", title="t", content_hash="c")
    _ship(monkeypatch, {"fb-cccccccccccc": "0.0.1"})

    _close_shipped_findings(timeout=0.0)
    out = capsys.readouterr().out
    assert "ticket(s)" not in out, out


def test_existing_ticket_is_byte_identical_after_the_closer(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    # Same id in the jsonl index AND as a ticket with a conversation and a reject.
    _legacy_record(root, "fb-dddddddddddd", "already ticketed")
    fs.create(root, fb_id="fb-dddddddddddd", kind="bug", title="already ticketed",
              content_hash="dddddddddddd")
    fs.reply(root, "fb-dddddddddddd", "maintainer", "looked at it")
    fs.resolve(root, "fb-dddddddddddd", rtype="reject", reason="adopter-owned")
    before = fs.path(root, "fb-dddddddddddd").read_bytes()
    _legacy_record(root, "fb-aaaaaaaaaaaa", "something else to backfill")
    _ship(monkeypatch, {"fb-aaaaaaaaaaaa": "0.0.1"})

    _close_shipped_findings(timeout=0.0)
    assert fs.path(root, "fb-dddddddddddd").read_bytes() == before


def test_unshipped_ticketless_record_gets_a_ticket_and_stays_open(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    _legacy_record(root, "fb-eeeeeeeeeeee", "not fixed yet")
    _legacy_record(root, "fb-aaaaaaaaaaaa", "fixed")
    _ship(monkeypatch, {"fb-aaaaaaaaaaaa": "0.0.1"})

    _close_shipped_findings(timeout=0.0)
    rec = fs.load(root, "fb-eeeeeeeeeeee")
    assert rec is not None
    assert fs.derive_state(rec) == "open"


def _file(title: str) -> argparse.Namespace:
    return argparse.Namespace(title=title, ref=None, list=False, kind="friction",
                              detail="what happened", repro="", severity=None)


def test_filing_without_a_sink_says_saved_here_and_how_to_share(
        tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert feedback.run(_file("filing message is truthful without a sink")) == 0
    out = capsys.readouterr().out
    assert "findings/" in out
    assert "prusik report --export" in out
    assert "ride" not in out.lower(), out


def test_filing_with_a_sink_names_the_drop_it_wrote(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "drop"
    _project(tmp_path, monkeypatch, sink=sink)
    assert feedback.run(_file("filing message names the sink drop")) == 0
    out = capsys.readouterr().out
    drops = list(sink.glob("*.fb.jsonl"))
    assert len(drops) == 1
    assert str(drops[0]) in out, out
    assert "ride" not in out.lower(), out


def test_banner_and_list_header_make_no_delivery_claim(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert gate.session_start() == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    line = next(ln for ln in ctx.splitlines() if "prusik feedback" in ln)
    assert "ride" not in line.lower(), line
    assert "this repo" in line, line

    assert feedback.run(_file("something to list")) == 0
    capsys.readouterr()
    ns = argparse.Namespace(title=None, ref=None, list=True)
    assert feedback.run(ns) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert "ride" not in header.lower(), header
    assert "this repo" in header, header
