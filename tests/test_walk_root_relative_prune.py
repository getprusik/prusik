"""Skip-dir pruning is relative to the walk root: a project whose root has an ANCESTOR
named like a skip-dir (`build`, `worktrees`, ...) is still walked (fb-2a4222ba7797)."""
# moat-finding: fb-2a4222ba7797

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from prusik import discovery, scan, verify_loop


def _project_under_build_ancestor() -> tuple[Path, Path]:
    base = Path(tempfile.mkdtemp())
    root = base / "build" / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("def f():\n    return 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_mod.py").write_text("def test_mod_works():\n    assert True\n")
    (root / "build").mkdir()                      # a skip-dir INSIDE the root
    (root / "build" / "gen.py").write_text("def test_mod_works():\n    pass\n")
    return base, root


def test_discovery_walks_root_under_skip_named_ancestor():
    base, root = _project_under_build_ancestor()
    try:
        rel = {p.relative_to(root).as_posix() for p in discovery._iter_source_files(root)}
        assert "pkg/mod.py" in rel
        assert "build/gen.py" not in rel
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_scan_collects_root_under_skip_named_ancestor():
    base, root = _project_under_build_ancestor()
    try:
        files, stats = scan._collect_files(root, 1000, True)
        rel = {p.relative_to(root).as_posix() for p in files}
        assert "pkg/mod.py" in rel
        assert "build/gen.py" not in rel
        assert stats["skipped_dirs"] == ["build"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_verify_loop_finds_test_under_skip_named_ancestor():
    base, root = _project_under_build_ancestor()
    try:
        hits = verify_loop._find_test_in_suite("test_mod_works", root)
        assert [Path(h).as_posix().endswith("tests/test_mod.py") for h in hits] == [True]
    finally:
        shutil.rmtree(base, ignore_errors=True)
