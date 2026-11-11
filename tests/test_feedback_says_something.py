"""A finding must say something.

`prusik feedback list` was filed as an empty finding titled "list" with rc=0: the positional is a
title or a ticket verb, and any other word was taken as a title. A one-word title with no detail
and no repro is refused and nothing is written. Everything that says something still files.

moat-finding: fb-84cc77a2bbc4
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from prusik import feedback
from tests._common import _mktmp_project


def _args(title, **kw):
    base = dict(title=title, ref=None, list=False, kind="friction", severity=None,
                detail=None, repro=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _written(tmp):
    """Everything filing could leave behind: tickets, the index, the ledger."""
    tickets = sorted(p.name for p in (tmp / "findings").glob("fb-*.json")) \
        if (tmp / "findings").exists() else []
    index = (tmp / ".sprint" / "feedback.jsonl")
    ledger = (tmp / ".sprint" / "ledger.jsonl")
    return (tickets,
            index.read_text() if index.exists() else "",
            ledger.read_text() if ledger.exists() else "")


@pytest.mark.parametrize("word", ["list", "ls", "close", "status", "help", "  list  "])
def test_one_word_title_alone_is_refused_and_writes_nothing(word, capsys):
    tmp = _mktmp_project()
    try:
        before = _written(tmp)
        rc = feedback.run(_args(word))
        err = capsys.readouterr().err
        assert rc == 2
        assert _written(tmp) == before == ([], "", "")
        assert "--list" in err
        for verb in feedback._TICKET_VERBS:
            assert verb in err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_blank_detail_does_not_rescue_a_one_word_title():
    tmp = _mktmp_project()
    try:
        assert feedback.run(_args("list", detail="   ", repro="")) == 2
        assert _written(tmp)[0] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("kw", [
    dict(title="scoped coverage false-fails"),
    dict(title="two words"),
    dict(title="flake", detail="test_x fails one run in five"),
    dict(title="flake", repro="pytest tests/test_x.py"),
])
def test_a_finding_that_says_something_still_files(kw):
    tmp = _mktmp_project()
    try:
        title = kw.pop("title")
        assert feedback.run(_args(title, **kw)) == 0
        tickets, index, _ = _written(tmp)
        assert len(tickets) == 1
        assert title in index
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_flag_and_ticket_verbs_are_not_refused(capsys):
    tmp = _mktmp_project()
    try:
        assert feedback.run(_args(None, list=True)) == 0
        assert "no findings filed yet" in capsys.readouterr().out
        assert feedback.run(_args("a real finding")) == 0
        fid = feedback.load(tmp)[0]["id"]
        assert feedback.run(_args("show", ref=fid)) == 0
        assert "a real finding" in capsys.readouterr().out
        assert len(_written(tmp)[0]) == 1          # the verb filed nothing
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
