"""Per-stack contract extractors for the binding-mismatch detector
(v0.19.0, F §4 assertion-depth gap closure for the DEV-1 root class).

Three binding classes the detector cares about:

  1. template-fetch-URL ↔ route-path
     A template references a URL (`fetch("X")`, `hx-get="X"`, form action)
     that must resolve to a registered route. DEV-1 root #1: template
     fetched `/clients/search` but the route was registered on a router
     with `prefix="/invoices"` → actual path `/invoices/clients/search`
     → 404 in browser; assertions on the handler unit-test passed.

  2. form-name ↔ handler-key
     A template emits `<input name="X">`; the handler must read `X` via
     `form.get("X")` or equivalent. DEV-1 root #2: template emitted
     `new_client_legal_name` but handler read `inline_client_name`
     → silent dropthrough (no error, just no effect); template tests
     asserted render success but never asserted the binding.

  3. response-shape ↔ client-parser
     Lower priority; out of v0.19.0 scope. Tracked for future increment.

This module is per-stack. v0.19.0 ships the FastAPI+Jinja extractors
(field-grounded). Next.js / Rust+axum / etc. each need their own
extractors; queued behind a 2nd observation outside FastAPI.

All extractors are best-effort regex on real source. They produce
candidate bindings for the cross-checker; the cross-check decides what
to flag. False-positive rate matters; conservative extraction beats
exhaustive — the flag is decision-support, not a gate-block.
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------- FastAPI route extraction ----------

# Match: @router.get("path") / @app.post("path", ...) / etc.
# Captures: the HTTP method (lowercase) and the path string literal.
# Also captures router-name so prefix lookup is possible.
_FASTAPI_ROUTE_RE = re.compile(
    r"""
    @(?P<router>\w+)                       # router var name
    \.(?P<method>get|post|put|patch|delete|head|options|websocket)
    \s*\(\s*
    (?P<q>['"])
    (?P<path>[^'"]+)
    (?P=q)
    """, re.VERBOSE)

# Match: APIRouter(prefix="..." or no prefix). Captures the prefix if
# present. Used to resolve route paths to their fully-qualified form.
_ROUTER_DEF_RE = re.compile(
    r"""(?P<name>\w+)\s*=\s*APIRouter\s*\(
        (?:[^)]*?prefix\s*=\s*(?P<q>['"])(?P<prefix>[^'"]*)(?P=q))?
        [^)]*\)
    """, re.VERBOSE)

# Match: app.include_router(router, prefix="...")
_INCLUDE_ROUTER_RE = re.compile(
    r"""\.include_router\s*\(
        \s*(?P<name>\w+)
        (?:[^)]*?prefix\s*=\s*(?P<q>['"])(?P<prefix>[^'"]*)(?P=q))?
        [^)]*\)
    """, re.VERBOSE)

# Match: a handler form-get / form-bracket access.
# Captures: the key string literal.
_FORM_GET_RE = re.compile(
    r"""(?:form|request\.form|data)         # common receiver names
        \s*(?:\.get\(|\[)
        (?P<q>['"])
        (?P<key>[^'"]+)
        (?P=q)
    """, re.VERBOSE)


def extract_fastapi_routes(text: str) -> list[dict]:
    """From a Python source file, extract route registrations.

    Returns: list of {router, method, path, line} dicts. The path is the
    LOCAL path on the router (NOT the fully-qualified path; see
    extract_router_prefixes to resolve that).
    """
    out = []
    for m in _FASTAPI_ROUTE_RE.finditer(text):
        # Find the line number of the start of the match
        line = text[:m.start()].count("\n") + 1
        out.append({
            "router": m.group("router"),
            "method": m.group("method").lower(),
            "path": m.group("path"),
            "line": line,
        })
    return out


def extract_router_prefixes(text: str) -> dict[str, str]:
    """Build a router_name → prefix map from a Python source file.

    Looks at:
      - APIRouter(prefix="X") definitions
      - app.include_router(name, prefix="Y") overrides (include_router's
        prefix is APPENDED to the router's own prefix per FastAPI semantics;
        for the conservative first pass we record both as candidates and
        the cross-checker considers either as a possible resolution)
    """
    prefixes: dict[str, str] = {}
    for m in _ROUTER_DEF_RE.finditer(text):
        if m.group("prefix") is not None:
            prefixes[m.group("name")] = m.group("prefix")
    # include_router overrides — note we OVERLAY (not append) for the
    # first pass; conservative since we can't easily track which prefix
    # is active for a given route without full import resolution.
    for m in _INCLUDE_ROUTER_RE.finditer(text):
        if m.group("prefix") is not None:
            prev = prefixes.get(m.group("name"), "")
            prefixes[m.group("name")] = prev + m.group("prefix")
    return prefixes


def extract_handler_form_keys(text: str) -> list[dict]:
    """Form-key reads from a handler. Returns {key, line} list."""
    out = []
    for m in _FORM_GET_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        out.append({"key": m.group("key"), "line": line})
    return out


# ---------- Jinja / HTML template extraction ----------

# Match: fetch("X") / fetch('X')
_FETCH_RE = re.compile(r"""fetch\s*\(\s*(?P<q>['"])(?P<url>[^'"]+)(?P=q)""")
# Match: hx-get="X" / hx-post="X" / etc. (HTMX)
_HX_RE = re.compile(
    r"""hx-(?:get|post|put|patch|delete)\s*=\s*(?P<q>['"])(?P<url>[^'"]+)(?P=q)""")
# Match: <form action="X" ...> / action='X'
_FORM_ACTION_RE = re.compile(
    r"""<form\b[^>]*\baction\s*=\s*(?P<q>['"])(?P<url>[^'"]+)(?P=q)""",
    re.IGNORECASE)
# Match: <input name="X" ...> or <input ... name='X'> (name attribute,
# anywhere in the tag). Conservative — matches HTML/Jinja literal inputs;
# misses dynamic name="{{ foo }}" cases (intentional — those depend on
# template context the static scan can't resolve).
_INPUT_NAME_RE = re.compile(
    r"""<input\b[^>]*\bname\s*=\s*(?P<q>['"])(?P<name>[^'"{]+)(?P=q)""",
    re.IGNORECASE)
# Also catch <textarea name="X"> and <select name="X">
_FIELD_NAME_RE = re.compile(
    r"""<(?:textarea|select)\b[^>]*\bname\s*=\s*(?P<q>['"])(?P<name>[^'"{]+)(?P=q)""",
    re.IGNORECASE)


def extract_template_fetches(text: str) -> list[dict]:
    """URL references in a template. Returns {url, kind, line} list.
    Kinds: 'fetch', 'hx', 'form-action'."""
    out = []
    for m in _FETCH_RE.finditer(text):
        out.append({"url": m.group("url"), "kind": "fetch",
                     "line": text[:m.start()].count("\n") + 1})
    for m in _HX_RE.finditer(text):
        out.append({"url": m.group("url"), "kind": "hx",
                     "line": text[:m.start()].count("\n") + 1})
    for m in _FORM_ACTION_RE.finditer(text):
        out.append({"url": m.group("url"), "kind": "form-action",
                     "line": text[:m.start()].count("\n") + 1})
    return out


def extract_form_names(text: str) -> list[dict]:
    """<input name="X"> / textarea / select name attributes from a
    template. Returns {name, line} list."""
    out = []
    for m in _INPUT_NAME_RE.finditer(text):
        out.append({"name": m.group("name"),
                     "line": text[:m.start()].count("\n") + 1})
    for m in _FIELD_NAME_RE.finditer(text):
        out.append({"name": m.group("name"),
                     "line": text[:m.start()].count("\n") + 1})
    return out


# ---------- File-shape classifiers ----------

def is_python_route_file(path: Path) -> bool:
    """Heuristic — does this Python file likely contain FastAPI routes?
    Used to scope extraction; conservative."""
    if path.suffix != ".py":
        return False
    try:
        snippet = path.read_text(errors="ignore")[:4000]
    except OSError:
        return False
    return "APIRouter" in snippet or ".get(" in snippet and "router" in snippet \
           or ".post(" in snippet and ("app" in snippet or "router" in snippet)


def is_template_file(path: Path) -> bool:
    """Identifies Jinja/HTML templates. FastAPI+Jinja typically uses
    .html or .jinja2 extensions in a `templates/` subdir."""
    if path.suffix.lower() in (".html", ".jinja", ".jinja2", ".htm"):
        return True
    # Catch templates that lack the extension but live under templates/
    if "templates" in path.parts:
        return path.suffix == "" or path.suffix.lower() == ".tpl"
    return False
