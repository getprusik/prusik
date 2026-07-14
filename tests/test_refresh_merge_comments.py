"""fb-80eb508aa7fd: `prusik refresh/update` must never crash on a VALID-YAML
sprint-config that contains bare-hash spacer comments.

ruamel's emitter.write_comment does `value[-1]` with no length guard, so a
CommentToken with an empty value crashes serialize with IndexError. The additive
merge's node-copy of a top-level key can produce exactly that empty comment — so
an adopter with bare-`#` spacer lines got MERGE FAILED and could never `prusik
update` to receive any enforcement delta. These pin the fix.

moat-finding: fb-80eb508aa7fd
"""

from __future__ import annotations

import io

import pytest
import yaml
from ruamel.yaml import YAML

from prusik.refresh_merge import (
    _iter_comment_tokens,
    _sanitize_empty_comments,
    merge_sprint_config_yaml,
)


def _load_with_empty_comment():
    """A CommentedMap holding the exact crash state: a comment token whose value
    is the empty string (what the merge's node-copy can leave behind)."""
    y = YAML()
    data = y.load("a: 1  # trailing comment\nb: 2\n")
    tokens = list(_iter_comment_tokens(getattr(data, "ca", None)))
    assert tokens, "fixture must have a comment token to empty out"
    tokens[0].value = ""
    return y, data


def test_empty_comment_value_crashes_ruamel_without_the_guard():
    """Proof the hazard is real: an empty comment value makes ruamel's serialize
    raise IndexError. (If a future ruamel fixes this upstream, this test flips —
    at which point the guard is belt-and-suspenders, not load-bearing.)"""
    y, data = _load_with_empty_comment()
    with pytest.raises(IndexError):
        y.dump(data, io.StringIO())


def test_sanitize_empty_comments_makes_serialize_safe():
    y, data = _load_with_empty_comment()
    _sanitize_empty_comments(data)          # the fix, run before the dump
    out = io.StringIO()
    y.dump(data, out)                       # must NOT raise
    text = out.getvalue()
    assert "a:" in text and "b:" in text


def test_merge_is_clean_on_bare_hash_comment_config():
    """The finding's regression: a sprint-config with a bare-hash comment line
    must refresh clean AND the additive top-level delta must land."""
    tmpl = ('phases:\n  - name: scoping\n    writable: ["x/**"]\n'
            'new_top_level_gate:\n  enabled: true\n')
    proj = ('phases:\n  - name: scoping\n    writable: ["x/**"]\n'
            '#\n# a bare-hash spacer comment block\n')
    out, summary = merge_sprint_config_yaml(tmpl, proj)
    d = yaml.safe_load(out)
    assert isinstance(d, dict), "merged output must be valid YAML"
    assert "new_top_level_gate" in d, "the additive delta must land, not crash"
