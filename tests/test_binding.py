"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: binding.

Run: uv run python -m pytest tests/test_binding.py -v
Or run the whole suite: uv run python -m pytest tests/ -v

Shared helpers live in tests/_common.py (private; pytest does not
collect leading-underscore modules). v0.23.0 split tests/test_smoke.py
by domain to keep individual files navigable.
"""

# noqa: F401 — wildcard imports below intentionally re-export everything
# from _common (prusik modules, helpers, the tempfile/json/os toolbelt).
# F401 individual unused-name warnings would obscure the rest.
from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_bridge, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)


# ---------- v0.19.0 — binding-mismatch detection (DEV-1 root) ----------

def test_v0190_extract_fastapi_routes_with_prefix():
    """The router-prefix resolution is the load-bearing piece for DEV-1
    root #1 (template fetched `/clients/search` but the route was
    registered on a router with prefix=`/invoices`)."""
    from prusik.binding_detect import (
        extract_fastapi_routes, extract_router_prefixes)
    src = (
        "from fastapi import APIRouter\n"
        "invoices_router = APIRouter(prefix=\"/invoices\")\n"
        "@invoices_router.get(\"/clients/search\")\n"
        "async def search_clients(): pass\n"
    )
    routes = extract_fastapi_routes(src)
    prefixes = extract_router_prefixes(src)
    assert len(routes) == 1
    assert routes[0]["router"] == "invoices_router"
    assert routes[0]["path"] == "/clients/search"
    assert prefixes["invoices_router"] == "/invoices"


def test_v0190_extract_template_fetches_and_form_names():
    from prusik.binding_detect import (
        extract_template_fetches, extract_form_names)
    tpl = (
        "<form action=\"/clients/search\" method=\"post\">\n"
        "  <input type=\"text\" name=\"new_client_legal_name\" />\n"
        "  <button hx-get=\"/clients/search\">Search</button>\n"
        "</form>\n"
        "<script>fetch('/clients/search').then(r => r.json());</script>\n"
    )
    fetches = extract_template_fetches(tpl)
    urls = [f["url"] for f in fetches]
    assert "/clients/search" in urls
    # All three kinds covered
    kinds = {f["kind"] for f in fetches}
    assert kinds == {"fetch", "hx", "form-action"}
    names = [n["name"] for n in extract_form_names(tpl)]
    assert "new_client_legal_name" in names


def test_v0190_dev1_root1_fetch_url_route_path_mismatch_flagged():
    """Reproduce DEV-1 root #1: template fetches /clients/search; route
    is registered on invoices_router with prefix=/invoices → actual
    /invoices/clients/search. Cross-checker must flag."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Touched route: prefixed router
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "invoices.py").write_text(
            "from fastapi import APIRouter\n"
            "invoices_router = APIRouter(prefix=\"/invoices\")\n"
            "@invoices_router.get(\"/clients/search\")\n"
            "async def search_clients(): pass\n"
        )
        # Touched template: fetches the LOCAL path without the prefix
        (tmp / "templates").mkdir(exist_ok=True)
        (tmp / "templates" / "_inline_client_form.html").write_text(
            "<button hx-get=\"/clients/search\">Search</button>\n"
        )
        from prusik.binding_check import find_unbinding_pairs
        findings = find_unbinding_pairs(
            [tmp / "src" / "invoices.py",
             tmp / "templates" / "_inline_client_form.html"],
            tmp,
        )
        fetch_flags = [f for f in findings if f["class"] == "fetch_url"]
        assert fetch_flags, f"expected fetch_url flag, got: {findings}"
        # The suggested fix should include the prefixed full path
        assert any("/invoices/clients/search" in (f.get("expected") or [])
                   for f in fetch_flags), \
            f"expected suggestion of /invoices/clients/search, got: {fetch_flags}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0190_dev1_root2_form_name_handler_key_dropthrough_flagged():
    """Reproduce DEV-1 root #2: form emits name='new_client_legal_name';
    handler reads form.get('inline_client_name'). Cross-checker must flag."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "invoices.py").write_text(
            "from fastapi import APIRouter, Request\n"
            "router = APIRouter()\n"
            "@router.post(\"/save\")\n"
            "async def save_client(request: Request):\n"
            "    form = await request.form()\n"
            "    name = form.get(\"inline_client_name\")\n"
            "    return {\"name\": name}\n"
        )
        (tmp / "templates").mkdir(exist_ok=True)
        (tmp / "templates" / "_inline_client_form.html").write_text(
            "<form action=\"/save\" method=\"post\">\n"
            "  <input type=\"text\" name=\"new_client_legal_name\" />\n"
            "</form>\n"
        )
        from prusik.binding_check import find_unbinding_pairs
        findings = find_unbinding_pairs(
            [tmp / "src" / "invoices.py",
             tmp / "templates" / "_inline_client_form.html"],
            tmp,
        )
        form_flags = [f for f in findings if f["class"] == "form_name"]
        assert form_flags, f"expected form_name flag, got: {findings}"
        # The flagged name is the template's name (the dropthrough one)
        assert any(f.get("name") == "new_client_legal_name"
                   for f in form_flags), f"got: {form_flags}"
        # And the expected hint should surface the key the handler DOES read
        assert any("inline_client_name" in (f.get("expected") or [])
                   for f in form_flags), f"got: {form_flags}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0190_no_flag_when_binding_is_correct():
    """Negative case — when fetch URL matches route fully (incl. prefix)
    and form names ARE read by handlers, the cross-checker emits NO
    flags. (False-positive control — the scan must not surface noise on
    correct code.)"""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "api.py").write_text(
            "from fastapi import APIRouter, Request\n"
            "router = APIRouter(prefix=\"/api\")\n"
            "@router.post(\"/save\")\n"
            "async def save(request: Request):\n"
            "    form = await request.form()\n"
            "    return {\"x\": form.get(\"some_key\")}\n"
        )
        (tmp / "templates").mkdir(exist_ok=True)
        (tmp / "templates" / "form.html").write_text(
            "<form action=\"/api/save\" method=\"post\">\n"
            "  <input name=\"some_key\" />\n"
            "</form>\n"
        )
        from prusik.binding_check import find_unbinding_pairs
        findings = find_unbinding_pairs(
            [tmp / "src" / "api.py",
             tmp / "templates" / "form.html"],
            tmp,
        )
        assert findings == [], f"clean code should produce no flags: {findings}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0190_check_bindings_command_e2e():
    """End-to-end via `prusik gate check-bindings`: scans worktrees/* and
    emits findings + ledger events. Reproduces DEV-1 #1 with files in
    worktrees/ (prusik's standard authoring location)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "worktrees" / "solo" / "src").mkdir(parents=True)
        (tmp / "worktrees" / "solo" / "src" / "invoices.py").write_text(
            "from fastapi import APIRouter\n"
            "invoices_router = APIRouter(prefix=\"/invoices\")\n"
            "@invoices_router.get(\"/clients/search\")\n"
            "async def search(): pass\n"
        )
        (tmp / "worktrees" / "solo" / "templates").mkdir(parents=True)
        (tmp / "worktrees" / "solo" / "templates" / "form.html").write_text(
            "<button hx-get=\"/clients/search\">Search</button>\n"
        )
        import argparse as _ap
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gate.check_bindings(_ap.Namespace(
                feature="demo", touched_set=None))
        out = buf.getvalue()
        assert rc == 0, "check-bindings is informational, never gating"
        assert "fetch-URL" in out and "/clients/search" in out, \
            f"expected fetch_url flag in output:\n{out}"
        # Verify ledger event emitted
        from prusik.ledger import read_all
        evs = [e for e in read_all()
               if e.get("event") == "reviewer_binding_flagged"]
        assert evs, "ledger event reviewer_binding_flagged must be emitted"
        assert evs[0]["binding_class"] == "fetch_url"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)



# ============================================================
# v0.22.0 — Cross-stack parity (Express/Next.js binding extractors)
# ============================================================

def test_v0220_express_route_extraction():
    """Express route regex matches app.X / router.X / customRouter.X
    across HTTP methods. Backtick literals captured; interpolations not."""
    from prusik.binding_detect_js import extract_express_routes
    src = """
    const app = express();
    app.get('/users', listUsers);
    router.post("/items", createItem);
    clientsRouter.delete(`/items/${id}`, deleteItem);
    app.use("/admin", adminRouter);
    """
    routes = extract_express_routes(src)
    paths = {(r["router"], r["method"], r["path"]) for r in routes}
    assert ("app", "get", "/users") in paths
    assert ("router", "post", "/items") in paths
    # `.use(` MUST NOT be counted as a route (it's a mount)
    assert ("app", "use", "/admin") not in paths
    # Backtick with interpolation: bail at $ — the route is NOT captured
    # (conservative — we'd rather miss a dynamic path than mis-bind one).
    interpolated = [r for r in routes if "items/" in r["path"]]
    assert len(interpolated) == 0


def test_v0220_express_use_prefix_captured():
    """app.use('/prefix', router) becomes a router-name → prefix map."""
    from prusik.binding_detect_js import extract_router_prefixes_js
    src = """
    app.use('/invoices', clientsRouter);
    app.use("/admin", adminRouter);
    """
    pfx = extract_router_prefixes_js(src)
    assert pfx["clientsRouter"] == "/invoices"
    assert pfx["adminRouter"] == "/admin"


def test_v0220_nextjs_app_router_routes():
    """app/<segments>/route.ts exports → routes at /<segments>. Each
    exported HTTP-method function becomes a route entry."""
    import tempfile
    from prusik.binding_detect_js import extract_nextjs_routes
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        route_dir = td_path / "app" / "clients" / "search"
        route_dir.mkdir(parents=True)
        route_file = route_dir / "route.ts"
        route_file.write_text("""
export async function GET(request: Request) { return Response.json({}); }
export const POST = async (request: Request) => Response.json({});
""")
        routes = extract_nextjs_routes(route_file, td_path)
    paths = {(r["method"], r["path"]) for r in routes}
    assert ("get", "/clients/search") in paths
    assert ("post", "/clients/search") in paths


def test_v0220_nextjs_pages_api_routes():
    """pages/api/<segments>.ts → /api/<segments> (Pages Router)."""
    import tempfile
    from prusik.binding_detect_js import extract_nextjs_routes
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        api_dir = td_path / "pages" / "api"
        api_dir.mkdir(parents=True)
        f = api_dir / "users.ts"
        f.write_text("export default function handler(req, res) {}")
        routes = extract_nextjs_routes(f, td_path)
    assert any(r["path"] == "/api/users" for r in routes)


def test_v0220_jsx_form_name_extraction():
    """JSX <input name="X"/> extracted; dynamic name={...} ignored."""
    from prusik.binding_detect_js import extract_jsx_form_names
    src = """
    function Form() {
      return (
        <form>
          <input name="legal_name" />
          <input name={dynamic} />
          <textarea name='notes' />
        </form>
      );
    }
    """
    names = {n["name"] for n in extract_jsx_form_names(src)}
    assert "legal_name" in names
    assert "notes" in names
    # Dynamic name SHOULD NOT be captured (would create FPs).
    assert "dynamic" not in names


def test_v0220_js_handler_form_keys_req_body():
    """req.body.X / req.body['X'] captured as handler form-keys."""
    from prusik.binding_detect_js import extract_handler_form_keys_js
    src = """
    function create(req, res) {
      const name = req.body.legal_name;
      const role = req.body['user_role'];
      const tag = req.query.tag;
    }
    """
    keys = {h["key"] for h in extract_handler_form_keys_js(src)}
    assert "legal_name" in keys
    assert "user_role" in keys
    assert "tag" in keys


def test_v0220_eval_case_004_express_fetch_url_class():
    """Case-004 (JS-stack DEV-1 root #1 mechanism) — must hit on initial,
    must NOT false-fire on clean. Substantiates JS binding-detection."""
    from prusik import eval as kit_eval
    cases = [c for c in kit_eval.list_cases()
             if c["id"] == "case-004-express-fetch-url-mismatch"]
    assert cases, "case-004 (cross-stack) missing from corpus"
    result = kit_eval.run_case(cases[0])
    assert result["ok"], f"case-004 failed: {result['checks']}"


def test_v0220_cross_file_express_mount_resolves():
    """Express mounts the prefix in app.use(...) IN A DIFFERENT FILE
    than the route definition. The union_prefixes resolution must
    bridge that. This is the key fix that makes case-004 hit."""
    import tempfile
    from prusik.binding_check import find_unbinding_pairs
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "server").mkdir()
        # app.use(...) mount is HERE
        (root / "server" / "index.ts").write_text("""
import express from "express";
import { clientsRouter } from "./clients-router";
const app = express();
app.use("/invoices", clientsRouter);
""")
        # The route definition is HERE — no prefix declared in-file
        (root / "server" / "clients-router.ts").write_text("""
import { Router } from "express";
export const clientsRouter = Router();
clientsRouter.get("/clients/search", (req, res) => res.json({}));
""")
        (root / "components").mkdir()
        # Template fetches the BARE path — should flag
        (root / "components" / "X.tsx").write_text("""
function X() {
  const onClick = () => fetch("/clients/search?q=1");
  return <button onClick={onClick}>Go</button>;
}
""")
        touched = [
            root / "server" / "index.ts",
            root / "server" / "clients-router.ts",
            root / "components" / "X.tsx",
        ]
        findings = find_unbinding_pairs(touched, root)
    fetch_findings = [f for f in findings if f["class"] == "fetch_url"]
    assert len(fetch_findings) == 1, \
        f"expected 1 fetch_url finding (the cross-file Express prefix mismatch); got {len(fetch_findings)}"
    assert "/invoices/clients/search" in fetch_findings[0]["expected"], \
        "the cross-file resolution must suggest the prefixed path"


def test_v0220_is_js_route_file_classifies_correctly():
    """is_js_route_file recognizes Express files, Next.js App Router,
    Next.js Pages API; rejects non-route JS files."""
    import tempfile
    from prusik.binding_detect_js import is_js_route_file
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Express
        ex = root / "server.ts"
        ex.write_text("app.get('/foo', () => {});")
        assert is_js_route_file(ex)
        # Next.js App Router
        ar_dir = root / "app" / "users"
        ar_dir.mkdir(parents=True)
        ar = ar_dir / "route.ts"
        ar.write_text("export async function GET() {}")
        assert is_js_route_file(ar)
        # Next.js Pages API
        pages = root / "pages" / "api"
        pages.mkdir(parents=True)
        pf = pages / "users.ts"
        pf.write_text("export default handler;")
        assert is_js_route_file(pf)
        # Non-route JS file
        comp = root / "Button.tsx"
        comp.write_text("export const Button = () => <button>X</button>;")
        assert not is_js_route_file(comp)


