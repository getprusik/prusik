"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: detectors (prusik/detectors/) — Phase 1 of the pluggable detector
API. The Finding contract + the two built-in adapters; scan output must stay
byte-identical (legacy round-trip).

Run: uv run python -m pytest tests/test_detectors.py -v
"""

from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)

from prusik import detectors
from prusik.detectors import binding as binding_det, test_reach as reach_det
from prusik.detectors.base import Finding, ScanContext


# ---------- registry ----------

def test_builtin_registry_has_both_detectors():
    assert set(detectors.BUILTIN) == {"binding", "test-reach"}
    for name, mod in detectors.BUILTIN.items():
        assert mod.NAME == name
        assert callable(mod.detect)
        assert mod.DESCRIPTION


# ---------- Finding contract ----------

def test_finding_to_json_normalized_view():
    f = Finding(detector="binding", cls="fetch_url", severity="medium",
                message="m", file="t.html", line=7, expected=["/api/user"],
                meta={"url": "/api/usr"})
    j = f.to_json()
    assert j["detector"] == "binding" and j["class"] == "fetch_url"
    assert j["file"] == "t.html" and j["line"] == 7
    assert j["meta"]["url"] == "/api/usr"


def test_binding_finding_to_json_carries_all_engine_info():
    """No legacy dict — the canonical to_json must hold everything the engine
    produced (url/kind in meta, template→file, line, expected, msg→message)."""
    src = {"class": "fetch_url", "severity": "medium", "template": "t.html",
           "template_line": 7, "url": "/x", "kind": "hx-get",
           "expected": ["/y"], "msg": "boom"}
    j = binding_det._to_finding(src).to_json()
    assert j["detector"] == "binding" and j["class"] == "fetch_url"
    assert j["file"] == "t.html" and j["line"] == 7
    assert j["expected"] == ["/y"] and j["message"] == "boom"
    assert j["meta"]["url"] == "/x" and j["meta"]["kind"] == "hx-get"


def test_finding_to_json_is_complete_for_custom():
    f = Finding(detector="custom", cls="x", severity="high", message="m",
                file="a.py", line=3, expected=["b"], meta={"k": 1})
    j = f.to_json()
    assert j["detector"] == "custom" and j["file"] == "a.py" and j["line"] == 3
    assert j["meta"]["k"] == 1


# ---------- binding adapter ----------

def test_binding_adapter_normalized_fields():
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "invoices.py").write_text(
            "from fastapi import APIRouter\n"
            'invoices_router = APIRouter(prefix="/invoices")\n'
            '@invoices_router.get("/clients/search")\n'
            "async def search_clients(): pass\n"
        )
        (tmp / "templates").mkdir(exist_ok=True)
        (tmp / "templates" / "_inline_client_form.html").write_text(
            '<button hx-get="/clients/search">Search</button>\n'
        )
        files = [tmp / "src" / "invoices.py",
                 tmp / "templates" / "_inline_client_form.html"]
        findings = binding_det.detect(ScanContext(root=tmp, files=files))
        fetch = [f for f in findings if f.cls == "fetch_url"]
        assert fetch, "expected a fetch_url finding"
        f = fetch[0]
        assert f.detector == "binding" and f.severity == "medium"
        assert f.file and f.file.endswith("_inline_client_form.html")
        assert any("/invoices/clients/search" in e for e in f.expected)
        assert f.meta.get("url")  # url preserved in meta
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_binding_adapter_empty_when_bindings_correct():
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "api.py").write_text(
            "from fastapi import APIRouter\n"
            'r = APIRouter()\n@r.get("/clients/search")\n'
            "async def s(): pass\n"
        )
        (tmp / "templates").mkdir(exist_ok=True)
        (tmp / "templates" / "form.html").write_text(
            '<button hx-get="/clients/search">x</button>\n'
        )
        files = [tmp / "src" / "api.py", tmp / "templates" / "form.html"]
        findings = binding_det.detect(ScanContext(root=tmp, files=files))
        assert [f for f in findings if f.cls == "fetch_url"] == []
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


# ---------- test-reach adapter mapping (unit; engine covered elsewhere) ----------

def test_reach_adapter_maps_to_normalized():
    src = {"class": "route", "contract_id": "/api/x", "contract_kind": "GET route",
           "file_hint": "src/api.py", "references": ["tests/test_x.py:4"]}
    f = reach_det._to_finding(src)
    assert f.detector == "test-reach" and f.cls == "route"
    assert f.severity == "info"
    assert f.file == "src/api.py"
    assert "/api/x" in f.message
    assert f.meta["references"] == ["tests/test_x.py:4"]
    assert f.meta["contract_id"] == "/api/x"


def test_reach_adapter_empty_file_hint_becomes_none():
    src = {"class": "form_name", "contract_id": "email", "contract_kind": "form field name",
           "file_hint": "", "references": ["t.py:1"]}
    f = reach_det._to_finding(src)
    assert f.file is None
    assert f.meta["contract_id"] == "email"


# ---------- scan integration: output unchanged ----------

def test_scan_json_routes_through_detectors_unchanged():
    """scan --json still emits binding_findings/test_reach_findings; the values
    match the engines directly (scan now routes through the adapters)."""
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "invoices.py").write_text(
            "from fastapi import APIRouter\n"
            'r = APIRouter(prefix="/invoices")\n'
            '@r.get("/clients/search")\n'
            "async def s(): pass\n"
        )
        (root / "templates").mkdir()
        (root / "templates" / "f.html").write_text(
            '<button hx-get="/clients/search">x</button>\n'
        )
        out = _capture_stdout(lambda: kit_scan.scan(root=root, json_output=True))
        data = json.loads(out)
        # canonical findings list is the only representation; legacy keys gone
        assert "findings" in data and "detectors" in data
        assert "binding_findings" not in data and "test_reach_findings" not in data
        assert data["total"] == len(data["findings"])
        f = next(f for f in data["findings"]
                 if f["detector"] == "binding" and f["class"] == "fetch_url")
        assert f["file"] and f["line"] and f["meta"].get("url")  # normalized shape


# ========== Phase 2: registry load() + config ==========

def test_load_default_is_builtins():
    reg = detectors.load(config={})
    assert set(reg) == {"binding", "test-reach"}


def test_load_enabled_restricts():
    reg = detectors.load(config={"enabled": ["binding"]})
    assert set(reg) == {"binding"}


def test_load_disabled_drops():
    reg = detectors.load(config={"disabled": ["test-reach"]})
    assert set(reg) == {"binding"}


def test_scan_rc_fail_on_severity():
    from prusik.scan import _scan_rc
    hi = [Finding("d", "c", "high", "m")]
    info = [Finding("d", "c", "info", "m")]
    assert _scan_rc(hi, ["high"], 1) == 1
    assert _scan_rc(info, ["high"], 1) == 0       # info not in fail_on → pass
    assert _scan_rc(info, None, 1) == 1            # no fail_on → historical (any finding)
    assert _scan_rc([], None, 0) == 0


def test_detector_config_reads_block():
    from prusik.scan import _detector_config
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); (root / ".claude").mkdir()
        (root / ".claude" / "sprint-config.yaml").write_text(
            "detectors:\n  enabled: [binding]\n  fail_on: [high]\n")
        cfg = _detector_config(root)
        assert cfg["enabled"] == ["binding"] and cfg["fail_on"] == ["high"]


# ========== Phase 3: project-local .claude/detectors/*.py ==========

_LOCAL_DETECTOR = '''
from prusik.detectors.base import Finding
NAME = "flagme"
DESCRIPTION = "flags any file literally named flagme.py"
def detect(ctx):
    return [Finding(detector=NAME, cls="named", severity="high",
                    message="file is named flagme.py",
                    file=str(f.relative_to(ctx.root)))
            for f in ctx.files if f.name == "flagme.py"]
'''


def _repo_with_local_detector(td, detector_src=_LOCAL_DETECTOR):
    root = Path(td)
    (root / ".claude" / "detectors").mkdir(parents=True)
    (root / ".claude" / "detectors" / "flagme.py").write_text(detector_src)
    (root / "flagme.py").write_text("x = 1\n")
    return root


def test_load_picks_up_local_detector():
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with_local_detector(td)
        reg = detectors.load(root=root, config={})
        assert "flagme" in reg and reg["flagme"].NAME == "flagme"


def test_load_respects_no_local():
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with_local_detector(td)
        reg = detectors.load(root=root, config={}, allow_local=False)
        assert "flagme" not in reg
        reg2 = detectors.load(root=root, config={"allow_local_detectors": False})
        assert "flagme" not in reg2


def test_load_skips_malformed_local(capsys=None):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".claude" / "detectors").mkdir(parents=True)
        (root / ".claude" / "detectors" / "broken.py").write_text("NAME = 'x'\n")  # no detect()
        (root / ".claude" / "detectors" / "syntaxerr.py").write_text("def detect(:\n")
        reg = detectors.load(root=root, config={})
        assert set(reg) == {"binding", "test-reach"}  # neither bad file registered


def test_scan_surfaces_local_detector_findings():
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with_local_detector(td)
        out = _capture_stdout(lambda: kit_scan.scan(root=root, json_output=True))
        data = json.loads(out)
        assert "flagme" in data["detectors"]
        flagged = [f for f in data["findings"] if f["detector"] == "flagme"]
        assert flagged and flagged[0]["severity"] == "high"
        assert data["total"] >= 1
        # single canonical shape — no legacy keys
        assert "binding_findings" not in data


def test_scan_detectors_flag_filters_to_named():
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with_local_detector(td)
        # only run flagme → binding is excluded
        out = _capture_stdout(lambda: kit_scan.scan(
            root=root, json_output=True, detector_names=["flagme"]))
        data = json.loads(out)
        assert data["detectors"] == ["flagme"]


def test_scan_no_local_flag_excludes_custom():
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with_local_detector(td)
        out = _capture_stdout(lambda: kit_scan.scan(
            root=root, json_output=True, allow_local=False))
        data = json.loads(out)
        assert "flagme" not in data["detectors"]


def test_scan_fail_on_gates_rc_end_to_end():
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        root = _repo_with_local_detector(td)
        (root / ".claude" / "sprint-config.yaml").write_text(
            "detectors:\n  fail_on: [high]\n")
        # flagme emits high → rc 1
        rc = kit_scan.scan(root=root, json_output=True)
        # capture suppressed; just assert rc
        assert rc == 1
        # if fail_on only lists 'medium', the high finding shouldn't gate
        (root / ".claude" / "sprint-config.yaml").write_text(
            "detectors:\n  fail_on: [medium]\n")
        rc2 = kit_scan.scan(root=root, json_output=True)
        assert rc2 == 0


# ========== Phase 4: custom detectors flow into findings / metrics / ci-comment ==========

def test_findings_scan_source_surfaces_custom_detector():
    """`prusik findings --source scan` runs the registry → a custom detector's
    finding appears, tagged with detector + the v1.1 schema."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        (tmp / ".claude" / "detectors").mkdir(parents=True, exist_ok=True)
        (tmp / ".claude" / "detectors" / "flagme.py").write_text(_LOCAL_DETECTOR)
        (tmp / "flagme.py").write_text("x = 1\n")
        result = kit_findings.collect(source="scan")
        assert result["schema_version"] == "1.1"
        custom = [f for f in result["findings"] if f.get("detector") == "flagme"]
        assert custom, f"expected a flagme finding, got {result['findings']}"
        assert custom[0]["kind"] == "detector"
        assert custom[0]["severity"] == "high"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_findings_event_carries_detector_field():
    from prusik import findings as kit_findings
    f = kit_findings._event_to_finding(
        {"event": "reviewer_binding_flagged", "ts": "t", "binding_class": "fetch_url"})
    assert f["detector"] == "binding"
    g = kit_findings._event_to_finding(
        {"event": "detector_flagged", "ts": "t", "detector": "my-check",
         "cls": "x", "severity": "high", "summary": "boom"})
    assert g["detector"] == "my-check" and g["kind"] == "detector"
    assert g["severity"] == "high" and g["summary"] == "boom"


def test_metrics_counts_detector_flagged():
    from prusik import metrics
    records = [
        {"ts": "t", "event": "detector_flagged", "detector": "my-check",
         "cls": "x", "severity": "high", "feature": "a"},
        {"ts": "t", "event": "detector_flagged", "detector": "my-check",
         "cls": "x", "severity": "high", "feature": "a"},
        {"ts": "t", "event": "reviewer_binding_flagged", "feature": "a"},
    ]
    m = metrics.compute(records)
    assert m["caught_before_merge"]["custom_detector_flags"] == 2
    assert m["by_detector"]["my-check"] == 2
    # headline includes binding (1) + custom (2)
    assert m["headline_caught_before_merge"] >= 3


def test_ci_comment_renders_custom_detector():
    from prusik import ci_comment
    data = {
        "root": "/r", "stats": {"total_files": 3}, "total": 1,
        "detectors": ["binding", "flagme"],
        "findings": [{"detector": "flagme", "class": "named", "severity": "high",
                      "file": "src/x.py", "line": 2, "message": "bad thing"}],
    }
    md = ci_comment.format_comment(data)
    assert "### flagme (1)" in md
    assert "[high]" in md and "src/x.py:2" in md and "bad thing" in md


def test_gate_check_bindings_logs_custom_detector_flagged():
    """The reviewer path (check-bindings) also runs project-local detectors and
    logs detector_flagged → reaches the ledger → metrics."""
    from prusik import gate, ledger
    import argparse as _ap
    tmp = _mktmp_project()
    try:
        (tmp / ".claude" / "detectors").mkdir(parents=True, exist_ok=True)
        (tmp / ".claude" / "detectors" / "flagme.py").write_text(_LOCAL_DETECTOR)
        wt = tmp / "worktrees" / "solo"
        wt.mkdir(parents=True)
        (wt / "flagme.py").write_text("x = 1\n")
        args = _ap.Namespace(feature="feat", touched_set=None)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = gate.check_bindings(args)
        assert rc == 0
        events = [e for e in ledger.read_all() if e.get("event") == "detector_flagged"]
        assert events and events[0]["detector"] == "flagme"
        assert events[0]["severity"] == "high"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)
