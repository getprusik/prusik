"""Holistic product-fit gate — the evidence-resolution contract.

The gate's whole value is that a product-fit acknowledgement is accepted ONLY
when its references resolve against real repo state — a fabricated "it fits"
cannot pass. These tests pin exactly that: a coherent acknowledgement passes,
and every non-resolving claim (phantom pillar, phantom related feature,
un-canonical concept, silent concept duplication) is blocked. Plus the dormant
(no-charter) and bootstrap paths.
"""

from __future__ import annotations

import shutil

from prusik import product_fit as pf
from tests._common import _mktmp_project


def _charter(tmp, pillars="- P1 zero-fabrication trust\n- P2 minutes-not-hours",
             glossary="- customer: a party that receives an invoice\n"
                      "- workspace: a tenant boundary"):
    (tmp / "design").mkdir(exist_ok=True)
    (tmp / "design" / "product.md").write_text(
        f"# Product charter\n\n## North-star\nBe great.\n\n"
        f"## Pillars\n{pillars}\n\n## Glossary\n{glossary}\n")


def _fit(tmp, feature, advances="- P1 — hardens the trust guarantee",
         related="- none", concepts="- customer [canonical]"):
    d = tmp / "design" / feature
    d.mkdir(parents=True, exist_ok=True)
    (d / "product-fit.md").write_text(
        f"# fit\n\n## Advances\n{advances}\n\n## Related\n{related}\n\n"
        f"## Concepts\n{concepts}\n")


def _brief(tmp, feature):
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{feature}.md").write_text("## Goal\nx\n")


def test_dormant_when_no_charter():
    tmp = _mktmp_project()
    try:
        ok, errs = pf.check("feat", root=tmp)
        assert ok and errs == [], "no charter → gate dormant, must pass"
    finally:
        shutil.rmtree(tmp)


def test_charter_present_but_no_acknowledgement_blocks():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        ok, errs = pf.check("feat", root=tmp)
        assert not ok and any("missing product-fit" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_resolving_acknowledgement_passes():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        _fit(tmp, "feat")  # P1 pillar, related none, customer canonical
        ok, errs = pf.check("feat", root=tmp)
        assert ok, errs
    finally:
        shutil.rmtree(tmp)


def test_phantom_pillar_blocks():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        _fit(tmp, "feat", advances="- P9 — advances a pillar that isn't real")
        ok, errs = pf.check("feat", root=tmp)
        assert not ok and any("not a pillar" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_related_must_resolve_to_existing_brief():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        # cite a related feature that has no brief → blocked
        _fit(tmp, "feat", related="- ghost-feature: extends")
        ok, errs = pf.check("feat", root=tmp)
        assert not ok and any("ghost-feature" in e for e in errs)
        # now create the brief → resolves
        _brief(tmp, "ghost-feature")
        ok2, _ = pf.check("feat", root=tmp)
        assert ok2
    finally:
        shutil.rmtree(tmp)


def test_canonical_concept_must_be_in_glossary():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        _fit(tmp, "feat", concepts="- invoice [canonical]")  # not in glossary
        ok, errs = pf.check("feat", root=tmp)
        assert not ok and any("not in the charter glossary" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_new_concept_registers_but_duplication_is_blocked():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        # a genuinely new term with a definition → allowed
        _fit(tmp, "feat", concepts="- reminder [new: a scheduled nudge to pay]")
        assert pf.check("feat", root=tmp)[0]
        # new term without a definition → blocked
        _fit(tmp, "feat", concepts="- reminder [new:]")
        assert not pf.check("feat", root=tmp)[0]
        # re-registering an EXISTING canonical term as [new] → duplication, blocked
        _fit(tmp, "feat", concepts="- customer [new: yet another customer]")
        ok, errs = pf.check("feat", root=tmp)
        assert not ok and any("already canonical" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_untagged_concept_blocks():
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        _fit(tmp, "feat", concepts="- customer")  # no [canonical]/[new] tag
        ok, errs = pf.check("feat", root=tmp)
        assert not ok and any("must tag" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_bootstrap_drafts_charter_with_existing_features():
    tmp = _mktmp_project()
    try:
        _brief(tmp, "alpha")
        _brief(tmp, "beta")
        assert not pf.charter_path(tmp).exists()
        rc = pf.bootstrap(root=tmp)
        assert rc == 0 and pf.charter_path(tmp).exists()
        text = pf.charter_path(tmp).read_text()
        assert "alpha" in text and "beta" in text, "existing features seeded"
        # bootstrap won't overwrite an existing charter
        assert pf.bootstrap(root=tmp) == 0
    finally:
        shutil.rmtree(tmp)
