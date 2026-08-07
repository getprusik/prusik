"""CI credit is a claim about EXECUTION (fb-39bd12ff439b).

moat-finding:fb-39bd12ff439b — a green CI job credited criteria whose specs
run in NO job: 12 of 22 e2e specs (all auth flows) executed nowhere in CI.
The executed set must come from the RUNNER'S OWN resolver, never the
workflow's YAML arg list; unresolvable invocations are loud and cover
nothing.
"""

import os
import stat

from prusik import ci_exec
from prusik.detectors import ci_orphan_specs
from prusik.detectors.base import ScanContext

WF = """name: ci
jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - name: Run e2e
        run: |
          npx playwright test \\
            tests/e2e/a.spec.ts \\
            tests/e2e/b.spec.ts
  unit:
    steps:
      - run: pytest tests/unit -q
  dyn:
    steps:
      - run: npx playwright test ${{ matrix.shard }}
"""

FAKE_PLAYWRIGHT = """#!/bin/bash
if [[ "$*" != *"--list"* ]]; then echo "not a list call" >&2; exit 2; fi
echo "  [Desktop Chrome] › a.spec.ts:10:5 › t1"
echo "  [Desktop Chrome] › b.spec.ts:12:5 › t2"
echo "Total: 2 tests in 2 files"
"""

FAKE_PYTEST = """#!/bin/bash
if [[ "$*" != *"--collect-only"* ]]; then echo "not collect" >&2; exit 2; fi
echo "tests/unit/test_core.py::test_a"
echo "tests/unit/test_core.py::test_b"
echo "2 tests collected"
"""


def _repo(tmp_path, monkeypatch, wf=WF, fakes=True):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(wf)
    d = tmp_path / "tests" / "e2e"
    d.mkdir(parents=True)
    for n in ("a.spec.ts", "b.spec.ts", "login.spec.ts"):
        (d / n).write_text("// spec\n")
    if fakes:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, body in (("npx", FAKE_PLAYWRIGHT), ("pytest", FAKE_PYTEST)):
            p = bindir / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return tmp_path


def test_extraction_normalizes_continuations_and_flags_dynamic(
        tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, fakes=False)
    invs = ci_exec.test_invocations(root)
    by_job = {i["job"]: i for i in invs}
    assert "tests/e2e/a.spec.ts tests/e2e/b.spec.ts" in by_job["e2e"]["command"]
    assert "\\" not in by_job["e2e"]["command"]
    assert by_job["unit"]["command"].startswith("pytest")
    assert by_job["dyn"]["dynamic"] is True


def test_resolver_derives_playwright_file_set(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    inv = [i for i in ci_exec.test_invocations(root) if i["job"] == "e2e"][0]
    assert ci_exec.resolve_invocation(inv, root) == {"a.spec.ts", "b.spec.ts"}


def test_resolver_derives_pytest_file_set(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    inv = [i for i in ci_exec.test_invocations(root) if i["job"] == "unit"][0]
    assert ci_exec.resolve_invocation(inv, root) == {"tests/unit/test_core.py"}


def test_dynamic_and_spawnfail_are_unresolved_not_empty(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, fakes=False)   # no stubs on PATH
    invs = ci_exec.test_invocations(root)
    dyn = [i for i in invs if i["job"] == "dyn"][0]
    assert ci_exec.resolve_invocation(dyn, root) is None
    e2e = [i for i in invs if i["job"] == "e2e"][0]
    monkeypatch.setenv("PATH", "/nonexistent")
    assert ci_exec.resolve_invocation(e2e, root) is None


def test_union_reports_resolved_and_unresolved(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    u = ci_exec.executed_union(root)
    assert {"a.spec.ts", "b.spec.ts"} <= u["resolved"]
    assert [i["job"] for i in u["unresolved"]] == ["dyn"]


def test_membership_by_suffix(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    ok, why = ci_exec.credit_check(root, {"ci_executes": "tests/e2e/a.spec.ts"})
    assert ok
    ok, why = ci_exec.credit_check(root,
                                   {"ci_executes": "tests/e2e/login.spec.ts"})
    assert not ok and "login.spec.ts" in why


def test_spec_extracted_from_verify_command_when_not_explicit(
        tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    entry = {"verify_command":
             "npx playwright test tests/e2e/login.spec.ts --project=chrome"}
    ok, why = ci_exec.credit_check(root, entry)
    assert not ok and "login.spec.ts" in why


def test_no_spec_reference_claims_nothing_and_passes(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    ok, why = ci_exec.credit_check(
        root, {"ci_verify_command": "gh pr checks 12 --required"})
    assert ok


def test_orphan_detector_names_uncovered_specs(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    findings = ci_orphan_specs.detect(
        ScanContext(root=root, files=list(root.rglob("*"))))
    msgs = [f.message for f in findings]
    assert any("login.spec.ts" in m for m in msgs)          # orphan named
    assert not any("a.spec.ts:" in m for m in msgs)
    assert any("unresolved" in m.lower() for m in msgs)     # dyn is loud


def test_orphan_detector_loud_when_nothing_resolves(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, fakes=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    findings = ci_orphan_specs.detect(
        ScanContext(root=root, files=list(root.rglob("*"))))
    assert findings                                          # never silence
    assert not any("orphan" in f.cls for f in findings)      # no false claim
    assert all("unresolved" in (f.cls + f.message).lower() for f in findings)
