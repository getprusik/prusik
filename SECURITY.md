# Security

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/getprusik/prusik/security/advisories/new),
not in a public issue.

Include the affected version, how to reproduce it, and the impact you expect. Prusik is an early
project with no support SLA. Reports are handled on a best-effort basis.

## Supported versions

Security fixes land in the latest release on [PyPI](https://pypi.org/project/prusik/). Older
versions are not patched, so upgrade with `prusik update`.

## What prusik does with your data

Prusik runs in your environment. It has no telemetry and no account, and it has no service of its
own to send data to.

- **Local records.** The ledger, findings and reports are plain files in your repository, mostly
  under `.sprint/`.
- **Tamper evidence.** The ledger is append-only by convention, not by cryptography. Anyone with
  write access to the repository can edit it, so treat it as evidence, not as a signed record.
- **Update check.** This is the only network call prusik makes on its own. It reads public GitHub
  and PyPI endpoints at most once a day at sprint start, and also runs in `prusik doctor` and
  `prusik update`. It sends no project data, and it skips silently when offline or blocked. There
  is no setting to turn it off today.
- **Integrations you configure.** The CI Action, issue-tracker sync and `prusik infra-check` reach
  only the services you point them at.
- **Sharing.** Nothing is shared unless you act. An export is anonymized by default and is a file
  you choose what to do with. `feedback_sink` is off by default, and writes anonymized findings
  to a directory you configure.

Prusik runs the commands you give it, and its hooks run on your agent's tool calls, so review its
configuration as you would any other development tool.
