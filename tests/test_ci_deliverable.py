"""fb-e340cd203897 — a sprint whose deliverable IS a CI job/workflow can complete 'done'
with the deliverable's RUNTIME never observed: a job triggered only on pull_request /
schedule / workflow_dispatch never fires on the sprint's push, so 'reviewing PASS'
proves the code, not the running job (a nightly's first real run was RED — the
deliverable was broken and 'done' hid it). The wired≠observed class (fb-41877c6a453f)
recurring one meta-level up: the sprint that BUILDS a CI gate never observes the gate.

moat-finding: fb-e340cd203897
moat-finding: fb-556f5caebef2
"""

from __future__ import annotations

import subprocess

import yaml

from prusik import ci_deliverable as cd


def _wf(on):
    return {True: on, "jobs": {"j": {"steps": [{"run": "npx playwright test"}]}}}


# ---- push_observes: the trigger logic, incl. the GitHub `on:`→True YAML gotcha ------

def test_push_observes_across_on_forms():
    assert cd.push_observes(_wf("push"), "main")
    assert cd.push_observes(_wf(["push", "pull_request"]), "main")
    assert cd.push_observes(_wf({"push": None}), "main")            # `push:` no filter
    assert cd.push_observes(_wf({"push": {"branches": ["main", "release/*"]}}), "main")
    assert cd.push_observes(_wf({"push": {"branches": ["release/*"]}}), "release/9")
    # NOT observed by the sprint's push:
    assert not cd.push_observes(_wf({"pull_request": {"branches": ["main"]}}), "main")
    assert not cd.push_observes(_wf({"schedule": [{"cron": "0 3 * * *"}]}), "main")
    assert not cd.push_observes(_wf({"push": {"branches": ["release/*"]}}), "main")
    assert not cd.push_observes(_wf({"push": {"branches-ignore": ["main"]}}), "main")


def test_on_key_is_read_from_the_yaml_boolean_true():
    # PyYAML maps a bare `on:` key to boolean True; the real GitHub workflow shape
    data = yaml.safe_load("on:\n  pull_request:\n    branches: [main]\njobs: {}\n")
    assert True in data and "on" not in data          # the gotcha we handle
    assert cd.trigger_events(data) == {"pull_request"}
    assert not cd.push_observes(data, "main")


# ---- end-to-end over a real repo: flag the unobserved deliverables ------------------

def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path):
    r = tmp_path
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t.test")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n")
    _git(r, "add", "README.md")
    _git(r, "commit", "-m", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, capture_output=True,
                          text=True).stdout.strip()
    (r / ".sprint").mkdir(exist_ok=True)
    (r / ".sprint" / "state.json").write_text(
        f'{{"feature":"feat","phase":"integrating","base_commit":"{base}"}}')
    wd = r / ".github" / "workflows"
    wd.mkdir(parents=True)
    (wd / "ci.yml").write_text("on: [push, pull_request]\njobs: {t: {steps: [{run: pytest}]}}\n")
    (wd / "gate.yml").write_text(
        "on:\n  pull_request:\n    branches: [main]\njobs: {g: {steps: [{run: npx playwright test}]}}\n")
    (wd / "nightly.yml").write_text(
        "on:\n  schedule: [{cron: '0 3 * * *'}]\n  workflow_dispatch:\njobs: {s: {steps: [{run: pw}]}}\n")
    _git(r, "add", ".github")
    _git(r, "commit", "-m", "workflows")
    return r


def test_unobserved_flags_pr_and_schedule_only_not_push(tmp_path, monkeypatch):
    r = _repo(tmp_path)
    monkeypatch.chdir(r)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    names = {i["workflow"] for i in cd.unobserved(r, "main", "feat")}
    assert names == {"gate.yml", "nightly.yml"}       # ci.yml (push) is observed → excluded


def test_gate_check_advisory_then_require_blocks_then_observed_clears(tmp_path, monkeypatch, capsys):
    r = _repo(tmp_path)
    monkeypatch.chdir(r)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    # advisory: never blocks, but records the event and names the fix
    assert cd.gate_check(r, {}, "feat", "sprint-complete") is True
    out = capsys.readouterr().out
    assert "ci-observe ADVISORY" in out and "mark-ci-observed" in out
    events = [__import__("json").loads(x) for x in
              (r / ".sprint" / "ledger.jsonl").read_text().splitlines()]
    assert any(e["event"] == "ci_deliverable_unobserved" for e in events)
    # require: hard-blocks
    assert cd.gate_check(r, {"ci_observe": {"require": True}}, "feat", "sprint-complete") is False
    # record BOTH as observed-green → gate clears
    from prusik import ledger
    for wf in ("gate.yml", "nightly.yml"):
        ledger.append("ci_deliverable_observed", feature="feat", workflow=wf, run="123")
    assert cd.gate_check(r, {"ci_observe": {"require": True}}, "feat", "sprint-complete") is True


def test_no_workflow_change_is_clean(tmp_path, monkeypatch):
    r = _repo(tmp_path)
    # advance HEAD past the workflow commit with a NON-workflow change; base still sees
    # the workflows though — so instead assert a repo whose base == HEAD (no diff) is clean
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, capture_output=True,
                          text=True).stdout.strip()
    (r / ".sprint" / "state.json").write_text(
        f'{{"feature":"feat","phase":"integrating","base_commit":"{head}"}}')
    assert cd.unobserved(r, "main", "feat") == []
    assert cd.gate_check(r, {"ci_observe": {"require": True}}, "feat", "sprint-complete") is True
