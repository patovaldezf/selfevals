"""Cost model — turn token usage into USD using each lab's real pricing schema.

The labs do not expose a runtime pricing API, so selfevals models the *schema*
(input/output per-million-token rates, cache read/write multipliers, a batch
discount, and a context-tier extension point) and seeds a table with
publicly-known rates. Every entry carries an `as_of_date` and a `source` so a
consumer can tell how stale a number is and override it.

The framework is agnostic and never fabricates a price: a model that is not in
the table yields no cost (and a one-time warning), rather than a guessed number.

Usage:

    from selfevals.runner.pricing import estimate_cost
    cost = estimate_cost("anthropic", "claude-sonnet-4-6", tokens)
    # cost is a CostBreakdown, or None if the model is unknown.

Override / extend the default table:

    from selfevals.runner.pricing import DEFAULT_PRICE_TABLE, ModelPricing
    DEFAULT_PRICE_TABLE.register(
        "anthropic", "my-finetune",
        ModelPricing(input_per_mtok=5.0, output_per_mtok=15.0,
                     as_of_date="2026-05", source="internal"),
    )
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from selfevals.schemas.trace import CostBreakdown, TokenBreakdown

_PER_MTOK = 1_000_000.0


@dataclass(frozen=True)
class ModelPricing:
    """Pricing schema for one model.

    Rates are USD per 1,000,000 tokens. The cache multipliers are applied to
    the input rate: a cache *read* is cheaper than fresh input (Anthropic 0.1x,
    OpenAI ~0.5x cached input), a cache *write* carries a premium on Anthropic
    (1.25x) and is not a separate line item on OpenAI (1.0x).
    """

    input_per_mtok: float
    output_per_mtok: float
    cache_read_multiplier: float = 1.0
    cache_write_multiplier: float = 1.0
    batch_multiplier: float = 1.0
    """Multiplier applied to input+output when the call ran on the batch tier
    (Anthropic/OpenAI both bill batch at 0.5x)."""

    as_of_date: str = ""
    source: str = ""
    context_tiers: dict[str, ModelPricing] = field(default_factory=dict)
    """Extension point for context-window-tier pricing (e.g. Anthropic's
    >200K-token premium). Seeded empty; a consumer can register a tier and the
    caller picks it explicitly. Not auto-selected here — selfevals does not see
    the prompt length at pricing time."""


def price_call(
    tokens: TokenBreakdown,
    pricing: ModelPricing,
    *,
    batch: bool = False,
) -> CostBreakdown:
    """Price one call's token breakdown against a model's pricing schema.

    Faithful to the published schema:

    - fresh input billed at the input rate,
    - cache reads at input rate x cache_read_multiplier,
    - cache writes at input rate x cache_write_multiplier,
    - output (plus reasoning tokens, billed as output by provider convention)
      at the output rate,
    - the batch discount, when applicable, scales every component.
    """
    batch_factor = pricing.batch_multiplier if batch else 1.0
    input_rate = pricing.input_per_mtok / _PER_MTOK
    output_rate = pricing.output_per_mtok / _PER_MTOK

    input_cost = tokens.input * input_rate * batch_factor
    cache_read_cost = (
        tokens.input_cache_read * input_rate * pricing.cache_read_multiplier * batch_factor
    )
    cache_creation_cost = (
        tokens.input_cache_creation * input_rate * pricing.cache_write_multiplier * batch_factor
    )
    output_cost = (tokens.output + tokens.reasoning) * output_rate * batch_factor
    total = input_cost + cache_read_cost + cache_creation_cost + output_cost
    return CostBreakdown(
        input=input_cost,
        cache_read=cache_read_cost,
        cache_creation=cache_creation_cost,
        output=output_cost,
        total=total,
    )


def _normalize(name: str) -> str:
    """Normalize a model name for table lookup.

    Lowercases and strips a trailing date pin (e.g. `-20251001`) and a trailing
    `:`-style version pin so that `claude-sonnet-4-6` and
    `claude-sonnet-4-6-20260101` resolve to the same entry. Conservative — it
    only trims an all-digit tail segment.
    """
    base = name.strip().lower()
    base = base.split(":", 1)[0]
    parts = base.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 6:
        base = parts[0]
    return base


class PriceTable:
    """A (provider, model) → ModelPricing lookup with override support."""

    def __init__(self, prices: dict[tuple[str, str], ModelPricing] | None = None) -> None:
        self._prices: dict[tuple[str, str], ModelPricing] = dict(prices or {})
        self._warned_unknown: set[tuple[str, str]] = set()

    def register(self, provider: str, model: str, pricing: ModelPricing) -> None:
        """Add or override pricing for a model."""
        self._prices[(provider.strip().lower(), _normalize(model))] = pricing

    def lookup(self, provider: str, model: str) -> ModelPricing | None:
        return self._prices.get((provider.strip().lower(), _normalize(model)))

    def estimate(
        self,
        provider: str,
        model: str,
        tokens: TokenBreakdown,
        *,
        batch: bool = False,
    ) -> CostBreakdown | None:
        """Price a call, or return None (with a one-time warning) if unknown.

        Never fabricates a price for a model that is not in the table.
        """
        pricing = self.lookup(provider, model)
        if pricing is None:
            key = (provider.strip().lower(), _normalize(model))
            if key not in self._warned_unknown:
                self._warned_unknown.add(key)
                warnings.warn(
                    f"no pricing for {provider}/{model}; reporting $0 cost for this "
                    f"model. Register it via DEFAULT_PRICE_TABLE.register(...) to "
                    f"get real costs.",
                    stacklevel=2,
                )
            return None
        return price_call(tokens, pricing, batch=batch)


# Publicly-known list prices as of 2026-01, USD per 1M tokens. These are the
# common current models; extend the table with `.register(...)` for others or
# to override when prices change. Anthropic: cache read 0.1x, cache write 1.25x.
# OpenAI: cached input ~0.5x, no separate cache-write line (1.0x). Both bill the
# batch tier at 0.5x.
_ANTHROPIC_SRC = "https://www.anthropic.com/pricing (as of 2026-01)"
_OPENAI_SRC = "https://openai.com/api/pricing (as of 2026-01)"


def _anthropic(input_rate: float, output_rate: float) -> ModelPricing:
    return ModelPricing(
        input_per_mtok=input_rate,
        output_per_mtok=output_rate,
        cache_read_multiplier=0.1,
        cache_write_multiplier=1.25,
        batch_multiplier=0.5,
        as_of_date="2026-01",
        source=_ANTHROPIC_SRC,
    )


def _openai(input_rate: float, output_rate: float, *, cached_input_rate: float) -> ModelPricing:
    return ModelPricing(
        input_per_mtok=input_rate,
        output_per_mtok=output_rate,
        cache_read_multiplier=(cached_input_rate / input_rate) if input_rate else 1.0,
        cache_write_multiplier=1.0,
        batch_multiplier=0.5,
        as_of_date="2026-01",
        source=_OPENAI_SRC,
    )


_DEFAULT_PRICES: dict[tuple[str, str], ModelPricing] = {
    # Anthropic Claude family.
    ("anthropic", "claude-opus-4-1"): _anthropic(15.0, 75.0),
    ("anthropic", "claude-opus-4"): _anthropic(15.0, 75.0),
    ("anthropic", "claude-sonnet-4-6"): _anthropic(3.0, 15.0),
    ("anthropic", "claude-sonnet-4-5"): _anthropic(3.0, 15.0),
    ("anthropic", "claude-sonnet-4"): _anthropic(3.0, 15.0),
    ("anthropic", "claude-haiku-4-5"): _anthropic(1.0, 5.0),
    ("anthropic", "claude-3-5-haiku"): _anthropic(0.80, 4.0),
    # OpenAI GPT / o-series family.
    ("openai", "gpt-4o"): _openai(2.5, 10.0, cached_input_rate=1.25),
    ("openai", "gpt-4o-mini"): _openai(0.15, 0.60, cached_input_rate=0.075),
    ("openai", "gpt-4.1"): _openai(2.0, 8.0, cached_input_rate=0.50),
    ("openai", "gpt-4.1-mini"): _openai(0.40, 1.60, cached_input_rate=0.10),
    ("openai", "o3"): _openai(2.0, 8.0, cached_input_rate=0.50),
    ("openai", "o4-mini"): _openai(1.10, 4.40, cached_input_rate=0.275),
}

DEFAULT_PRICE_TABLE = PriceTable(_DEFAULT_PRICES)


def estimate_cost(
    provider: str,
    model: str,
    tokens: TokenBreakdown,
    *,
    table: PriceTable | None = None,
    batch: bool = False,
) -> CostBreakdown | None:
    """Estimate a call's cost against the default (or a supplied) price table.

    Returns a `CostBreakdown`, or `None` when the model is unknown (a one-time
    warning is emitted). Never fabricates a price.
    """
    return (table or DEFAULT_PRICE_TABLE).estimate(provider, model, tokens, batch=batch)
