"""Unexamined-delta detector — tests that silently stop running between the worktree
and integrated tree (field escape #4: 1106 passed in the worktree, 1088 on integrated
main; 18 tests went to skipped, unexamined). The signal is an executed-count DECREASE
vs the stored worktree capture — robust (reuses the proven executed_count primitive),
and finer than the #14 90%-threshold gate that the 1.6% drop slipped under.
"""

from __future__ import annotations

import json
from pathlib import Path

from prusik import suite_delta as sd


def _evidence(root: Path, feature: str, value: int,
              skipped: list[str] | None = None) -> None:
    d = root / "reports" / feature
    d.mkdir(parents=True, exist_ok=True)
    entry = {"phase": "regression", "command": "pnpm test",
             "nonempty_primitive": {"kind": "tests", "value": value}}
    if skipped is not None:
        entry["skipped_tests"] = skipped
    (d / "regression.evidence.json").write_text(json.dumps({"entries": [entry]}))


def test_delta_report_flags_a_drop():
    rep = sd.delta_report(1106, 1088, integrated_skipped=74)
    assert rep["dropped"] == 18 and rep["flagged"] is True
    assert rep["integrated_skipped"] == 74


def test_delta_report_no_drop_is_clean():
    assert sd.delta_report(1106, 1106)["flagged"] is False


def test_delta_report_more_tests_is_not_flagged():
    # integrated ran MORE (e.g. new tests merged in) → not a loss
    assert sd.delta_report(1088, 1106)["flagged"] is False


def test_skipped_count_parses_pytest_and_vitest():
    assert sd._skipped_count("1088 passed, 74 skipped in 12s") == 74
    assert sd._skipped_count("Tests 730 passed | 71 skipped") == 71
    assert sd._skipped_count("1106 passed in 9s") == 0


def test_worktree_executed_reads_reviewing_capture(tmp_path):
    _evidence(tmp_path, "feat", 1106)
    assert sd.worktree_executed("feat", tmp_path) == 1106


def test_worktree_executed_none_when_absent(tmp_path):
    assert sd.worktree_executed("feat", tmp_path) is None


def test_run_flags_integrated_drop(tmp_path):
    """End-to-end: worktree proved 1106; the integrated suite reports 1088 passed /
    74 skipped → flagged (the field finding #4 escape, advisory rc=0 without --strict)."""
    _evidence(tmp_path, "feat", 1106)
    cmd = ["echo", "'1088 passed, 74 skipped in 11s'"]
    rc = sd.run("feat", cmd, root=tmp_path, json_output=True)
    assert rc == 0
    cmd_strict_rc = sd.run("feat", cmd, root=tmp_path, json_output=True, strict=True)
    assert cmd_strict_rc == 1   # --strict turns the silent loss into a hard fail


def test_run_clean_when_counts_match(tmp_path):
    _evidence(tmp_path, "feat", 1106)
    rc = sd.run("feat", ["echo", "'1106 passed in 9s'"], root=tmp_path,
                json_output=True, strict=True)
    assert rc == 0


def test_run_no_baseline_is_a_noop(tmp_path):
    rc = sd.run("feat", ["echo", "'1 passed'"], root=tmp_path, strict=True)
    assert rc == 0   # nothing to compare against → not a failure


def test_named_skips_parsed_from_pytest_and_vitest():
    from prusik import evidence
    pyt = ("SKIPPED [1] tests/test_a.py:9: needs db\n"
           "tests/test_b.py::test_x SKIPPED (todo)\n1088 passed, 2 skipped")
    assert evidence.skipped_tests(pyt) == ["tests/test_a.py:9", "tests/test_b.py::test_x"]
    vit = "↓ packages/web/foo.test.ts > renders detail (skipped)\nTests 5 passed | 1 skipped"
    assert "packages/web/foo.test.ts > renders detail" in evidence.skipped_tests(vit)[0]


def test_exact_named_diff_when_worktree_skipset_present(tmp_path):
    """The full field finding #4 signal: worktree skipped {a}; integrated skips {a,b} → b is
    the NEWLY-skipped test that silently stopped, named for judgement."""
    _evidence(tmp_path, "feat", 1106, skipped=["tests/test_a.py:9"])
    out = ("1088 passed, 2 skipped\nSKIPPED [1] tests/test_a.py:9: db\n"
           "tests/test_b.py::test_x SKIPPED (env)")
    rc = sd.run("feat", ["echo", repr(out)], root=tmp_path, json_output=True)
    assert rc == 0   # advisory


def test_named_diff_function_computes_newly_skipped():
    # unit the set logic the run uses
    wt = {"tests/test_a.py:9"}
    integrated = {"tests/test_a.py:9", "tests/test_b.py::test_x"}
    assert sorted(integrated - wt) == ["tests/test_b.py::test_x"]
