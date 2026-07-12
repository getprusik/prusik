"""Plan-time blast-radius — surface tests at risk BEFORE the build (v0.63.0;
sharpened v0.64.0 from an adopter's [13:10] bridge diagnostic on clients-list-fidelity).

An adopter's single biggest cost across three sprints: a structural change to an
existing module rippled into EXISTING tests living OUTSIDE the sprint's touched
set — so reviewing (touched-set-only, by design) never loaded them, and the
break surfaced one-or-more fix-rounds later (team-invites: ~145 tests broke on a
missing mock stub; clients-list: a unit stub, same shape). This runs test-reach
at PLAN time, using `design/<feature>/plan.md`, so the planner sees what's about
to ripple and exercises it before the build — turning N fix-rounds into 1.

Two complementary signals, ranked by confidence:

  1. SYMBOL-REACH (highest confidence) — when `## Interfaces` adds a new method
     to a class/Protocol (e.g. `paid_ytd_by_client` on InvoiceRepository), find
     tests that MOCK/STUB that repo but don't set the new attribute: a guaranteed
     mock-leak (AttributeError / MagicMock into a real call) at run time. This is
     the exact shape that broke clients-list + team-invites.
  2. ROUTE/TEMPLATE/FORM CONTRACT-REACH — tests outside the touched set that
     reference a contract the plan changes. INTERSECTED with the handlers the
     plan actually NAMES (in `## Build order` / `## Interfaces`), so touching a
     big `routes.py` doesn't flag every unrelated route in it (clients-list:
     48 → ~5 once filtered to the `clients_*` handlers the plan names).

A SIGNAL, not a gate (same boundary as reviewer-time test-reach): prusik
surfaces the at-risk tests; whether a flagged test actually breaks is
project-context the planner adjudicates.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from prusik import schema
from prusik.binding_detect import (
    extract_fastapi_routes, extract_router_prefixes, is_python_route_file,
)
from prusik.test_reach import _grep_tests, find_test_reach

_SYMBOL_SECTIONS = ("## Build order", "## Interfaces")
_BACKTICK_IDENT = re.compile(r"`([A-Za-z_]\w*)`")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_CLASS_RE = re.compile(r"class\s+([A-Za-z_]\w*)\s*\(")
_MOCK_CLASS_RE = r"(?:MagicMock|AsyncMock|Mock)"
_REPORT_MAX = 12
_ADVISORY_MAX = 6


def _plan_path(feature: str, root: Path) -> Path:
    return root / "design" / feature / "plan.md"


def plan_modules(feature: str, root: Path) -> tuple[list[str], list[str]]:
    """(existing, new) module paths from design/<feature>/plan.md Modules touched.
    Existing = files that exist now (their change can break tests). New files
    (`+ path`) have no existing tests to break yet, so they're reported
    separately, not scanned."""
    plan_path = _plan_path(feature, root)
    if not plan_path.exists():
        return [], []
    sections = schema.parse_sections(plan_path.read_text())
    items = schema.extract_list_items(sections.get("## Modules touched", ""))
    existing: list[str] = []
    new: list[str] = []
    for it in items:
        tok, is_new = schema.extract_module_token(it)
        if not tok:
            continue
        if is_new:
            new.append(tok)
        elif (root / tok).exists():
            existing.append(tok)
    return existing, new


# ---- plan symbol parsing (Build order + Interfaces) ----

def _named_symbols(plan_text: str) -> set[str]:
    """Backtick-wrapped identifiers the plan NAMES in Build order / Interfaces —
    the handlers/symbols it actually touches (`clients_list`, `clients_update`,
    `paid_ytd_by_client`, …)."""
    secs = schema.parse_sections(plan_text)
    syms: set[str] = set()
    for sec in _SYMBOL_SECTIONS:
        for m in _BACKTICK_IDENT.finditer(secs.get(sec, "")):
            syms.add(m.group(1))
    return syms


def _new_methods(plan_text: str) -> list[tuple[str, str]]:
    """(owner_class, method) for new methods declared in ## Interfaces — the
    `def m(` lines under a `class X(...)` mention. These are the symbols a mock
    of X would be missing."""
    body = schema.parse_sections(plan_text).get("## Interfaces", "")
    out: list[tuple[str, str]] = []
    current = ""
    for line in body.splitlines():
        cm = _CLASS_RE.search(line)
        if cm:
            current = cm.group(1)
        dm = _DEF_RE.match(line)
        if dm and current:
            out.append((current, dm.group(1)))
    return out


# ---- #1: route → handler, filter to plan-named handlers ----

def _routes_with_handlers(text: str) -> list[tuple[str, str, str]]:
    """(router, local_path, handler_name) per route. Handler = the first `def`
    within a few lines after the route decorator."""
    lines = text.splitlines()
    out: list[tuple[str, str, str]] = []
    for r in extract_fastapi_routes(text):
        handler = ""
        start = r["line"] - 1
        for j in range(start, min(start + 8, len(lines))):
            dm = _DEF_RE.match(lines[j])
            if dm:
                handler = dm.group(1)
                break
        out.append((r["router"], r["path"], handler))
    return out


def _named_route_paths(touched: list[Path], named: set[str], root: Path) -> set[str]:
    """Qualified route paths in the touched files whose handler the plan NAMES."""
    paths: set[str] = set()
    for p in touched:
        if not is_python_route_file(p):
            continue
        try:
            text = p.read_text()
        except OSError:
            continue
        prefixes = extract_router_prefixes(text)
        for router, path, handler in _routes_with_handlers(text):
            if handler and handler in named:
                prefix = prefixes.get(router, "")
                paths.add((prefix + path) if prefix else path)
    return paths


# ---- #2: symbol-reach (mock-leak risk) ----

def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _owner_hints(owner: str) -> set[str]:
    """Tokens a test that mocks `owner` would mention — the class name, its
    snake form, and a `<base>_repo` stub-variable convention."""
    snake = _snake(owner)
    base = re.sub(r"_(repository|repo|protocol|service|store|gateway)$", "", snake)
    return {owner, snake, f"{base}_repo", f"{base}_repository"}


def _repo_vars(owner: str) -> set[str]:
    """The variable names a test would bind a mock of `owner` to."""
    snake = _snake(owner)
    base = re.sub(r"_(repository|repo|protocol|service|store|gateway)$", "", snake)
    return {f"{base}_repo", f"{base}_repository", snake}


def _real_impl_re(owner: str) -> re.Pattern:
    """Matches construction of a CONCRETE impl of the repo — a prefixed class
    like `PsycopgInvoiceRepository(`. A file that builds the real thing can't
    mock-leak, so its presence is a strong negative signal."""
    base = re.sub(r"(Repository|Protocol|Service|Store|Gateway)$", "", owner)
    return re.compile(rf"\b\w+{re.escape(base)}Repository\s*\(")


def _mock_binds_repo(text: str, repo_vars: set[str]) -> bool:
    """True iff a mock actually BINDS to the repo — `<repo> = MagicMock()`, a
    `_make_stub_<repo>`/`fake_<repo>` factory, or `patch(...<repo>...)` — not a
    bare `stub`/`_repo` token that merely co-occurs (prose, test-data, an
    unrelated page stub). This is what took an adopter's FP rate 5/5 → 0/5."""
    for v in repo_vars:
        ev = re.escape(v)
        if re.search(rf"\b{ev}\s*=\s*\w*{_MOCK_CLASS_RE}\s*\(", text):
            return True
        if re.search(rf"(?:stub|make|fake|mock)\w*{ev}\b", text):
            return True
        if re.search(rf"patch\([^)]*{ev}", text):
            return True
    return False


def _symbol_reach(plan_text: str, touched: list[Path], root: Path) -> list[dict]:
    """Tests that MOCK/STUB a class the plan adds a method to, but never set the
    new method — a guaranteed mock-leak. Two filters keep the FP rate near zero
    (from an adopter's verified 5/5-FP calibration): a file that constructs the REAL
    impl can't mock-leak (excluded), and the mock must actually bind to the repo
    var (not a bare token co-occurring in prose/test-data)."""
    touched_set = {p.resolve() for p in touched}
    findings: list[dict] = []
    for owner, method in _new_methods(plan_text):
        real_impl = _real_impl_re(owner)
        repo_vars = _repo_vars(owner)
        candidates: set[str] = set()
        for hint in _owner_hints(owner):
            candidates |= set(_grep_tests(root, hint, touched_set, max_hits=50))
        at_risk: list[str] = []
        for rel in sorted(candidates):
            try:
                ttext = (root / rel).read_text()
            except OSError:
                continue
            if method in ttext:                  # stub already sets it → safe
                continue
            if real_impl.search(ttext):          # builds the real impl → can't leak
                continue
            if not _mock_binds_repo(ttext, repo_vars):   # not actually a repo mock
                continue
            at_risk.append(rel)
        if at_risk:
            findings.append({
                "class": "symbol",
                "contract_id": f"{owner}.{method}",
                "contract_kind": "new method — mock-leak risk",
                "references": at_risk[:5],
            })
    return findings


# ---- compose ----

def _import_reach(existing: list[str], root: Path) -> tuple[list[str], list[str]]:
    """Raise the prediction's RECALL via the import graph (field retro: the gate's
    honesty == the dep-graph's recall). Contract-reach only sees tests that name a
    changed route/template/form literal; a test that simply IMPORTS a changed
    module is a silent miss. The dep-graph already has those edges — use them: for
    each changed module, the TEST files that import it (directly) are at-risk too.
    Returns (at-risk test files, modules that had any graph edge — a coverage
    signal: a changed module with NO edge is a recall blind spot the graph can't
    help with, the hollow-edges ceiling)."""
    from prusik import discovery
    from prusik.test_reach import _is_test_file
    tests: set[str] = set()
    covered: list[str] = []
    for m in existing:
        prefix = re.sub(r"\.\w+$", "", m)        # strip extension → module prefix
        try:
            hits = discovery.blast_radius(prefix, root)
        except (OSError, ValueError):
            continue
        if hits:
            covered.append(m)
        for h in hits:
            if _is_test_file(root / h, root):
                tests.add(h)
    return sorted(tests), covered


def plan_test_reach(feature: str, root: Path) -> dict:
    existing, new = plan_modules(feature, root)
    touched = [root / m for m in existing]
    plan_text = _plan_path(feature, root).read_text() \
        if _plan_path(feature, root).exists() else ""

    route_reach = find_test_reach(touched, root) if touched else []
    # #1 — intersect findings with what the plan actually NAMES, so touching a
    # big routes/settings file doesn't flag every unrelated contract defined in
    # it. Routes: keep those whose handler the plan names. Form/handler keys:
    # keep those named in the plan (an unchanged billing form's 16 fields are
    # noise). Templates stay — they're already scoped to touched files. Conserva-
    # tive: only filters when the plan names symbols, and never drops all routes
    # on a handler-mapping miss (`not named_paths` keeps them).
    named = _named_symbols(plan_text)
    named_paths = _named_route_paths(touched, named, root) if named else set()

    def _keep(f: dict) -> bool:
        cls = f.get("class")
        if cls == "route":
            return (not named_paths) or (f["contract_id"] in named_paths)
        if cls in ("form_name", "handler_key"):
            return f["contract_id"] in named
        return True  # templates (already touched-scoped) + anything else

    if named:
        route_reach = [f for f in route_reach if _keep(f)]

    # #2 — symbol-reach, ranked ABOVE route-reach (higher confidence).
    symbol_reach = _symbol_reach(plan_text, touched, root) if plan_text else []

    # v0.115.0 (An adopter recall): union the contract/symbol reach with IMPORT reach
    # from the dep-graph, so a test that imports a changed module — but names no
    # route/template/form literal — is no longer a silent miss.
    import_reach, graph_covered = _import_reach(existing, root)
    at_risk = sorted({t for f in (symbol_reach + route_reach)
                      for t in f["references"]} | set(import_reach))
    return {
        "feature": feature,
        "modules_existing": existing,
        "modules_new": new,
        "symbol_reach": symbol_reach,
        "reach": route_reach,
        "import_reach": import_reach,
        "graph_covered": graph_covered,     # changed modules the graph has edges for
        "at_risk_tests": at_risk,
    }


def _ranked_route_reach(reach: list[dict]) -> list[dict]:
    """Sharpest first: a contract referenced by fewer out-of-set tests is more
    specific than one every smoke test mentions."""
    return sorted(reach, key=lambda f: len(f["references"]))


def _all_findings_ranked(result: dict) -> list[dict]:
    """Symbol-reach first (guaranteed mock-leak), then route/template/form
    contract-reach sharpest-first."""
    return list(result["symbol_reach"]) + _ranked_route_reach(result["reach"])


def advisory(feature: str, root: Path) -> str | None:
    result = plan_test_reach(feature, root)
    findings = _all_findings_ranked(result)
    if not findings:
        return None
    n_tests = len(result["at_risk_tests"])
    n_sym = len(result["symbol_reach"])
    head = (f"[prusik-gate] plan-reach ADVISORY — {n_tests} test file(s) outside "
            f"this plan's module set may break")
    if n_sym:
        head += f" ({n_sym} mock-leak risk(s) — highest confidence)"
    lines = [head + ".",
             "  Won't be loaded by reviewing (touched-set only) — exercise these "
             "before the build to avoid a post-integration fix-round:"]
    for f in findings[:_ADVISORY_MAX]:
        lines.append(f"    · {f['contract_kind']} {f['contract_id']} "
                     f"→ {', '.join(f['references'])}")
    if len(findings) > _ADVISORY_MAX:
        lines.append(f"    … +{len(findings) - _ADVISORY_MAX} more — "
                     f"`prusik plan-reach {feature}` for the full list.")
    return "\n".join(lines)


def _format_report(result: dict) -> str:
    feat = result["feature"]
    if not result["modules_existing"] and not result["modules_new"]:
        return (f"[plan-reach] no plan.md Modules touched found for '{feat}' "
                f"(design/{feat}/plan.md). Nothing to analyze.")
    findings = _all_findings_ranked(result)
    lines = [f"plan-reach — {feat}",
             f"  modules: {len(result['modules_existing'])} existing, "
             f"{len(result['modules_new'])} new (new files have no existing "
             f"tests to break)"]
    if not findings:
        lines.append("  ✓ no tests outside the touched set reference these "
                     "modules' contracts or mock their changed symbols.")
        return "\n".join(lines)
    if result["symbol_reach"]:
        lines.append(f"  ⚠ {len(result['symbol_reach'])} mock-leak risk(s) "
                     f"(highest confidence — a stub missing the new method):")
        for f in result["symbol_reach"]:
            lines.append(f"    {f['contract_id']}")
            for ref in f["references"]:
                lines.append(f"        → {ref}")
    route = _ranked_route_reach(result["reach"])
    if route:
        lines.append(f"  ⚠ {len(result['at_risk_tests'])} at-risk test file(s) "
                     f"total — contract-reach (sharpest first):")
        for f in route[:_REPORT_MAX]:
            lines.append(f"    {f['contract_kind']}: {f['contract_id']}")
            for ref in f["references"]:
                lines.append(f"        → {ref}")
        if len(route) > _REPORT_MAX:
            lines.append(f"    … +{len(route) - _REPORT_MAX} more — --json "
                         f"for the full set.")
    lines.append("  (signal, not a gate — confirm whether each would exercise "
                 "your change.)")
    return "\n".join(lines)


def _prediction_path(feature: str, root: Path) -> Path:
    return root / ".sprint" / f"blast-prediction.{feature}.json"


def record_prediction(feature: str, root: Path) -> dict:
    """Persist the plan-time blast-radius PREDICTION structured, so reviewing can
    later VERIFY it was consumed (field retro #1: the prediction was prose,
    computed, then ignored — the 28-test break was its price). The at-risk set is
    already computed by plan_test_reach; this just makes it durable + auditable."""
    result = plan_test_reach(feature, root)
    pred = {
        "feature": feature,
        "at_risk_tests": result["at_risk_tests"],
        "symbol_leak_tests": sorted(
            {t for f in result["symbol_reach"] for t in f["references"]}),
    }
    if (root / ".sprint").exists():
        _prediction_path(feature, root).write_text(json.dumps(pred, indent=2))
    return pred


def verify_prediction(feature: str, root: Path) -> dict:
    """Consume the prediction at reviewing: which predicted at-risk tests were NOT
    touched this sprint. A predicted-regressing test the build never updated is the
    foreseen regression the harness made and ignored — surface it by name. Recomputes
    deterministically if no persisted prediction exists (idempotent)."""
    from prusik import consistency
    p = _prediction_path(feature, root)
    pred = json.loads(p.read_text()) if p.exists() else record_prediction(feature, root)
    at_risk = list(pred.get("at_risk_tests", []))
    touched = consistency.sprint_changed_files(root)
    unverified = [t for t in at_risk if t not in touched]
    return {
        "feature": feature,
        "at_risk_tests": at_risk,
        "touched": [t for t in at_risk if t in touched],
        "unverified": unverified,           # predicted-regressing, never updated
        "symbol_leak_tests": list(pred.get("symbol_leak_tests", [])),
    }


def verification_advisory(feature: str, root: Path) -> str | None:
    """Reviewing-side advisory naming the predictions the build left unconsumed.
    None when every predicted at-risk test was touched (or none were predicted)."""
    v = verify_prediction(feature, root)
    if not v["unverified"]:
        return None
    lines = [
        f"[prusik-gate] blast-radius VERIFY — {len(v['unverified'])} of "
        f"{len(v['at_risk_tests'])} predicted at-risk test(s) were NOT updated "
        f"this sprint (scope's Blast-radius prediction, unconsumed):"]
    for t in v["unverified"]:
        leak = " (mock-leak risk)" if t in v["symbol_leak_tests"] else ""
        lines.append(f"    · {t}{leak}")
    lines.append("  These tests reference a changed contract but weren't touched "
                 "— verify they still assert correct behavior under the change "
                 "(a guard added to a route they exercise will reject the old "
                 "assumption), not pass vacuously. `prusik blast-verify "
                 f"{feature}` for detail.")
    return "\n".join(lines)


_PYTEST_FAIL = re.compile(r"^FAILED\s+(\S+?)(?:::|\s|$)", re.M)
_VITEST_FAIL = re.compile(r"^\s*(?:FAIL|×|✗)\s+(\S+\.(?:test|spec)\.[jt]sx?)", re.M)


def failed_test_files(output: str) -> set[str]:
    """Test FILES that failed, parsed from a regression run's own output (pytest
    `FAILED path::test`, vitest `FAIL path`). The observed-breaks half of the
    recall measurement — the tool's own signal, not narrated."""
    files = set()
    for m in _PYTEST_FAIL.finditer(output):
        files.add(m.group(1))
    for m in _VITEST_FAIL.finditer(output):
        files.add(m.group(1))
    return files


def recall_report(predicted: list[str], broke: set[str]) -> dict:
    """Ground-truth recall = predicted ∩ broke / broke. The `silent_misses` (broke
    AND NOT predicted) are the edges the graph DIDN'T have — the exact thing the
    gate exists to prevent, and the input to the moat-loop applied to the graph
    itself: classify each miss's edge-class (path? DI? contract?), encode an
    extractor, add a regression case → never miss that edge-class twice."""
    p, b = set(predicted), set(broke)
    hits = p & b
    misses = b - p
    return {
        "predicted": sorted(p), "broke": sorted(b),
        "hits": sorted(hits), "silent_misses": sorted(misses),
        "recall_pct": round(100 * len(hits) / len(b)) if b else None,
    }


def recall_run(feature: str, command: list[str] | None,
               root: Path | None = None, json_output: bool = False) -> int:
    """`prusik blast-recall <feature> -- <regression-cmd>` — run the regression,
    parse which tests actually broke, and measure how many the blast-radius
    prediction caught. Surfaces the SILENT MISSES (un-predicted breaks): each is a
    new edge-class to encode. Recall ≫ precision for a safety gate — a low number
    means the graph is missing dynamic edge-classes (route/DI/contract), not that
    the parser is bad.

    PRECONDITION (An adopter): feed the FULL suite, not a subset. The loop learns from
    broke ∩ ¬predicted, which only works if every miss is OBSERVABLE — that is the
    full-suite floor (#14). The three findings compose at three latencies: #14
    catches every miss late-but-total → blast-recall encodes each into an
    extractor → the blast gate (#1) catches that class early forever after. So an
    unencoded edge-class gets exactly one free late-catch before promotion. Run
    against a subset and misses go unobserved → false-high recall."""
    import subprocess

    from prusik import ledger
    root = root or ledger.project_root()
    cmd = list(command or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[prusik-blast-recall] give a regression command after `--`, e.g. "
              "`prusik blast-recall <feat> -- pytest -q`", file=sys.stderr)
        return 2
    predicted = verify_prediction(feature, root)["at_risk_tests"]
    try:
        proc = subprocess.run(["/bin/bash", "-c", " ".join(cmd)], cwd=str(root),
                              capture_output=True, text=True, timeout=1800,
                              check=False)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[prusik-blast-recall] regression command failed to run: {e}",
              file=sys.stderr)
        return 2
    rep = recall_report(predicted, failed_test_files(combined))
    ledger.append("blast_recall", feature=feature, recall_pct=rep["recall_pct"],
                  broke=len(rep["broke"]), predicted=len(rep["predicted"]),
                  silent_misses=len(rep["silent_misses"]))
    if json_output:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    if not rep["broke"]:
        print(f"[prusik-blast-recall] no test failures observed for '{feature}' "
              f"— nothing to measure recall against.")
        return 0
    print(f"[prusik-blast-recall] recall {rep['recall_pct']}% — predicted "
          f"{len(rep['hits'])} of {len(rep['broke'])} observed regression(s).")
    if rep["silent_misses"]:
        print("  SILENT MISSES (broke but NOT predicted — the graph lacked the "
              "edge; classify + encode the edge-class so it's never missed again):")
        for t in rep["silent_misses"]:
            print(f"    ✗ {t}")
    else:
        print("  ✓ every observed regression was in the at-risk set.")
    print("  (recall is honest only against the FULL suite — the #14 floor that "
          "makes every miss observable; a subset under-reports silent misses.)")
    return 0


def verify_run(feature: str, root: Path | None = None, json_output: bool = False,
               strict: bool = False) -> int:
    """`prusik blast-verify <feature>` — did the build consume the plan's
    blast-radius prediction? Advisory (rc 0) by default; `--strict` (or the
    reviewing gate with `require_blast_radius_verified`) returns rc≠0 on any
    predicted-regressing test left untouched."""
    from prusik import ledger
    root = root or ledger.project_root()
    v = verify_prediction(feature, root)
    ledger.append("blast_verify", feature=feature,
                  at_risk=len(v["at_risk_tests"]), unverified=len(v["unverified"]),
                  strict=strict)
    if json_output:
        print(json.dumps(v, indent=2, default=str))
    else:
        adv = verification_advisory(feature, root)
        print(adv if adv else
              f"[prusik-gate] blast-radius VERIFY ✓ — all "
              f"{len(v['at_risk_tests'])} predicted at-risk test(s) for "
              f"'{feature}' were touched this sprint.")
    return 1 if (strict and v["unverified"]) else 0


def run(feature: str, root: Path | None = None, json_output: bool = False) -> int:
    from prusik import ledger
    root = root or ledger.project_root()
    result = plan_test_reach(feature, root)
    record_prediction(feature, root)   # persist the prediction for reviewing to verify
    ledger.append("plan_test_reach", feature=feature,
                  at_risk_count=len(result["at_risk_tests"]),
                  symbol_leaks=len(result["symbol_reach"]),
                  contracts=len(result["reach"]),
                  modules_existing=len(result["modules_existing"]))
    if json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(_format_report(result))
    return 0
