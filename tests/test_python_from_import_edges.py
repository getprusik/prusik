"""fb-27ad79ae220b — the Python dep-graph records the module a from-import names.

The extractor recorded `from pkg import module` as an edge to `pkg` alone and skipped relative
imports entirely, so `blast_radius` and the plan-time at-risk-test prediction (whose honesty IS
the graph's recall) missed every importer written in the most common Python idiom: on this repo
ledger.py had 18 attributable importers out of 75 real ones. The old test fixture even contained
`from pkg import a` — but only ever queried the coarse `pkg` prefix.

Precision matters as much as recall: `base.name` is recorded only for real project modules, so
attributes and externals (`typing.Any`) never become graph nodes, and no internal marker or
unresolved relative spec may leak into the persisted graph.

moat-finding: fb-27ad79ae220b
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from prusik import discovery

_ROOT = Path(__file__).resolve().parent.parent


def _proj(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def _deps(root: Path, rel: str) -> list[str]:
    return discovery.build_graph(root)["forward"][rel]


_PKG = {"a/__init__.py": "", "a/m.py": "M = 1\n",
        "a/b/__init__.py": "def func():\n    pass\n", "a/b/x.py": "X = 1\n",
        "a/pkg/__init__.py": "", "a/pkg/q.py": "Q = 1\n"}


# ---- one rule: base always, base.n only for real project modules ------------------------------

def test_absolute_from_import_records_base_and_module_names(tmp_path):
    root = _proj(tmp_path, {**_PKG, "user.py": "from a.b import x, func\n"})
    deps = _deps(root, "user.py")
    assert "a.b" in deps and "a.b.x" in deps
    assert "a.b.func" not in deps                    # an attribute, not a module


def test_alias_records_the_imported_name_not_the_alias(tmp_path):
    root = _proj(tmp_path, {**_PKG, "user.py": "from a.b import x as z\n"})
    deps = _deps(root, "user.py")
    assert "a.b.x" in deps and "a.b.z" not in deps


def test_star_import_records_the_base_only(tmp_path):
    root = _proj(tmp_path, {**_PKG, "user.py": "from a.b import *\n"})
    assert _deps(root, "user.py") == ["a.b"]


def test_external_attributes_add_nothing(tmp_path):
    root = _proj(tmp_path, {"user.py": "from typing import Any\nfrom os import path\n"})
    assert _deps(root, "user.py") == ["os", "typing"]


@pytest.mark.parametrize("stmt,expected,absent", [
    ("from . import x", {"a.b", "a.b.x"}, set()),
    ("from .x import X", {"a.b.x"}, {"a.b.x.X"}),
    ("from .. import m", {"a", "a.m"}, set()),
    ("from ..pkg import q", {"a.pkg", "a.pkg.q"}, set()),
])
def test_relative_imports_resolve_against_the_module_package(tmp_path, stmt, expected, absent):
    root = _proj(tmp_path, {**_PKG, "a/b/c.py": stmt + "\n"})
    deps = set(_deps(root, "a/b/c.py"))
    assert expected <= deps and not (absent & deps)


def test_init_file_package_is_its_own_directory(tmp_path):
    root = _proj(tmp_path, {**_PKG, "a/b/__init__.py": "from . import x\n"})
    assert set(_deps(root, "a/b/__init__.py")) == {"a.b", "a.b.x"}


def test_root_level_and_above_root_relative_imports_are_dropped(tmp_path):
    root = _proj(tmp_path, {**_PKG, "x.py": "", "top.py": "from . import x\n",
                            "a/c.py": "from ... import zzz\n"})
    assert _deps(root, "top.py") == []               # no package — never a bare "x"
    assert _deps(root, "a/c.py") == []               # climbs above the root


def test_namespace_package_dirs_count_as_modules(tmp_path):
    root = _proj(tmp_path, {"ns/sub/mod.py": "V = 1\n", "user.py": "from ns.sub import mod\n"})
    assert "ns.sub.mod" in _deps(root, "user.py")


def test_imports_anywhere_in_the_file_count(tmp_path):
    root = _proj(tmp_path, {**_PKG, "user.py": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from a.b import x\n"
        "try:\n    from a import m\nexcept ImportError:\n    pass\n"
        "def f():\n    from a.pkg import q\n")})
    assert {"a.b.x", "a.m", "a.pkg.q"} <= set(_deps(root, "user.py"))


def test_no_marker_or_relative_spec_reaches_the_graph(tmp_path):
    root = _proj(tmp_path, {**_PKG, "a/b/c.py": "from . import x\nfrom .. import m, nope\n",
                            "top.py": "from . import x\nfrom a.b import func\n"})
    g = discovery.build_graph(root)
    every = [d for deps in g["forward"].values() for d in deps] + list(g["reverse"])
    assert every and not [d for d in every if d.startswith((".", "?")) or "|" in d]


# ---- consumers: blast_radius and plan-time import reach -----------------------------------------

def test_blast_radius_finds_every_import_form_and_keeps_sibling_precision(tmp_path):
    root = _proj(tmp_path, {
        "pkg/__init__.py": "", "pkg/mod.py": "def f():\n    pass\n", "pkg/sib.py": "S = 1\n",
        "u_import.py": "import pkg.mod\n",
        "u_from.py": "from pkg import mod\n",
        "u_attr.py": "from pkg.mod import f\n",
        "pkg/u_rel.py": "from . import mod\n",
        "u_sibling.py": "from pkg import sib\n"})
    discovery.dep_graph(root)
    hits = set(discovery.blast_radius("pkg/mod", root))
    assert {"u_import.py", "u_from.py", "u_attr.py", "pkg/u_rel.py"} <= hits
    assert "u_sibling.py" not in hits


def test_plan_import_reach_flags_a_from_importing_test(tmp_path):
    from prusik import blast_plan
    root = _proj(tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "V = 1\n",
                            "tests/test_mod.py": "from pkg import mod\n\n"
                                                 "def test_v():\n    assert mod.V\n"})
    discovery.dep_graph(root)
    tests, covered = blast_plan._import_reach(["pkg/mod.py"], root)
    assert "tests/test_mod.py" in tests and "pkg/mod.py" in covered


# ---- this repo, against an independent oracle --------------------------------------------------

def _oracle_importers(root: Path, target: str) -> set[str]:
    """Files whose AST imports `target` — stdlib ast + importlib.util.resolve_name + filesystem
    checks only; never the extractor or resolver under test."""
    def is_module(dotted: str) -> bool:
        p = root.joinpath(*dotted.split("."))
        return (p.with_suffix(".py").is_file() or (p / "__init__.py").is_file()
                or (p.is_dir() and any(p.rglob("*.py"))))

    def hit(name: str) -> bool:
        return name == target or name.startswith(target + ".")

    out = set()
    for rel in discovery.current_modules(root):
        if not rel.endswith(".py"):
            continue
        parts = Path(rel).with_suffix("").parts
        package = ".".join(parts if parts[-1] == "__init__" else parts[:-1]).removesuffix(
            ".__init__")
        try:
            tree = ast.parse((root / rel).read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(hit(a.name) for a in node.names):
                out.add(rel)
            elif isinstance(node, ast.ImportFrom):
                try:
                    base = importlib.util.resolve_name("." * node.level + (node.module or ""),
                                                       package) if node.level else node.module
                except (ImportError, ValueError):
                    continue
                if not base:
                    continue
                if hit(base) or any(a.name != "*" and is_module(f"{base}.{a.name}")
                                    and hit(f"{base}.{a.name}") for a in node.names):
                    out.add(rel)
    return out


def test_repo_ledger_importers_match_an_independent_oracle():
    graph = discovery.build_graph(_ROOT)                 # in memory — the repo's graph file untouched
    got = set(discovery.blast_radius("prusik/ledger", _ROOT, graph=graph))
    want = _oracle_importers(_ROOT, "prusik.ledger")
    assert len(want) > 40                                # far above the old 18
    assert got == want, (sorted(want - got)[:10], sorted(got - want)[:10])
