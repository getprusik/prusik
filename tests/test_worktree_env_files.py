"""A fresh git worktree doesn't inherit gitignored files → secret-dependent tests
FALSE-RED in the worktree and MASK the real failure the full-suite gate exists to
catch.

moat-finding: fb-91c75e51c214

(An adopter: 28 spurious 'Plaid not configured' reds in a worktree hid 1 real
regression.) Default WARNs; `worktree_env_files` opts into auto-linking.
"""

from __future__ import annotations

from pathlib import Path

from prusik import worktree_setup


def _project(tmp_path: Path, *, env=(".env",), config: str = "") -> tuple[Path, Path]:
    """A root with gitignored env files + a fresh (empty) worktree lacking them."""
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    for name in env:
        (root / name).write_text("PLAID_SECRET=sk_live_xxx\n")
    if config:
        (root / ".claude" / "sprint-config.yaml").write_text(config)
    target = tmp_path / "wt"
    target.mkdir()
    return root, target


def test_warns_by_default_no_link(tmp_path, capsys):
    root, target = _project(tmp_path)
    linked = worktree_setup._carry_env_files(root, target, do_link=True)
    assert linked == []                              # no allowlist → nothing linked
    assert not (target / ".env").exists()            # secret NOT surfaced by default
    err = capsys.readouterr().err
    assert "absent from this worktree" in err and ".env" in err   # cause surfaced


def test_optin_links_allowlisted(tmp_path):
    root, target = _project(
        tmp_path, env=(".env", ".env.local"),
        config="worktree_env_files:\n  - .env\n  - '.env.*'\n")
    linked = worktree_setup._carry_env_files(root, target, do_link=True)
    assert set(linked) == {".env", ".env.local"}
    # symlinked to the canonical root file — no secret COPY on disk
    assert (target / ".env").is_symlink()
    assert (target / ".env").resolve() == (root / ".env").resolve()


def test_optin_but_dryrun_does_not_link(tmp_path):
    root, target = _project(tmp_path, config="worktree_env_files:\n  - .env\n")
    linked = worktree_setup._carry_env_files(root, target, do_link=False)
    assert linked == [] and not (target / ".env").exists()   # link only on --run


def test_no_env_files_is_silent(tmp_path, capsys):
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    target = tmp_path / "wt"
    target.mkdir()
    assert worktree_setup._carry_env_files(root, target, do_link=True) == []
    assert capsys.readouterr().err == ""        # no env files → no noise


def test_partial_mirror_root_skips_advisory(tmp_path, capsys):
    """target == root (Python partial-mirror) must NOT warn — env files ARE present."""
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / ".env").write_text("X=1\n")
    rc = worktree_setup.run(dir_=None, do_run=False, root=root)
    assert rc == 0
    assert "absent from this worktree" not in capsys.readouterr().err
