# Example: greenfield adoption

A fresh project demonstrating `prusik init` followed by a full sprint flow.

## Setup

```bash
# from repo root
pip install -e .

# in this example dir
cd examples/greenfield
prusik init --conventions ~/workspace/python/best-practices
```

## Run a sample sprint

The example ships a pre-authored brief at `briefs/taskping-mvp.md` so you can walk the full flow without authoring.

```bash
prusik discovery all
prusik gate sprint-start taskping-mvp     # enters scoping phase
# (a scoping role would produce design/taskping-mvp/scope.md here)
prusik gate advance triage --feature taskping-mvp
prusik triage --feature taskping-mvp      # pure-code; produces decisions/taskping-mvp.json
# follow-on phases: planning → building → reviewing → integrating
prusik status
prusik digest
```

The sample brief is deliberately minimal — five fields total. Everything else is derived.
