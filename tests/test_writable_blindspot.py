"""Writable-gate symlink blindspot (v0.50.0).

An absolute always-writable pattern (OS scratch like /tmp/**, or ~/.claude/…)
must free OUT-OF-TREE paths only. It must NOT disable the writable gate for the
project's OWN in-tree files when the project itself lives under such a path —
the /tmp→/private/tmp symlink made any /tmp-rooted project (e.g. a CI checkout)
silently lose its scope-drift gate. These tests reproduce that mechanism
deterministically (no dependence on the real /tmp) by making the project's own
parent an absolute always_writable pattern.
"""

from __future__ import annotations

from tests._common import phases  # noqa: F401,E402


def _cfg(always):
    return {"always_writable": list(always),
            "phases": [{"name": "building", "writable": ["worktrees/*/**"]}]}


def test_in_tree_out_of_lane_path_still_caught(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    # the project's OWN parent is absolutely always-writable — the blindspot setup
    cfg = _cfg([str(tmp_path / "**")])
    ok, _ = phases.is_path_writable("src/app.py", cfg, "building", "feat", root=root)
    assert ok is False, "in-tree out-of-lane write must stay GATED despite the " \
                        "absolute always-writable pattern covering the project root"


def test_in_tree_in_lane_path_allowed(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    cfg = _cfg([str(tmp_path / "**")])
    ok, _ = phases.is_path_writable("worktrees/solo/x.py", cfg, "building",
                                    "feat", root=root)
    assert ok is True


def test_out_of_tree_scratch_still_writable(tmp_path):
    # The legitimate use the absolute pattern exists for: scratch OUTSIDE the
    # project tree stays writable.
    root = tmp_path / "proj"
    root.mkdir()
    cfg = _cfg([str(tmp_path / "**")])
    sibling = str(tmp_path / "scratch.log")          # under the pattern, outside root
    ok, _ = phases.is_path_writable(sibling, cfg, "building", "feat", root=root)
    assert ok is True


def test_in_tree_relative_always_writable_still_honored(tmp_path):
    # Relative escape hatches (reports/kit-trial/**, .sprint/status/**) are
    # intentional and must keep working in-tree.
    root = tmp_path / "proj"
    root.mkdir()
    cfg = _cfg(["reports/kit-trial/**"])
    ok, _ = phases.is_path_writable("reports/kit-trial/journal.md", cfg,
                                    "building", "feat", root=root)
    assert ok is True
