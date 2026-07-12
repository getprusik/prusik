"""Affected-test selection (v0.69.0, an adopter enabler #5).

The sentinel runs the FULL suite (+ staging + live smoke) every fix-round — an adopter:
~20-45 min × 3-4 rounds. Most of those runs end in a small failure that a tiny
subset would have caught in seconds. This emits the affected subset to run FIRST
(fail-fast), so a fix-round dies on the cheap signal instead of after a 30-min
full run.

The selection composes existing signals — no new heavy machinery:
  - test files the builders actually wrote (worktrees/<role>/)
  - name-convention matches for touched source modules (test_<module>.py)
  - test-reach: tests referencing a contract the touched modules expose

HARD INVARIANT (no silent coverage gap): this is FAIL-FAST ONLY. The full suite
still runs once at green and remains the ship gate — a far regression won't be in
the affected subset by construction. `affected_tests` never weakens a gate; it
only ORDERS work. The CLI says so loudly, and `full_suite_required` is always True.
"""

from __future__ import annotations

from pathlib import Path

from prusik import schema
from prusik.test_reach import _is_test_file

_SRC_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")
_MIN_BASE = 4          # don't name-match on a 3-char base like "app" (too broad)


def _is_source(rel: str) -> bool:
    p = Path(rel)
    return p.suffix in _SRC_SUFFIXES and not _looks_test(rel)


def _looks_test(rel: str) -> bool:
    low = rel.lower()
    parts = Path(low).parts
    return ("tests" in parts or "test" in parts
            or Path(low).name.startswith("test_")
            or low.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.js",
                             ".spec.ts", ".spec.tsx", ".spec.js")))


def _source_modules(feature: str, root: Path) -> list[str]:
    """Existing source files the sprint touches, from scope.md `## Modules touched`
    (the authoritative boundary, present at sentinel time)."""
    scope = root / "design" / feature / "scope.md"
    if not scope.exists():
        return []
    secs = schema.parse_sections(scope.read_text())
    items = schema.extract_list_items(secs.get("## Modules touched", ""))
    out: list[str] = []
    for it in items:
        tok, is_new = schema.extract_module_token(it)
        if tok and not is_new and _is_source(tok) and (root / tok).exists():
            out.append(tok)
    return sorted(set(out))


def _worktree_test_files(root: Path) -> set[str]:
    """Test files the builders wrote (worktrees/<role>/) — always affected."""
    wt = root / "worktrees"
    hits: set[str] = set()
    if not wt.exists():
        return hits
    for role in wt.iterdir():
        if not role.is_dir():
            continue
        for f in role.rglob("*"):
            if f.is_file() and f.suffix in _SRC_SUFFIXES \
                    and _is_test_file(f, role):
                hits.add(str(f.relative_to(role)))
    return hits


def _name_matched_tests(modules: list[str], root: Path) -> set[str]:
    """Tests whose filename references a touched module's basename
    (test_<module>.py and friends)."""
    bases = {Path(m).stem for m in modules if len(Path(m).stem) >= _MIN_BASE}
    if not bases:
        return set()
    hits: set[str] = set()
    for tdir in ("tests", "test"):
        d = root / tdir
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if (f.is_file() and f.suffix in _SRC_SUFFIXES
                    and _is_test_file(f, root)):
                stem = f.stem.lower()
                if any(b.lower() in stem for b in bases):
                    hits.add(str(f.relative_to(root)))
    return hits


def affected_tests(feature: str, root: Path) -> dict:
    """The fast-fail subset to run before the full suite. `full_suite_required`
    is always True — this orders work, it never gates.

    Reuses the SHARPENED plan-reach output (named-handler-filtered route reach +
    symbol/mock-leak reach) rather than raw test-reach, so the subset stays tight
    and relevant (on real clients-list: ~15, not the 53 raw reach produced by
    pulling in every unrelated route in a touched routes.py)."""
    modules = _source_modules(feature, root)
    affected: set[str] = set()
    affected |= _worktree_test_files(root)          # the builders' own tests
    affected |= _name_matched_tests(modules, root)  # test_<module>.py
    from prusik import blast_plan                    # sharp reach (#2)
    affected |= set(blast_plan.plan_test_reach(feature, root)["at_risk_tests"])
    return {
        "feature": feature,
        "touched_modules": modules,
        "affected": sorted(affected),
        "full_suite_required": True,
    }


def run(feature: str, root: Path | None = None, json_output: bool = False) -> int:
    from prusik import ledger
    root = root or ledger.project_root()
    result = affected_tests(feature, root)
    ledger.append("affected_tests", feature=feature,
                  affected=len(result["affected"]),
                  modules=len(result["touched_modules"]))
    if json_output:
        import json
        print(json.dumps(result, indent=2))
        return 0
    aff = result["affected"]
    if not aff:
        print(f"affected-tests — {feature}\n  (no affected tests identified from "
              f"scope + worktrees — run the full suite.)")
        return 0
    print(f"affected-tests — {feature}")
    print(f"  {len(aff)} affected test file(s) — run these FIRST (fail-fast):")
    for t in aff:
        print(f"    {t}")
    print("  ⚠ FULL SUITE still required at green — this subset is fail-fast only,")
    print("    NEVER the ship decision (a far regression won't be in it).")
    return 0
