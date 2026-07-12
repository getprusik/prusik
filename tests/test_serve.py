"""Coverage for the Tier-3 brief-authoring GUI (v0.43.0+).

serve.py shipped at 0% coverage — and that blind spot was hiding a stale
`claude-team-kit` brand string in the rendered page. These tests cover the
substance without a live socket: the schema→form rendering, the
form→brief→validation round-trip (the actual contract — a brief the GUI
writes must pass the same validator a hand-authored one does), and the POST
handler's write/validate/ledger path via a fake request.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from prusik import schema, serve


def _brief_schema() -> dict:
    return schema.load_schema("brief")


# ---------- render functions ----------

def test_render_form_smoke_and_current_brand():
    page = serve._render_form(_brief_schema())
    assert "claude-team-kit" not in page, "stale brand — rename residue"
    assert "prusik" in page
    assert 'name="slug"' in page
    # schema→form coupling: every required field's section label is rendered.
    for spec in _brief_schema()["required_fields"].values():
        assert spec["section"].lstrip("# ").strip() in page


def test_render_form_renders_type_enum_values():
    page = serve._render_form(_brief_schema())
    for v in _brief_schema()["required_fields"]["type"]["values"]:
        assert f'value="{v}"' in page


def test_rendered_brief_passes_validation():
    """The whole point of the GUI: what it writes must validate."""
    form = {
        "slug": "email-receipts",
        "goal": "Add email receipts on successful checkout for paying customers",
        "success": "Receipt arrives within 10 seconds of payment",
        "type": "new_feature",
    }
    content = serve._render_brief(form, _brief_schema())
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "email-receipts.md"
        p.write_text(content)
        ok, errors = schema.validate_brief(p)
    assert ok, f"GUI-authored brief must pass validate_brief: {errors}"


def test_rendered_brief_omits_empty_optionals():
    bs = _brief_schema()
    form = {"goal": "Add a small utility function plus one test",
            "success": "Returns the result within one second", "type": "doc"}
    content = serve._render_brief(form, bs)
    for name, spec in bs["optional_fields"].items():
        assert spec["section"] not in content, f"empty optional {name} leaked in"


def test_rendered_brief_includes_filled_optional():
    bs = _brief_schema()
    opt = list(bs["optional_fields"])
    if not opt:
        return
    name = opt[0]
    section = bs["optional_fields"][name]["section"]
    form = {"goal": "Add a small utility function plus one test",
            "success": "Returns the result within one second", "type": "doc",
            name: "a provided optional value"}
    content = serve._render_brief(form, bs)
    assert section in content


# ---------- POST handler (socket-free fake request) ----------

class _FakeHandler(serve._Handler):
    def __init__(self, body: bytes, root: Path, path: str = "/submit"):
        self.path = path
        self.headers = {"content-length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.root = root
        self.brief_schema = schema.load_schema("brief")

    # neutralize the wire protocol
    def send_response(self, code, *a):
        pass

    def send_header(self, *a):
        pass

    def end_headers(self):
        pass

    def reply_obj(self) -> dict:
        return json.loads(self.wfile.getvalue().decode())


def test_post_writes_valid_brief_and_replies_ok():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        body = json.dumps({
            "slug": "receipts",
            "goal": "Add email receipts on successful checkout for customers",
            "success": "Receipt arrives within 10 seconds of payment",
            "type": "new_feature",
        }).encode()
        h = _FakeHandler(body, root)
        h.do_POST()
        reply = h.reply_obj()
        assert reply["ok"] is True, reply
        assert (root / "briefs" / "receipts.md").exists()


def test_post_rejects_bad_slug():
    with tempfile.TemporaryDirectory() as d:
        body = json.dumps({"slug": "Bad Slug!", "type": "doc"}).encode()
        h = _FakeHandler(body, Path(d))
        h.do_POST()
        reply = h.reply_obj()
        assert reply["ok"] is False
        assert any("slug" in e.lower() for e in reply["errors"])


def test_post_rejects_invalid_json():
    with tempfile.TemporaryDirectory() as d:
        h = _FakeHandler(b"{not json", Path(d))
        h.do_POST()
        assert h.reply_obj()["ok"] is False


def test_post_rejects_duplicate_slug():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        body = json.dumps({
            "slug": "dup",
            "goal": "Add email receipts on successful checkout for customers",
            "success": "Receipt arrives within 10 seconds of payment",
            "type": "new_feature",
        }).encode()
        _FakeHandler(body, root).do_POST()           # first write succeeds
        h2 = _FakeHandler(body, root)
        h2.do_POST()                                 # second must refuse
        reply = h2.reply_obj()
        assert reply["ok"] is False
        assert any("exists" in e for e in reply["errors"])


def test_post_keeps_file_but_reports_when_invalid():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        body = json.dumps({"slug": "thin", "goal": "too short",
                           "success": "x", "type": "new_feature"}).encode()
        h = _FakeHandler(body, root)
        h.do_POST()
        reply = h.reply_obj()
        assert reply["ok"] is False
        assert reply.get("path"), "invalid brief is left on disk for hand-edit"
        assert (root / "briefs" / "thin.md").exists()


def test_do_get_renders_form():
    with tempfile.TemporaryDirectory() as d:
        h = _FakeHandler(b"", Path(d), path="/")
        h.do_GET()
        assert b"New Brief" in h.wfile.getvalue()
