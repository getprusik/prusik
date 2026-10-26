"""fb-63d1c3a15af1 + fb-35f64503cc75 — Python module names follow the source root.

The dep-graph named every Python module by its path from the project root, but a src-layout
project imports `pkg.mod` from `src/pkg/mod.py`. Its from-import edges never matched a project
module and a blast_radius query on `src/pkg/mod.py` looked for `src.pkg.mod`, so plan-time
import reach found NOTHING on the src-layout design partner (v0.225.0 added +1 edge there vs
+483 on the engine). A changed package `__init__.py` likewise had zero reach: it was queried as
`pkg.__init__`.

Adversarial cases: a TypeScript `src/` must not change at all; a project that imports `src.*`
absolutely (src as a namespace package) must keep its `src.*` edges; a stored "not src-layout"
decision must not trigger a rebuild on every query.

moat-finding: fb-63d1c3a15af1
moat-finding: fb-35f64503cc75
"""

from __future__ import annotations

import json
from pathlib import Path

from prusik import discovery

_ROOT = Path(__file__).resolve().parent.parent


def _proj(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


_SRC = {"src/pkg/__init__.py": "", "src/pkg/mod.py": "V = 1\n", "src/pkg/sib.py": "S = 1\n",
        "src/pkg/sub/__init__.py": "", "src/pkg/sub/deep.py": "D = 1\n",
        "tests/__init__.py": ""}


def _graph(root: Path) -> dict:
    return discovery.build_graph(root)


# ---- naming follows the source root -----------------------------------------------------------

def test_src_layout_is_detected_and_recorded(tmp_path):
    root = _proj(tmp_path, {**_SRC, "tests/test_mod.py": "from pkg import mod\n"})
    assert _graph(root)["stats"]["python_source_root"] == "src"


def test_from_import_in_a_src_layout_records_the_module(tmp_path):
    root = _proj(tmp_path, {**_SRC, "tests/test_mod.py": "from pkg import mod\n"})
    deps = _graph(root)["forward"]["tests/test_mod.py"]
    assert "pkg.mod" in deps and "pkg" in deps


def test_relative_import_inside_src_resolves_to_pkg_names(tmp_path):
    root = _proj(tmp_path, {**_SRC, "src/pkg/user.py": "from . import mod\nfrom .sub import deep\n"})
    deps = set(_graph(root)["forward"]["src/pkg/user.py"])
    assert {"pkg", "pkg.mod", "pkg.sub", "pkg.sub.deep"} <= deps
    assert not [d for d in deps if d.startswith("src")]


def test_no_src_prefixed_duplicates_or_markers_reach_the_graph(tmp_path):
    root = _proj(tmp_path, {**_SRC, "src/pkg/user.py": "from . import mod\n",
                            "tests/test_mod.py": "from pkg.sub import deep\nimport pkg.mod\n"})
    g = _graph(root)
    every = [d for deps in g["forward"].values() for d in deps] + list(g["reverse"])
    assert not [d for d in every if d.startswith(("src.", ".", "?")) or "|" in d]


# ---- blast_radius maps Python paths (additively) ------------------------------------------------

def _hits(root: Path, path: str) -> set[str]:
    return set(discovery.blast_radius(path, root, graph=_graph(root)))


def test_blast_radius_maps_every_src_layout_path_form(tmp_path):
    root = _proj(tmp_path, {**_SRC, "tests/test_a.py": "from pkg import mod\n",
                            "tests/test_b.py": "import pkg.mod\n",
                            "tests/test_sib.py": "from pkg import sib\n"})
    for q in ("src/pkg/mod", "src/pkg/mod.py"):
        hits = _hits(root, q)
        assert {"tests/test_a.py", "tests/test_b.py"} <= hits, q
        assert "tests/test_sib.py" not in hits, q
    assert {"tests/test_a.py", "tests/test_sib.py"} <= _hits(root, "src/pkg")   # package dir


def test_package_init_path_queries_the_package(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/mod.py": "V = 1\n", "user.py": "import pkg\n",
             "user_sub.py": "from pkg.mod import V\n"}
    root = _proj(tmp_path, files)
    for q in ("pkg/__init__", "pkg/__init__.py"):
        assert {"user.py", "user_sub.py"} <= _hits(root, q), q
    src_root = _proj(tmp_path / "s", {**_SRC, "tests/test_p.py": "import pkg\n"})
    assert "tests/test_p.py" in _hits(src_root, "src/pkg/__init__")


def test_namespace_dir_under_src_maps(tmp_path):
    root = _proj(tmp_path, {"src/ns/mod.py": "V = 1\n", "user.py": "from ns import mod\n"})
    assert "user.py" in _hits(root, "src/ns")


def test_typescript_src_paths_are_unchanged(tmp_path):
    files = {"src/config.ts": "export const c = 1\n",
             "src/app.ts": "import { c } from './config'\nimport cfg from 'config'\n",
             "config.py": "X = 1\n", "user.py": "import config\n"}
    root = _proj(tmp_path, files)
    graph = _graph(root)
    hits = set(discovery.blast_radius("src/config", root, graph=graph))
    # exactly today's matching: the TS relative importer only — never the npm `config` external
    # nor the Python `config` module that a bare mapped name would have caught
    assert "user.py" not in hits


def test_empty_mapped_name_is_never_queried(tmp_path):
    root = _proj(tmp_path, {**_SRC, "__init__.py": "", "tests/test_a.py": "from pkg import mod\n"})
    assert _hits(root, "src") == set()
    assert _hits(root, "__init__") == set()


def test_plan_import_reach_for_src_layout_and_package_init(tmp_path):
    from prusik import blast_plan
    root = _proj(tmp_path, {**_SRC, "tests/test_mod.py": "from pkg import mod\n\n"
                                                         "def test_v():\n    assert mod.V\n",
                            "tests/test_pkg.py": "import pkg\n\ndef test_p():\n    assert pkg\n"})
    discovery.dep_graph(root)
    tests, _ = blast_plan._import_reach(["src/pkg/mod.py"], root)
    assert "tests/test_mod.py" in tests
    tests, _ = blast_plan._import_reach(["src/pkg/__init__.py"], root)
    assert "tests/test_pkg.py" in tests


# ---- what must NOT change ---------------------------------------------------------------------

def test_src_package_and_absolute_src_importers_keep_src_names(tmp_path):
    pkg_src = _proj(tmp_path / "a", {"src/__init__.py": "", "src/utils.py": "U = 1\n",
                                     "app.py": "from src import utils\n"})
    g = _graph(pkg_src)
    assert g["stats"]["python_source_root"] is None
    assert "src.utils" in g["forward"]["app.py"]
    ns_src = _proj(tmp_path / "b", {"src/utils.py": "U = 1\n", "app.py": "import src.utils\n"})
    g = _graph(ns_src)
    assert g["stats"]["python_source_root"] is None             # absolute `src` import disqualifies
    assert "app.py" in discovery.blast_radius("src/utils", ns_src, graph=g)


def test_engine_repo_graph_is_unchanged_apart_from_the_stats_key():
    g = _graph(_ROOT)
    assert g["stats"]["python_source_root"] is None
    assert not [d for deps in g["forward"].values() for d in deps if d.startswith("src.")]


def test_stale_cache_without_the_decision_is_rebuilt_once(tmp_path, monkeypatch):
    root = _proj(tmp_path, {**_SRC, "tests/test_mod.py": "from pkg import mod\n"})
    (root / ".sprint").mkdir()
    gp = root / ".sprint" / "dep-graph.json"
    gp.write_text(json.dumps({"forward": {}, "reverse": {}, "stats": {}}))   # pre-change cache
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    calls = []
    real = discovery.dep_graph
    monkeypatch.setattr(discovery, "dep_graph", lambda r=None: calls.append(1) or real(r))
    for _ in range(3):
        assert "tests/test_mod.py" in discovery.blast_radius("src/pkg/mod", root)
    assert calls == [1]


def test_stored_null_decision_never_triggers_a_rebuild(tmp_path, monkeypatch):
    root = _proj(tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "V = 1\n",
                            "user.py": "from pkg import mod\n"})
    discovery.dep_graph(root)
    calls = []
    monkeypatch.setattr(discovery, "dep_graph", lambda r=None: calls.append(1) or 0)
    for _ in range(3):
        assert "user.py" in discovery.blast_radius("pkg/mod", root)
    assert calls == []


def test_in_memory_graph_is_never_rebuilt_or_written(tmp_path, monkeypatch):
    root = _proj(tmp_path, {**_SRC, "tests/test_mod.py": "from pkg import mod\n"})
    calls = []
    monkeypatch.setattr(discovery, "dep_graph", lambda r=None: calls.append(1) or 0)
    discovery.blast_radius("src/pkg/mod", root, graph={"forward": {}, "reverse": {}, "stats": {}})
    assert calls == [] and not (root / ".sprint" / "dep-graph.json").exists()
