"""Test-set-reach pre-check (v0.20.0).

The cross-touch-set coverage gap surfaced by live-cc on
m4-gate-domain-debt (occurrence 1) and m4-suspect-skip-audit (occurrence
2 with THREE instances in one sprint, all caught at post-integration,
none at reviewing). Per the v0.12.0 §4 boundary characterization:

  > Reviewing operates against the partial worktree mirror (only files
  > the sprint touched, per v0.7.0 / B17 — a deliberate design avoiding
  > the worktree-vs-integrated false-positive class). Tests that exist
  > OUTSIDE the touched-set but assert on the same contract a touched
  > file implements are STRUCTURALLY NOT LOADED by reviewing — a
  > cross-contract conflict is surfaced only by the post-integration
  > full-suite gate, which is the load-bearing backstop by design.

v0.20.0 mechanizes a **reviewer-time SIGNAL** (NOT a gate) for tests
outside the touched set that reference contracts the sprint touched.
This gets the catch one layer earlier than the post-integration gate
WHEN the cost is acceptable — and decision-support means the reviewer
sees what's coming, even if prusik can't force them to exercise it.

Per the design pass and the [09:07] live-cc observation: layered defense
is still load-bearing (post-integration full-suite is *the* backstop).
This pre-check trades a manageable false-positive rate for earlier
visibility. Conservative scope keeps the FP rate honest:

  - Only TOUCHED routes/templates/handlers are extracted (reuses the
    v0.19.0 binding_detect.py extractors — shared infrastructure).
  - Tests considered = tests/** outside the touched set (the worktrees/
    subtree). Same-touched-set tests aren't "outside-reach" by
    definition.
  - References that count: literal string occurrence in test files.
    Conservative — not assertion-context-aware in v0.20.0 (deferred:
    parsing pytest's assert AST or .toBe()/expect() in JS is per-stack
    and costly; the literal-grep heuristic surfaces what the operator
    needs to look at, with the FP cost of "test mentions the literal
    but in a setup/helper context" — acceptable for a flag).
  - Top-N capped (default 5) per touched contract — the goal is "pay
    attention to these," not "audit 50 things."

Honest boundary (same shape as v0.18.0 skip-flag, v0.19.0 binding-flag):
prusik MECHANIZES the flag; adjudicating whether a flagged test will
actually exercise the changed contract correctly is project-context
territory (mission boundary). Reviewer/operator decides.
"""

from __future__ import annotations

import re
from pathlib import Path
from prusik.binding_detect import (
    extract_fastapi_routes, extract_router_prefixes,
    extract_handler_form_keys, extract_form_names,
    is_python_route_file, is_template_file,
)
from prusik.binding_detect_js import (
    extract_express_routes, extract_router_prefixes_js,
    extract_handler_form_keys_js, extract_nextjs_routes,
    extract_jsx_form_names, is_js_route_file,
    is_jsx_template_file, is_js_handler_file,
)


# Conservative: only files matching these patterns are considered "tests"
# for reach analysis. Mirrors the existing prusik conventions (tests/, test_,
# *_test.*).
_TEST_DIRS = ("tests", "test")
_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_|.*_test\.)\w+$")


def _is_test_file(p: Path, root: Path) -> bool:
    """A file is 'test' if it's under tests/ or test/ dirs OR has a
    test_*.py / *_test.py shape."""
    try:
        rel = p.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    if any(d in parts for d in _TEST_DIRS):
        return True
    return bool(_TEST_FILE_RE.search(str(rel)))


def _extract_touched_contracts(touched_files: list[Path],
                                root: Path) -> dict:
    """From the touched set, extract all contract literals that
    out-of-set tests might reference. Returns a dict of:
      {
        "routes": [{path, qualified_path, file}, ...],
        "templates": [{name, file}, ...],     # template filenames
        "form_names": [name, ...],
        "handler_keys": [key, ...],
      }
    Reuses the v0.19.0 binding_detect extractors — shared infra."""
    py_files = [p for p in touched_files if is_python_route_file(p)]
    tmpl_files = [p for p in touched_files if is_template_file(p)]
    js_route_files = [p for p in touched_files if is_js_route_file(p)]
    jsx_template_files = [p for p in touched_files if is_jsx_template_file(p)]
    js_handler_files = [p for p in touched_files if is_js_handler_file(p)]

    routes: list[dict] = []
    prefixes_per_file: dict[Path, dict] = {}
    for py in py_files:
        try:
            text = py.read_text()
        except OSError:
            continue
        prefixes_per_file[py] = extract_router_prefixes(text)
        for r in extract_fastapi_routes(text):
            prefix = prefixes_per_file[py].get(r["router"], "")
            qualified = (prefix + r["path"]) if prefix else r["path"]
            routes.append({"path": r["path"],
                           "qualified_path": qualified,
                           "file": py, "method": r["method"]})
    # v0.22.0 — JS/TS routes
    for js in js_route_files:
        try:
            text = js.read_text()
        except OSError:
            continue
        js_prefixes = extract_router_prefixes_js(text)
        prefixes_per_file[js] = js_prefixes
        for r in extract_express_routes(text):
            prefix = js_prefixes.get(r["router"], "")
            qualified = (prefix + r["path"]) if prefix else r["path"]
            routes.append({"path": r["path"],
                           "qualified_path": qualified,
                           "file": js, "method": r["method"]})
        for r in extract_nextjs_routes(js, root):
            routes.append({"path": r["path"],
                           "qualified_path": r["path"],
                           "file": js, "method": r["method"]})

    templates: list[dict] = []
    form_names_set: set[str] = set()
    for tpl in tmpl_files:
        templates.append({"name": tpl.name, "file": tpl})
        try:
            text = tpl.read_text()
        except OSError:
            continue
        for n in extract_form_names(text):
            form_names_set.add(n["name"])
    for tpl in jsx_template_files:
        templates.append({"name": tpl.name, "file": tpl})
        try:
            text = tpl.read_text()
        except OSError:
            continue
        for n in extract_jsx_form_names(text):
            form_names_set.add(n["name"])

    handler_keys: set[str] = set()
    for py in py_files:
        try:
            text = py.read_text()
        except OSError:
            continue
        for h in extract_handler_form_keys(text):
            handler_keys.add(h["key"])
    for js in js_handler_files:
        try:
            text = js.read_text()
        except OSError:
            continue
        for h in extract_handler_form_keys_js(text):
            handler_keys.add(h["key"])

    return {
        "routes": routes,
        "templates": templates,
        "form_names": sorted(form_names_set),
        "handler_keys": sorted(handler_keys),
    }


def _grep_tests(root: Path, needle: str,
                 exclude_paths: set[Path],
                 max_hits: int = 5) -> list[str]:
    """Find up to max_hits test files containing the literal needle,
    excluding any path in exclude_paths (the touched set). Returns
    relative paths."""
    import subprocess as _sp
    if not needle or any(ch in needle for ch in "*?[]\\$`'\""):
        return []  # Unsafe characters; bail out
    skip_dirs = (".sprint", "reports", "worktrees", ".git", "__pycache__",
                 "node_modules", "dist", "build", ".pytest_cache",
                 ".mypy_cache", ".ruff_cache", ".runtime")
    # We want HITS only inside test files — restrict to tests/ + test/.
    test_roots = []
    for d in _TEST_DIRS:
        candidate = root / d
        if candidate.exists():
            test_roots.append(str(candidate))
    if not test_roots:
        return []
    args = ["grep", "-rIl", "-F"]  # -F: fixed-string match (safer)
    for d in skip_dirs:
        args += ["--exclude-dir", d]
    args += [needle] + test_roots
    try:
        out = _sp.run(args, capture_output=True, text=True, timeout=15,
                       check=False).stdout
    except (OSError, _sp.TimeoutExpired):
        return []
    matches: list[str] = []
    excluded_abs = {p.resolve() for p in exclude_paths}
    for ln in out.splitlines():
        if not ln.strip():
            continue
        try:
            abspath = Path(ln).resolve()
        except OSError:
            continue
        if abspath in excluded_abs:
            continue  # The test IS in the touched set; reach is reviewed
        try:
            rel = abspath.relative_to(root.resolve())
            matches.append(str(rel))
        except ValueError:
            matches.append(ln)
        if len(matches) >= max_hits:
            break
    return matches


def find_test_reach(touched_files: list[Path], root: Path) -> list[dict]:
    """Scan tests/** outside the touched set for references to contracts
    the sprint touched. Returns findings:

      [{class, contract_id, contract_kind, file_hint, references: [tests]}]

    contract_kind ∈ {"route", "template", "form_name", "handler_key"}.

    Conservative — only literal-string occurrence in test files.
    Assertion-context awareness deferred to a follow-up pass; for v0.20.0
    the flag is "these test files mention this contract literal — check
    if they'd exercise your change."
    """
    contracts = _extract_touched_contracts(touched_files, root)
    touched_set = {p.resolve() for p in touched_files}
    findings: list[dict] = []

    # Routes — use the FULLY-QUALIFIED path (the form an HTTP-shaped test
    # would assert against). Bare local path on a prefixed router isn't
    # what a real test references.
    for r in contracts["routes"]:
        # Skip the truly-trivial paths that any test might mention by
        # accident (e.g. "/" alone) — conservative FP control.
        if r["qualified_path"] in ("/", ""):
            continue
        hits = _grep_tests(root, r["qualified_path"], touched_set)
        if hits:
            findings.append({
                "class": "route",
                "contract_id": r["qualified_path"],
                "contract_kind": f"{r['method'].upper()} route",
                "file_hint": str(r["file"].relative_to(root))
                              if r["file"].is_relative_to(root) else str(r["file"]),
                "references": hits,
            })

    # Templates — file name (e.g. "_inline_client_form.html"). Test
    # references to template files are commonly via TemplateResponse
    # assertions or render-output asserts.
    for t in contracts["templates"]:
        hits = _grep_tests(root, t["name"], touched_set)
        if hits:
            findings.append({
                "class": "template",
                "contract_id": t["name"],
                "contract_kind": "template",
                "file_hint": str(t["file"].relative_to(root))
                              if t["file"].is_relative_to(root) else str(t["file"]),
                "references": hits,
            })

    # Form names — short tokens common in form-test assertions.
    # Filter generic short tokens (<4 chars) to reduce FPs.
    for name in contracts["form_names"]:
        if len(name) < 4:
            continue
        hits = _grep_tests(root, name, touched_set)
        if hits:
            findings.append({
                "class": "form_name",
                "contract_id": name,
                "contract_kind": "form field name",
                "file_hint": "",
                "references": hits,
            })

    # Handler keys — similar to form names but on the handler side.
    for key in contracts["handler_keys"]:
        if len(key) < 4:
            continue
        # Dedup against form-names already flagged (same literal often
        # appears on both sides — only report once)
        if any(f["contract_id"] == key for f in findings):
            continue
        hits = _grep_tests(root, key, touched_set)
        if hits:
            findings.append({
                "class": "handler_key",
                "contract_id": key,
                "contract_kind": "handler form key",
                "file_hint": "",
                "references": hits,
            })

    return findings
