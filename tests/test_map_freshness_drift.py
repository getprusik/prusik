"""map_freshness gated on map.md age + GLOBAL drift %, which dilutes a single
subsystem's change below threshold. A dependency merged into the feature's OWN
subsystem 0-2d before a sprint passed the age + global-% checks, so scoping read a
stale map of exactly what it scopes. The gate is now FEATURE-SCOPED: a drifted module
matching the feature's subsystem fails regardless of the global %, compared against a
FRESH module walk so a just-merged file is seen (not the cached dep-graph).

fb-76ff51b273de.

moat-finding: fb-76ff51b273de
"""

from __future__ import annotations

import json

from prusik import discovery, gate

# the picker that merged into the categorization subsystem after the map was generated
_PICKER = ["src/components/CategoryPicker.tsx", "src/hooks/useCategories.ts",
           "src/types/categorization-types.ts"]
# 20 unrelated stable modules so the GLOBAL drift % stays well under the 30% threshold
_STABLE = [f"src/m{i}.ts" for i in range(20)]


def _seed_fingerprint(root, modules):
    sp = root / ".sprint"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "map-fingerprint.json").write_text(json.dumps({"modules": modules}))


def _seed_depgraph(root, modules):
    sp = root / ".sprint"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "dep-graph.json").write_text(json.dumps({"forward": {m: [] for m in modules}}))


def test_feature_drift_terms_from_slug(tmp_path):
    # 'categorization' → stem 'catego'; 'depth' is in the stoplist → dropped
    assert gate._feature_drift_terms("categorization-depth", tmp_path) == ["catego"]


def test_feature_scoped_drift_matches_subsystem(tmp_path, monkeypatch):
    _seed_fingerprint(tmp_path, _STABLE)                     # map predates the picker
    monkeypatch.setattr(discovery, "current_modules",
                        lambda root=None: set(_STABLE + _PICKER))   # HEAD has it now
    hits = discovery.feature_scoped_drift(tmp_path, ["catego"])
    assert set(hits) == set(_PICKER)                         # all three picker modules


def test_feature_scoped_drift_no_terms_or_missing(tmp_path, monkeypatch):
    _seed_fingerprint(tmp_path, _STABLE)
    monkeypatch.setattr(discovery, "current_modules", lambda root=None: set(_STABLE))
    assert discovery.feature_scoped_drift(tmp_path, []) == []        # no terms → no hits
    assert discovery.feature_scoped_drift(tmp_path / "nope", ["catego"]) is None  # no fp


def test_gate_fails_on_feature_scoped_drift_under_global_threshold(tmp_path, monkeypatch):
    # GLOBAL drift (fingerprint vs cached dep-graph) = 0% — well UNDER 30% (the live miss
    # was global-under-threshold). The FRESH walk, however, sees the picker.
    _seed_fingerprint(tmp_path, _STABLE)
    _seed_depgraph(tmp_path, _STABLE)                        # cached graph still pre-picker
    monkeypatch.setattr(discovery, "current_modules",
                        lambda root=None: set(_STABLE + _PICKER))
    config = {"pre_sprint_gates": {"map_fresh": {"check": "map_freshness", "max_drift_pct": 30}}}

    unmet = gate._check_pre_sprint_gates(config, "categorization-depth", tmp_path)

    assert any("subsystem drifted" in m and "CategoryPicker" in m for m in unmet), unmet
    assert any("feature-scoped" in m for m in unmet)


def test_gate_passes_when_feature_subsystem_untouched(tmp_path, monkeypatch):
    # ADVERSARIAL no-false-positive: a DIFFERENT feature whose subsystem did NOT drift.
    # The picker drift is irrelevant to 'invoice-pdf-export'; global % is 0.
    _seed_fingerprint(tmp_path, _STABLE)
    _seed_depgraph(tmp_path, _STABLE)
    monkeypatch.setattr(discovery, "current_modules",
                        lambda root=None: set(_STABLE + _PICKER))
    config = {"pre_sprint_gates": {"map_fresh": {"check": "map_freshness", "max_drift_pct": 30}}}

    unmet = gate._check_pre_sprint_gates(config, "invoice-pdf-export", tmp_path)

    assert unmet == []          # invoice subsystem untouched + global under threshold


def test_gate_still_fails_on_high_global_drift(tmp_path, monkeypatch):
    # the original global-% floor still works for a feature with no subsystem match
    _seed_fingerprint(tmp_path, _STABLE)
    _seed_depgraph(tmp_path, _PICKER)            # cached graph: 20 removed + 3 added → ~100%
    monkeypatch.setattr(discovery, "current_modules", lambda root=None: set(_PICKER))
    config = {"pre_sprint_gates": {"map_fresh": {"check": "map_freshness", "max_drift_pct": 30}}}

    unmet = gate._check_pre_sprint_gates(config, "invoice-pdf-export", tmp_path)

    assert any("exceeds 30" in m for m in unmet)
