"""Absence detector — catch a plan-declared deliverable silently NOT produced.

An adopter's escape class #1: critics review the DIFF (what's present, is it correct);
absence has no diff, so a builder that omits a promised file escapes
presence/correctness review entirely (chunk-7 declared a detail-view e2e in the
plan; the builder shipped component + API tests and just didn't write it — every
reviewing-phase critic passed). The fix is the one thing review structurally can't
be: reconcile what the plan DECLARED against what the worktree actually CONTAINS.

Two signals, both deliberately HIGH-PRECISION — a false flag here erodes the gate
(an operator habituated to overriding it skips the real omission too, the
--skip-lint trap):
  1. a plan-declared FILE PATH (a `+ new` file in `## Modules touched`, or a
     backtick path named in `## Build order` / `## Test plan`) that exists nowhere
     in the worktree → a promised artifact never produced. Matched by basename too,
     so a file created under a slightly different path is NOT falsely flagged.
  2. a non-empty `## Test plan` with ZERO test files among the sprint's changed
     files → tests were committed-to and none appear (An adopter's exact omission).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from prusik import blast_plan, catch_quality, ledger, schema
from prusik.gate import _DERIVED_DIRS

# dirs to skip when indexing the tree for presence — the canonical derived-build set
# (single source of truth in gate.py) plus VCS + orchestration dirs that hold no
# product deliverables.
_SKIP_DIRS = _DERIVED_DIRS | {".git", ".sprint", "reports"}

# a backtick-wrapped token that looks like a FILE path: starts with a word char
# (so a `*.x` glob or `.dotfragment` doesn't yield a leading-junk capture) and ends
# in a dotted extension. The caller further requires a `/` so bare basenames and
# patterns in prose aren't mistaken for specific declared deliverables.
_BACKTICK_FILE = re.compile(r"`([\w][\w./-]*\.[A-Za-z][\w]{0,5})`")
# template placeholders (`<happy path>`) — unfilled, never counted as a commitment.
_PLACEHOLDER = re.compile(r"^\s*<.*>\s*$")
# test files across ecosystems: Python (test_*.py, *_test.py) + JS/TS
# (*.test.ts, *.spec.ts, *.e2e.ts, and e2e/ or __tests__/ dirs).
_TEST_FILE = re.compile(
    r"(?:^|/)(?:test_[\w-]+\.py"
    r"|[\w-]+_test\.py"
    r"|[\w.-]+\.(?:test|spec|e2e)\.[jt]sx?)$"
    r"|(?:^|/)(?:__tests__|e2e)/")
_SECTIONS = ("## Build order", "## Test plan")


def _is_test_path(rel: str) -> bool:
    return bool(_TEST_FILE.search(rel))


def _looks_like_file(tok: str) -> bool:
    base = tok.rstrip("/").rsplit("/", 1)[-1]
    return "." in base and not base.startswith(".")


def declared_files(feature: str, root: Path) -> set[str]:
    """File paths the plan PROMISED: `+ new` files in Modules touched + backtick
    file paths named in Build order / Test plan. Directories are excluded (low
    precision); only file-like tokens count."""
    plan_path = root / "design" / feature / "plan.md"
    if not plan_path.exists():
        return set()
    out: set[str] = set()
    _existing, new = blast_plan.plan_modules(feature, root)
    out.update(t for t in new if _looks_like_file(t))
    sections = schema.parse_sections(plan_path.read_text())
    for sec in _SECTIONS:
        for m in _BACKTICK_FILE.finditer(sections.get(sec, "")):
            tok = m.group(1)
            # require a real path (a `/`), not a bare basename or glob fragment in
            # prose — those are too imprecise to charge as a specific deliverable
            if "/" in tok and _looks_like_file(tok):
                out.add(tok)
    return out


def _repo_basenames(root: Path) -> set[str]:
    """Every filename present anywhere in the tree (worktrees included; build/dep/
    orchestration dirs excluded). Presence is checked by BASENAME so a file the plan
    named bare (`Foo.test.tsx`) but the builder wrote at a full path counts as
    present — precision over recall: we flag absence ONLY when no file by that name
    exists anywhere, which is the unambiguous 'never produced' signal."""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        out.update(filenames)
    return out


def _present(root: Path, rel: str, basenames: set[str]) -> bool:
    if (root / rel).exists():
        return True
    return rel.rsplit("/", 1)[-1] in basenames


def absence_check(feature: str, root: Path) -> dict[str, Any]:
    """Reconcile plan-declared files against the worktree. Returns the missing
    files + whether a committed test plan produced no test file."""
    from prusik import consistency
    basenames = _repo_basenames(root)
    missing = sorted(
        f for f in declared_files(feature, root)
        if not _present(root, f, basenames))
    changed = consistency.sprint_changed_files(root)

    plan_path = root / "design" / feature / "plan.md"
    test_plan_unmet = False
    if plan_path.exists():
        sections = schema.parse_sections(plan_path.read_text())
        items = [t for t in schema.extract_list_items(sections.get("## Test plan", ""))
                 if t.strip() and not _PLACEHOLDER.match(t)]
        changed_tests = {c for c in changed if _is_test_path(c)}
        # only assert "tests committed but none produced" when we can SEE the diff
        # (worktrees present mid-sprint). Post-integration the worktrees are gone →
        # `changed` is empty → we have no visibility, so we don't claim a miss.
        test_plan_unmet = bool(items) and bool(changed) and not changed_tests
    return {
        "feature": feature,
        "missing_files": missing,
        "test_plan_unmet": test_plan_unmet,
        "clean": not missing and not test_plan_unmet,
    }


def _flags_to_resolve(records: list[dict],
                      is_present: Callable[[str], bool]) -> list[str]:
    """Pure core: catch_ids of absence flags whose named-missing files are now present
    and aren't already resolved. A flag → named-gap → gap-closed is the derivable
    truth trail (An adopter), so precision is DERIVED, not hand-labelled."""
    already = {r.get("catch_id") for r in records
               if r.get("event") == "catch_resolved"}
    out: list[str] = []
    for r in records:
        if r.get("event") != "absence_flagged":
            continue
        cid = catch_quality.catch_id(r)
        if cid in already:
            continue
        files = r.get("missing_files") or []
        if files and any(is_present(f) for f in files):
            out.append(cid)
    return out


def resolve_prior_flags(root: Path) -> int:
    """Auto-credit any absence flag whose named gap has since been produced — the
    detector observed the closure (it has FS access), so it emits the resolution
    (auto-sourced `catch_resolved`) instead of waiting for a hand-label. Idempotent.
    Returns the count newly resolved."""
    records = ledger.read_all()
    basenames = _repo_basenames(root)
    n = 0
    for cid in _flags_to_resolve(records, lambda f: _present(root, f, basenames)):
        ledger.append("catch_resolved", catch_id=cid, verdict="true_catch",
                      reason="absence flag closed — a named-missing file now exists",
                      source="auto")
        n += 1
    return n


def run(feature: str, json_output: bool = False, strict: bool = False) -> int:
    """`prusik absence-check <feature>` — advisory by default; `--strict` returns
    rc≠0 so a sprint can opt into hard-gating it at building→reviewing."""
    root = ledger.project_root()
    from prusik import calibration
    strict = strict or calibration.is_promoted("absence_detector", root)
    resolve_prior_flags(root)   # auto-credit any flag whose gap has since closed
    rep = absence_check(feature, root)
    if json_output:
        print(json.dumps(rep, indent=2))
        return 1 if (strict and not rep["clean"]) else 0

    if rep["clean"]:
        print(f"[prusik-absence] '{feature}': every plan-declared file is present "
              f"and the test plan produced tests. No absence escape.")
        return 0

    # a real catch of the out-of-diff absence class — record it (improves the
    # absence class's recall: caught EARLY here instead of escaping to integration).
    ledger.append("absence_flagged", feature=feature,
                  missing=len(rep["missing_files"]),
                  missing_files=rep["missing_files"],
                  test_plan_unmet=rep["test_plan_unmet"])
    print(f"[prusik-absence] '{feature}': plan-declared deliverables not found "
          f"(absence has no diff — review can't catch this):")
    for m in rep["missing_files"]:
        print(f"    ✗ {m}  — named in the plan, exists nowhere in the worktree")
    if rep["test_plan_unmet"]:
        print("    ✗ ## Test plan declares tests, but the diff added NO test file")
    print("\n  Produce the missing artifact, or amend the plan if it's "
          "deliberately out of scope (a logged decision, not a silent omission).")
    return 1 if strict else 0
