"""read_all() is memoized on (size, mtime) so the 27 history-mining call-sites don't
re-parse the whole unbounded ledger on every call. The cache MUST stay correct: an append
(this process or another) has to be visible on the next read, and a caller mutating the
returned list must not poison the cache. Adversarial coverage — a cache that served stale
events would silently corrupt catch-quality / convergence / fix-round decisions.
"""

from __future__ import annotations

from prusik import ledger


def test_append_is_visible_on_next_read(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert ledger.read_all() == []
    ledger.append("first", n=1)
    got = ledger.read_all()
    assert [r["event"] for r in got] == ["first"]
    # a SECOND append must invalidate the cache (size grew) — the new event is seen
    ledger.append("second", n=2)
    assert [r["event"] for r in ledger.read_all()] == ["first", "second"]


def test_external_append_invalidates_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    ledger.append("seed", n=0)
    assert len(ledger.read_all()) == 1                  # primes the cache
    # simulate another process appending directly to the file
    path = ledger.ledger_path()
    with open(path, "a") as f:
        f.write('{"event": "external", "ts": "x"}\n')
    assert [r["event"] for r in ledger.read_all()] == ["seed", "external"]


def test_mutating_the_result_does_not_poison_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    ledger.append("e", n=1)
    first = ledger.read_all()
    first.append({"event": "INJECTED"})                  # caller mutates the returned list
    first.clear()                                        # ... aggressively
    again = ledger.read_all()                            # cache must be untouched
    assert [r["event"] for r in again] == ["e"]
