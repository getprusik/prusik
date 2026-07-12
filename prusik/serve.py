"""Tier-3 GUI: a tiny local web form for authoring briefs.

Stdlib http.server only — no external web framework. Renders the form from
brief-schema.yaml so a change to the schema flows to the form automatically.

Run: `prusik serve` (defaults to http://localhost:8765). Non-engineer stakeholders
can point a browser at the URL, fill in five fields, and submit — the handler
writes briefs/<feature>.md and runs `prusik gate brief` to validate.
"""

from __future__ import annotations

import html
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from prusik import schema
from prusik.ledger import project_root, append


def _render_form(brief_schema: dict) -> str:
    req = brief_schema.get("required_fields", {})
    opt = brief_schema.get("optional_fields", {})
    rows: list[str] = []

    def field_row(name: str, spec: dict, required: bool) -> str:
        section = spec["section"]
        hint_parts = []
        if "min_words" in spec:
            hint_parts.append(f"min {spec['min_words']} words")
        if "max_words" in spec:
            hint_parts.append(f"max {spec['max_words']} words")
        if "must_contain_any" in spec:
            hint_parts.append(spec.get("must_contain_any_description",
                                      f"must contain one of: {spec['must_contain_any']}"))
        hint = " · ".join(hint_parts)
        label = html.escape(section.lstrip("# ").strip())
        required_mark = "<span class=req>*</span>" if required else ""
        ftype = spec.get("type", "text")
        if ftype == "enum":
            opts_html = "".join(
                f'<option value="{html.escape(v)}">{html.escape(v)}</option>'
                for v in spec.get("values", [])
            )
            default = spec.get("default", "")
            default_html = f' value="{html.escape(default)}"' if default else ""
            control = (f'<select name="{name}"{default_html}>'
                       f'<option value="">—</option>{opts_html}</select>')
        else:
            control = f'<textarea name="{name}" rows="3"></textarea>'
        return f"""
<div class=field>
  <label>{label} {required_mark}</label>
  {control}
  {'<div class=hint>' + html.escape(hint) + '</div>' if hint else ''}
</div>
"""

    rows.extend(field_row(n, s, True) for n, s in req.items())
    rows.extend(field_row(n, s, False) for n, s in opt.items())
    body = "\n".join(rows)

    return f"""<!doctype html>
<html>
<head>
<meta charset=utf-8>
<title>New Brief — prusik</title>
<style>
  body {{ font: 14px/1.5 -apple-system, sans-serif; max-width: 640px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1 {{ font-size: 20px; margin-bottom: 0; }}
  .sub {{ color: #666; margin-bottom: 2em; }}
  .field {{ margin-bottom: 1em; }}
  label {{ display: block; font-weight: 600; margin-bottom: 0.25em; }}
  textarea, select, input {{ width: 100%; padding: 0.5em; font: inherit; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }}
  .hint {{ color: #888; font-size: 12px; margin-top: 0.25em; }}
  .req {{ color: #c33; }}
  button {{ padding: 0.6em 1.2em; background: #0366d6; color: white; border: 0; border-radius: 4px; font: inherit; cursor: pointer; }}
  button:hover {{ background: #024ea8; }}
  #result {{ margin-top: 1.5em; padding: 1em; border-radius: 4px; display: none; }}
  #result.ok {{ background: #efe; border: 1px solid #9d9; }}
  #result.err {{ background: #fee; border: 1px solid #d99; }}
  pre {{ white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>New Brief</h1>
<div class=sub>Intent contract. Five fields. Scope is derived by prusik, not declared here.</div>
<div class=field>
  <label>Feature slug <span class=req>*</span></label>
  <input name="slug" placeholder="e.g. email-receipts" pattern="[a-z0-9-]+" required>
  <div class=hint>lowercase letters, digits, hyphens only — becomes briefs/&lt;slug&gt;.md</div>
</div>
{body}
<button onclick="submit()">Submit</button>
<div id="result"></div>
<script>
async function submit() {{
  const form = {{}};
  document.querySelectorAll('textarea, select, input').forEach(e => form[e.name] = e.value.trim());
  const res = await fetch('/submit', {{
    method: 'POST',
    headers: {{ 'content-type': 'application/json' }},
    body: JSON.stringify(form),
  }});
  const data = await res.json();
  const el = document.getElementById('result');
  el.className = data.ok ? 'ok' : 'err';
  el.style.display = 'block';
  el.innerHTML = data.ok
    ? '<strong>Brief written:</strong> ' + data.path + '<br>Next: run <code>/sprint-start ' + data.slug + '</code>'
    : '<strong>Invalid:</strong><pre>' + (data.errors || []).map(e => '· ' + e).join('\\n') + '</pre>';
}}
</script>
</body>
</html>
"""


def _render_brief(form: dict, brief_schema: dict) -> str:
    """Compose briefs/<slug>.md from form fields in the order the schema defines."""
    out_lines: list[str] = []
    for name, spec in brief_schema.get("required_fields", {}).items():
        value = form.get(name, "").strip()
        out_lines.append(spec["section"])
        out_lines.append(value if value else "")
        out_lines.append("")
    for name, spec in brief_schema.get("optional_fields", {}).items():
        value = form.get(name, "").strip()
        if not value:
            continue
        out_lines.append(spec["section"])
        out_lines.append(value)
        out_lines.append("")
    return "\n".join(out_lines).rstrip() + "\n"


class _Handler(BaseHTTPRequestHandler):
    brief_schema: dict = {}
    root: Path = Path(".")

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path not in ("/", ""):
            self.send_response(404); self.end_headers(); return
        body = _render_form(self.brief_schema).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/submit":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("content-length", 0))
        try:
            form = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self._reply({"ok": False, "errors": ["invalid JSON"]})
        slug = form.get("slug", "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
            return self._reply({"ok": False, "errors": ["invalid slug (lowercase letters/digits/hyphens only)"]})
        briefs_dir = self.root / "briefs"
        briefs_dir.mkdir(exist_ok=True)
        brief_path = briefs_dir / f"{slug}.md"
        if brief_path.exists():
            return self._reply({"ok": False, "errors": [f"brief already exists: {brief_path}"]})
        content = _render_brief(form, self.brief_schema)
        brief_path.write_text(content)
        ok, errors = schema.validate_brief(brief_path)
        if not ok:
            # Leave file so author can hand-edit; still report errors.
            append("serve_brief_invalid", slug=slug, errors=errors)
            return self._reply({"ok": False, "errors": errors, "path": str(brief_path)})
        append("serve_brief_authored", slug=slug, path=str(brief_path))
        return self._reply({"ok": True, "path": str(brief_path), "slug": slug})

    def _reply(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8765, root: Path | None = None) -> int:
    root = root or project_root()
    _Handler.brief_schema = schema.load_schema("brief")
    _Handler.root = root
    httpd = HTTPServer(("127.0.0.1", port), _Handler)
    print(f"[serve] brief authoring at http://127.0.0.1:{port}")
    print(f"[serve] writes briefs/ in {root}")
    print("[serve] Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    return 0
