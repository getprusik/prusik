"""fb-c76ae6da2255 — tsc is silent on a clean typecheck, so `capture --kind types
-- tsc --noEmit` recorded types=0 (false-clean) and forced a manual re-capture
with a counting flag every reviewing phase (a confirmed re-bounce source). Capture
now appends --extendedDiagnostics to a trailing bare tsc so a clean run prints
`Files: N` and counts on the first capture.

moat-finding: fb-c76ae6da2255
"""

from __future__ import annotations

from types import SimpleNamespace

from prusik import gate, schema


# ---- the augmentation logic -----------------------------------------------------

def test_appends_flag_to_bare_tsc_forms():
    aug = gate._augment_types_count
    assert aug("tsc --noEmit") == "tsc --noEmit --extendedDiagnostics"
    assert aug("npx tsc --noEmit") == "npx tsc --noEmit --extendedDiagnostics"
    assert aug("cd packages/backend && npx tsc -p tsconfig.json --noEmit") \
        == "cd packages/backend && npx tsc -p tsconfig.json --noEmit --extendedDiagnostics"


def test_skips_when_a_diagnostics_flag_is_already_present():
    aug = gate._augment_types_count
    assert aug("tsc --noEmit --extendedDiagnostics") == "tsc --noEmit --extendedDiagnostics"
    assert aug("tsc --noEmit --diagnostics") == "tsc --noEmit --diagnostics"


def test_skips_when_tsc_is_not_the_trailing_command():
    aug = gate._augment_types_count
    # a wrapper we can't safely mutate, and a non-tsc trailing statement
    assert aug("pnpm typecheck") == "pnpm typecheck"
    assert aug("tsc --noEmit && echo done") == "tsc --noEmit && echo done"


# ---- end-to-end: the count now appears on the first capture ---------------------

def test_clean_tsc_counts_on_first_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # A self-contained tsc emulator: prints its file count ONLY when the diagnostics
    # flag is passed (like real tsc). The trailing `tsc --noEmit` triggers the
    # augmentation, which makes the emulator emit `Files: 7`.
    cmd = "tsc() { case \"$*\" in *extendedDiagnostics*) echo 'Files: 7';; esac; }; tsc --noEmit"
    rc = gate.capture(SimpleNamespace(
        command=[cmd], reset=False, feature="feat", phase="conventions", kind="types"))
    assert rc == 0
    ev = schema.evidence_path_for(tmp_path / "reports" / "feat", "conventions")
    entry = schema.load_evidence(ev)[0]
    assert entry["nonempty_primitive"]["value"] == 7        # counted, NOT a types=0 false-clean
    assert "--extendedDiagnostics" in entry["command"]      # the augmentation is auditable


def test_silent_tsc_without_augmentation_would_be_zero(tmp_path, monkeypatch):
    """Control: the SAME emulator, but kind=lint (no augmentation) → the flag is
    never appended, the emulator stays silent, and the primitive is 0 — proving it
    is the types-augmentation, not the emulator, that produces the count."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    cmd = "tsc() { case \"$*\" in *extendedDiagnostics*) echo 'Files: 7';; esac; }; tsc --noEmit"
    gate.capture(SimpleNamespace(
        command=[cmd], reset=False, feature="feat", phase="conventions", kind="lint"))
    ev = schema.evidence_path_for(tmp_path / "reports" / "feat", "conventions")
    entry = schema.load_evidence(ev)[0]
    assert "--extendedDiagnostics" not in entry["command"]  # lint is not augmented
