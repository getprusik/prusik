# field-ts evidence corpus

Verbatim `prusik prove` output captured from **adopter** (a pure TS/turbo monorepo),
the first serious TypeScript adopter of prusik. This is the real-world evidence
behind the v0.53.0 TS/turbo evidence adapter — forcing-function findings #1
(pytest/mypy-shaped extractor) and #1.5 (a gameable false-clean from loose
"looks-clean" markers).

`tests/test_prove_ts.py` **reads the small logs here directly**, so a regression
fixture can never drift from reality by transcription (the failure mode that was
caught live: a guessed `Files: 523` vs the real `2614`).

| log | shape | extractor result |
|---|---|---|
| `01-vitest-single.log` | vitest, one package, green | tests → **730** (not 778; excludes `Test Files`) |
| `04-tsc-silent.log` | clean `tsc --noEmit` (silent) | types → **0** (no scope signal → unproven) |
| `05-tsc-extdiag-files2614.log` | `tsc --extendedDiagnostics` | types → **2614** (real files-checked → proven) |
| `06-eslint-silent.log` | clean `eslint` (silent) | lint → **0** (unproven) |
| `08-turbo-lint-cache.log` | turbo cache replay (`>>> FULL TURBO`) | lint → **0** (banner is never evidence) |
| `09-contracts-check.log` | a `contracts:check` script | tests → **0** (kind mismatch, not a gap) |

Kept **local only** (multi-MB, not committed — regenerable from adopter):
`02-vitest-multi.log` (full `pnpm -r test`, → 1655), `03-tsc-r-real-errors.log`,
`07-eslint-json-filepath188.log` (eslint `-f json`, → 188 filePaths).
