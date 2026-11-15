"""JUnit XML evidence across the surfaces: prove, criteria, prove-red, capture, SARIF, action.yml.

Criterion `junit-surfaces`. The hash tests are the proof that reports a `--junit` capture's own
run writes never move the judged-code hash, and that when they would, the capture is refused
instead of stamped against a tree its own reports changed.

moat-finding: fb-511273152fe7
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from prusik import evidence, gate, ledger, prove, sarif, schema

REPO = Path(__file__).resolve().parent.parent
FEATURE = "feat"
OK_XML = ('<testsuite><testcase classname="c" name="a"/>'
          '<testcase classname="c" name="b"><failure/></testcase>'
          '<testcase classname="c" name="s"><skipped/></testcase></testsuite>')


def _writer(rel: str, body: str = OK_XML) -> str:
    """A shell command that writes a report and prints nothing."""
    d = os.path.dirname(rel)
    return f"mkdir -p {d} && printf '%s' '{body}' > {rel}"


def _events(name: str) -> list[dict]:
    return [e for e in ledger.read_all() if e.get("event") == name]


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / ".sprint").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def _prove(capsys, cmd, **kw):
    capsys.readouterr()
    rc = prove.run(["--", "bash", "-c", cmd], json_output=kw.pop("json_output", True), **kw)
    return rc, capsys.readouterr()


# ---- prove ----------------------------------------------------------------------------------

def test_prove_junit_counts_from_reports_and_json_fields(proj, capsys):
    rc, out = _prove(capsys, _writer("r/a.xml"), junit="r/*.xml")
    v = json.loads(out.out)
    assert rc == 0 and v["proven"] and v["executed"] == 2
    assert v["junit_files"] == ["r/a.xml"]
    assert v["failed_tests"] == ["c::b"] and v["skipped_tests"] == ["c::s"]


def test_prove_junit_stale_report_not_proven(proj, capsys):
    p = proj / "r" / "old.xml"
    p.parent.mkdir()
    p.write_text(OK_XML)
    os.utime(p, (1_000_000, 1_000_000))
    rc, out = _prove(capsys, "true", junit="r/*.xml")
    v = json.loads(out.out)
    assert rc == 1 and not v["proven"]
    assert "'r/*.xml'" in v["reason"] and "1 older match(es)" in v["reason"]


@pytest.mark.parametrize("kind", ["lint", "types"])
def test_prove_junit_with_lint_or_types_is_usage_error(proj, capsys, kind):
    rc, out = _prove(capsys, "true", kind=kind, junit="r/*.xml")
    assert rc == 2 and "--junit" in out.err


def test_prove_junit_sarif_proven_carries_fields(proj, capsys):
    rc, out = _prove(capsys, _writer("r/a.xml"), junit="r/*.xml",
                     json_output=False, sarif_output=True)
    run = json.loads(out.out)["runs"][0]
    assert rc == 0 and run["results"] == []
    assert run["properties"] == {"junit_files": ["r/a.xml"], "failed_tests": ["c::b"],
                                 "skipped_tests": ["c::s"]}


def test_prove_junit_sarif_not_proven_carries_fields(proj, capsys):
    rc, out = _prove(capsys, "true", junit="r/*.xml", json_output=False, sarif_output=True)
    run = json.loads(out.out)["runs"][0]
    assert rc == 1 and len(run["results"]) == 1
    assert run["properties"] == {"junit_files": [], "failed_tests": [], "skipped_tests": []}


def test_prove_without_junit_json_keys_unchanged(proj, capsys):
    rc, out = _prove(capsys, "echo '2 passed'")
    assert rc == 0
    assert set(json.loads(out.out)) == {"command", "kind", "exit_code", "executed",
                                        "min_executed", "proven", "reason"}
    v = {"command": "c", "kind": "tests", "exit_code": 0, "executed": 1, "min_executed": 1,
         "proven": True, "reason": "r"}
    assert "properties" not in sarif.from_prove(v)["runs"][0]


def test_prove_run_ledger_records_glob_and_file_count(proj, capsys):
    _prove(capsys, _writer("r/a.xml"), junit="r/*.xml")
    ev = _events("prove_run")[-1]
    assert ev["junit"] == "r/*.xml" and ev["junit_files"] == 1
    _prove(capsys, "echo '1 passed'")
    assert "junit" not in _events("prove_run")[-1]


def _stub_gradle(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    g = bindir / "gradle"
    g.write_text("#!/bin/sh\nexit 0\n")
    g.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_gradle_zero_count_hint_same_in_prove_and_criterion(proj, capsys, monkeypatch):
    _stub_gradle(proj, monkeypatch)
    hint = evidence.junit_hint("gradle test")
    assert hint
    capsys.readouterr()
    rc = prove.run(["--", "gradle", "test"], json_output=True)
    assert rc == 1 and hint in json.loads(capsys.readouterr().out)["reason"]
    _criteria(proj, [{"id": "c1", "description": "d", "verify_command": "gradle test",
                      "kind": "tests"}])
    ok, results = gate._run_success_criteria(FEATURE, proj)
    assert not ok
    assert hint in (proj / results[0]["output_path"]).read_text()


# ---- criteria -------------------------------------------------------------------------------

def _criteria(tmp, entries):
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{FEATURE}.md").write_text("## Goal\nx\n")
    (tmp / "briefs" / f"{FEATURE}.criteria.yaml").write_text(
        yaml.safe_dump({"schema_version": "1.0", "criteria": entries}))


def test_gate_brief_rejects_bad_junit_field(proj):
    base = {"id": "c1", "description": "d", "verify_command": "true"}
    cf = proj / "briefs" / f"{FEATURE}.criteria.yaml"

    def check(extra):
        _criteria(proj, [{**base, **extra}])
        return schema.validate_criteria_file(cf, proj)

    ok, errs = check({"kind": "tests", "junit": "build/*.xml"})
    assert ok, errs
    ok, errs = check({"junit": "build/*.xml"})
    assert not ok and any("junit" in e and "kind: tests" in e for e in errs)
    ok, errs = check({"kind": "tests", "junit": 5})
    assert not ok and any("non-empty glob string" in e for e in errs)
    ok, errs = check({"kind": "tests", "junit": " "})
    assert not ok and any("non-empty glob string" in e for e in errs)
    ok, errs = check({"kind": "tests", "junit": "/abs/*.xml"})
    assert not ok and any("not absolute" in e for e in errs)


def test_criterion_junit_drives_verify(proj):
    _criteria(proj, [{"id": "c1", "description": "d", "kind": "tests",
                      "verify_command": _writer("out/r.xml"), "junit": "out/*.xml"}])
    ok, results = gate._run_success_criteria(FEATURE, proj)
    assert ok and results[0]["executed"] == 2
    ev = _events("success_criterion_verified")[-1]
    assert ev["junit"] == "out/*.xml" and ev["junit_files"] == 1
    # a verify that writes nothing leaves only the (now stale) report
    os.utime(proj / "out" / "r.xml", (1_000_000, 1_000_000))
    _criteria(proj, [{"id": "c1", "description": "d", "kind": "tests",
                      "verify_command": "true", "junit": "out/*.xml"}])
    ok, results = gate._run_success_criteria(FEATURE, proj)
    assert not ok
    assert "older match(es) ignored" in (proj / results[0]["output_path"]).read_text()


def test_red_baseline_for_junit_criterion_requires_matching_glob(proj):
    crit = {"id": "c1", "description": "d", "kind": "tests", "prove_red": True,
            "verify_command": "exit 3", "junit": "out/*.xml"}
    _criteria(proj, [crit])
    # a stdout-era baseline recorded without junit does not credit the junit criterion
    ledger.append("criterion_red_baseline", feature=FEATURE, id="c1",
                  verify_command="exit 3", exit_code=3)
    assert not gate._red_baseline_exists(proj, FEATURE, "c1", "exit 3")
    rc = gate.prove_red(SimpleNamespace(feature=FEATURE, id=None))
    assert rc == 0
    assert gate._red_baseline_exists(proj, FEATURE, "c1", "exit 3")
    ev = _events("criterion_red_baseline")[-1]
    assert ev["junit"] == "out/*.xml" and ev["junit_files"] == [] and ev["executed"] == 0
    assert "junit_error" in ev
    # and a non-junit criterion is credited by a non-junit baseline exactly as before
    _criteria(proj, [{"id": "c2", "description": "d", "verify_command": "exit 3",
                      "prove_red": True}])
    ledger.append("criterion_red_baseline", feature=FEATURE, id="c2",
                  verify_command="exit 3", exit_code=3)
    assert gate._red_baseline_exists(proj, FEATURE, "c2", "exit 3")


# ---- capture --------------------------------------------------------------------------------

def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=str(cwd), check=True, capture_output=True)


def _repo(tmp_path: Path, monkeypatch, ignore: str = "build/\ntarget/\n") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / ".sprint").mkdir()
    (root / "src.py").write_text("x = 1\n")
    (root / ".gitignore").write_text(ignore + ".sprint/\nreports/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def _cap(cmd: str, junit: str | None, phase="regression", kind="tests") -> int:
    return gate.capture(SimpleNamespace(command=[cmd], reset=False, feature=FEATURE,
                                        phase=phase, kind=kind, junit=junit))


def _entries(root: Path, phase="regression") -> list[dict]:
    p = schema.evidence_path_for(root / "reports" / FEATURE, phase)
    return schema.load_evidence(p) if p.exists() else []


def test_capture_junit_fills_entry_from_reports(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    assert _cap(_writer("build/test-results/test/TEST-a.xml"),
                "build/test-results/**/*.xml") == 0
    e = _entries(root)[0]
    assert e["nonempty_primitive"] == {"kind": "tests", "value": 2}
    assert e["observed_failures"] == 1
    assert e["failing_tests"] == ["c::b"] and e["skipped_tests"] == ["c::s"]
    assert e["junit_files"] == ["build/test-results/test/TEST-a.xml"]


def test_capture_junit_glob_resolves_against_exec_dir(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    sub = root / "mod"
    sub.mkdir()
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(sub))
    assert _cap(_writer("build/r.xml"), "build/*.xml") == 0
    assert _entries(root)[0]["exec_dir"] == "mod"
    assert (sub / "build" / "r.xml").exists()


@pytest.mark.parametrize("case", ["stale-only", "malformed", "doctype"])
def test_capture_junit_exit0_unread_refused_before_diagnose(tmp_path, monkeypatch, capsys, case):
    root = _repo(tmp_path, monkeypatch)
    if case == "stale-only":
        p = root / "build" / "old.xml"
        p.parent.mkdir()
        p.write_text(OK_XML)
        os.utime(p, (1_000_000, 1_000_000))
        cmd, want = "true", ["'build/*.xml'", "1 older match(es)"]
    elif case == "malformed":
        cmd, want = _writer("build/bad.xml", "<testsuite><testcase"), ["build/bad.xml",
                                                                       "not well-formed"]
    else:
        cmd = _writer("build/dt.xml",
                      '<!DOCTYPE x [<!ENTITY a "b">]><testsuite><testcase name="t"/></testsuite>')
        want = ["build/dt.xml", "DOCTYPE"]
    capsys.readouterr()
    assert _cap(cmd, "build/*.xml") == 1
    err = capsys.readouterr().err
    assert all(w in err for w in want)
    ev = _events("capture_junit_unread")
    assert len(ev) == 1 and ev[0]["junit"] == "build/*.xml"
    assert not _events("capture_non_evidence")
    assert _entries(root) == []


def test_capture_junit_red_run_without_report_recorded(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    assert _cap("echo compile error >&2; exit 2", "build/*.xml") == 2
    e = _entries(root)[0]
    assert e["exit_code"] == 2 and e["nonempty_primitive"]["value"] == 0
    assert e["observed_failures"] == 0 and e["junit_files"] == []
    assert e["junit_error"]
    assert not _events("capture_junit_unread")


def test_capture_junit_with_non_tests_kind_is_usage_error(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    assert _cap("true", "build/*.xml", kind="lint") == 2
    assert _entries(root) == []


def _hash_and_stale(root: Path):
    h = gate._judged_hash("regression-sentinel", FEATURE, None, root)
    stale = gate._evidence_unsatisfied(f"reports/{FEATURE}/regression.evidence.json",
                                       FEATURE, root)
    return h, stale


def test_capture_junit_reports_in_gitignored_build_do_not_stale_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    before, _ = _hash_and_stale(root)
    assert _cap(_writer("build/test-results/test/TEST-a.xml"),
                "build/test-results/**/*.xml") == 0
    first = _entries(root)[0]
    after, stale = _hash_and_stale(root)
    assert before == after == first["worktree_hash"]
    assert stale is None or "stale" not in stale
    # a second capture by another phase, rewriting the Gradle report and adding a Surefire one
    assert _cap(_writer("target/surefire-reports/TEST-b.xml") + "; "
                + _writer("build/test-results/test/TEST-a.xml"),
                "target/surefire-reports/*.xml", phase="conventions") == 0
    assert _hash_and_stale(root)[0] == first["worktree_hash"]


def test_capture_junit_reports_in_partial_mirror_do_not_stale_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    mirror = root / "worktrees" / "backend-builder"
    mirror.mkdir(parents=True)
    (mirror / "src.py").write_text("x = 1\n")
    monkeypatch.chdir(mirror)
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(mirror))
    before, _ = _hash_and_stale(root)
    assert _cap(_writer("build/test-results/test/TEST-a.xml"),
                "build/test-results/**/*.xml") == 0
    first = _entries(root)[0]
    assert first["exec_dir"] == "worktrees/backend-builder"
    assert _cap(_writer("target/surefire-reports/TEST-b.xml"),
                "target/surefire-reports/*.xml", phase="conventions") == 0
    after, stale = _hash_and_stale(root)
    assert before == after == first["worktree_hash"]
    assert stale is None or "stale" not in stale


def test_capture_junit_reports_in_unignored_dir_refused_not_stamped(tmp_path, monkeypatch,
                                                                    capsys):
    root = _repo(tmp_path, monkeypatch, ignore="")
    capsys.readouterr()
    assert _cap(_writer("build/test-results/test/TEST-a.xml"),
                "build/test-results/**/*.xml") == 1
    err = capsys.readouterr().err
    ev = _events("capture_non_evidence")
    assert ev and ev[-1]["mode"] == "worktree_changed_mid_run"
    assert "build/test-results/test/TEST-a.xml" in err
    assert _entries(root) == []


# ---- action.yml -----------------------------------------------------------------------------

def _action():
    from ruamel.yaml import YAML
    return YAML(typ="safe").load((REPO / "action.yml").read_text())


def _gen_step(doc) -> dict:
    return next(s for s in doc["runs"]["steps"] if s.get("id") == "gen")


def _run_gen(tmp_path: Path, junit: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    doc = _action()
    step = _gen_step(doc)
    defaults = {k: v.get("default", "") for k, v in doc["inputs"].items()}
    defaults.update(source="prove", command="echo hi")
    script = re.sub(r"\$\{\{\s*inputs\.([\w-]+)\s*\}\}",
                    lambda m: defaults[m.group(1)], step["run"])
    bindir, work = tmp_path / "bin", tmp_path / "work"
    bindir.mkdir()
    work.mkdir()
    argv_log = tmp_path / "argv.txt"
    stub = bindir / "prusik"
    stub.write_text(f'#!/bin/bash\nfor a in "$@"; do printf "%s\\n" "$a"; done > {argv_log}\n')
    stub.chmod(0o755)
    (work / "build" / "test-results").mkdir(parents=True)
    (work / "build" / "test-results" / "a.xml").write_text("<x/>")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "PRUSIK_JUNIT": junit,
           "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(tmp_path / "out")}
    proc = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=work, env=env,
                          capture_output=True, text=True)
    argv = argv_log.read_text().splitlines() if argv_log.exists() else []
    return proc, argv


def test_action_yml_junit_input_passes_glob_to_prove(tmp_path):
    doc = _action()
    assert doc["inputs"]["junit"]["default"] == ""
    for s in doc["runs"]["steps"]:
        assert "inputs.junit" not in s.get("run", "")
    assert _gen_step(doc)["env"]["PRUSIK_JUNIT"] == "${{ inputs.junit }}"
    proc, argv = _run_gen(tmp_path, "build/test-results/**/*.xml")
    assert proc.returncode == 0, proc.stderr
    assert argv[argv.index("--junit") + 1] == "build/test-results/**/*.xml"   # not expanded


def test_action_yml_junit_with_quote_passes_verbatim(tmp_path):
    glob = 'build/it\'s "x" $(touch pwned)/*.xml'
    proc, argv = _run_gen(tmp_path, glob)
    assert proc.returncode == 0, proc.stderr
    assert argv[argv.index("--junit") + 1] == glob
    assert not (tmp_path / "work" / "pwned").exists()


def test_action_yml_empty_junit_omits_flag(tmp_path):
    proc, argv = _run_gen(tmp_path, "")
    assert proc.returncode == 0, proc.stderr
    assert "--junit" not in argv and "prove" in argv
