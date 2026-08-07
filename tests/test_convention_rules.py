"""Repo-wide declarative convention sweep (fb-7f0bc1640914).

moat-finding:fb-7f0bc1640914 — 8 pagination-tiebreak violations shipped and
persisted invisibly: the conventions-enforcer gates diffs, so untouched
files were never re-examined. Diff-gating stops NEW debt; only a sweep
retires OLD debt.
"""

from prusik.detectors import convention_rules
from prusik.detectors.base import ScanContext

RULES = """rules:
  - id: no-console-in-services
    description: services use the logger, never console.*
    files: "src/services/**"
    forbid: "console\\\\.(log|warn|error)"
  - id: paginated-order-tiebreak
    description: a LIMIT query file must carry an ORDER BY tiebreak
    files: "src/repo/**"
    pair:
      if_match: "\\\\.limit\\\\("
      must_match: "orderBy"
"""


def _repo(tmp_path, rules=RULES):
    if rules is not None:
        d = tmp_path / ".claude" / "conventions"
        d.mkdir(parents=True)
        (d / "rules.yaml").write_text(rules)
    svc = tmp_path / "src" / "services"
    svc.mkdir(parents=True)
    (svc / "billing.ts").write_text(
        "log.info('x')\nconsole.log('debug')\nconsole.error('y')\n")
    (svc / "clean.ts").write_text("log.info('ok')\n")
    repo = tmp_path / "src" / "repo"
    repo.mkdir(parents=True)
    (repo / "user.ts").write_text("q.limit(50)\n")                 # violation
    (repo / "tx.ts").write_text("q.limit(50).orderBy('id')\n")     # clean
    other = tmp_path / "src" / "other.ts"
    other.write_text("console.log('outside glob — not this rule')\n")
    return ScanContext(root=tmp_path,
                       files=[p for p in tmp_path.rglob("*") if p.is_file()])


def test_forbid_names_each_violating_line(tmp_path):
    fs = convention_rules.detect(_repo(tmp_path))
    hits = [(f.file, f.line) for f in fs if f.cls == "no-console-in-services"]
    assert hits == [("src/services/billing.ts", 2),
                    ("src/services/billing.ts", 3)]


def test_pair_flags_file_missing_counterpart(tmp_path):
    fs = convention_rules.detect(_repo(tmp_path))
    hits = [f.file for f in fs if f.cls == "paginated-order-tiebreak"]
    assert hits == ["src/repo/user.ts"]


def test_glob_scoping_respected(tmp_path):
    fs = convention_rules.detect(_repo(tmp_path))
    assert not any(f.file == "src/other.ts" for f in fs)


def test_absent_rules_file_is_dormant(tmp_path):
    fs = convention_rules.detect(_repo(tmp_path, rules=None))
    assert fs == []


def test_invalid_regex_is_loud_never_skipped(tmp_path):
    bad = 'rules:\n  - id: broken\n    files: "**"\n    forbid: "([unclosed"\n'
    fs = convention_rules.detect(_repo(tmp_path, rules=bad))
    assert any(f.cls == "invalid-rule" and "broken" in f.message for f in fs)


def test_rule_missing_fields_is_loud(tmp_path):
    bad = 'rules:\n  - id: half\n    files: "**"\n'
    fs = convention_rules.detect(_repo(tmp_path, rules=bad))
    assert any(f.cls == "invalid-rule" and "half" in f.message for f in fs)
