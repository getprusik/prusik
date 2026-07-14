"""Nested additive merge of pre_sprint_gates — so newly-shipped enforcement gates
actually reach adopters via `prusik update`.

Before this, the config merge was "top-level key present → project wins", with
nested merge only for phases — so an adopter who already had a pre_sprint_gates
block never received a newly-added nested gate (product_fit .8–.12), making the
enforcement undeliverable. These pin the add-if-absent / project-wins semantics.
"""

from __future__ import annotations

import yaml

from prusik.refresh_merge import merge_sprint_config_yaml

_PHASES = 'phases:\n  - name: scoping\n    writable: ["x/**"]\n'


def _merge(tmpl, proj):
    out, summary = merge_sprint_config_yaml(tmpl, proj)
    return yaml.safe_load(out), summary


def test_new_gate_lands_for_adopter_with_existing_block():
    tmpl = _PHASES + ("pre_sprint_gates:\n"
                      "  brief_critique:\n    enabled: true\n"
                      "  product_fit:\n    enabled: true\n    check: product_fit\n"
                      "    require_critique: true\n")
    proj = _PHASES + "pre_sprint_gates:\n  brief_critique:\n    enabled: true\n"
    d, s = _merge(tmpl, proj)
    psg = d["pre_sprint_gates"]
    assert psg["product_fit"]["require_critique"] is True, "whole gate block lands"
    assert "pre_sprint_gates.product_fit" in s["added_gates"]
    assert psg["brief_critique"]["enabled"] is True, "existing gate preserved"


def test_existing_gate_is_project_wins_not_overridden():
    """The blast-radius guard: an adopter's own value is never overridden — their
    enabled:false opt-out survives, while a genuinely-missing sub-key still flows in."""
    tmpl = _PHASES + ("pre_sprint_gates:\n  product_fit:\n    enabled: true\n"
                      "    check: product_fit\n")
    proj = _PHASES + "pre_sprint_gates:\n  product_fit:\n    enabled: false\n"
    d, _ = _merge(tmpl, proj)
    pf = d["pre_sprint_gates"]["product_fit"]
    assert pf["enabled"] is False, "adopter's enabled:false must be preserved"
    assert pf.get("check") == "product_fit", "missing sub-key still added additively"


def test_new_subkey_of_existing_gate_flows_in():
    tmpl = _PHASES + ('pre_sprint_gates:\n  map_freshness:\n    enabled: true\n'
                      '    scoped_hint: "new"\n')
    proj = _PHASES + "pre_sprint_gates:\n  map_freshness:\n    enabled: true\n"
    d, _ = _merge(tmpl, proj)
    assert d["pre_sprint_gates"]["map_freshness"]["scoped_hint"] == "new"


def test_whole_block_added_when_adopter_lacks_pre_sprint_gates():
    tmpl = _PHASES + "pre_sprint_gates:\n  product_fit:\n    enabled: true\n"
    d, s = _merge(tmpl, _PHASES)
    assert "product_fit" in d["pre_sprint_gates"]
    assert "pre_sprint_gates" in s["added_top_level_keys"]


def test_nested_merge_is_idempotent():
    tmpl = _PHASES + ("pre_sprint_gates:\n  brief_critique:\n    enabled: true\n"
                      "  product_fit:\n    enabled: true\n")
    proj = _PHASES + "pre_sprint_gates:\n  brief_critique:\n    enabled: true\n"
    out1, s1 = merge_sprint_config_yaml(tmpl, proj)
    _, s2 = merge_sprint_config_yaml(tmpl, out1)
    assert s1["added_gates"] and not s2["added_gates"], "2nd merge adds nothing"
