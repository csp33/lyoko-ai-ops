"""Unit tests for ModelCostEstimator in infrastructure/llm layer."""

from unittest.mock import MagicMock

import pytest
from lyoko.infrastructure.llm.cost_estimator import ModelCostEstimator


def test_estimates_mini_tier_pricing():
    estimator = ModelCostEstimator()
    # 1000 input tokens = $0.00015, 500 output tokens = $0.00030 -> $0.00045
    cost = estimator.estimate_cost_usd("gpt-4o-mini", 1000, 500)
    assert pytest.approx(cost, rel=1e-5) == 0.00045


def test_estimates_standard_4o_pricing():
    estimator = ModelCostEstimator()
    # 1000 input tokens = $0.0025, 500 output tokens = $0.0050 -> $0.0075
    cost = estimator.estimate_cost_usd("gpt-4o", 1000, 500)
    assert pytest.approx(cost, rel=1e-5) == 0.0075


def test_estimates_fallback_pricing():
    estimator = ModelCostEstimator()
    cost = estimator.estimate_cost_usd("gpt-4-turbo", 1000, 500)
    assert pytest.approx(cost, rel=1e-5) == 0.0045


def test_matches_dated_model_snapshots():
    estimator = ModelCostEstimator()
    cost = estimator.estimate_cost_usd("gpt-4o-mini-2024-07-18", 1000, 500)
    assert pytest.approx(cost, rel=1e-5) == 0.00045


def test_custom_injected_pricing_table():
    from lyoko.infrastructure.llm.cost_estimator import ModelPricing

    custom_table = {
        "deepseek-chat": ModelPricing(input_usd_per_1m=0.14, output_usd_per_1m=0.28),
    }
    estimator = ModelCostEstimator(pricing_table=custom_table)
    cost = estimator.estimate_cost_usd("deepseek-chat", 1000, 1000)
    assert pytest.approx(cost, rel=1e-5) == 0.00042


def test_extracts_usage_from_usage_metadata():
    estimator = ModelCostEstimator()
    msg = MagicMock()
    msg.usage_metadata = {"input_tokens": 120, "output_tokens": 45}
    in_tok, out_tok = estimator.extract_usage(msg)
    assert in_tok == 120
    assert out_tok == 45


def test_extracts_usage_from_response_metadata_fallback():
    estimator = ModelCostEstimator()
    msg = MagicMock()
    msg.usage_metadata = None
    msg.response_metadata = {"token_usage": {"prompt_tokens": 80, "completion_tokens": 30}}
    in_tok, out_tok = estimator.extract_usage(msg)
    assert in_tok == 80
    assert out_tok == 30


def test_extracts_zero_when_no_metadata():
    estimator = ModelCostEstimator()
    msg = MagicMock()
    msg.usage_metadata = None
    msg.response_metadata = None
    in_tok, out_tok = estimator.extract_usage(msg)
    assert in_tok == 0
    assert out_tok == 0
