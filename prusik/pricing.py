"""Pricing: what measured usage cost, from the one declared table (fb-1d6f8de5b8d3).

`prusik/prices.json` is the only place a price lives: USD per million tokens, per model id,
for input, output, 5-minute cache writes, 1-hour cache writes and cache reads, with the
official pricing page it was read from and the date. A model that is not in the table has no
price: pricing it raises `UnpricedModel`, never zero and never a neighbour's price, so a cap
is never compared against a number nobody could read.

The cap is API-list-price equivalent. It is not a claude.ai plan limit.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PRICE_CATEGORIES = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
_TABLE = Path(__file__).parent / "prices.json"
LABEL = "API list price"

# The replacement for each retired token key, named in the refusal.
_REPLACEMENT = {"budget_tokens": "budget_usd", "session_budget_tokens": "session_budget_usd"}


class UnpricedModel(Exception):
    """A usage row names a model with no entry in prices.json."""

    def __init__(self, model: str):
        super().__init__(f"no price for model {model!r} in prusik/prices.json "
                         f"(a model that is not in the table is never priced by guess)")
        self.model = model


@lru_cache(maxsize=1)
def table() -> dict:
    """The parsed price table. Raises if it is missing or malformed: a broken table must be
    loud (callers turn it into an unmeasured cost), never an empty one."""
    data = json.loads(_TABLE.read_text())
    models = data["models"]
    for mid, entry in models.items():
        missing = [c for c in PRICE_CATEGORIES if not isinstance(entry.get(c), (int, float))]
        if missing:
            raise ValueError(f"prices.json: {mid} has no numeric price for {missing}")
    return data


def _rates(entry: dict, prompt_tokens: int) -> dict:
    """A model's per-million rates for a request of `prompt_tokens` (input + all cache writes
    + cache reads): its long-context rates when over the threshold."""
    long = entry.get("long_context")
    if long and prompt_tokens > long["over_prompt_tokens"]:
        return long
    return entry


def input_price_per_token(model: str, *, prompt_tokens: int) -> float:
    """USD per input token for one response of `model` with a prompt of `prompt_tokens`: the
    unit that makes a row's cost an input-equivalent token count. Same table and long-context
    rule as `price_row` (one source of truth, prices.json)."""
    entry = table()["models"].get(model)
    if entry is None:
        raise UnpricedModel(model)
    return _rates(entry, prompt_tokens)["input"] / 1_000_000


def price_row(model: str, *, input: int, output: int, cache_write: int,  # noqa: A002
              cache_write_5m: int, cache_write_1h: int, cache_read: int) -> dict[str, float]:
    """USD per category for one response. Writes with a 5m/1h split are priced per TTL; the
    part of `cache_write` the split does not account for is priced at the 1h rate, the
    conservative upper bound. A model with `long_context` pays those rates when the
    request's prompt (input + all cache writes + cache reads) is over its threshold."""
    entry = table()["models"].get(model)
    if entry is None:
        raise UnpricedModel(model)
    rates = _rates(entry, input + cache_write + cache_read)
    w1 = cache_write_1h + max(0, cache_write - cache_write_5m - cache_write_1h)
    counts = {"input": input, "output": output, "cache_write_5m": cache_write_5m,
              "cache_write_1h": w1, "cache_read": cache_read}
    return {c: counts[c] * rates[c] / 1_000_000 for c in PRICE_CATEGORIES}


def unsplit_writes(cache_write: int, cache_write_5m: int, cache_write_1h: int) -> int:
    """Cache-write tokens the transcript gave no 5m/1h split for."""
    return max(0, cache_write - cache_write_5m - cache_write_1h)


def legacy_budget_keys(config: object, where: str = ".claude/sprint-config.yaml") -> list[str]:
    """One message per leftover `budget_tokens` / `session_budget_tokens` key in a sprint
    config (the drive block and every phase). Nothing is converted: a token count has no
    single price, so the operator picks the dollar cap."""
    found: list[tuple[str, str]] = []
    if isinstance(config, dict):
        drive = config.get("drive")
        if isinstance(drive, dict):
            found += [(f"drive.{k}", k) for k in _REPLACEMENT if k in drive]
        for ph in config.get("phases") or []:
            if isinstance(ph, dict) and "budget_tokens" in ph:
                found.append((f"phases[{ph.get('name')}].budget_tokens", "budget_tokens"))
    return [f"{where}: {path} is retired; budgets are now in USD ({LABEL}). Replace it with "
            f"{path[:-len(k)]}{_REPLACEMENT[k]} (a dollar amount). Nothing is converted "
            f"automatically, because a token count has no single price."
            for path, k in found]
