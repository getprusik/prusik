"""The writable-scope deny message — the fleet's worst-re-bouncing remedy
(`writable_scope`, up to ~70% re-bounce). The rewrite shows the WHOLE writable
set + an explicit don't-retry, so an agent stops guessing paths one rejection at
a time. These pin the anti-re-bounce content; `prusik bounces` measures whether
it actually drops the rate in the field."""

from __future__ import annotations

from prusik import gate

_CFG = {"phases": [
    {"name": "building", "writable": ["worktrees/{teammate}/**", "reports/{feature}/**"]},
    {"name": "docs-only", "writable": ["docs/{feature}/**"]},   # no worktree pattern
]}


def test_lists_the_whole_writable_set_not_just_the_blocked_path():
    msg = gate._writable_scope_deny_msg(
        "write", "src/x.py", "not in writable patterns", _CFG, "building", "feat")
    assert "blocks write to src/x.py" in msg          # still names the blocked path
    assert "may write ONLY to:" in msg                 # NEW: shows the allowed set...
    assert "reports/feat/**" in msg                     # ...resolved, so the agent sees it
    assert "worktrees/*/**" in msg


def test_no_worktree_route_gives_explicit_dont_retry():
    # docs-only has writable patterns but NO worktree pattern → no redirect hint;
    # the remedy must still steer to the allowed set and say stop guessing.
    msg = gate._writable_scope_deny_msg(
        "bash redirect", "build.log", "not writable", _CFG, "docs-only", "feat")
    assert "docs/feat/**" in msg
    assert "don't retry other locations" in msg


def test_worktree_redirect_still_offered_when_available():
    msg = gate._writable_scope_deny_msg(
        "write", "src/x.py", "not writable", _CFG, "building", "feat")
    assert "worktrees/" in msg and "→" in msg          # concrete route for THIS target


def test_empty_writable_phase_points_to_worktree_or_advance():
    # A phase declaring no writable location (e.g. reviewing on an empty config)
    # must not dead-end — route to the worktree or the owning phase.
    msg = gate._writable_scope_deny_msg(
        "write", "src/x.py", "out of scope", {}, "reviewing", "feat")
    assert "blocks write to src/x.py" in msg
    assert "builder worktree" in msg or "advance" in msg
