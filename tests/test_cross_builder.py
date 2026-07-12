"""Cross-builder interface-contract validation (v0.68.0, field finding #7) — symbols
defined in >1 worktree are parallel-builder drift; catch it at building→reviewing
instead of a 30-min sentinel later."""

from __future__ import annotations

import shutil

from tests._common import _capture_stdout, _mktmp_project  # noqa: F401,E402
from prusik import cross_builder


def _worktree(tmp, role, files: dict):
    d = tmp / "worktrees" / role
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


def _plan(tmp, feature, interfaces=""):
    p = tmp / "design" / feature / "plan.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"## Modules touched\n- `a.py`\n\n## Interfaces\n{interfaces}\n")


# ---------- the adopter drift shape ----------

def test_duplicate_symbol_across_worktrees_flagged():
    """Two builders both define `AlreadyActiveMemberError` — the exact adopter
    drift. Must be flagged, and ranked plan-declared (it's in ## Interfaces)."""
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "backend-builder-a", {
            "api/members.py": "class AlreadyActiveMemberError(Exception):\n    pass\n"})
        _worktree(tmp, "backend-builder-b", {
            "api/invites.py": "class AlreadyActiveMemberError(Exception):\n    pass\n"})
        _plan(tmp, "team-invites",
              "Raise `AlreadyActiveMemberError` from the invite path.")
        dups = cross_builder.duplicate_symbols(tmp, "team-invites")
        assert len(dups) == 1
        assert dups[0]["symbol"] == "AlreadyActiveMemberError"
        assert dups[0]["plan_declared"] is True
        assert set(dups[0]["worktrees"]) == {"backend-builder-a", "backend-builder-b"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_symbol_in_one_worktree_is_clean():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "a", {"x.py": "class Widget:\n    pass\n"})
        _worktree(tmp, "b", {"y.py": "class Gadget:\n    pass\n"})
        _plan(tmp, "feat")
        assert cross_builder.duplicate_symbols(tmp, "feat") == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_common_and_test_names_not_flagged():
    """Generic framework names (Config, __init__) and test helpers recur across
    builders legitimately — not drift."""
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "a", {
            "m.py": "class Config:\n    pass\ndef __init__(self):\n    pass\n",
            "tests/helper.py": "def make_stub():\n    pass\n"})
        _worktree(tmp, "b", {
            "n.py": "class Config:\n    pass\n",
            "tests/helper2.py": "def make_stub():\n    pass\n"})
        _plan(tmp, "feat")
        assert cross_builder.duplicate_symbols(tmp, "feat") == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_single_worktree_is_noop():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "only", {"x.py": "class Foo:\n    pass\n"})
        _plan(tmp, "feat")
        assert cross_builder.duplicate_symbols(tmp, "feat") == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_js_function_duplicate_flagged():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "fe-a", {
            "a.ts": "export function formatMoney(c) { return c; }\n"})
        _worktree(tmp, "fe-b", {
            "b.ts": "export function formatMoney(c) { return c; }\n"})
        _plan(tmp, "feat")
        dups = cross_builder.duplicate_symbols(tmp, "feat")
        assert any(d["symbol"] == "formatMoney" for d in dups)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- #7c: arity mismatch (mock/stub-vs-real signature drift) ----------

def test_arity_mismatch_flagged():
    """Two builders define `find_member_by_user` with different signatures — the
    real impl takes (self, workspace_id, user_id), a stub takes (self, ws). A
    guaranteed TypeError at integration; must be flagged arity_mismatch."""
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "backend", {
            "repo.py": "class R:\n"
                       "    def find_member_by_user(self, workspace_id, user_id):\n"
                       "        return None\n"})
        _worktree(tmp, "test-writer", {
            "fakes.py": "class FakeR:\n"
                        "    def find_member_by_user(self, ws):\n"
                        "        return None\n"})
        _plan(tmp, "team-invites", "`find_member_by_user` on WorkspaceRepository.")
        dups = cross_builder.duplicate_symbols(tmp, "team-invites")
        d = next(d for d in dups if d["symbol"] == "find_member_by_user")
        assert d["arity_mismatch"] is True
        assert set(d["arities"].values()) == {2, 1}     # (ws, user) vs (ws)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_same_arity_duplicate_not_arity_mismatch():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "a", {"x.py": "def shared_helper(a, b):\n    return a\n"})
        _worktree(tmp, "b", {"y.py": "def shared_helper(a, b):\n    return b\n"})
        _plan(tmp, "feat")
        d = next(d for d in cross_builder.duplicate_symbols(tmp, "feat")
                 if d["symbol"] == "shared_helper")
        assert d["arity_mismatch"] is False          # same arity → just a dup
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_variadic_def_not_arity_flagged():
    """A *args/kw-only signature is unbounded — don't flag (conservative)."""
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "a", {"x.py": "def widget_build(self, a, *args):\n    return a\n"})
        _worktree(tmp, "b", {"y.py": "def widget_build(self, a, b, c):\n    return a\n"})
        _plan(tmp, "feat")
        d = next(d for d in cross_builder.duplicate_symbols(tmp, "feat")
                 if d["symbol"] == "widget_build")
        assert d["arity_mismatch"] is False          # one side variadic → no flag
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_arity_of_helper():
    assert cross_builder._arity_of("self, a, b") == (2, False)
    assert cross_builder._arity_of("self, a, b=1") == (1, False)   # default not required
    assert cross_builder._arity_of("self, a, *args") == (None, True)
    assert cross_builder._arity_of("self, a, *, conn=None") == (None, True)  # kw-only
    assert cross_builder._arity_of("a: Dict[str, int], b") == (2, False)     # nested comma


# ---------- #7b: CSS naming drift (BEM-vs-flat) ----------

def test_css_bem_vs_flat_drift_flagged():
    """fe-css defines `.client__table` (BEM); fe-templates uses `client-table`
    (flat) — same concept, different string, across builders. The exact field seam. Flagged; a framework utility (`flex`) is NOT."""
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "fe-css", {
            "styles.css": ".client__table { width: 100%; }\n.slide-over {}\n"})
        _worktree(tmp, "fe-templates", {
            "list.html": '<table class="client-table flex">\n'
                         '<div class="slide-over"></div>\n'})
        drift = cross_builder.css_drift(tmp)
        flagged = {d["used_class"] for d in drift}
        assert "client-table" in flagged          # ↔ client__table
        assert "flex" not in flagged               # framework utility, no variant
        assert "slide-over" not in flagged         # used == defined, fine
        d = next(d for d in drift if d["used_class"] == "client-table")
        assert d["defined_as"] == ["client__table"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_css_no_drift_when_names_agree():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "css", {"s.css": ".client-table {}\n"})
        _worktree(tmp, "tpl", {"x.html": '<div class="client-table"></div>\n'})
        assert cross_builder.css_drift(tmp) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- advisory + CLI ----------

def test_advisory_string_and_none():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "a", {"x.py": "class SharedThing:\n    pass\n"})
        _worktree(tmp, "b", {"y.py": "class SharedThing:\n    pass\n"})
        _plan(tmp, "feat", "`SharedThing` is the contract.")
        adv = cross_builder.advisory(tmp, "feat")
        assert adv and "SharedThing" in adv and "plan-declared" in adv
        # clean project → None
        tmp2 = _mktmp_project()
        _worktree(tmp2, "a", {"x.py": "class Lonely:\n    pass\n"})
        _plan(tmp2, "feat")
        assert cross_builder.advisory(tmp2, "feat") is None
        shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_emits_ledger_event():
    tmp = _mktmp_project()
    try:
        _worktree(tmp, "a", {"x.py": "class Dup:\n    pass\n"})
        _worktree(tmp, "b", {"y.py": "class Dup:\n    pass\n"})
        _plan(tmp, "feat")
        out = _capture_stdout(lambda: cross_builder.run("feat", root=tmp))
        assert "cross-check" in out
        assert "cross_builder_check" in (tmp / ".sprint" / "ledger.jsonl").read_text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
