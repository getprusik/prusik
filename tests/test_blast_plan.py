"""Plan-time blast-radius (v0.63.0, field finding #2) — run test-reach at PLAN time on the
plan's Modules touched, so a structural change's ripple into out-of-set tests is
caught before the build, not a fix-round later."""

from __future__ import annotations

import shutil

from tests._common import _capture_stdout, _mktmp_project  # noqa: F401,E402
from prusik import blast_plan


def _write_plan(root, feature, modules_block):
    p = root / "design" / feature / "plan.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"## Modules touched\n{modules_block}\n")
    return p


# ---------- plan_modules parsing ----------

def test_plan_modules_splits_existing_and_new():
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "existing.py").write_text("#")
        _write_plan(tmp, "feat",
                    "- `src/existing.py` — change it\n"
                    "+ `src/brand_new.py` — new module\n")
        existing, new = blast_plan.plan_modules("feat", tmp)
        assert existing == ["src/existing.py"]
        assert new == ["src/brand_new.py"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plan_modules_empty_when_no_plan():
    tmp = _mktmp_project()
    try:
        assert blast_plan.plan_modules("nope", tmp) == ([], [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- the adopter scenario: a touched route, a test OUTSIDE the set ----------

def _touched_route_with_external_test():
    """A FastAPI route module the plan touches, plus a test that lives OUTSIDE
    the plan's module set but asserts on that route's path — exactly the shape
    that broke team-invites/clients-list a fix-round late."""
    tmp = _mktmp_project()
    (tmp / "api").mkdir()
    (tmp / "api" / "team.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/team')\n"
        "@router.post('/invite')\n"
        "def invite():\n"
        "    return {}\n")
    (tmp / "tests").mkdir()
    # this test references the qualified route but is NOT in the plan's modules
    (tmp / "tests" / "test_team_routes.py").write_text(
        "def test_invite():\n"
        "    resp = client.post('/team/invite')\n"
        "    assert resp.status_code == 200\n")
    return tmp


def test_plan_reach_flags_out_of_set_test_referencing_touched_route():
    tmp = _touched_route_with_external_test()
    try:
        _write_plan(tmp, "team-invites", "- `api/team.py` — add authz guard\n")
        result = blast_plan.plan_test_reach("team-invites", tmp)
        assert "tests/test_team_routes.py" in result["at_risk_tests"]
        kinds = {f["contract_kind"] for f in result["reach"]}
        assert any("route" in k.lower() for k in kinds)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_advisory_string_lists_at_risk_tests():
    tmp = _touched_route_with_external_test()
    try:
        _write_plan(tmp, "team-invites", "- `api/team.py` — add authz guard\n")
        adv = blast_plan.advisory("team-invites", tmp)
        assert adv is not None
        assert "plan-reach ADVISORY" in adv
        assert "tests/test_team_routes.py" in adv
        assert "/team/invite" in adv
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_advisory_none_when_clean():
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "util.py").write_text("def helper():\n    return 1\n")
        _write_plan(tmp, "feat", "- `src/util.py` — tweak helper\n")
        # no route/template/form contracts, no out-of-set test references
        assert blast_plan.advisory("feat", tmp) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- ranking + cap (keep the signal actionable) ----------

def test_ranked_reach_sharpest_first():
    reach = [
        {"contract_id": "/broad", "references": ["a", "b", "c", "d"]},
        {"contract_id": "/sharp", "references": ["x"]},
        {"contract_id": "/mid", "references": ["m", "n"]},
    ]
    ordered = [f["contract_id"] for f in blast_plan._ranked_route_reach(reach)]
    assert ordered == ["/sharp", "/mid", "/broad"]   # fewest refs first


# ---------- #1: intersect route-reach with plan-NAMED handlers (v0.64.0) ----------

def test_named_handler_filter_drops_unrelated_routes():
    """A touched routes.py defines two routes; the plan only NAMES one handler
    (clients_list) in Build order. Reach must keep only the named route's test,
    not the unrelated /support one — the 48→5 noise kill."""
    tmp = _mktmp_project()
    try:
        (tmp / "app").mkdir()
        (tmp / "app" / "routes.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/clients')\n"
            "def clients_list():\n"
            "    return {}\n"
            "@router.get('/support')\n"
            "def support_page():\n"
            "    return {}\n")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_clients.py").write_text(
            "def t(): client.get('/clients')\n")
        (tmp / "tests" / "test_support.py").write_text(
            "def t(): client.get('/support')\n")
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(
            "## Modules touched\n- `app/routes.py` — change clients list\n\n"
            "## Build order\n- Fix `clients_list`: add the view-model join.\n")
        result = blast_plan.plan_test_reach("feat", tmp)
        flagged = set(result["at_risk_tests"])
        assert "tests/test_clients.py" in flagged
        assert "tests/test_support.py" not in flagged   # unrelated route dropped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- #2: symbol-reach (mock-leak risk), the exact adopter shape ----------

def test_symbol_reach_flags_stub_missing_new_method():
    """The clients-list shape: Interfaces adds `paid_ytd_by_client` to the
    InvoiceRepository Protocol; a test stubs the invoice repo WITHOUT that
    method → guaranteed mock-leak. Must be flagged, ranked above route-reach."""
    tmp = _mktmp_project()
    try:
        (tmp / "domain").mkdir()
        (tmp / "domain" / "repositories.py").write_text(
            "class InvoiceRepository:\n    ...\n")
        (tmp / "tests").mkdir()
        # mocks the invoice repo but never sets paid_ytd_by_client → leak
        (tmp / "tests" / "test_clients_routes.py").write_text(
            "def _make_stub_invoice_repo():\n"
            "    m = MagicMock()\n"
            "    m.list_for_workspace.return_value = []\n"
            "    return m\n")
        # a test that DOES set it → not at risk
        (tmp / "tests" / "test_dashboard.py").write_text(
            "def t():\n"
            "    repo = MagicMock()\n"
            "    repo.paid_ytd_by_client.return_value = {}\n")
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(
            "## Modules touched\n- `domain/repositories.py` — add method\n\n"
            "## Interfaces\n"
            "Inside `class InvoiceRepository(Protocol)`:\n"
            "```python\n"
            "def paid_ytd_by_client(self, workspace_id):\n"
            "    ...\n"
            "```\n")
        result = blast_plan.plan_test_reach("feat", tmp)
        sym = result["symbol_reach"]
        assert sym, "should flag a mock-leak risk"
        assert sym[0]["contract_id"] == "InvoiceRepository.paid_ytd_by_client"
        refs = sym[0]["references"]
        assert "tests/test_clients_routes.py" in refs    # stub missing the method
        assert "tests/test_dashboard.py" not in refs     # stub sets it → safe
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_symbol_reach_excludes_real_impl_and_cooccurrence():
    """An adopter's verified 5/5-FP calibration: tests that build the REAL repo can't
    mock-leak, and a bare `stub`/`_repo` token co-occurring (prose, test-data)
    isn't a mock binding. Both must be excluded; only a true bound-mock missing
    the method survives."""
    tmp = _mktmp_project()
    try:
        (tmp / "domain").mkdir()
        (tmp / "domain" / "repositories.py").write_text("class InvoiceRepository:\n    ...\n")
        (tmp / "tests").mkdir()
        # FP 1: builds the real PsycopgInvoiceRepository → can't mock-leak
        (tmp / "tests" / "test_behavior_real.py").write_text(
            "def t():\n"
            "    invoice_repo = PsycopgInvoiceRepository(conn)\n"
            "    invoice_repo.list_for_workspace('w')\n")
        # FP 2: 'stub' only in prose + invoice_repo used against real DB, no mock
        (tmp / "tests" / "test_prose.py").write_text(
            "def t():\n"
            "    # don't stub this route; invoice_repo hits the real DB\n"
            "    invoice_repo.list_for_workspace('w')\n")
        # TRUE POSITIVE: a bound stub factory missing the new method
        (tmp / "tests" / "test_unit_clients.py").write_text(
            "def _make_stub_invoice_repo():\n"
            "    m = MagicMock()\n"
            "    return m\n")
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(
            "## Modules touched\n- `domain/repositories.py` — add method\n\n"
            "## Interfaces\nInside `class InvoiceRepository(Protocol)`:\n"
            "```python\ndef paid_ytd_by_client(self, w):\n    ...\n```\n")
        sym = blast_plan.plan_test_reach("feat", tmp)["symbol_reach"]
        refs = sym[0]["references"] if sym else []
        assert "tests/test_unit_clients.py" in refs       # true positive kept
        assert "tests/test_behavior_real.py" not in refs  # real impl excluded
        assert "tests/test_prose.py" not in refs          # co-occurrence excluded
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mock_binds_repo_distinguishes_binding_from_cooccurrence():
    rv = blast_plan._repo_vars("InvoiceRepository")
    assert blast_plan._mock_binds_repo("invoice_repo = MagicMock()\n", rv)
    assert blast_plan._mock_binds_repo("def _make_stub_invoice_repo(): ...\n", rv)
    assert not blast_plan._mock_binds_repo("# stub note; invoice_repo.list()\n", rv)
    assert not blast_plan._mock_binds_repo("repo = PsycopgInvoiceRepository(c)\n", rv)


def test_new_methods_and_owner_hints():
    plan = ("## Interfaces\nInside `class InvoiceRepository(Protocol)`:\n"
            "```python\ndef paid_ytd_by_client(self, x):\n    ...\n```\n")
    assert blast_plan._new_methods(plan) == [("InvoiceRepository", "paid_ytd_by_client")]
    hints = blast_plan._owner_hints("InvoiceRepository")
    assert "invoice_repo" in hints and "InvoiceRepository" in hints


# ---------- CLI + ledger ----------

def test_run_emits_ledger_event_and_text():
    tmp = _touched_route_with_external_test()
    try:
        _write_plan(tmp, "team-invites", "- `api/team.py` — add authz guard\n")
        import os
        os.environ["CLAUDE_PROJECT_DIR"] = str(tmp)
        out = _capture_stdout(lambda: blast_plan.run("team-invites", root=tmp))
        assert "plan-reach" in out and "at-risk" in out
        ledger = (tmp / ".sprint" / "ledger.jsonl").read_text()
        assert "plan_test_reach" in ledger
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
