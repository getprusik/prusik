"""Pre-flight infra gate (v0.65.0, field finding #1) — health-check declared infra before
verify_commands and FAIL CLOSED if down, instead of letting commands false-skip."""

from __future__ import annotations

import shutil
import socket

import yaml

from tests._common import _capture_stdout, _mktmp_project  # noqa: F401,E402
from prusik import infra_check, schema


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _listening_port() -> tuple[socket.socket, int]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


# ---------- tcp checks (deterministic via real sockets) ----------

def test_tcp_up_when_port_listening():
    s, port = _listening_port()
    try:
        r = infra_check.check_requirement({"name": "db", "tcp": f"127.0.0.1:{port}"})
        assert r["up"] is True and r["kind"] == "tcp"
    finally:
        s.close()


def test_tcp_down_when_port_closed():
    port = _free_port()      # nothing listening
    r = infra_check.check_requirement({"name": "db", "tcp": f"127.0.0.1:{port}"},
                                      timeout=1.0)
    assert r["up"] is False
    assert "unreachable" in r["detail"]


def test_tcp_bad_target():
    r = infra_check.check_requirement({"name": "db", "tcp": "no-port"})
    assert r["up"] is False


def test_http_down_when_refused():
    port = _free_port()
    r = infra_check.check_requirement(
        {"name": "app", "http": f"http://127.0.0.1:{port}/healthz"}, timeout=1.0)
    assert r["up"] is False and r["kind"] == "http"


# ---------- verify_criteria_infra ----------

def _criteria(tmp, requires):
    p = tmp / "briefs" / "feat.criteria.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": "1.0", "requires": requires,
           "criteria": [{"id": "A1", "description": "x", "verify_command": "true"}]}
    p.write_text(yaml.safe_dump(doc))
    return p


def test_verify_infra_all_up():
    tmp = _mktmp_project()
    s, port = _listening_port()
    try:
        cp = _criteria(tmp, [{"name": "db", "tcp": f"127.0.0.1:{port}"}])
        ok, results = infra_check.verify_criteria_infra(cp, timeout=1.0)
        assert ok is True and len(results) == 1
    finally:
        s.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_infra_fails_closed_when_down():
    tmp = _mktmp_project()
    try:
        port = _free_port()
        cp = _criteria(tmp, [{"name": "db", "tcp": f"127.0.0.1:{port}"}])
        ok, results = infra_check.verify_criteria_infra(cp, timeout=1.0)
        assert ok is False
        assert results[0]["up"] is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_requires_block_is_noop():
    tmp = _mktmp_project()
    try:
        p = tmp / "briefs" / "feat.criteria.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(yaml.safe_dump(
            {"schema_version": "1.0",
             "criteria": [{"id": "A1", "description": "x", "verify_command": "true"}]}))
        ok, results = infra_check.verify_criteria_infra(p)
        assert ok is True and results == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- the gate fails closed (no false-green) ----------

def test_run_success_criteria_blocks_when_infra_down():
    from prusik import gate
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir(exist_ok=True)
        (tmp / "briefs" / "feat.md").write_text("# brief\n")
        port = _free_port()
        _crit = tmp / "briefs" / "feat.criteria.yaml"
        _crit.write_text(yaml.safe_dump({
            "schema_version": "1.0",
            "requires": [{"name": "db", "tcp": f"127.0.0.1:{port}"}],
            "criteria": [{"id": "A1", "description": "x", "verify_command": "true"}],
        }))
        all_passed, results = gate._run_success_criteria("feat", tmp)
        assert all_passed is False, "infra down must block, not false-green"
        assert results[0]["id"] == "<infra-preflight>"
        assert "db" in results[0]["infra_down"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- schema validation of the requires block ----------

def test_validate_requires_good_and_bad():
    tmp = _mktmp_project()
    try:
        good = _criteria(tmp, [
            {"name": "db", "tcp": "localhost:5432"},
            {"name": "app", "http": "http://localhost:8000/healthz",
             "expect_status": 200}])
        ok, errs = schema.validate_criteria_file(good, project_root=tmp)
        assert ok, errs
        bad = _criteria(tmp, [
            {"tcp": "localhost:5432"},                 # missing name
            {"name": "x", "tcp": "no-port"},           # bad tcp
            {"name": "y", "http": "localhost"},        # not a URL
            {"name": "z"}])                            # no target
        ok, errs = schema.validate_criteria_file(bad, project_root=tmp)
        assert not ok
        joined = " ".join(errs)
        assert "name" in joined and "tcp" in joined and "http" in joined
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
