"""The reviewer-evidence worktree hash must ignore DERIVED build output (dist/,
node_modules/, …). Otherwise any build-triggering capture regenerates dist/,
moves the hash, and stales the OTHER reviewer's evidence — so the two reviewer
evidences (regression + conventions) can only co-exist at one hash by accident of
command choice.

moat-finding: fb-b4eb142e5740
"""

from __future__ import annotations

from prusik.gate import _worktree_substantive_hash


def _wt(root, files: dict[str, str]) -> None:
    base = root / "worktrees" / "backend"
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def test_derived_build_output_does_not_move_hash(tmp_path):
    _wt(tmp_path, {
        "packages/api/src/index.ts": "export const x = 1;\n",
        "packages/api/dist/index.js": "exports.x=1;\n",   # build output
        "node_modules/dep/index.js": "module.exports={};\n",
        ".turbo/cache/abc": "blob\n",
    })
    h1 = _worktree_substantive_hash(tmp_path)

    # a build regenerates dist/ + node_modules (different bytes) — hash must hold
    (tmp_path / "worktrees/backend/packages/api/dist/index.js").write_text(
        "exports.x=1;/*rebuilt at a different time*/\n")
    (tmp_path / "worktrees/backend/node_modules/dep/index.js").write_text(
        "module.exports={rebuilt:true};\n")
    (tmp_path / "worktrees/backend/.turbo/cache/abc").write_text("blob2\n")
    h2 = _worktree_substantive_hash(tmp_path)
    assert h1 == h2, "derived build output must not move the reviewer-evidence hash"


def test_real_source_change_still_moves_hash(tmp_path):
    """The exclusion must NOT make the gate blind: a real source edit moves it."""
    _wt(tmp_path, {"packages/api/src/index.ts": "export const x = 1;\n",
                   "packages/api/dist/index.js": "exports.x=1;\n"})
    h1 = _worktree_substantive_hash(tmp_path)
    (tmp_path / "worktrees/backend/packages/api/src/index.ts").write_text(
        "export const x = 2;\n")              # genuine source change
    assert _worktree_substantive_hash(tmp_path) != h1
