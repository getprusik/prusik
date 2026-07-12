# case-001 — DEV-1 root #1: fetch-URL ↔ route-path

**Surfaced in**: adopter m4-dev1-jit-inline-client-fix (DEV-1, 2026-05-18).
The clean v0.11 verdict trial that established F is field-exercised
also surfaced this assertion-depth gap as the actual root mechanism.

**Defect class**: template references a URL but the route is registered
on a router with a `prefix=`. The HTTP-accessible path is
`prefix + path`, not just `path`. Unit tests on the handler pass; the
integrated browser request 404s.

**Prusik check that catches it**: `prusik gate check-bindings`, the
`fetch_url` class (v0.19.0). With `--touched-set` covering both the
route file AND the template, the cross-checker resolves the router
prefix and flags the mismatch.

**Trial reference**: DEV-1's `/clients/search` template fetch vs
`@invoices_router.get("/clients/search")` on router with
`prefix="/invoices"` → actual `/invoices/clients/search` in browser
→ 404. The manually-hardened canary caught it; v0.19.0 mechanizes the
detection.
