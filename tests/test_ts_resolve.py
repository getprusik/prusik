"""TS/JS module-graph edge resolution (v0.54.1, finding #6) — workspace pkgs,
tsconfig paths, relative; the JSONC-glob parse regression; dep_graph wiring."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import discovery
from prusik.ts_resolve import TsResolver, external_form, _load_jsonc


def _mono() -> Path:
    d = Path(tempfile.mkdtemp(prefix="kit-tsr-"))
    (d / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
    (d / "packages/shared/src").mkdir(parents=True)
    (d / "packages/shared/package.json").write_text('{"name": "@an adopter/shared"}')
    (d / "packages/shared/src/index.ts").write_text("export {}\n")
    (d / "packages/shared/src/util.ts").write_text("export {}\n")
    (d / "packages/frontend/src/components").mkdir(parents=True)
    (d / "packages/frontend/package.json").write_text('{"name": "@an adopter/frontend"}')
    # tsconfig with a glob in include — the exact shape that broke the naive
    # block-comment strip ("@/*" / "**/*.ts" look like /* … */)
    (d / "packages/frontend/tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}, '
        '"include": ["**/*.ts", "**/*.tsx"]}')
    (d / "packages/frontend/src/components/Card.tsx").write_text("export {}\n")
    (d / "packages/frontend/src/x.ts").write_text("export {}\n")
    (d / "packages/frontend/src/app.ts").write_text(
        "import {C} from '@/components/Card'\n"
        "import {U} from '@an adopter/shared'\n"
        "import {x} from './x'\n"
        "import React from 'react'\n")
    return d


_IMP = "packages/frontend/src/app.ts"


def test_workspace_package_import():
    r = TsResolver(_mono())
    assert r.resolve(_IMP, "@an adopter/shared") == "packages/shared/src/index.ts"
    assert r.resolve(_IMP, "@an adopter/shared/util") == "packages/shared/src/util.ts"


def test_tsconfig_paths_alias():
    r = TsResolver(_mono())
    assert r.resolve(_IMP, "@/components/Card") == \
        "packages/frontend/src/components/Card.tsx"


def test_relative_import_with_ext_resolution():
    r = TsResolver(_mono())
    assert r.resolve(_IMP, "./x") == "packages/frontend/src/x.ts"
    assert r.resolve(_IMP, "./does-not-exist") is None   # kept external by caller


def test_external_bare_import_unresolved():
    r = TsResolver(_mono())
    assert r.resolve(_IMP, "react") is None


def test_jsonc_glob_does_not_clobber_paths():
    # regression: include "**/*.ts" must not eat the paths block (the /* … */
    # false-block-comment bug). Raw-JSON-first parse fixes it.
    d = _mono()
    data = _load_jsonc(d / "packages" / "frontend" / "tsconfig.json")
    assert data["compilerOptions"]["paths"] == {"@/*": ["./src/*"]}


def test_external_form_normalization():
    assert external_form("react") == "react"
    assert external_form("@types/node") == "@types/node"
    assert external_form("@reduxjs/toolkit/query") == "@reduxjs/toolkit"
    assert external_form("./keep") == "./keep"


def test_dep_graph_resolves_ts_edges_to_real_files():
    d = _mono()
    cwd = os.getcwd()
    try:
        os.chdir(d)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        assert discovery.dep_graph() == 0
        graph = json.loads((d / ".sprint" / "dep-graph.json").read_text())
        edges = graph["forward"][_IMP]
        assert "packages/shared/src/index.ts" in edges
        assert "packages/frontend/src/components/Card.tsx" in edges
        assert "packages/frontend/src/x.ts" in edges
        assert "react" in edges                       # external kept
        # the headline: no literal @-alias edges survive
        assert not any(e.startswith("@") for e in edges)
        # reverse graph shows inbound edges on shared modules
        assert _IMP in graph["reverse"]["packages/shared/src/index.ts"]
    finally:
        os.chdir(cwd)
