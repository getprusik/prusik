"""prusik criterion resolve — the defer→resolve complement (field finding #22, v0.105.0)."""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import criterion, schema
from prusik.ledger import read_all


def _write_criteria(tmp, *, blocked=True, cmd="true"):
    (tmp / "briefs").mkdir(exist_ok=True)
    p = tmp / "briefs" / "feat.criteria.yaml"
    p.write_text(
        "criteria:\n"
        "  - id: flows-e2e\n"
        "    description: live stripe flows\n"
        f"    verify_command: \"{cmd}\"\n"
        "    expected_exit: 0\n"
        f"    blocked_external: {'true' if blocked else 'false'}\n"
        "    blocked_reason: needs a live Stripe key\n")
    return p


def test_resolve_passes_flips_blocked_and_records_evidence():
    tmp = _mktmp_project()
    try:
        p = _write_criteria(tmp, blocked=True, cmd="true")     # exits 0 == expected
        rc = criterion.resolve("feat", "flows-e2e", root=tmp)
        assert rc == 0
        # blocked_external flipped in the file
        crit = next(c for c in schema.load_criteria(p) if c["id"] == "flows-e2e")
        assert crit["blocked_external"] is False
        # evidence recorded with honest provenance
        ev = [r for r in read_all()
              if r.get("event") == "success_criterion_verified"
              and r.get("id") == "flows-e2e"]
        assert ev and ev[-1]["passed"] is True
        assert ev[-1]["resolution"] == "from_blocked_external"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_refuses_non_deferred_criterion():
    """The integrity guard: resolve is NOT a backdoor to green a criterion that was
    never deferred — those go through reviewing."""
    tmp = _mktmp_project()
    try:
        _write_criteria(tmp, blocked=False, cmd="true")
        rc = criterion.resolve("feat", "flows-e2e", root=tmp)
        assert rc == 2                                          # rejected
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_failing_command_stays_deferred():
    tmp = _mktmp_project()
    try:
        p = _write_criteria(tmp, blocked=True, cmd="false")    # exits 1 != expected 0
        assert criterion.resolve("feat", "flows-e2e", root=tmp) == 0      # advisory
        assert criterion.resolve("feat", "flows-e2e", root=tmp, strict=True) == 1
        # still deferred — not flipped, no false resolution
        crit = next(c for c in schema.load_criteria(p) if c["id"] == "flows-e2e")
        assert crit["blocked_external"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_missing_criterion_or_file():
    tmp = _mktmp_project()
    try:
        assert criterion.resolve("nofile", "x", root=tmp) == 2        # no criteria file
        _write_criteria(tmp, blocked=True)
        assert criterion.resolve("feat", "no-such-id", root=tmp) == 2  # missing id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
