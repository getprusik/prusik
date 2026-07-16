"""Per-finding ticket store (designed with live-cc, fb-c91c2be85603).

The heart: state is DERIVED from (resolution + latest verify run), and closure is
GATED on a captured green run (exit 0 AND executed>=1). The adversarial cases — a red
verify, and a zero-executed/all-skipped verify — must NEVER close a finding.
"""

from __future__ import annotations

import sys

from prusik import feedback_store as fs


def _open(tmp_path, **kw):
    return fs.create(tmp_path, fb_id="fb-x", kind="bug", title="t",
                     content_hash="h", **kw)


def _mk(tmp_path, fid, **kw):
    return fs.create(tmp_path, fb_id=fid, kind="bug", title=fid,
                     content_hash=fid.replace("fb-", ""), **kw)


def test_filing_a_finding_drops_the_store_readme(tmp_path):
    # systemic steer: an agent inspecting findings/ (deciding commit-vs-gitignore)
    # must find guidance right there — the recurring wrong inference is to gitignore
    # this git-tracked store, which loses closure history.
    fs.create(tmp_path, fb_id="fb-aaaaaaaaaaaa", kind="bug", title="t",
              content_hash="a")
    readme = tmp_path / "findings" / "README.md"
    assert readme.exists()
    body = readme.read_text()
    assert "GIT-TRACKED" in body
    assert "gitignore" in body                    # tells you NOT to
    assert "verify_history" in body               # names why it's load-bearing


def test_close_shipped_verifies_green_surfaces_the_rest(tmp_path):
    """The update→verify closer: a shipped finding with a GREEN verify closes; a RED
    verify stays open (proof-gated — a release note doesn't close a ticket); a shipped
    finding with no verify is surfaced; a not-shipped finding is untouched."""
    _mk(tmp_path, "fb-aaaaaaaaaaaa")   # green verify → closes
    _mk(tmp_path, "fb-bbbbbbbbbbbb")   # red verify → stays open
    _mk(tmp_path, "fb-cccccccccccc")   # shipped, no verify → surfaced
    _mk(tmp_path, "fb-dddddddddddd")   # not in shipped set → untouched
    fs.resolve(tmp_path, "fb-aaaaaaaaaaaa", rtype="fix",
               verify="echo '1 passed'", fixed_in="0.9.0")
    fs.resolve(tmp_path, "fb-bbbbbbbbbbbb", rtype="fix",
               verify="exit 1", fixed_in="0.9.0")
    out = fs.close_shipped(
        tmp_path, {"fb-aaaaaaaaaaaa", "fb-bbbbbbbbbbbb", "fb-cccccccccccc"})
    assert out["closed"] == ["fb-aaaaaaaaaaaa"]
    assert out["still_red"] == ["fb-bbbbbbbbbbbb"]
    assert out["needs_verify"] == ["fb-cccccccccccc"]
    assert fs.is_closed(fs.load(tmp_path, "fb-aaaaaaaaaaaa"))
    assert not fs.is_closed(fs.load(tmp_path, "fb-bbbbbbbbbbbb"))
    # fb-dddddddddddd was never touched
    assert "fb-dddddddddddd" not in sum(out.values(), [])


def test_close_shipped_skips_already_closed(tmp_path):
    _mk(tmp_path, "fb-eeeeeeeeeeee")
    fs.resolve(tmp_path, "fb-eeeeeeeeeeee", rtype="fix", verify="echo '1 passed'")
    fs.verify(tmp_path, "fb-eeeeeeeeeeee")               # already green-closed
    out = fs.close_shipped(tmp_path, {"fb-eeeeeeeeeeee"})
    assert out["already_closed"] == ["fb-eeeeeeeeeeee"]
    assert out["closed"] == []                            # not re-verified


def test_lattice_open_then_acknowledged(tmp_path):
    _open(tmp_path)
    assert fs.derive_state(fs.load(tmp_path, "fb-x")) == "open"
    fs.reply(tmp_path, "fb-x", fs.ROLE_AUTHOR, "looking at it")
    assert fs.derive_state(fs.load(tmp_path, "fb-x")) == "acknowledged"


def test_fix_is_not_closed_until_verified_green(tmp_path):
    _open(tmp_path)
    fs.resolve(tmp_path, "fb-x", rtype="fix", verify="echo '3 passed'", fixed_in="v1")
    assert fs.derive_state(fs.load(tmp_path, "fb-x")) == "fixed"   # proposed, unproven
    state, entry = fs.verify(tmp_path, "fb-x")
    assert entry["verdict"] is True and entry["executed_count"] == 3
    assert state == "verified-closed"                              # closes on PROOF


def test_red_verify_never_closes(tmp_path):
    _open(tmp_path)
    fs.resolve(tmp_path, "fb-x", rtype="fix", verify="exit 1")
    state, entry = fs.verify(tmp_path, "fb-x")
    assert entry["verdict"] is False and state == "fixed"         # red → not closed


def test_zero_executed_verify_never_closes(tmp_path):
    """The fb-32b3a89cc1d5 honesty class: exit 0 but nothing ran ≠ closed."""
    _open(tmp_path)
    fs.resolve(tmp_path, "fb-x", rtype="fix", verify="echo 'no tests collected'")
    state, entry = fs.verify(tmp_path, "fb-x")
    assert entry["exit"] == 0 and entry["executed_count"] == 0
    assert entry["verdict"] is False and state == "fixed"         # green-but-empty ≠ proof


def test_verified_closed_auto_reopens_on_regression(tmp_path):
    _open(tmp_path)
    fs.resolve(tmp_path, "fb-x", rtype="fix", verify="echo '5 passed'")
    assert fs.verify(tmp_path, "fb-x")[0] == "verified-closed"
    # the verify command's world changes (regression): now make it fail
    fs.resolve(tmp_path, "fb-x", rtype="fix", verify="exit 1")
    assert fs.verify(tmp_path, "fb-x")[0] == "reopened"           # self-resurrects


def test_reject_is_wontfix_with_reason(tmp_path):
    _open(tmp_path)
    fs.resolve(tmp_path, "fb-x", rtype="reject", reason="by-design: X")
    rec = fs.load(tmp_path, "fb-x")
    assert fs.derive_state(rec) == "wontfix"
    assert rec["resolution"]["reason"] == "by-design: X"


def test_resolution_validation(tmp_path):
    import pytest
    _open(tmp_path)
    with pytest.raises(ValueError):                              # fix needs a verify cmd
        fs.resolve(tmp_path, "fb-x", rtype="fix")
    with pytest.raises(ValueError):                              # reject needs a reason
        fs.resolve(tmp_path, "fb-x", rtype="reject")


def test_create_is_idempotent_never_clobbers(tmp_path):
    _open(tmp_path)
    fs.resolve(tmp_path, "fb-x", rtype="fix", verify="echo '1 passed'")
    fs.reply(tmp_path, "fb-x", fs.ROLE_ADOPTER, "still seeing it")
    # re-filing the same finding must return the EXISTING ticket, not reset it
    again = fs.create(tmp_path, fb_id="fb-x", kind="bug", title="t", content_hash="h")
    assert again["resolution"] is not None and len(again["thread"]) == 1


def test_verify_selector_scopes(tmp_path):
    # two verified-closed findings; only one references the touched module
    for fid, vcmd in [("fb-a", "pytest billing/ -q && echo '2 passed'"),
                      ("fb-b", "pytest web/ -q && echo '2 passed'")]:
        fs.create(tmp_path, fb_id=fid, kind="bug", title="t", content_hash=fid)
        fs.resolve(tmp_path, fid, rtype="fix", verify=vcmd)
        # force a green without running pytest: simplest, replace verify with echo
        fs.resolve(tmp_path, fid, rtype="fix", verify="echo '2 passed'",
                   fixed_in="v1")
        fs.verify(tmp_path, fid)
    # tag them so 'touched' can match: put module in repro
    a = fs.load(tmp_path, "fb-a"); a["repro"] = "pytest billing/"; fs.save(tmp_path, a)
    b = fs.load(tmp_path, "fb-b"); b["repro"] = "pytest web/"; fs.save(tmp_path, b)
    touched = fs.verify_selector(tmp_path, touched=["billing"])
    assert [r[0] for r in touched] == ["fb-a"]                   # scoped to billing
    allc = fs.verify_selector(tmp_path, all_closed=True)
    assert sorted(r[0] for r in allc) == ["fb-a", "fb-b"]        # full sweep


def test_migrate_backfills_legacy_index(tmp_path):
    """`feedback migrate` turns the legacy .sprint/feedback.jsonl index into per-finding
    tickets — idempotent, deduped by id, existing tickets untouched (live-cc/an adopter)."""
    from prusik import feedback as F
    (tmp_path / ".sprint").mkdir()
    for kind, title in [("bug", "a bug"), ("friction", "a friction"), ("bug", "a bug")]:
        F.append(tmp_path, F.build_record(kind, title, ts="2026-06-06T00:00:00"))
    new, total = F.migrate_to_tickets(tmp_path)
    assert new == 2 and total == 2                      # 3 records, 2 distinct ids
    assert len(fs.load_all(tmp_path)) == 2
    # a pre-existing ticket with a resolution is NOT clobbered by re-migrate
    fid = fs.load_all(tmp_path)[0]["id"]
    fs.resolve(tmp_path, fid, rtype="fix", verify="echo '1 passed'")
    new2, _ = F.migrate_to_tickets(tmp_path)
    assert new2 == 0 and fs.load(tmp_path, fid)["resolution"] is not None


def test_harvest_candidate_honesty_gate():
    """live-cc honesty bar: a dedicated NEW test is harvestable; a -m/-k-scoped run of
    a PRE-EXISTING suite authors nothing new → NOT harvestable (no moat-count inflation)."""
    def cand(verify):
        return fs.harvest_candidate({"resolution": {"type": "fix", "verify": verify}})
    # genuinely-new dedicated tests (finding-zero, fb-876ad6010f72 shapes)
    assert cand("pytest tests/test_feedback_store.py -q") == "tests/test_feedback_store.py"
    assert cand("pytest tests/test_convergence_noop_success.py -q") == \
        "tests/test_convergence_noop_success.py"
    # the `python -m pytest <file>` MODULE invocation authors a new test just like
    # `pytest <file>` — its `-m pytest` must NOT be misread as `-m <markexpr>` selection
    assert cand("python -m pytest tests/test_dedicated.py -q") == "tests/test_dedicated.py"
    assert cand("/usr/bin/python3 -m pytest tests/test_dedicated.py") == \
        "tests/test_dedicated.py"
    # fb-32b3a89cc1d5 shape: marker-scoped run of an existing suite → nothing to harvest
    assert cand('pytest tests/test_validation.py -m "not browser_smoke"') is None
    assert cand("pytest -k something tests/test_x.py") is None       # -k selection
    assert cand("python -m pytest -m slow tests/test_x.py") is None  # module form + real -m selection
    assert cand("pytest tests/a.py tests/b.py") is None             # not a single file
    # a reject (no fix) has no verify to harvest
    assert fs.harvest_candidate({"resolution": {"type": "reject", "reason": "x"}}) is None


def test_harvest_tags_only_a_genuinely_new_verified_test(tmp_path):
    # a dedicated test file, finding verified-closed → harvest tags it once
    t = tmp_path / "tests"; t.mkdir()
    (t / "test_dedicated.py").write_text('"""a finding-derived test."""\n\ndef test_x():\n    assert 1\n')
    fs.create(tmp_path, fb_id="fb-h", kind="bug", title="t", content_hash="h")
    # Invoke pytest via the running interpreter so the verify subprocess is
    # hermetic — a bare `pytest` PATH lookup exits 126 in an isolated tmp dir
    # (no venv on the subprocess PATH), which is the environment, not the SUT.
    fs.resolve(tmp_path, "fb-h", rtype="fix",
               verify=f"{sys.executable} -m pytest tests/test_dedicated.py -q")
    fs.verify(tmp_path, "fb-h")
    tagged = fs.harvest(tmp_path, "fb-h")
    assert tagged == "tests/test_dedicated.py"
    assert "moat-finding: fb-h" in (t / "test_dedicated.py").read_text()
    # idempotent: re-harvest is a no-op (one tag per finding, honest count)
    assert fs.harvest(tmp_path, "fb-h") is None


def test_harvest_skips_scoped_reuse(tmp_path):
    t = tmp_path / "tests"; t.mkdir()
    (t / "test_existing.py").write_text("def test_y():\n    assert 1\n")
    fs.create(tmp_path, fb_id="fb-r", kind="bug", title="t", content_hash="h")
    fs.resolve(tmp_path, "fb-r", rtype="fix",
               verify='pytest tests/test_existing.py -m "not slow"')
    fs.verify(tmp_path, "fb-r")
    assert fs.harvest(tmp_path, "fb-r") is None                     # reuse → skip cleanly
    assert "moat-finding" not in (t / "test_existing.py").read_text()
