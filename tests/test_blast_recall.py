"""Graph recall for the blast-radius gate (field retro #1 follow-on, v0.115.0+).

Recall is EDGE-CLASS coverage, not parser quality (An adopter). v0.115.0 adds the
import edge class (module-granular) + a measurement loop: compute the silent
misses (broke ∩ ¬predicted) so every missed edge-class becomes the next extractor.
"""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import blast_plan, discovery


def test_import_reach_flags_a_test_with_no_contract():
    """An adopter's silent miss, import flavor: a unit test that IMPORTS a changed
    module but names no route/template/form literal was invisible to contract
    reach. The dep-graph has the edge — now used."""
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "billing.py").write_text("def compute(x):\n    return x * 2\n")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_billing_unit.py").write_text(
            "from src.billing import compute\n"
            "def test_compute():\n    assert compute(2) == 4\n")
        (tmp / "design" / "feat").mkdir(parents=True)
        (tmp / "design" / "feat" / "plan.md").write_text(
            "## Modules touched\n- src/billing.py\n")
        discovery.dep_graph(tmp)
        r = blast_plan.plan_test_reach("feat", tmp)
        assert r["reach"] == []                       # no contract reach at all
        assert "tests/test_billing_unit.py" in r["import_reach"]   # graph caught it
        assert "tests/test_billing_unit.py" in r["at_risk_tests"]
        assert "src/billing.py" in r["graph_covered"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_failed_test_files_parses_pytest_and_vitest():
    out = (
        "FAILED tests/test_billing.py::test_checkout - AssertionError\n"
        "FAILED tests/test_billing.py::test_refund\n"
        "FAIL  packages/web/tests/routes.test.ts > free tier 403\n"
        "  ✗ apps/api/bills.spec.tsx\n")
    files = blast_plan.failed_test_files(out)
    assert "tests/test_billing.py" in files            # deduped across two failures
    assert "packages/web/tests/routes.test.ts" in files
    assert "apps/api/bills.spec.tsx" in files


def test_recall_report_computes_recall_and_silent_misses():
    """The measurement loop: of the tests that broke, how many were predicted?
    The un-predicted breaks are the silent misses (the edges the graph lacked)."""
    rep = blast_plan.recall_report(
        predicted=["tests/a.py", "tests/b.py"],
        broke={"tests/a.py", "tests/c.py", "tests/d.py"})
    assert rep["recall_pct"] == 33                     # 1 of 3 broke were predicted
    assert rep["hits"] == ["tests/a.py"]
    assert rep["silent_misses"] == ["tests/c.py", "tests/d.py"]  # encode these edge-classes


def test_recall_report_no_breaks_is_none():
    rep = blast_plan.recall_report(["tests/a.py"], set())
    assert rep["recall_pct"] is None and rep["silent_misses"] == []


def test_python_graph_is_module_granular():
    """The plugin records the full dotted path (was top-package only — the recall
    ceiling). A coarse query still matches via the prefix walk."""
    import json
    tmp = _mktmp_project()
    try:
        (tmp / "src" / "billing").mkdir(parents=True)
        (tmp / "src" / "billing" / "__init__.py").write_text("")
        (tmp / "src" / "billing" / "core.py").write_text("X = 1\n")
        (tmp / "app.py").write_text("from src.billing.core import X\nimport src.billing\n")
        discovery.dep_graph(tmp)
        deps = json.loads((tmp / ".sprint" / "dep-graph.json").read_text())["forward"]["app.py"]
        assert "src.billing.core" in deps and "src.billing" in deps   # full paths
        # module-granular query hits; a sibling module does not (precision kept)
        assert "app.py" in discovery.blast_radius("src/billing/core", tmp)
        assert "app.py" not in discovery.blast_radius("src/billing/other", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
