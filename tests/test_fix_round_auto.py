"""Auto-classified fix-round escalation (v0.70.0, field finding #3) — the sentinel records
its residual a/b/c split; `escalate --auto` reads it and recommends (advisory)."""

from __future__ import annotations

import os
import shutil

from tests._common import _capture_stdout, _mktmp_project  # noqa: F401,E402
from prusik import fix_round


def _proj():
    tmp = _mktmp_project()
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp)
    return tmp


# ---------- recommend_decision rule ----------

def test_test_fixable_zero_source_recommends_extend_once():
    tmp = _proj()
    try:
        fix_round.classify("feat", test_fixable=3, source_defect=0, root=tmp)
        rec, why = fix_round.recommend_decision("feat", tmp)
        assert rec == "extend-once"
        assert "0 source defects" in why
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_source_defect_routes_to_human():
    tmp = _proj()
    try:
        fix_round.classify("feat", test_fixable=2, source_defect=1, root=tmp)
        rec, why = fix_round.recommend_decision("feat", tmp)
        assert rec == "human-review"
        assert "source-defect" in why
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pre_existing_only_routes_to_human():
    tmp = _proj()
    try:
        fix_round.classify("feat", pre_existing=2, root=tmp)
        rec, why = fix_round.recommend_decision("feat", tmp)
        assert rec == "human-review"
        assert "inherited" in why or "pre-existing" in why
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_latest_residual_wins():
    tmp = _proj()
    try:
        fix_round.classify("feat", source_defect=1, root=tmp)        # earlier
        fix_round.classify("feat", test_fixable=2, source_defect=0, root=tmp)  # later
        rec, _ = fix_round.recommend_decision("feat", tmp)
        assert rec == "extend-once"          # latest classification governs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- escalate --auto (advisory, never auto-applies) ----------

def test_escalate_auto_recommends_and_does_not_apply():
    tmp = _proj()
    try:
        fix_round.classify("feat", test_fixable=3, source_defect=0, root=tmp)
        out = _capture_stdout(lambda: fix_round.escalate("feat", auto=True, root=tmp))
        assert "recommendation" in out and "extend-once" in out
        assert "apply:" in out               # tells operator the apply command
        # advisory: NO escalation event was recorded by --auto
        ledger_txt = (tmp / ".sprint" / "ledger.jsonl").read_text()
        assert "fix_round_escalation" not in ledger_txt
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_escalate_auto_without_classification_guides():
    tmp = _proj()
    try:
        rc = fix_round.escalate("feat", auto=True, root=tmp)
        assert rc == 2                       # nothing to read
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_classify_emits_ledger_and_prints_recommendation():
    tmp = _proj()
    try:
        out = _capture_stdout(
            lambda: fix_round.classify("feat", test_fixable=1, root=tmp))
        assert "residual recorded" in out and "extend-once" in out
        assert "fix_round_residual" in (tmp / ".sprint" / "ledger.jsonl").read_text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
