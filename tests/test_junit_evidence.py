"""JUnit XML evidence reader (`prusik.evidence.read_junit`) — counts, freshness, XML safety.

Criterion `junit-reader`. A report is the run's own structured evidence; a stale, malformed
or DOCTYPE-bearing one proves nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from prusik import evidence

FIX = Path(__file__).parent / "fixtures" / "junit"


def _write(p: Path, body: str, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _suite(*cases: str) -> str:
    return f'<testsuite name="s" tests="999">{"".join(cases)}</testsuite>'


def test_nested_testsuites_count_each_testcase_once(tmp_path):
    shutil.copy(FIX / "gradle-nested-testsuites.xml", tmp_path / "r.xml")
    r = evidence.read_junit("r.xml", tmp_path, 0)
    assert r.error is None
    assert r.testcases == 5          # not 50 / 40 from the inflated tests= attributes


def test_classification_skipped_failure_error_passed(tmp_path):
    _write(tmp_path / "r.xml", _suite(
        '<testcase classname="c" name="ok"/>',
        '<testcase classname="c" name="sk"><skipped/></testcase>',
        '<testcase classname="c" name="fa"><failure/></testcase>',
        '<testcase classname="c" name="er"><error/></testcase>',
        '<testcase classname="c" name="both"><failure/><skipped/></testcase>'))
    r = evidence.read_junit("r.xml", tmp_path, 0)
    assert r.testcases == 5
    assert r.executed == 3           # ok, fa, er — skipped never counts
    assert r.failed == 2
    assert r.failed_tests == ("c::fa", "c::er")
    assert r.skipped_tests == ("c::sk", "c::both")


def test_name_is_classname_colon_name_or_bare_name(tmp_path):
    _write(tmp_path / "r.xml", _suite(
        '<testcase classname="a.B" name="x"><failure/></testcase>',
        '<testcase classname="" name="bare"><failure/></testcase>',
        '<testcase name="noattr"><failure/></testcase>'))
    r = evidence.read_junit("r.xml", tmp_path, 0)
    assert r.failed_tests == ("a.B::x", "bare", "noattr")


def test_stale_report_ignored_and_reason_names_glob_and_count(tmp_path):
    since = evidence.run_start()
    _write(tmp_path / "old.xml", _suite('<testcase name="t"/>'), since - 10)
    r = evidence.read_junit("*.xml", tmp_path, since)
    assert r.files == () and r.stale == 1
    assert "'*.xml'" in r.error and "(1 older match(es) ignored)" in r.error
    assert r.executed == 0


def test_fresh_and_stale_mixed_reads_only_fresh(tmp_path):
    since = evidence.run_start()
    _write(tmp_path / "old.xml", _suite('<testcase name="old"/>'), since - 10)
    _write(tmp_path / "new.xml", _suite('<testcase name="new"/>'))
    r = evidence.read_junit("*.xml", tmp_path, since)
    assert r.error is None and r.files == ("new.xml",) and r.stale == 1
    assert r.testcases == 1


def test_doctype_fails_closed_naming_file(tmp_path):
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
            '<testsuite><testcase name="&c;"/></testsuite>')
    _write(tmp_path / "evil.xml", bomb)
    t0 = time.monotonic()
    r = evidence.read_junit("evil.xml", tmp_path, 0)
    assert time.monotonic() - t0 < 1
    assert r.error == ("evil.xml: carries a DOCTYPE, which JUnit reports never need; "
                       "report rejected")
    assert r.executed == 0 and r.testcases == 0


def test_malformed_fails_closed_naming_file(tmp_path):
    _write(tmp_path / "bad.xml", "<testsuite><testcase name='x'>")
    r = evidence.read_junit("bad.xml", tmp_path, 0)
    assert r.error.startswith("bad.xml: not well-formed XML (")
    assert r.error.endswith("); report rejected")
    assert r.executed == 0


def test_real_pytest_junitxml_matches_pytest_summary(tmp_path):
    (tmp_path / "test_x.py").write_text(
        "import pytest\n"
        "def test_a(): pass\n"
        "def test_b(): pass\n"
        "def test_c(): assert 0\n"
        "@pytest.mark.skip\n"
        "def test_d(): pass\n")
    since = evidence.run_start()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--junitxml", str(tmp_path / "r.xml"),
         "-p", "no:cacheprovider", "-q", "test_x.py"],
        cwd=tmp_path, capture_output=True, text=True)
    r = evidence.read_junit("r.xml", tmp_path, since)
    assert r.error is None
    assert r.executed == evidence.executed_count("tests", proc.stdout) == 3
    assert len(r.failed_tests) == 1 and len(r.skipped_tests) == 1


def test_gradle_fixture_true_counts(tmp_path):
    d = tmp_path / "build" / "test-results" / "test"
    d.mkdir(parents=True)
    shutil.copy(FIX / "gradle-nested-testsuites.xml", d / "TEST-a.xml")
    r = evidence.read_junit("build/test-results/**/*.xml", tmp_path, 0)
    assert (r.testcases, r.executed, r.failed) == (5, 4, 2)
    assert r.skipped_tests == ("com.acme.MathTest::ignoredForNow",)
    assert r.files == ("build/test-results/test/TEST-a.xml",)


def test_surefire_fixture_true_counts(tmp_path):
    d = tmp_path / "target" / "surefire-reports"
    d.mkdir(parents=True)
    shutil.copy(FIX / "surefire-TEST-sample.xml", d / "TEST-a.xml")
    r = evidence.read_junit("target/surefire-reports/*.xml", tmp_path, 0)
    assert (r.testcases, r.executed, r.failed) == (5, 4, 2)   # flakyFailure = passed
    assert r.skipped_tests == ("com.acme.ServiceTest::optional",)
    assert "com.acme.ServiceTest::flaky" not in r.failed_tests


def test_zero_testcase_reports_not_proven_with_reason(tmp_path):
    _write(tmp_path / "r.xml", "<testsuite/>")
    r = evidence.read_junit("r.xml", tmp_path, 0)
    ok, why = evidence.junit_verdict(0, r, 1)
    assert not ok
    assert why == "1 fresh report(s) matching 'r.xml' held no testcases"


def test_nonzero_exit_never_proven_with_green_reports(tmp_path):
    _write(tmp_path / "r.xml", _suite('<testcase name="t"/>'))
    r = evidence.read_junit("r.xml", tmp_path, 0)
    assert evidence.junit_verdict(0, r, 1)[0] is True
    ok, why = evidence.junit_verdict(1, r, 1)
    assert not ok and "exited 1" in why


def test_junit_hint_names_runner_and_default_glob():
    for cmd in ("gradle test", "./gradlew test", "cd x && ./gradlew test"):
        h = evidence.junit_hint(cmd)
        assert "--junit" in h and "build/test-results/**/*.xml" in h
    for cmd in ("mvn test", "./mvnw -q verify"):
        h = evidence.junit_hint(cmd)
        assert "--junit" in h and "target/surefire-reports/*.xml" in h
    assert evidence.junit_hint("pytest -q") is None
