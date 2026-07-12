# case-002 — DEV-1 root #2: form-field-name dropthrough

**Surfaced in**: adopter m4-dev1-jit-inline-client-fix (DEV-1, 2026-05-18).

**Defect class**: template's `<input name="X">` posts under one key;
handler reads `form.get("Y")` (or `request.form["Y"]`) under a
different key. Browser submission silently drops the value — handler
sees None / KeyError / silent-default.

**Prusik check that catches it**: `prusik gate check-bindings`, the
`form_name` class (v0.19.0). With `--touched-set` covering both the
form template AND the handler, the cross-checker reports the symmetric
"consumed-but-not-provided" / "provided-but-not-consumed" finding.

**Trial reference**: DEV-1's inline-client form had
`<input name="new_client_legal_name">` while the handler read
`form.get("inline_client_name")` — submission appeared to succeed but
the legal-name field never reached the persistence layer.
