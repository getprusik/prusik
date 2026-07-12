"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: test_suggestion (kit/test_suggestion.py, v0.27.0).

The suggestion module turns a binding-mismatch flag into a scaffolded
test the operator can paste. These tests verify the scaffolds parse,
target the right URL/name, and use the appropriate stack idiom.
"""

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
import ast


def test_v0270_suggest_fetch_url_python_emits_pytest_scaffold():
    """Python+FastAPI fetch_url finding gets a pytest+TestClient scaffold
    that mentions the qualified URL and uses the correct HTTP method."""
    from prusik import test_suggestion
    finding = {
        "class": "fetch_url",
        "template": "templates/foo.html",  # .html → python stack
        "url": "/clients/search",
        "expected": ["/invoices/clients/search"],
        "method": "get",
    }
    sug = test_suggestion.suggest_for_finding(finding)
    assert sug is not None
    assert sug["stack"] == "python"
    assert sug["class"] == "fetch_url"
    assert sug["target"] == "/invoices/clients/search"
    assert "TestClient" in sug["code"]
    assert "/invoices/clients/search" in sug["code"]
    assert "client.get(" in sug["code"]
    assert sug["name"].startswith("test_")


def test_v0270_suggest_fetch_url_js_emits_supertest_scaffold():
    """JS-stack fetch_url finding gets a jest+supertest scaffold."""
    from prusik import test_suggestion
    finding = {
        "class": "fetch_url",
        "template": "components/Search.tsx",  # .tsx → js stack
        "url": "/clients/search",
        "expected": ["/invoices/clients/search"],
        "method": "get",
    }
    sug = test_suggestion.suggest_for_finding(finding)
    assert sug is not None
    assert sug["stack"] == "js"
    assert "supertest" in sug["code"]
    assert "/invoices/clients/search" in sug["code"]
    assert "expect(response.status).not.toBe(404)" in sug["code"]


def test_v0270_suggest_form_name_python_emits_form_post_scaffold():
    """form_name finding gets a scaffold that posts the form field."""
    from prusik import test_suggestion
    finding = {
        "class": "form_name",
        "template": "templates/form.html",
        "name": "legal_name",
        "expected": ["other_key"],
    }
    sug = test_suggestion.suggest_for_finding(finding)
    assert sug is not None
    assert sug["stack"] == "python"
    assert sug["class"] == "form_name"
    assert sug["target"] == "legal_name"
    assert "'legal_name': 'test-value'" in sug["code"]
    assert "client.post" in sug["code"]


def test_v0270_suggest_form_name_js_emits_send_scaffold():
    """JS form_name finding uses .send({}) idiom of supertest."""
    from prusik import test_suggestion
    finding = {
        "class": "form_name",
        "template": "components/Form.tsx",
        "name": "user_name",
        "expected": [],
    }
    sug = test_suggestion.suggest_for_finding(finding)
    assert sug is not None
    assert sug["stack"] == "js"
    assert ".send({ user_name: 'test-value' })" in sug["code"]


def test_v0270_suggest_python_scaffold_is_valid_python():
    """The generated Python scaffold must parse — otherwise the operator
    pastes a syntax error. ast.parse fails on syntax errors."""
    from prusik import test_suggestion
    finding = {
        "class": "fetch_url",
        "template": "templates/foo.html",
        "expected": ["/api/x"],
        "method": "post",
    }
    sug = test_suggestion.suggest_for_finding(finding)
    # The scaffold has <your_module> placeholders — replace them for parsing
    code = sug["code"].replace("<your_module>", "my_app")
    # ast.parse raises SyntaxError on invalid syntax; pass = parses cleanly
    ast.parse(code)


def test_v0270_suggest_form_name_python_scaffold_is_valid_python():
    """form_name scaffold also parses."""
    from prusik import test_suggestion
    finding = {
        "class": "form_name",
        "template": "templates/form.html",
        "name": "legal_name",
    }
    sug = test_suggestion.suggest_for_finding(finding)
    code = sug["code"].replace("<your_module>", "my_app").replace(
        "<your_form_action_path>", "/path")
    ast.parse(code)


def test_v0270_suggest_returns_none_when_finding_lacks_data():
    """If `expected` is empty, prusik can't name the qualified URL —
    no scaffold can be generated. The function returns None rather
    than emitting a broken scaffold (no-silent-fallback discipline)."""
    from prusik import test_suggestion
    finding = {
        "class": "fetch_url",
        "template": "templates/foo.html",
        "url": "/x",
        "expected": [],  # nothing to suggest
    }
    assert test_suggestion.suggest_for_finding(finding) is None


def test_v0270_suggest_returns_none_for_unsupported_class():
    """Unsupported finding classes get None — explicit, not a stub
    that pretends to suggest something."""
    from prusik import test_suggestion
    finding = {"class": "some_future_class"}
    assert test_suggestion.suggest_for_finding(finding) is None


def test_v0270_binding_check_attaches_suggested_test_to_findings():
    """find_unbinding_pairs (the public detector) now adds
    suggested_test to each finding. End-to-end integration test."""
    from prusik.binding_check import find_unbinding_pairs
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "templates").mkdir()
        (root / "src" / "api.py").write_text(
            "from fastapi import APIRouter\n"
            "r = APIRouter(prefix='/v1')\n"
            "@r.get('/items')\n"
            "def items(): return []\n"
        )
        (root / "templates" / "page.html").write_text(
            "<button hx-get='/items'>Go</button>\n"  # missing prefix
        )
        findings = find_unbinding_pairs([
            root / "src" / "api.py",
            root / "templates" / "page.html",
        ], root)
        assert len(findings) == 1
        f = findings[0]
        assert "suggested_test" in f
        assert f["suggested_test"] is not None
        assert "/v1/items" in f["suggested_test"]["code"]


def test_v0270_safe_name_handles_url_specials():
    """The test-name slug must be a valid Python identifier even for
    URLs with slashes / dashes / dots."""
    from prusik.test_suggestion import _safe_name
    assert _safe_name("/invoices/clients/search").replace("_", "").isalnum()
    assert _safe_name("/api/v1/users-list") == "api_v1_users_list"
    # Slug starts with non-digit (Python identifier rule)
    assert not _safe_name("/123/path")[0].isdigit() or \
        _safe_name("/123/path").startswith("_")


def test_v0270_findings_scan_source_includes_suggested_test():
    """When --source scan, the agent JSON contract carries the
    suggested_test field on each binding finding."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "templates").mkdir()
        (tmp / "src" / "api.py").write_text(
            "from fastapi import APIRouter\n"
            "r = APIRouter(prefix='/v1')\n"
            "@r.get('/items')\n"
            "def items(): return []\n"
        )
        (tmp / "templates" / "page.html").write_text(
            "<button hx-get='/items'>Go</button>\n"
        )
        result = kit_findings.collect(source="scan")
        assert result["stats"]["count"] >= 1
        # Find a binding_mismatch finding and verify suggested_test
        binding = [f for f in result["findings"]
                   if f["kind"] == "binding_mismatch"]
        assert binding, "scan should produce a binding_mismatch finding"
        assert binding[0].get("suggested_test"), \
            ("scan-source findings must carry suggested_test for the "
             "agent to consume — that's the v0.27.0 promise")
    finally:
        os.chdir("/"); shutil.rmtree(tmp)
