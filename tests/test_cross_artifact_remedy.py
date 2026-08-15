"""fb-25937f6926fa — cross_artifact_inconsistency was the worst UNTARGETED remedy in
the fleet bounces metric (19/34 = 56% wasted re-bounces): the advance-block message
named the drift but not the fix-route, and never said a bare retry is futile, so
agents re-ran `gate advance` unchanged and bounced identically. The remedy now states
the block re-fires byte-identically without a disk edit and names the source-of-truth
route per drift-type; the cache-artifact item leads with the immediate delete, not
next-time prevention. gate_class + detection are unchanged so `prusik bounces`
re-measurement is a clean before/after.

moat-finding: fb-25937f6926fa
"""

from __future__ import annotations

from prusik import consistency, gate


def test_remedy_footer_states_retry_is_futile_and_names_every_route():
    footer = gate._cross_artifact_remedy_footer("checkout")
    low = footer.lower()
    # THE anti-re-bounce line: a bare retry cannot pass (this is what was missing)
    assert "will not pass" in low
    assert "byte-identically" in low
    # a source-of-truth route for each drift-type the checks can emit
    assert "deviations.md" in footer                 # out-of-boundary file
    assert "design/checkout/deviations.md" in footer  # feature is interpolated
    assert "rm -rf" in footer                        # worktree cache dir
    assert "brief" in low                            # brief type/size conflict


def test_remedy_footer_tolerates_unknown_feature():
    assert "design/<feature>/deviations.md" in gate._cross_artifact_remedy_footer(None)


def test_cache_artifact_item_leads_with_the_immediate_delete():
    # the check output must put the FIX-NOW (delete) ahead of the PREVENT (--no-cache),
    # so the agent acts on the block instead of only learning next-sprint hygiene.
    import os
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp(prefix="prusik-cache-"))
    try:
        wt = tmp / "worktrees" / "solo" / "src" / ".pytest_cache"
        wt.mkdir(parents=True)
        lines = consistency.worktrees_clean_of_cache_artifacts(tmp, "feat")
        assert lines, "a worktree cache dir must be flagged"
        joined = "\n".join(lines)
        assert "FIX NOW" in joined and "rm -rf" in joined
        # ordering: the immediate delete appears before the prevention advice
        assert joined.index("FIX NOW") < joined.index("--no-cache")
    finally:
        import shutil
        shutil.rmtree(tmp)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
