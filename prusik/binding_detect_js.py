"""JS/TS binding-mismatch extractors (v0.22.0 — cross-stack parity).

Closes the FastAPI-only lock-in surfaced by world-class adopter
calibration. The v0.19.0 binding detector mechanized DEV-1's actual
defect class for Python+FastAPI; v0.22.0 ships parity for the
JavaScript / TypeScript funnel (Express + Next.js).

Three binding classes, same shape as the Python detector:

  1. template-fetch-URL ↔ route-path
     JSX `fetch('/path')`, `<form action="/path">`, `<input formAction="...">`
     vs Express `app.get('/path', ...)` / Next.js Pages Router (`/api/X.ts`
     → `/api/X`) / App Router (`app/X/route.ts` → `/X`).

  2. form-name ↔ handler-key
     JSX `<input name="X" />` vs handler `req.body.X` / `formData.get("X")`
     / `request.formData()` patterns. Silent dropthrough — the FE-BE
     dropthrough is just as easy in JS as in Python.

  3. response-shape ↔ client-parser
     Out of v0.22.0 scope (same as v0.19.0).

Conservative-first design (same discipline as the Python detector):
best-effort regex, FP rate matters more than exhaustive detection.
Worst case: no extraction → no flag → fall back on F's other layers.

Honest scope:
  - Express + Next.js App Router routes covered. Koa/Fastify/Hono
    queued behind 2nd-occurrence per prusik's recurrence discipline.
  - JSX detection only — pure-HTML templates fall through to the
    Jinja-style detector (which also matches `<input name=...>`).
  - TypeScript types not parsed; we look at runtime call shapes.
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------- Express route extraction ----------
# Matches: app.get('/path', ...) / router.post("/path", ...) /
#          someRouter.delete(`/path/${id}`, ...) — backticks captured
#          with the literal-prefix only (interpolations stripped).
_EXPRESS_ROUTE_RE = re.compile(
    r"""(?P<router>\w+)\s*\.
        (?P<method>get|post|put|patch|delete|head|options|all|use)
        \s*\(\s*
        (?P<q>['"`])
        (?P<path>[^'"`$]+)        # bail out at interpolation start
        (?P=q)
    """, re.VERBOSE)

# Matches: const router = express.Router({mountpath: '/x'}) — Express has
# no first-class router-prefix like FastAPI, but mount points come from
# app.use("/prefix", router). Capture those:
_EXPRESS_USE_PREFIX_RE = re.compile(
    r"""(?P<app>\w+)\s*\.use\s*\(\s*
        (?P<q>['"])
        (?P<prefix>/[^'"]*)
        (?P=q)
        \s*,\s*
        (?P<router>\w+)
    """, re.VERBOSE)

# Matches: req.body.X / req.body["X"] / req.body['X']
# Also: req.query.X (less common in form-name binding but covered)
_JS_HANDLER_KEY_RE = re.compile(
    r"""req\s*\.\s*(?:body|query)\s*
        (?:
            \.\s*(?P<dot_key>[A-Za-z_$][A-Za-z0-9_$]*)
          | \[\s*(?P<q>['"])(?P<bracket_key>[^'"]+)(?P=q)\s*\]
        )
    """, re.VERBOSE)

# Matches: formData.get("X") / formData.get('X') — Next.js App Router
# server-action / route-handler style. Also catches a bare-variable
# formData binding (e.g. `const formData = await req.formData()`).
_FORMDATA_GET_RE = re.compile(
    r"""\w+\s*\.\s*get\s*\(\s*
        (?P<q>['"])
        (?P<key>[^'"]+)
        (?P=q)
    """, re.VERBOSE)


def extract_express_routes(text: str) -> list[dict]:
    """From a JS/TS source file, extract Express route registrations.

    Returns: list of {router, method, path, line} dicts. `path` is the
    LOCAL path on the router; prefix resolution via extract_router_prefixes_js.
    """
    out = []
    for m in _EXPRESS_ROUTE_RE.finditer(text):
        method = m.group("method").lower()
        # `.use(` is mount, not a route — skip when it isn't paired with
        # an HTTP-method semantic (true mounts have a path + handler; we
        # only count `.use("/path", router)` mounts via _EXPRESS_USE_PREFIX_RE)
        if method == "use":
            continue
        line = text[:m.start()].count("\n") + 1
        out.append({
            "router": m.group("router"),
            "method": method,
            "path": m.group("path"),
            "line": line,
        })
    return out


def extract_router_prefixes_js(text: str) -> dict[str, str]:
    """Build a router_name → mount-prefix map from Express `app.use(...)`."""
    prefixes: dict[str, str] = {}
    for m in _EXPRESS_USE_PREFIX_RE.finditer(text):
        # The router being mounted gets the prefix.
        prefixes[m.group("router")] = m.group("prefix")
    return prefixes


def extract_handler_form_keys_js(text: str) -> list[dict]:
    """Extract handler-side form-key reads from JS/TS source.

    Sources covered:
      - req.body.X / req.body["X"] (Express style)
      - req.query.X / req.query["X"]
      - formData.get("X") (Next.js App Router server-action style; the
        regex matches *any* `.get("X")` call, which has FPs — see
        binding_check_js for the FP-control logic that filters down to
        forms-touched handlers only)

    Returns: [{key, line, source: 'body'|'query'|'formdata'}]
    """
    out = []
    for m in _JS_HANDLER_KEY_RE.finditer(text):
        key = m.group("dot_key") or m.group("bracket_key")
        line = text[:m.start()].count("\n") + 1
        out.append({"key": key, "line": line,
                     "source": "body" if "body" in m.group(0) else "query"})
    # FormData.get() — only counts when the file references `formData`
    # somewhere (FP control vs matching every `.get("X")` call site).
    if "formData" in text or "FormData" in text:
        for m in _FORMDATA_GET_RE.finditer(text):
            # Match the receiver-name: only if it's *Data-shaped (formData,
            # body, etc.) — filters chained calls on URLs / Maps / etc.
            receiver = m.group(0).split(".")[0].strip()
            if "formData" in receiver.lower() or "data" in receiver.lower():
                out.append({"key": m.group("key"),
                             "line": text[:m.start()].count("\n") + 1,
                             "source": "formdata"})
    return out


# ---------- Next.js file-routing extraction ----------

def extract_nextjs_routes(file_path: Path, root: Path) -> list[dict]:
    """Next.js routes come from FILE STRUCTURE, not call syntax.

    App Router: `app/<segments>/route.ts` → /<segments>
    Pages API:  `pages/api/<segments>.ts` → /api/<segments>
    Dynamic:    [param] → :param (kept literal in finding; the FP control
                is the comparator deciding match vs strict-equality)

    Returns: list of {router: 'nextjs', method, path, line: 1} dicts
    (lines aren't meaningful for file-routed APIs; we use 1).
    """
    try:
        rel = file_path.relative_to(root) if file_path.is_absolute() else file_path
    except ValueError:
        return []
    parts = rel.parts
    if not parts:
        return []

    # App Router: app/<segments>/route.{ts,js,tsx,jsx}
    if parts[0] == "app" and parts[-1] in ("route.ts", "route.js",
                                              "route.tsx", "route.jsx"):
        segments = parts[1:-1]
        path = "/" + "/".join(s.replace("[", ":").replace("]", "") for s in segments)
        # Each exported HTTP-method handler in the file becomes a route.
        # Look for `export async function GET / POST / etc.`
        try:
            text = file_path.read_text(errors="ignore")
        except OSError:
            return []
        methods = []
        for m in re.finditer(
            r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
            text):
            methods.append(m.group(1).lower())
        # Also handle `export const GET = ...` / `export const POST = ...`
        for m in re.finditer(
            r"export\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
            text):
            methods.append(m.group(1).lower())
        if not methods:
            return []
        return [{"router": "nextjs", "method": meth, "path": path or "/",
                 "line": 1} for meth in sorted(set(methods))]

    # Pages API: pages/api/<segments>.{ts,js}
    if len(parts) >= 2 and parts[0] == "pages" and parts[1] == "api":
        last = parts[-1]
        name, _, ext = last.partition(".")
        if ext not in ("ts", "js"):
            return []
        # /api/<intermediate>/<name>
        mid_segments = parts[2:-1]
        path_parts = list(mid_segments) + ([name] if name != "index" else [])
        path = "/api/" + "/".join(
            p.replace("[", ":").replace("]", "") for p in path_parts) \
            if path_parts else "/api"
        path = path.rstrip("/") or "/api"
        return [{"router": "nextjs", "method": "any", "path": path,
                 "line": 1}]

    return []


# ---------- JSX / template extraction ----------

# Matches: <input name="X" /> / <input ... name='X' ...>
# JSX has the same attribute syntax as HTML. Captures literal-only names
# (skips dynamic name={...} — same conservative rule as the Python one).
_JSX_INPUT_NAME_RE = re.compile(
    r"""<input\b[^>]*\bname\s*=\s*(?P<q>['"])(?P<name>[^'"{]+)(?P=q)""",
    re.IGNORECASE)
_JSX_FIELD_NAME_RE = re.compile(
    r"""<(?:textarea|select)\b[^>]*\bname\s*=\s*(?P<q>['"])(?P<name>[^'"{]+)(?P=q)""",
    re.IGNORECASE)

# Matches: fetch('/path') / fetch("/path") / fetch(`/path`)
# (Backticks: literal-prefix only — bail at $ for interpolation.)
_JS_FETCH_RE = re.compile(
    r"""\bfetch\s*\(\s*(?P<q>['"`])(?P<url>[^'"`$]+)(?P=q)""")
# axios.get('/path') / .post('/path') etc.
_AXIOS_RE = re.compile(
    r"""axios\s*\.\s*(?:get|post|put|patch|delete|head|options)
        \s*\(\s*(?P<q>['"`])(?P<url>[^'"`$]+)(?P=q)
    """, re.VERBOSE)
# Form action: <form action="/path" ...>
_JSX_FORM_ACTION_RE = re.compile(
    r"""<form\b[^>]*\baction\s*=\s*(?P<q>['"])(?P<url>[^'"{]+)(?P=q)""",
    re.IGNORECASE)


def extract_jsx_form_names(text: str) -> list[dict]:
    """<input/textarea/select name="X"> from JSX. Returns [{name, line}]."""
    out = []
    for m in _JSX_INPUT_NAME_RE.finditer(text):
        out.append({"name": m.group("name"),
                     "line": text[:m.start()].count("\n") + 1})
    for m in _JSX_FIELD_NAME_RE.finditer(text):
        out.append({"name": m.group("name"),
                     "line": text[:m.start()].count("\n") + 1})
    return out


def extract_jsx_fetches(text: str) -> list[dict]:
    """URL references from JSX/TS source. Returns [{url, kind, line}].
    Kinds: 'fetch', 'axios', 'form-action'."""
    out = []
    for m in _JS_FETCH_RE.finditer(text):
        out.append({"url": m.group("url"), "kind": "fetch",
                     "line": text[:m.start()].count("\n") + 1})
    for m in _AXIOS_RE.finditer(text):
        out.append({"url": m.group("url"), "kind": "axios",
                     "line": text[:m.start()].count("\n") + 1})
    for m in _JSX_FORM_ACTION_RE.finditer(text):
        out.append({"url": m.group("url"), "kind": "form-action",
                     "line": text[:m.start()].count("\n") + 1})
    return out


# ---------- File-shape classifiers ----------

_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_JSX_EXTS = (".jsx", ".tsx")


def is_js_route_file(path: Path) -> bool:
    """Heuristic — does this JS/TS file likely contain Express routes,
    or is it a Next.js route file (App Router or Pages API)?"""
    if path.suffix not in _JS_EXTS:
        return False
    # Next.js App Router: app/.../route.{ts,js,tsx,jsx}
    if path.name in ("route.ts", "route.js", "route.tsx", "route.jsx"):
        return True
    # Next.js Pages API: pages/api/**
    parts = path.parts
    for i, p in enumerate(parts):
        if p == "pages" and i + 1 < len(parts) and parts[i + 1] == "api":
            return True
    # Express style: scan for `.get(` / `.post(` etc. patterns on a router
    try:
        snippet = path.read_text(errors="ignore")[:4000]
    except OSError:
        return False
    return bool(_EXPRESS_ROUTE_RE.search(snippet))


def is_jsx_template_file(path: Path) -> bool:
    """Identifies JSX/TSX component files that may contain form elements."""
    if path.suffix not in _JSX_EXTS:
        return False
    # Quick content check: look for JSX-shaped content
    try:
        snippet = path.read_text(errors="ignore")[:4000]
    except OSError:
        return False
    return ("<input" in snippet.lower() or "<form" in snippet.lower()
            or "<textarea" in snippet.lower() or "<select" in snippet.lower()
            or "fetch(" in snippet or "axios" in snippet)


def is_js_handler_file(path: Path) -> bool:
    """A JS/TS file that likely reads form data (req.body / formData)."""
    if path.suffix not in _JS_EXTS:
        return False
    try:
        snippet = path.read_text(errors="ignore")[:4000]
    except OSError:
        return False
    return ("req.body" in snippet or "req.query" in snippet
            or "formData" in snippet or "FormData" in snippet)
