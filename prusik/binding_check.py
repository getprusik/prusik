"""Pair detection + cross-check for binding-mismatch (v0.19.0).

Given a touched-set (files modified in this sprint), find:

  - **fetch-URL mismatches**: a template fetches a URL that doesn't
    resolve to ANY touched route (404 risk; DEV-1 root #1).
  - **form-name dropthrough**: a template emits a `name="X"` that no
    touched handler reads as `form.get("X")` / `request.form["X"]`
    (silent dropthrough; DEV-1 root #2).

This is FLAG-only, not gate-blocking. Adjudicating whether a particular
mismatch is a real bug (vs. an intentional cross-module call to a route
not in scope) requires project context the static scan can't resolve —
mission boundary, same discipline as the skip-reason flag in v0.18.0.

Conservative rules:
  - Only consider templates + handlers in the TOUCHED set (the sprint
    actually modified them). Cross-cutting noise filtered out.
  - For fetch-URLs, the route resolution considers any router prefix
    declared in the touched files. If the URL exactly matches one of the
    touched routes' fully-qualified paths, no flag. Otherwise — flag.
  - For form-names, the cross-check is membership: if ANY touched
    handler reads the name, no flag (the binding is at least *present*
    somewhere in the touched set).
"""

from __future__ import annotations

from pathlib import Path
from prusik.binding_detect import (
    extract_fastapi_routes, extract_router_prefixes,
    extract_handler_form_keys, extract_template_fetches,
    extract_form_names, is_python_route_file, is_template_file,
)
from prusik.binding_detect_js import (
    extract_express_routes, extract_router_prefixes_js,
    extract_handler_form_keys_js, extract_nextjs_routes,
    extract_jsx_form_names, extract_jsx_fetches,
    is_js_route_file, is_jsx_template_file, is_js_handler_file,
)


def find_unbinding_pairs(touched_files: list[Path],
                         root: Path) -> list[dict]:
    """Cross-check touched files for binding mismatches.

    touched_files: paths (absolute or relative-to-root) of files the
                   sprint modified. The cross-check only flags issues
                   visible from within this set.
    root: project root (used to resolve relative paths).

    Returns a list of finding dicts with:
      {"class": "fetch_url" | "form_name",
       "severity": "info" | "medium",
       "template": ..., "template_line": ...,
       "expected": ..., "candidates": [...],
       "msg": "<human-readable description>"}
    """
    # Normalize paths
    abs_files: list[Path] = []
    for tf in touched_files:
        p = tf if tf.is_absolute() else (root / tf)
        if p.exists():
            abs_files.append(p)

    py_files = [p for p in abs_files if is_python_route_file(p)]
    tmpl_files = [p for p in abs_files if is_template_file(p)]
    # v0.22.0 — JS/TS sources. A file can be MULTIPLE roles at once
    # (a Next.js route.ts also "handles" the form). We classify each
    # independently rather than partition.
    js_route_files = [p for p in abs_files if is_js_route_file(p)]
    jsx_template_files = [p for p in abs_files if is_jsx_template_file(p)]
    js_handler_files = [p for p in abs_files if is_js_handler_file(p)]

    # Collect routes (path, method) — resolving prefix if declared in any
    # touched router file. Conservative: try {raw_path, prefix+raw_path}
    # as both legitimate candidates the consumer might call.
    routes: list[dict] = []
    prefixes_per_file: dict[Path, dict] = {}
    for py in py_files:
        try:
            text = py.read_text()
        except OSError:
            continue
        prefixes_per_file[py] = extract_router_prefixes(text)
        for r in extract_fastapi_routes(text):
            r["file"] = py
            routes.append(r)

    # v0.22.0 — Express + Next.js routes
    for js in js_route_files:
        try:
            text = js.read_text()
        except OSError:
            continue
        js_prefixes = extract_router_prefixes_js(text)
        prefixes_per_file[js] = js_prefixes
        for r in extract_express_routes(text):
            r["file"] = js
            routes.append(r)
        for r in extract_nextjs_routes(js, root):
            r["file"] = js
            routes.append(r)

    # Resolve each route's fully-qualified path. When a router declares
    # a prefix, the bare local path is NOT a legitimate consumer URL —
    # HTTP clients see prefix+path. (Treating bare-path as legitimate was
    # the bug that hid DEV-1 root #1: template fetched the bare path, the
    # routes file had the same bare path, and the missing-prefix mismatch
    # didn't surface.) For Next.js file-routed APIs, the path itself IS
    # the qualified path — no prefix layer.
    #
    # Cross-file prefix resolution (v0.22.0): Express mounts the prefix
    # in a DIFFERENT file than the route definition (app.use("/x", router)
    # is in server/index.ts; the route lives in server/router.ts). Union
    # all prefix maps across touched files keyed by router-name so the
    # cross-file binding resolves correctly. FastAPI usually co-locates,
    # but APIRouter+include_router has the same pattern — union helps both.
    union_prefixes: dict[str, str] = {}
    for _file, pmap in prefixes_per_file.items():
        for rname, prefix in pmap.items():
            if prefix and rname not in union_prefixes:
                union_prefixes[rname] = prefix

    qualified_paths: set[str] = set()
    for r in routes:
        if r["router"] == "nextjs":
            qualified_paths.add(r["path"])
            continue
        # Prefer same-file prefix declaration; fall back to union map.
        prefixes = prefixes_per_file.get(r["file"], {})
        prefix = prefixes.get(r["router"], "") or union_prefixes.get(r["router"], "")
        if prefix:
            qualified_paths.add(prefix + r["path"])
        else:
            qualified_paths.add(r["path"])

    # Collect handler form-keys from touched Python files + JS handlers
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

    findings: list[dict] = []

    # Class 1: template-fetch ↔ route-path  (Jinja/HTML side)
    for tpl in tmpl_files:
        try:
            text = tpl.read_text()
        except OSError:
            continue
        for f in extract_template_fetches(text):
            url = f["url"]
            # Skip non-route-shaped URLs (external, anchors, mailto, etc.)
            if not url.startswith("/"):
                continue
            # Strip query string for path-comparison; some templates
            # interpolate query params dynamically
            path_only = url.split("?", 1)[0]
            if path_only in qualified_paths:
                continue
            # Try a "close but not matching" heuristic: if any touched
            # route's bare-path suffix equals this URL, the prefix is
            # likely the missed piece — surface that as the suggested fix.
            suspects = [r for r in routes
                        if r["path"] == path_only or
                           (r["path"] and path_only.endswith(r["path"]))]
            if suspects:
                expected = sorted({
                    s["path"] if s["router"] == "nextjs"
                    else ((prefixes_per_file.get(s["file"], {}).get(s["router"], "")
                           or union_prefixes.get(s["router"], "")) + s["path"])
                    for s in suspects
                })
            else:
                expected = []
            findings.append({
                "class": "fetch_url",
                "severity": "medium",
                "template": str(tpl.relative_to(root))
                            if tpl.is_relative_to(root) else str(tpl),
                "template_line": f["line"],
                "kind": f["kind"],
                "url": url,
                "expected": expected,
                "msg": (f"Template fetches {url!r} but no touched route "
                        f"resolves there. Candidates touched: "
                        f"{expected if expected else '(none)'}. "
                        f"DEV-1 root #1 class: assertion-depth gap on "
                        f"template-fetch-URL ↔ route-path binding."),
            })

    # Class 2: form-name ↔ handler-key
    for tpl in tmpl_files:
        try:
            text = tpl.read_text()
        except OSError:
            continue
        for fname in extract_form_names(text):
            name = fname["name"]
            if name in handler_keys:
                continue  # Bound in at least one touched handler — OK
            findings.append({
                "class": "form_name",
                "severity": "medium",
                "template": str(tpl.relative_to(root))
                            if tpl.is_relative_to(root) else str(tpl),
                "template_line": fname["line"],
                "name": name,
                "expected": sorted(handler_keys)[:5],  # show top candidates
                "msg": (f"Template emits <input name={name!r}> but no "
                        f"touched handler reads that key. Touched handler "
                        f"keys: {sorted(handler_keys)[:5] or '(none)'}. "
                        f"DEV-1 root #2 class: assertion-depth gap on "
                        f"form-name ↔ handler-key binding (silent "
                        f"dropthrough)."),
            })

    # v0.22.0 — JSX side of class 1 (fetch_url) + class 2 (form_name).
    # Same cross-check logic, JS-flavored extractors.
    for tpl in jsx_template_files:
        try:
            text = tpl.read_text()
        except OSError:
            continue
        for f in extract_jsx_fetches(text):
            url = f["url"]
            if not url.startswith("/"):
                continue
            path_only = url.split("?", 1)[0]
            if path_only in qualified_paths:
                continue
            suspects = [r for r in routes
                        if r["path"] == path_only or
                           (r["path"] and path_only.endswith(r["path"]))]
            if suspects:
                expected = sorted({
                    s["path"] if s["router"] == "nextjs"
                    else ((prefixes_per_file.get(s["file"], {}).get(s["router"], "")
                           or union_prefixes.get(s["router"], "")) + s["path"])
                    for s in suspects
                })
            else:
                expected = []
            findings.append({
                "class": "fetch_url",
                "severity": "medium",
                "template": str(tpl.relative_to(root))
                            if tpl.is_relative_to(root) else str(tpl),
                "template_line": f["line"],
                "kind": f["kind"],
                "url": url,
                "expected": expected,
                "msg": (f"JSX fetches {url!r} but no touched route "
                        f"resolves there. Candidates touched: "
                        f"{expected if expected else '(none)'}. "
                        f"DEV-1 root #1 class on JS/TS side."),
            })
        for fname in extract_jsx_form_names(text):
            name = fname["name"]
            if name in handler_keys:
                continue
            findings.append({
                "class": "form_name",
                "severity": "medium",
                "template": str(tpl.relative_to(root))
                            if tpl.is_relative_to(root) else str(tpl),
                "template_line": fname["line"],
                "name": name,
                "expected": sorted(handler_keys)[:5],
                "msg": (f"JSX emits <input name={name!r}> but no "
                        f"touched handler reads that key. Touched "
                        f"handler keys: "
                        f"{sorted(handler_keys)[:5] or '(none)'}. "
                        f"DEV-1 root #2 class on JS/TS side (silent "
                        f"dropthrough)."),
            })

    # v0.27.0 — attach test suggestion to each finding (None when class
    # has no suggestion implementation). The agent / operator consumes
    # via finding["suggested_test"].
    from prusik.test_suggestion import suggest_for_finding
    for f in findings:
        f["suggested_test"] = suggest_for_finding(f)

    return findings
