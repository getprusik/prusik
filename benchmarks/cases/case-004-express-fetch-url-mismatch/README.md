# case-004 — JS/TS fetch-URL ↔ Express-route-mount

**Surfaced in**: cross-stack-parity calibration (v0.22.0 design).
First corpus case exercising the JS/TS extractor pair. The defect
class — JSX fetches `/path` but Express mounts the router under a
DIFFERENT prefix — is exactly DEV-1 root #1 on the JS side.

**Defect class**: identical to case-001 in mechanism; different stack
in surface. JSX `fetch('/clients/search')`; Express has
`app.use('/invoices', clientsRouter)` and `clientsRouter.get('/clients/search', ...)`.
Browser URL is `/invoices/clients/search`; the bare `/clients/search`
404s.

**Prusik check that catches it**: `prusik gate check-bindings` v0.22.0,
fetch_url class via the JS extractor path. With touched-set covering
both the route file AND the JSX component, the cross-checker resolves
the Express `app.use` prefix and flags the mismatch.
