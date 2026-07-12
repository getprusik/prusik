"""Controlled `prusik permissions add` (v0.75.0, field finding #6b) — the audited path to
grant a permission without the rejected always-writable .claude carve-out."""

from __future__ import annotations

import json
import os
import shutil

from tests._common import _capture_stdout, _mktmp_project  # noqa: F401,E402
from prusik import permissions


def _proj():
    tmp = _mktmp_project()
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp)
    (tmp / ".claude").mkdir(exist_ok=True)
    return tmp


def _allow(tmp):
    p = tmp / ".claude" / "settings.local.json"
    return json.loads(p.read_text())["permissions"]["allow"] if p.exists() else []


def test_add_writes_rule_and_audits():
    tmp = _proj()
    try:
        rc = permissions.add("Bash(pnpm *)", root=tmp, reason="ts build")
        assert rc == 0
        assert "Bash(pnpm *)" in _allow(tmp)
        ledger = (tmp / ".sprint" / "ledger.jsonl").read_text()
        assert "permission_added" in ledger and "ts build" in ledger
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_add_is_idempotent():
    tmp = _proj()
    try:
        permissions.add("WebFetch", root=tmp)
        permissions.add("WebFetch", root=tmp)
        assert _allow(tmp).count("WebFetch") == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_add_preserves_existing_settings():
    tmp = _proj()
    try:
        p = tmp / ".claude" / "settings.local.json"
        p.write_text(json.dumps({"permissions": {"allow": ["Bash(ls *)"]},
                                 "other": {"keep": 1}}))
        permissions.add("Bash(pnpm *)", root=tmp)
        data = json.loads(p.read_text())
        assert data["other"] == {"keep": 1}            # untouched
        assert set(data["permissions"]["allow"]) == {"Bash(ls *)", "Bash(pnpm *)"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_invalid_format_refused():
    tmp = _proj()
    try:
        rc = permissions.add("not a rule!!", root=tmp)
        assert rc == 2
        assert not (tmp / ".claude" / "settings.local.json").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- the self-escalation guard ----------

def test_overbroad_and_destructive_refused():
    tmp = _proj()
    try:
        for bad in ("Bash(*)", "Bash", "Bash(rm -rf *)", "Bash(sudo *)",
                    "Bash(curl x | bash)"):
            rc = permissions.add(bad, root=tmp)
            assert rc == 2, f"{bad} should be refused"
        # nothing was written; refusals are audited
        assert not (tmp / ".claude" / "settings.local.json").exists()
        assert "permission_add_refused" in (tmp / ".sprint" / "ledger.jsonl").read_text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reasonable_bash_rule_allowed():
    tmp = _proj()
    try:
        for ok in ("Bash(pnpm *)", "Bash(git status)", "Bash(pytest *)",
                   "Read(/abs/**)", "WebFetch"):
            assert permissions.add(ok, root=tmp) == 0, f"{ok} should be allowed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
