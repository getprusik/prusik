"""Additive sprint-config merge must not drag a following key's comment block
onto a newly-copied top-level key (cosmetic merge artifact that compounds at
every adopter on every refresh).

moat-finding: refresh-merge-dragged-comment

Found by dogfooding: An adopter and adopter both accumulated an orphaned `always_writable`
comment block at EOF, because the template puts that comment immediately after
`reviewing_defer_markers` — and ruamel parks it on that key's trailing token, so
copying the key into a project lacking it dragged the comment along.
"""

from __future__ import annotations

import yaml

from prusik.refresh_merge import merge_sprint_config_yaml

_TEMPLATE = """version: 1
reviewing_defer_markers:
- browser_smoke        # the marker's own comment

# Leading comment block that belongs to the NEXT key.
# Second line of it.
always_writable:
- "reports/**"
phases: []
"""


def test_dragged_comment_is_dropped_own_comment_kept():
    project = 'version: 1\nalways_writable:\n- "reports/**"\nphases: []\n'
    merged, summary = merge_sprint_config_yaml(_TEMPLATE, project)

    assert "reviewing_defer_markers" in summary["added_top_level_keys"]
    # data intact
    assert yaml.safe_load(merged)["reviewing_defer_markers"] == ["browser_smoke"]
    # the key's OWN comment survives…
    assert "the marker's own comment" in merged
    # …but the next key's block is NOT dragged in as an orphan
    assert "belongs to the NEXT key" not in merged
    assert merged.count("always_writable") == 1   # appears once (real key only)


def test_no_change_is_byte_identical():
    """The untangle must not perturb a no-op merge (project already complete)."""
    project = (
        "version: 1\n"
        "reviewing_defer_markers:\n- browser_smoke        # the marker's own comment\n"
        "always_writable:\n- \"reports/**\"\nphases: []\n"
    )
    merged, summary = merge_sprint_config_yaml(_TEMPLATE, project)
    assert summary["added_top_level_keys"] == []
    assert merged == project          # byte-identical no-op


def test_scalar_key_drag_is_also_untangled():
    """The same artifact on a SCALAR value (comment parked on the parent mapping)."""
    template = (
        "version: 1\n"
        "new_scalar: 5   # scalar's own note\n\n"
        "# next-key leading block\nexisting: 1\nphases: []\n"
    )
    project = "version: 1\nexisting: 1\nphases: []\n"
    merged, _ = merge_sprint_config_yaml(template, project)
    assert yaml.safe_load(merged)["new_scalar"] == 5
    assert "next-key leading block" not in merged
