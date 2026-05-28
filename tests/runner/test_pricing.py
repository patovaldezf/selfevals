from __future__ import annotations

import pytest

from selfevals.runner.pricing import (
    DEFAULT_PRICE_TABLE,
    ModelPricing,
    PriceTable,
    estimate_cost,
    price_call,
)
from selfevals.schemas.trace import TokenBreakdown


def _tokens(**kw: int) -> TokenBreakdown:
    base = {
        "input": 0,
        "input_cache_read": 0,
        "input_cache_creation": 0,
        "output": 0,
        "reasoning": 0,
    }
    base.update(kw)
    base["total"] = sum(base.values())
    return TokenBreakdown(**base)


def test_input_and_output_priced_per_million() -> None:
    pricing = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0)
    cost = price_call(_tokens(input=1_000_000, output=1_000_000), pricing)
    assert cost.input == pytest.approx(3.0)
    assert cost.output == pytest.approx(15.0)
    assert cost.total == pytest.approx(18.0)


def test_cache_read_multiplier() -> None:
    pricing = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0, cache_read_multiplier=0.1)
    cost = price_call(_tokens(input_cache_read=1_000_000), pricing)
    # cache read = input rate x 0.1
    assert cost.cache_read == pytest.approx(0.3)
    assert cost.input == pytest.approx(0.0)
    assert cost.total == pytest.approx(0.3)


def test_cache_write_multiplier() -> None:
    pricing = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0, cache_write_multiplier=1.25)
    cost = price_call(_tokens(input_cache_creation=1_000_000), pricing)
    # cache write = input rate x 1.25
    assert cost.cache_creation == pytest.approx(3.75)
    assert cost.total == pytest.approx(3.75)


def test_reasoning_billed_as_output() -> None:
    pricing = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0)
    cost = price_call(_tokens(reasoning=1_000_000), pricing)
    assert cost.output == pytest.approx(15.0)


def test_batch_multiplier_halves_cost() -> None:
    pricing = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0, batch_multiplier=0.5)
    full = price_call(_tokens(input=1_000_000, output=1_000_000), pricing)
    batched = price_call(_tokens(input=1_000_000, output=1_000_000), pricing, batch=True)
    assert batched.total == pytest.approx(full.total * 0.5)


def test_unknown_model_returns_none_and_warns() -> None:
    table = PriceTable()  # empty
    with pytest.warns(UserWarning, match="no pricing"):
        result = table.estimate("anthropic", "totally-made-up-model", _tokens(input=100))
    assert result is None


def test_unknown_model_warns_only_once() -> None:
    table = PriceTable()
    with pytest.warns(UserWarning):
        table.estimate("openai", "ghost", _tokens(input=10))
    # Second call for the same model must not warn again.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert table.estimate("openai", "ghost", _tokens(input=10)) is None


def test_register_override() -> None:
    table = PriceTable()
    table.register("acme", "model-x", ModelPricing(input_per_mtok=10.0, output_per_mtok=20.0))
    cost = table.estimate("acme", "model-x", _tokens(input=1_000_000))
    assert cost is not None
    assert cost.total == pytest.approx(10.0)


def test_model_name_normalization_strips_date_pin() -> None:
    table = PriceTable()
    table.register(
        "anthropic", "claude-sonnet-4-6", ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0)
    )
    # A date-pinned name resolves to the same entry.
    cost = table.estimate("anthropic", "claude-sonnet-4-6-20260101", _tokens(input=1_000_000))
    assert cost is not None
    assert cost.total == pytest.approx(3.0)


def test_default_table_has_common_models() -> None:
    cost = estimate_cost(
        "anthropic", "claude-sonnet-4-6", _tokens(input=1_000_000, output=1_000_000)
    )
    assert cost is not None
    # sonnet: $3 in / $15 out per Mtok.
    assert cost.input == pytest.approx(3.0)
    assert cost.output == pytest.approx(15.0)
    pricing = DEFAULT_PRICE_TABLE.lookup("anthropic", "claude-sonnet-4-6")
    assert pricing is not None
    assert pricing.as_of_date == "2026-01"
    assert pricing.source
