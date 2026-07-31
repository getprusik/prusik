# findings/ — prusik feedback ticket store (GIT-TRACKED — commit it)

Each `fb-<id>.json` is a durable ticket: the finding plus its thread, `resolution`,
and an append-only `verify_history`. **Commit these files** — do NOT gitignore them.

Why they must be tracked:
- Closure is DERIVED from `verify_history` (a finding is closed only by a captured
  green verify run). Gitignoring `findings/` loses that history on a fresh clone, so
  closed findings silently reappear as open.
- `prusik update` auto-closes findings whose fix shipped by writing `verify_history`
  here; untracked, those closures never persist or share.

This directory is the SOURCE OF TRUTH. The HQ export (`prusik report --export`) is the
outbox — a derived snapshot; it reads FROM here. Machine-written new tickets showing in
`git status` is signal (a finding was filed), not noise — commit them with the sprint.
