"""fb-41877c6a453f — a verify_in:ci criterion was credited when its spec was
merely WIRED into a CI job's run list, not when an actual green run OBSERVED it
execute. Beta shipped 3 latent bugs that surfaced on the first real CI run.

The credit now has two distinct halves: WIRED (credit_check — the spec is in a
resolvable job; recorded `ci_execution_wired`, necessary not sufficient) and
OBSERVED-GREEN (the ci_verify_command actually ran green on the integration
commit; recorded `ci_observed_green` with the commit + specs). Wired-with-specs
but no green-attesting command is refused as UNVERIFIED, never PASS.

moat-finding: fb-41877c6a453f
"""

from __future__ import annotations

import os

from prusik import gate, ci_exec, ledger, schema


def _project(tmp_path, criteria_yaml):
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    (tmp_path / ".sprint").mkdir()
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "feat.md").write_text("# feat\n")
    schema.criteria_path_for_brief(tmp_path / "briefs" / "feat.md").write_text(criteria_yaml)
    return tmp_path


def _events(tmp_path, name):
    return [r for r in ledger.read_all() if r.get("event") == name]


def _wire(monkeypatch):
    # Isolate the CREDIT logic from a full CI-workflow setup: pretend the spec is
    # wired into a resolvable job (credit_check passes).
    monkeypatch.setattr(ci_exec, "credit_check",
                        lambda root, entry: (True, "spec in CI job's run list"))


def test_wired_spec_without_green_command_is_unverified(tmp_path, monkeypatch):
    _wire(monkeypatch)
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    ci_executes: "admin-invite-panel.spec.ts"\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert not ok and results[0]["passed"] is False          # wired ≠ green → NOT credited
    out = (tmp_path / results[0]["output_path"]).read_text()
    assert "wired but never observed-green" in out and "UNVERIFIED" in out
    # wiring is recorded as WIRED, never as verified/observed-green
    assert _events(tmp_path, "ci_execution_wired")
    assert not _events(tmp_path, "ci_observed_green")


def test_wired_spec_with_green_ci_check_records_observed_green(tmp_path, monkeypatch):
    _wire(monkeypatch)
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    ci_executes: "admin-invite-panel.spec.ts"\n'
             '    ci_verify_command: "exit 0"\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert ok and results[0]["passed"] is True               # wired AND observed-green → credited
    green = _events(tmp_path, "ci_observed_green")
    assert len(green) == 1
    assert green[0]["specs"] == ["admin-invite-panel.spec.ts"]
    assert green[0]["commit"]                                 # bound to the integration commit


def test_red_ci_check_records_no_observed_green(tmp_path, monkeypatch):
    _wire(monkeypatch)
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    ci_executes: "admin-invite-panel.spec.ts"\n'
             '    ci_verify_command: "exit 1"\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert not ok and results[0]["passed"] is False          # red check → not green
    assert not _events(tmp_path, "ci_observed_green")          # nothing observed green


def test_unwired_spec_refused_before_the_green_question(tmp_path, monkeypatch):
    # credit_check FAILS (spec not in any job) → refused as ci_execution_refused,
    # and we never even reach the observed-green stage.
    monkeypatch.setattr(ci_exec, "credit_check",
                        lambda root, entry: (False, "CI does not execute: x.spec.ts"))
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    ci_executes: "x.spec.ts"\n    ci_verify_command: "exit 0"\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert not ok and results[0]["passed"] is False
    assert _events(tmp_path, "ci_execution_refused")
    assert not _events(tmp_path, "ci_observed_green")
