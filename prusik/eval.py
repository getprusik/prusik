"""Prusik eval suite runner (v0.21.0).

The empirical-substantiation move. Each case in `benchmarks/cases/` is
grounded in an observed defect class from a real trial sprint; the
runner exercises prusik's static checks against the case's
`initial-repo/` (bug PRESENT) and `clean/` (bug FIXED — FP control)
and reports hit/miss against `expected-outcomes.yaml`.

Honest scope (mirrors `benchmarks/README.md`):
  - This is prusik's OWN check-mechanism benchmark. It substantiates
    "prusik's checks fire on the defect classes the trial observed."
  - It is NOT yet a full agent-vs-control benchmark (that's queued).
  - The runner has NO real-LLM dependency — it invokes the same pure
    detection functions (find_unbinding_pairs, find_test_reach) that
    prusik's `prusik gate check-*` subcommands invoke, against the
    case-local file tree. Deterministic, runs in CI in seconds.

Mission boundary: prusik MECHANIZES the check. Adjudicating whether
a given finding is a true bug vs. an intentional non-binding is
operator territory — but the eval suite tests something cleaner:
"on a curated case where the answer IS known, does the check fire?"
That's the falsifiable claim the suite substantiates.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


CASES_DIR_NAME = "benchmarks/cases"


def _benchmarks_root() -> Path:
    """The benchmarks/ directory. Resolved relative to prusik package
    install location so it works whether run from source-checkout or
    from a pip-installed prusik."""
    # Walk up from prusik/eval.py until we find benchmarks/
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, here.parent.parent):
        if (candidate / CASES_DIR_NAME).is_dir():
            return candidate / CASES_DIR_NAME
    # Fallback: cwd
    cwd = Path.cwd()
    if (cwd / CASES_DIR_NAME).is_dir():
        return cwd / CASES_DIR_NAME
    return Path()  # empty; caller handles absence


def _load_yaml(path: Path) -> dict:
    """Load YAML using PyYAML (prusik's canonical dep — phases.py /
    init.py / brief_lint.py all use it). Returns {} on missing-file
    (caller decides whether that's an error)."""
    if not path.exists():
        return {}
    try:
        import yaml
        with path.open("r") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        print(f"[prusik-eval] WARNING: failed to load {path}: {exc}",
              file=sys.stderr)
        return {}


def list_cases() -> list[dict]:
    """Discover corpus cases. Returns sorted list of {id, path, defect_class}."""
    root = _benchmarks_root()
    if not root.exists():
        return []
    cases: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "expected-outcomes.yaml").exists():
            continue
        spec = _load_yaml(d / "expected-outcomes.yaml")
        cases.append({
            "id": d.name,
            "path": d,
            "defect_class": spec.get("defect_class", "?"),
            "trial_origin": spec.get("trial_origin", "?"),
        })
    return cases


def _resolve_touched_set(repo_root: Path, spec: dict) -> list[Path]:
    """Determine touched-set for a variant.

    If spec declares `touched_set:`, use that (paths relative to the
    variant's repo root). Else default: all files in the variant tree
    EXCEPT test files (tests/* are not the touched set; they're what
    check-test-reach scans OUT OF). This default suits binding cases
    where every authored file is "touched."
    """
    declared = spec.get("touched_set")
    if declared:
        out = []
        for p in declared:
            full = repo_root / p
            if full.exists():
                out.append(full)
        return out
    # Default: every file under repo_root EXCEPT tests/
    out = []
    for f in repo_root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(repo_root)
        if rel.parts and rel.parts[0] in ("tests", "test"):
            continue
        out.append(f)
    return out


def _run_check_bindings(repo_root: Path, spec: dict) -> list[dict]:
    """Invoke the same detection function `prusik gate check-bindings` uses."""
    from prusik.binding_check import find_unbinding_pairs
    touched = _resolve_touched_set(repo_root, spec)
    return find_unbinding_pairs(touched, repo_root)


def _run_check_test_reach(repo_root: Path, spec: dict) -> list[dict]:
    """Invoke the same detection function `prusik gate check-test-reach` uses."""
    from prusik.test_reach import find_test_reach
    touched = _resolve_touched_set(repo_root, spec)
    return find_test_reach(touched, repo_root)


_CHECK_DISPATCH = {
    "check_bindings": _run_check_bindings,
    "check_test_reach": _run_check_test_reach,
}


def _assert_findings(findings: list[dict], expect: dict) -> list[str]:
    """Apply a variant's expect-block (initial OR clean) to findings.
    Returns a list of failure strings (empty = all assertions passed)."""
    failures: list[str] = []

    if "expect_findings" in expect:
        if len(findings) != expect["expect_findings"]:
            failures.append(
                f"expect_findings={expect['expect_findings']}, got "
                f"{len(findings)}")
    if "expect_findings_min" in expect:
        if len(findings) < expect["expect_findings_min"]:
            failures.append(
                f"expect_findings_min={expect['expect_findings_min']}, got "
                f"{len(findings)}")
    if "expect_classes" in expect:
        found_classes = {f.get("class") for f in findings}
        for c in expect["expect_classes"]:
            if c not in found_classes:
                failures.append(
                    f"expect_classes contains {c!r}, but actual classes "
                    f"are {sorted(c for c in found_classes if c) or '[]'}")
    if "expect_suggested_path" in expect:
        suggested = [s for f in findings for s in f.get("expected", []) or []]
        if expect["expect_suggested_path"] not in suggested:
            failures.append(
                f"expect_suggested_path={expect['expect_suggested_path']!r} "
                f"not in actual suggestions {suggested}")
    if "expect_template_name" in expect:
        names = {f.get("name") for f in findings if f.get("class") == "form_name"}
        if expect["expect_template_name"] not in names:
            failures.append(
                f"expect_template_name={expect['expect_template_name']!r} "
                f"not in form-name findings {sorted(n for n in names if n)}")
    if "expect_handler_key" in expect:
        # In v0.20.0 the form_name finding carries handler-side keys in
        # `expected`. We assert the key appears in at least one finding's
        # candidates list.
        cands = [k for f in findings if f.get("class") == "form_name"
                 for k in (f.get("expected") or [])]
        if expect["expect_handler_key"] not in cands:
            failures.append(
                f"expect_handler_key={expect['expect_handler_key']!r} "
                f"not in candidates {sorted(set(cands))}")
    if "expect_reaching_file" in expect:
        refs = [r for f in findings for r in f.get("references", [])]
        # Match by suffix — case-local paths may have absolute or relative shape
        if not any(r.endswith(expect["expect_reaching_file"]) for r in refs):
            failures.append(
                f"expect_reaching_file={expect['expect_reaching_file']!r} "
                f"not in references {refs}")
    if "expect_contract_id" in expect:
        cid = expect["expect_contract_id"]
        contract_ids = [f.get("contract_id") for f in findings]
        if cid not in contract_ids:
            failures.append(
                f"expect_contract_id={cid!r} not in finding contract_ids "
                f"{contract_ids}")
    return failures


def run_case(case: dict) -> dict:
    """Run one case across all its declared checks, on initial-repo + clean.
    Returns a result dict with per-check hit/miss + aggregate."""
    spec = _load_yaml(case["path"] / "expected-outcomes.yaml")
    initial_repo = case["path"] / "initial-repo"
    clean_repo = case["path"] / "clean"

    check_specs = spec.get("checks", [])
    per_check: list[dict] = []
    for check in check_specs:
        name = check.get("name")
        fn = _CHECK_DISPATCH.get(name)
        if fn is None:
            per_check.append({
                "check": name, "ok": False,
                "errors": [f"unknown check: {name!r}"],
            })
            continue

        # Run on initial — finding is REQUIRED
        try:
            initial_findings = fn(initial_repo, spec)
        except Exception as exc:
            per_check.append({
                "check": name, "ok": False,
                "errors": [f"initial run raised: {type(exc).__name__}: {exc}"],
            })
            continue
        initial_failures = _assert_findings(initial_findings,
                                              check.get("on_initial", {}))

        # Run on clean — finding-count expectation is the FP control
        clean_failures: list[str] = []
        if clean_repo.exists() and check.get("on_clean") is not None:
            try:
                clean_findings = fn(clean_repo, spec)
                clean_failures = _assert_findings(clean_findings,
                                                    check.get("on_clean", {}))
            except Exception as exc:
                clean_failures = [
                    f"clean run raised: {type(exc).__name__}: {exc}"
                ]
        else:
            clean_findings = []

        ok = not initial_failures and not clean_failures
        per_check.append({
            "check": name,
            "ok": ok,
            "initial_findings": len(initial_findings),
            "clean_findings": len(clean_findings),
            "initial_failures": initial_failures,
            "clean_failures": clean_failures,
        })

    overall_ok = bool(per_check) and all(c["ok"] for c in per_check)
    return {
        "case_id": case["id"],
        "defect_class": case.get("defect_class", "?"),
        "trial_origin": case.get("trial_origin", "?"),
        "ok": overall_ok,
        "checks": per_check,
    }


def run_agent_control(case_filter: str | None = None,
                       json_output: bool = False) -> int:
    """Agent-control comparison: prusik-on vs prusik-off (vibe-coding) on the
    curated corpus. The substantiation move v0.25.0 ships.

    Frame: each corpus case represents a defect class an LLM agent
    actually produced (DEV-1 root #1/#2, m4-suspect-skip-audit cross-
    touch-set, etc.). The case's `initial-repo/` IS the "vibe-coding"
    output — what would ship without prusik gating. The case's check
    fires under prusik-on; under prusik-off, the buggy code reaches integration
    unflagged.

    For each case:
      - prusik-off baseline: 0 catches by construction (no detection ran)
      - prusik-on: run the check; count it as 1 catch if any finding fires

    Aggregate: "prusik-on caught N/total defects vibe-coding would have
    shipped." The corpus is curated; the claim is honest about that.
    But the corpus is *grounded in real trial defects*, so the claim
    that "agents do produce these defect classes" is empirically true,
    not synthetic-by-imagination.

    Returns rc=0 if prusik-on catches every case (the expected outcome),
    rc=1 if any case slips through under prusik-on (regression signal).
    """
    cases = list_cases()
    if case_filter:
        cases = [c for c in cases if c["id"].startswith(case_filter)]
    if not cases:
        msg = (f"[prusik-eval-ac] no cases found"
               f"{' matching ' + repr(case_filter) if case_filter else ''}.")
        if json_output:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1 if case_filter else 0

    per_case: list[dict] = []
    kit_on_catches = 0
    for case in cases:
        result = run_case(case)
        # The "catch" signal: did any check produce findings on initial-repo?
        # (Equivalently: did prusik ANY-flag for this case?)
        any_flag = any(c.get("initial_findings", 0) > 0
                        for c in result.get("checks", []))
        if any_flag:
            kit_on_catches += 1
        per_case.append({
            "case_id": result["case_id"],
            "defect_class": result["defect_class"],
            "trial_origin": result["trial_origin"],
            "vibe_coding_outcome": "ships the bug (no detection)",
            "kit_on_outcome": "caught" if any_flag else "missed",
            "kit_on_findings": sum(c.get("initial_findings", 0)
                                     for c in result.get("checks", [])),
            "checks_passed": result["ok"],
        })

    total = len(cases)
    delta = kit_on_catches  # vs prusik-off = 0
    rc = 0 if kit_on_catches == total else 1

    if json_output:
        out = {
            "framing": (
                "Each corpus case represents a defect class an LLM "
                "agent actually produced (grounded in trial sprints). "
                "prusik-off ships the bug; prusik-on catches it (or misses, "
                "which is a regression signal)."
            ),
            "aggregate": {
                "total_cases": total,
                "kit_off_catches": 0,  # by construction
                "kit_on_catches": kit_on_catches,
                "improvement_count": delta,
                "improvement_rate": (delta / total) if total else 0.0,
            },
            "per_case": per_case,
        }
        print(json.dumps(out, indent=2))
        return rc

    # Human-readable
    print("[prusik-eval-ac] agent-control comparison "
          "(synthetic vibe-coding baseline on a corpus grounded "
          "in real LLM-agent trial defects):\n")
    for r in per_case:
        mark = "✓" if r["kit_on_outcome"] == "caught" else "✗"
        print(f"  {mark} {r['case_id']}  ({r['defect_class']})")
        print(f"      vibe-coding (prusik-off): {r['vibe_coding_outcome']}")
        print(f"      prusik-on:                {r['kit_on_outcome']} "
              f"({r['kit_on_findings']} flag(s))")
        print(f"      trial origin: {r['trial_origin']}")
        print()
    print(f"[prusik-eval-ac] aggregate ({total} case(s)):")
    print("  vibe-coding (prusik-off): 0 catches — every defect ships")
    print(f"  prusik-on:                {kit_on_catches} catches")
    print(f"  improvement:           {delta}/{total} defects caught by prusik "
          f"that would ship under vibe-coding ({100*delta/total:.0f}%)")
    print()
    print("  Honest scope: corpus is curated (4 cases on first pass); "
          "every case is grounded in an OBSERVED LLM-agent defect from "
          "the trial. The agent-vs-control claim here is "
          "\"on this defect class, prusik catches what vibe-coding misses\". "
          "Broader \"on arbitrary agent runs\" would need recorded-LLM "
          "transcripts — that's the v0.26+ extension.")
    if rc != 0:
        print()
        print(f"  ✗ regression — {total - kit_on_catches} case(s) slipped "
              f"under prusik-on. Investigate before claiming current ship "
              f"catches the defect classes listed.")
    return rc


def compute_scorecard() -> dict[str, Any]:
    """The unified, version-stamped fidelity SCORECARD (the keystone) — folds the THREE
    reproducible signals into one artifact with a pass/fail FLOOR:

      1. divergence-injection — the 3 deterministic failure modes (scope-drift/writable,
         premature-push/deny, fabricated-done/evidence) vs THIS config: catch-rate +
         discrimination (controls must NOT be flagged).
      2. corpus catch-rate — the benchmark cases (each a defect class a real LLM agent
         produced in a trial) the gates must flag on initial-repo without firing on clean.
      3. agent-control delta — prusik-on catches vs the vibe-coding (prusik-off = 0 by
         construction) baseline: 'prusik catches what vibe-coding ships.'

    `floor_met` is True ONLY when every injection divergence is caught with zero
    false-blocks AND every corpus case passes — so a gate-weakening engine/config change
    drops it to False (the regression signal). This is the evidence layer the adopter
    trust report renders and the cross-harness fidelity check compares against."""
    from prusik import __version__, injection, phases

    inj: dict[str, Any] = {"available": False}
    config = phases.load_sprint_config()
    if config:
        with tempfile.TemporaryDirectory() as td:
            ir = injection.run_cases(config, Path(td))
        isum = injection.summarize(ir)
        inj = {
            "available": True,
            "catch_rate": isum["catch_rate"],          # [caught, total]
            "discrimination": isum["discrimination"],  # [unflagged-controls, total]
            "misses": [m["id"] for m in isum["misses"]],
            "false_blocks": [m["id"] for m in isum["false_blocks"]],
        }

    cases = list_cases()
    case_results = [run_case(c) for c in cases]
    corpus_passed = sum(1 for r in case_results if r["ok"])
    ac_catches = sum(1 for r in case_results
                     if any(c.get("initial_findings", 0) > 0 for c in r["checks"]))

    inj_ok = inj["available"] and not inj["misses"] and not inj["false_blocks"]
    corpus_ok = len(cases) > 0 and corpus_passed == len(cases)

    return {
        "prusik_version": __version__,
        "floor_met": bool(inj_ok and corpus_ok),
        "injection": inj,
        "corpus": {
            "cases_passed": corpus_passed,
            "cases_total": len(cases),
            "by_case": {r["case_id"]: {"defect_class": r["defect_class"], "ok": r["ok"]}
                        for r in case_results},
        },
        "agent_control": {
            "prusik_on_catches": ac_catches,
            "prusik_off_catches": 0,
            "cases_total": len(cases),
        },
    }


def scorecard(json_output: bool = False, out: str | None = None) -> int:
    """Emit the unified fidelity scorecard. rc=0 only when the floor is met (every
    injection divergence caught with no false-blocks AND every corpus case passes);
    rc=1 on any regression. `out` also writes the JSON artifact (for the trust report /
    cross-version comparison)."""
    card = compute_scorecard()
    if out:
        try:
            Path(out).write_text(json.dumps(card, indent=2) + "\n")
        except OSError as e:
            print(f"[prusik-eval] could not write scorecard to {out}: {e}")
    rc = 0 if card["floor_met"] else 1

    if json_output:
        print(json.dumps(card, indent=2))
        return rc

    inj = card["injection"]
    cor = card["corpus"]
    ac = card["agent_control"]
    print(f"[prusik-eval] FIDELITY SCORECARD — prusik {card['prusik_version']}  "
          f"({'✓ FLOOR MET' if card['floor_met'] else '✗ REGRESSION'})\n")
    if inj["available"]:
        ic, it = inj["catch_rate"]
        dc, dt = inj["discrimination"]
        print(f"  1. divergence-injection : {ic}/{it} caught · "
              f"{dc}/{dt} controls correctly NOT blocked")
        if inj["misses"]:
            print(f"       ✗ MISSED: {', '.join(inj['misses'])}")
        if inj["false_blocks"]:
            print(f"       ✗ FALSE-BLOCKED: {', '.join(inj['false_blocks'])}")
    else:
        print("  1. divergence-injection : (no sprint-config — run `prusik init`)")
    print(f"  2. corpus catch-rate    : {cor['cases_passed']}/{cor['cases_total']} "
          f"defect-class cases flagged (clean stays clean)")
    print(f"  3. agent-control delta  : prusik-on {ac['prusik_on_catches']}/"
          f"{ac['cases_total']} vs vibe-coding 0 — caught what would ship\n")
    if not card["floor_met"]:
        print("  ✗ A signal regressed — a gate may have weakened. Investigate before "
              "shipping; this floor is the evidence the trust report stands on.")
    return rc


def run(case_filter: str | None = None,
        json_output: bool = False) -> int:
    """Run the eval suite. Optional case_filter selects a single case
    (matched by prefix on case id). Returns rc=0 if all hit, rc=1 if
    any miss — falsifiable signal for CI."""
    cases = list_cases()
    if case_filter:
        cases = [c for c in cases if c["id"].startswith(case_filter)]
    if not cases:
        msg = (f"[prusik-eval] no cases found"
               f"{' matching ' + repr(case_filter) if case_filter else ''}.")
        if json_output:
            print(json.dumps({"error": msg, "cases": []}))
        else:
            print(msg)
        return 1 if case_filter else 0

    results = [run_case(c) for c in cases]

    if json_output:
        # Strip non-serializable bits
        out_results: list[dict[str, Any]] = []
        for r in results:
            out_results.append({
                "case_id": r["case_id"],
                "defect_class": r["defect_class"],
                "trial_origin": r["trial_origin"],
                "ok": r["ok"],
                "checks": r["checks"],
            })
        agg = {
            "total": len(results),
            "passed": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
        }
        print(json.dumps({"aggregate": agg, "results": out_results}, indent=2))
        return 0 if agg["failed"] == 0 else 1

    # Human-readable
    print(f"[prusik-eval] running {len(results)} case(s):\n")
    passed = 0
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['case_id']}  ({r['defect_class']})")
        for c in r["checks"]:
            cmark = "  ✓" if c["ok"] else "  ✗"
            print(f"    {cmark} {c['check']}: "
                  f"initial={c.get('initial_findings', '?')}, "
                  f"clean={c.get('clean_findings', '?')}")
            for fail in c.get("initial_failures", []):
                print(f"        initial: {fail}")
            for fail in c.get("clean_failures", []):
                print(f"        clean: {fail}")
            for err in c.get("errors", []):
                print(f"        error: {err}")
        if r["ok"]:
            passed += 1
        print()
    print(f"[prusik-eval] aggregate: {passed}/{len(results)} cases passed.")
    if passed < len(results):
        print("  Falsifiable miss(es) — investigate before claiming the "
              "prusik catches the defect classes listed.")
        return 1
    return 0
