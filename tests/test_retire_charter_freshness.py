"""charter_freshness is retired: sprint-start never emits it, the shipped template no longer
carries it, refresh removes the shipped-default block from adopter configs byte-exactly (and
keeps + names a customised one), and past fires still render in catch-quality.

moat-finding: fb-14a0d4bf5863
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from prusik import catch_quality, refresh_merge
from prusik.refresh import _user_content_loss
from tests._common import (
    _copy_sprint_config,
    _mktmp_project,
    _write_ledger,
    discovery,
    gate,
    kit_init,
    kit_refresh,
)

TEMPLATE = Path(__file__).resolve().parent.parent / "prusik" / "templates" / ".claude" / \
    "sprint-config.yaml"

# The exact text v0.269.0 shipped (template L261-262 and L282-288).
INNER = ("    # charter_freshness (top-level block below) warns — never blocks — when the\n"
         "    # charter has gone untouched for many completed sprints.\n")
TOP = ("# Charter freshness (advisory). Warns at sprint-start — NEVER blocks — when the\n"
       "# product charter (design/product.md) has gone untouched for >= max_sprints_stale\n"
       "# completed sprints, so a living WHAT-layer doesn't ossify into a fiction while\n"
       "# features ship. Dormant when there's no charter.\n"
       "charter_freshness:\n"
       "  enabled: true\n"
       "  max_sprints_stale: 8\n")


@pytest.fixture
def project():
    tmp = _mktmp_project()
    yield tmp
    shutil.rmtree(tmp)


def _inject_shipped(config: Path, block: str = TOP) -> str:
    """Put the v0.269.0 comment + block back where they shipped."""
    text = config.read_text()
    anchor = "    check: product_fit\n"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + INNER)
    anchor2 = "# Behavior-regression suite (opt-in)."
    assert text.count(anchor2) == 1
    text = text.replace(anchor2, block + "\n" + anchor2)
    config.write_text(text)
    return text


def test_sprint_start_never_emits_charter_freshness(project, capsys):
    cfg = _copy_sprint_config(project) or project / ".claude" / "sprint-config.yaml"
    doc = yaml.safe_load(cfg.read_text())
    doc["pre_sprint_gates"]["product_fit"]["enabled"] = False
    doc["charter_freshness"] = {"enabled": True, "max_sprints_stale": 8}
    cfg.write_text(yaml.safe_dump(doc))
    charter = project / "design" / "product.md"
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text("# Product\n## North star\nx\n")
    past = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(charter, (past, past))
    _write_ledger(project, [
        {"event": "sprint_complete", "ts": datetime(2026, 2, d, tzinfo=timezone.utc).isoformat()}
        for d in range(1, 14)])
    (project / "briefs").mkdir(exist_ok=True)
    (project / "briefs" / "f.md").write_text(
        "## Goal\nFix the typo in the retry log message.\n\n"
        "## Success criteria\nLog reads correctly within 1 line changed.\n\n## Type\nbug_fix\n")
    crit = project / "reports" / "f" / "brief-critique.txt"
    crit.parent.mkdir(parents=True, exist_ok=True)
    crit.write_text("PASS\n")
    (project / ".sprint" / "dep-graph.json").write_text(json.dumps(
        {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}))
    (project / "design" / "map.md").write_text("map placeholder")
    discovery.fingerprint_map()
    capsys.readouterr()
    assert gate.sprint_start(argparse.Namespace(feature="f", trivial=True)) == 0
    out = capsys.readouterr().out
    assert "charter freshness" not in out.lower()
    events = [json.loads(x)["event"] for x in
              (project / ".sprint" / "ledger.jsonl").read_text().splitlines() if x.strip()]
    assert "sprint_started" in events
    assert "charter_freshness_fired" not in events


def test_shipped_template_has_no_charter_freshness():
    assert "charter_freshness" not in TEMPLATE.read_text()


def test_refresh_retires_the_shipped_block_byte_exactly(project, capsys):
    assert kit_init.run() == 0
    cfg = project / ".claude" / "sprint-config.yaml"
    clean = cfg.read_text()
    injected = _inject_shipped(cfg)
    capsys.readouterr()
    assert kit_refresh.run() == 0
    out = capsys.readouterr().out
    after = cfg.read_text()
    assert "charter_freshness" not in after
    assert "Charter freshness" not in after
    removed = [ln for ln in difflib.ndiff(injected.splitlines(keepends=True),
                                          after.splitlines(keepends=True))
               if ln.startswith(("- ", "+ "))]
    assert all(ln.startswith("- ") for ln in removed), removed
    assert after == clean, "every other byte is the user's file, unchanged"
    assert "-charter_freshness (retired; no longer read)" in out
    capsys.readouterr()
    assert kit_refresh.run() == 0
    assert cfg.read_text() == after
    assert "charter_freshness" not in capsys.readouterr().out


def test_refresh_retires_a_merge_added_block_without_comments():
    """The adopter shape: merge appended the block at the end with no comment and no blank."""
    text = "phases: []\nworktree_env_files:\n- .env\ncharter_freshness:\n  enabled: true\n" \
           "  max_sprints_stale: 8\ndrive:\n  budget_usd: 5\n"
    out, retired, kept = refresh_merge.retire_top_level_defaults(text)
    assert retired == ["charter_freshness"] and kept == []
    assert out == "phases: []\nworktree_env_files:\n- .env\ndrive:\n  budget_usd: 5\n"
    assert refresh_merge.retire_top_level_defaults(out) == (out, [], [])


def test_refresh_keeps_and_names_a_customised_block(project, capsys):
    assert kit_init.run() == 0
    cfg = project / ".claude" / "sprint-config.yaml"
    injected = _inject_shipped(cfg, TOP.replace("max_sprints_stale: 8", "max_sprints_stale: 3"))
    capsys.readouterr()
    assert kit_refresh.run() == 0
    out = capsys.readouterr().out
    assert cfg.read_text() == injected, "a customised block and its comments are the user's"
    assert "! charter_freshness is no longer read — delete it by hand" in out


def test_content_loss_still_fails_closed_on_any_other_dropped_key():
    old = "charter_freshness:\n  enabled: true\ndrive:\n  budget_usd: 5\nx: 1\n"
    allowed = frozenset({"charter_freshness"})
    assert _user_content_loss(".claude/sprint-config.yaml", old, "x: 1\n",
                              allowed_drops=allowed) is not None
    assert _user_content_loss(".claude/sprint-config.yaml", old,
                              "drive:\n  budget_usd: 5\nx: 1\n", allowed_drops=allowed) is None
    assert _user_content_loss(".claude/sprint-config.yaml", old,
                              "drive:\n  budget_usd: 5\nx: 1\n") is not None


def test_past_fires_and_labels_still_render(project, capsys):
    fire = {"event": "charter_freshness_fired", "feature": "f", "reason": "stale",
            "ts": "2026-11-01T00:00:00+00:00"}
    cid = catch_quality.catch_id(fire)
    _write_ledger(project, [fire, {"event": "catch_resolved", "catch_id": cid,
                                   "verdict": catch_quality.FALSE_BLOCK, "reason": "noise",
                                   "source": "operator", "ts": "2026-11-02T00:00:00+00:00"}])
    capsys.readouterr()
    assert catch_quality.run() == 0
    out = capsys.readouterr().out
    assert "charter_freshness" in out
    assert "charter_freshness" not in catch_quality.PRODUCT_FIT_LAYERS
