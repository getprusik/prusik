"""Unexamined-delta detector — flag tests that silently stop running between the
worktree and the integrated tree.

Field escape #4 (its own confessed leak): the backend suite read 1106 passed / 56
skipped in the worktree but 1088 passed / 74 skipped on integrated main — 18 tests
went from running to skipped, hand-waved as "probably env-gated" without running it
down. "Probably benign, unverified" is the declare-done-without-running failure mode,
and nothing flagged the delta. The full-suite gate (#14) compares against the largest
green count at a 90% threshold, so a fine 1.6% drop slips under it; this catches the
exact worktree→integrated delta it misses.

The signal is robust and reuses the proven primitive: the reviewing-phase
`regression.evidence.json` already stores the WORKTREE executed count
(`executed_count` = passed+failed, skips excluded). This runs the suite once on the
CURRENT (integrated) tree and compares — an executed-count DECREASE means tests that
ran in the worktree no longer run (skipped, deselected, or vanished). Advisory: a
delta can be benign env-gating OR a real silent loss; the point is it must be
EXAMINED, not assumed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from prusik import evidence, ledger, suite_baseline

_SKIPPED_RE = re.compile(r"(\d+)\s+skipped", re.I)
_RUN_TIMEOUT_SEC = 1800


def _skipped_count(text: str) -> int:
    """Total skipped from a pytest/vitest summary ('… 74 skipped', 'Tests … | 71
    skipped'). The max match is the run total (per-file lines are smaller)."""
    nums = [int(n) for n in _SKIPPED_RE.findall(text)]
    return max(nums) if nums else 0


def _worktree_tests_entry(feature: str, root: Path) -> dict | None:
    """The reviewing-phase tests evidence entry for the feature, if present."""
    ev = root / "reports" / feature / "regression.evidence.json"
    if not ev.exists():
        return None
    try:
        data = json.loads(ev.read_text())
    except (OSError, ValueError):
        return None
    for e in (data.get("entries") or []):
        if e.get("nonempty_primitive", {}).get("kind") == "tests":
            return e
    return None


def worktree_executed(feature: str, root: Path) -> int | None:
    """The executed count proven in the worktree at reviewing. Falls back to the
    learned full-suite baseline (largest green count) when the capture is gone."""
    e = _worktree_tests_entry(feature, root)
    if e and isinstance(e.get("nonempty_primitive", {}).get("value"), int):
        return e["nonempty_primitive"]["value"]
    base = suite_baseline.load(root)
    return base or None


def worktree_skipped(feature: str, root: Path) -> list[str] | None:
    """The worktree's named skipped tests (captured at reviewing), or None if the
    capture predates skip-name recording — then the exact diff isn't derivable and we
    surface the integrated skip-set as candidates instead."""
    e = _worktree_tests_entry(feature, root)
    if e and "skipped_tests" in e:
        return list(e["skipped_tests"])
    return None


def delta_report(worktree_exec: int, integrated_exec: int,
                 integrated_skipped: int = 0) -> dict[str, Any]:
    """A pure reconciliation. `dropped` > 0 means fewer tests ran on the integrated
    tree than in the worktree — the silent loss to examine."""
    dropped = worktree_exec - integrated_exec
    return {
        "worktree_executed": worktree_exec,
        "integrated_executed": integrated_exec,
        "integrated_skipped": integrated_skipped,
        "dropped": dropped,
        "flagged": dropped > 0,
    }


def run(feature: str, command: list[str], *, root: Path | None = None,
        json_output: bool = False, strict: bool = False) -> int:
    """`prusik delta-check <feature> -- <full-suite-cmd>` — run the suite on the
    integrated tree and compare its executed count to the worktree capture."""
    root = root or ledger.project_root()
    cmd = list(command or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[prusik-delta] give the full-suite command after `--`, e.g. "
              "`prusik delta-check <feat> -- pytest -q`", file=sys.stderr)
        return 2
    wt = worktree_executed(feature, root)
    if wt is None:
        print(f"[prusik-delta] no worktree executed-count for '{feature}' "
              f"(reports/{feature}/regression.evidence.json absent and no suite "
              f"baseline) — nothing to compare against.")
        return 0
    try:
        proc = subprocess.run(["/bin/bash", "-c", " ".join(cmd)], cwd=str(root),
                              capture_output=True, text=True,
                              timeout=_RUN_TIMEOUT_SEC, check=False)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[prusik-delta] suite command failed to run: {e}", file=sys.stderr)
        return 2
    integrated = evidence.executed_count("tests", combined)
    int_skipped = evidence.skipped_tests(combined)
    rep = delta_report(wt, integrated, _skipped_count(combined))   # total from summary

    # Provenance, not a bare threshold (field finding #4): name the tests to JUDGE. When the
    # worktree skip-set was captured, the exact NEWLY-skipped set is derivable; else
    # the integrated skip-set is the candidate set to examine.
    wt_skipped = worktree_skipped(feature, root)
    if wt_skipped is not None:
        named = sorted(set(int_skipped) - set(wt_skipped))
        named_kind = "newly skipped vs the worktree"
    else:
        named = int_skipped
        named_kind = ("skipped on the integrated tree (worktree skip-set not "
                      "captured — cannot diff exactly)")
    rep["named_changed"] = named
    rep["named_kind"] = named_kind

    if json_output:
        print(json.dumps(rep, indent=2))
        return 1 if (strict and rep["flagged"]) else 0

    if not rep["flagged"] and not named:
        print(f"[prusik-delta] '{feature}': {integrated} tests ran on the integrated "
              f"tree vs {wt} in the worktree — no silent loss.")
        return 0

    ledger.append("delta_flagged", feature=feature, dropped=rep["dropped"],
                  worktree_executed=wt, integrated_executed=integrated,
                  named_changed=len(named))
    print(f"[prusik-delta] '{feature}': {rep['dropped']} fewer tests RAN on the "
          f"integrated tree ({wt} → {integrated}; {rep['integrated_skipped']} "
          f"skipped total).")
    if named:
        print(f"\n  {len(named)} test(s) {named_kind} — JUDGE each "
              f"(env-gating is benign; a silent stop is a coverage hole):")
        for t in named[:25]:
            print(f"    · {t}")
        if len(named) > 25:
            print(f"    … and {len(named) - 25} more (use --json for all)")
    else:
        print("\n  The suite output didn't name the skipped tests — re-run it with "
              "skip-listing (pytest `-rs` / vitest default reporter) to see which.")
    print("\n  delta-check surfaces provenance; it does not auto-decide. 'Probably "
          "benign, unverified' is declare-done-without-running — name them, judge.")
    return 1 if strict else 0
