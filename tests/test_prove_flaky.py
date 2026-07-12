"""A non-deterministic flake DEFEATS the A/B-vs-base `prove` (it can pass or fail on base
at random), so agents labelled any red 'flake / pre-existing' BY INSPECTION — the exact
crack a real regression walks through (fb-b351e5ef9de6: an agent asserted
'baseline-proven' that was never proven). `prove-flaky` makes flakiness SYSTEM-COMPUTED:
a baseline is recorded ONLY on DEMONSTRATED non-determinism (pass AND fail on identical
code). An all-FAIL is a deterministic failure, NOT a flake, and is refused.

moat-finding: fb-b351e5ef9de6
"""

from __future__ import annotations

from datetime import date

from prusik import baseline


def _alternating_cmd(tmp_path):
    """A command that passes on even invocations and fails on odd ones — genuinely
    non-deterministic across runs (state in a counter file)."""
    counter = tmp_path / "n"
    return (f'n=$(cat "{counter}" 2>/dev/null || echo 0); '
            f'echo $((n+1)) > "{counter}"; [ $((n % 2)) -eq 0 ]')


def test_prove_flaky_records_on_demonstrated_nondeterminism(tmp_path):
    ok, msg = baseline.prove_flaky(tmp_path, "tests/x::test_flaky",
                                   _alternating_cmd(tmp_path), runs=4)
    assert ok, msg
    assert "proven flaky" in msg.lower()
    entries = baseline.load(tmp_path)
    assert entries and entries[0]["test"] == "tests/x::test_flaky"
    assert entries[0]["kind"] == "flaky"
    assert "2P/2F" in entries[0]["note"]


def test_prove_flaky_refuses_all_pass(tmp_path):
    ok, msg = baseline.prove_flaky(tmp_path, "t", "true", runs=3)
    assert not ok
    assert "passed" in msg.lower()
    assert baseline.load(tmp_path) == []               # nothing baselined


def test_prove_flaky_refuses_deterministic_failure(tmp_path):
    # ADVERSARIAL: an all-FAIL is a DETERMINISTIC failure (a real/pre-existing regression).
    # It must NEVER be laundered as a flake — that is the crack this closes.
    ok, msg = baseline.prove_flaky(tmp_path, "t", "false", runs=4)
    assert not ok
    assert "deterministic" in msg.lower()
    assert baseline.load(tmp_path) == []


def test_prove_flaky_needs_at_least_two_runs(tmp_path):
    ok, msg = baseline.prove_flaky(tmp_path, "t", "true", runs=1)
    assert not ok
    assert "2 runs" in msg


def test_proven_flaky_is_tolerated_in_deselect_args(tmp_path):
    baseline.prove_flaky(tmp_path, "tests/x::test_flaky",
                         _alternating_cmd(tmp_path), runs=4)
    args = baseline.deselect_args(tmp_path, date.today())
    assert "--deselect" in args and "tests/x::test_flaky" in args


def test_prove_flaky_via_run_dispatch(tmp_path):
    rc = baseline.run("prove-flaky", test="tests/x::test_flaky",
                      command=_alternating_cmd(tmp_path), runs=4, root=tmp_path)
    assert rc == 0
    rc2 = baseline.run("prove-flaky", test="t", command="false", runs=3, root=tmp_path)
    assert rc2 == 2                                    # deterministic fail → non-zero
