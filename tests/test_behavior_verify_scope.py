"""brief-lint rejects a criterion verify_command that runs a behaviour/e2e test
directory broadly without excluding the project's deferred markers
(`reviewing_defer_markers`, default browser_smoke) or delegating to the prusik gate —
such a command runs live-server/browser smokes that can't reliably pass at
criterion-verify time (fb-cc918dfe40b8).

Convention-general + config-driven by design: it matches behaviour/e2e/acceptance dirs
across customer layouts (not one project's `tests/behavior`) and reads the deferred
markers from each project's own config (not a hardcoded `browser_smoke`).

moat-finding: fb-cc918dfe40b8
"""

from __future__ import annotations

from prusik import schema


def _check(tmp_path, vc, markers=schema.DEFAULT_DEFER_MARKERS):
    p = tmp_path / "feat.criteria.yaml"
    p.write_text('schema_version: "1.0"\ncriteria:\n  - id: behave\n'
                 f'    description: behaviour suite stays green\n'
                 f'    verify_command: "{vc}"\n')
    return schema.validate_criteria_file(p, project_root=tmp_path, defer_markers=markers)


def test_unscoped_behavior_dir_is_rejected(tmp_path):
    ok, errs = _check(tmp_path, "pytest tests/behavior")
    assert not ok and any("browser_smoke" in e for e in errs)


def test_trailing_slash_dir_also_rejected(tmp_path):
    ok, errs = _check(tmp_path, "python -m pytest tests/behavior/ -q")
    assert not ok and any("browser_smoke" in e for e in errs)


def test_scoped_not_browser_smoke_is_accepted(tmp_path):
    ok, _ = _check(tmp_path, "pytest tests/behavior -m 'not browser_smoke'")
    assert ok


def test_python_m_module_flag_not_confused_with_marker(tmp_path):
    """ADVERSARIAL: `python -m pytest` is the module flag, not the marker flag — the
    second `-m 'not browser_smoke'` is what scopes it. Must be accepted."""
    ok, _ = _check(tmp_path, "python -m pytest tests/e2e -m 'not browser_smoke'")
    assert ok


def test_specific_behavior_file_is_accepted(tmp_path):
    ok, _ = _check(tmp_path, "pytest tests/behavior/test_invoices.py")
    assert ok


def test_delegating_to_prusik_is_accepted(tmp_path):
    ok, _ = _check(tmp_path, "prusik prove -- pytest tests/behavior")
    assert ok


def test_unrelated_dir_not_flagged(tmp_path):
    ok, _ = _check(tmp_path, "pytest tests/unit -q")
    assert ok


# ---- convention-general: any customer's behaviour/e2e layout, not just tests/behavior

def test_generalizes_across_dir_conventions():
    f = schema._behavior_run_unscoped
    for d in ("tests/e2e", "acceptance", "features", "tests/smoke", "packages/x/tests/behaviour"):
        assert f(f"pytest {d}") is True, d
    # specific file or scoped → not flagged, for each convention
    assert f("pytest tests/e2e/test_login.py") is False
    assert f("pytest acceptance -m 'not browser_smoke'") is False


def test_behavioral_dir_is_not_falsely_flagged():
    """A different dir whose name merely STARTS with 'behavior' must not match."""
    assert schema._behavior_run_unscoped("pytest tests/behavioral") is False


# ---- config-driven: the markers come from the project, not a hardcoded string

def test_custom_configured_marker_is_honoured(tmp_path):
    # a project whose deferred marker is `e2e` (not browser_smoke)
    ok, errs = _check(tmp_path, "pytest tests/e2e", markers=("e2e",))
    assert not ok and any("e2e" in e for e in errs)
    # excluding the CONFIGURED marker satisfies it
    ok2, _ = _check(tmp_path, "pytest tests/e2e -m 'not e2e'", markers=("e2e",))
    assert ok2
    # excluding the WRONG marker does not
    ok3, _ = _check(tmp_path, "pytest tests/e2e -m 'not browser_smoke'", markers=("e2e",))
    assert not ok3


def test_multiple_markers_all_must_be_excluded():
    f = schema._behavior_run_unscoped
    markers = ("browser_smoke", "live_server")
    assert f("pytest tests/e2e -m 'not browser_smoke'", markers) is True   # missed live_server
    assert f("pytest tests/e2e -m 'not (browser_smoke or live_server)'", markers) is False
