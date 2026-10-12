"""Unit tests for BudgetGuardService and domain budget models."""

import pytest
from lyoko.application.safety.budget_guard import BudgetGuardService
from lyoko.domain.models.budget import (
    BudgetConfig,
    BudgetTripReason,
    BudgetUsage,
)


def test_budget_usage_accumulation():
    usage = BudgetUsage()
    usage.add_usage(input_tokens=1000, output_tokens=200, cost_usd=0.005)
    assert usage.turns_used == 1
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 200
    assert pytest.approx(usage.accumulated_cost_usd, rel=1e-5) == 0.005

    usage.add_usage(input_tokens=500, output_tokens=100, cost_usd=0.002)
    assert usage.turns_used == 2
    assert usage.input_tokens == 1500
    assert usage.output_tokens == 300
    assert pytest.approx(usage.accumulated_cost_usd, rel=1e-5) == 0.007


def test_budget_evaluation_proceeds_when_under_limits():
    config = BudgetConfig(max_turns=4, max_budget_usd=0.10)
    guard = BudgetGuardService(config)
    usage = BudgetUsage(turns_used=2, accumulated_cost_usd=0.04)

    eval_result = guard.evaluate_turn(usage)
    assert eval_result.can_proceed is True
    assert eval_result.reason is None


def test_budget_evaluation_halts_on_max_turns():
    config = BudgetConfig(max_turns=3, max_budget_usd=0.10)
    guard = BudgetGuardService(config)
    usage = BudgetUsage(turns_used=3, accumulated_cost_usd=0.02)

    eval_result = guard.evaluate_turn(usage)
    assert eval_result.can_proceed is False
    assert eval_result.reason == BudgetTripReason.MAX_TURNS_EXCEEDED
    assert "Turn ceiling reached" in eval_result.detail


def test_budget_evaluation_halts_on_cost_ceiling():
    config = BudgetConfig(max_turns=10, max_budget_usd=0.05)
    guard = BudgetGuardService(config)
    usage = BudgetUsage(turns_used=2, accumulated_cost_usd=0.06)

    eval_result = guard.evaluate_turn(usage)
    assert eval_result.can_proceed is False
    assert eval_result.reason == BudgetTripReason.MAX_BUDGET_EXCEEDED
    assert "Hard budget cap reached" in eval_result.detail


def test_cycle_detection_trips_on_consecutive_identical_tool_calls():
    config = BudgetConfig(cycle_detection_window=2)
    guard = BudgetGuardService(config)

    # First call - allowed
    eval1 = guard.evaluate_tool_call("k8s_get_pods", {"namespace": "default"})
    assert eval1.can_proceed is True

    # Second identical call - allowed as window is 2 and we only have 1 in history
    eval2 = guard.evaluate_tool_call("k8s_get_pods", {"namespace": "default"})
    assert eval2.can_proceed is True

    # Third identical call - window of 2 is filled with identical call!
    eval3 = guard.evaluate_tool_call("k8s_get_pods", {"namespace": "default"})
    assert eval3.can_proceed is False
    assert eval3.reason == BudgetTripReason.CYCLE_DETECTED
    assert "Cycle detected" in eval3.detail


def test_cycle_detection_allows_varying_arguments():
    config = BudgetConfig(cycle_detection_window=2)
    guard = BudgetGuardService(config)

    eval1 = guard.evaluate_tool_call("k8s_get_pods", {"namespace": "default"})
    assert eval1.can_proceed is True

    eval2 = guard.evaluate_tool_call("k8s_get_pods", {"namespace": "monitoring"})
    assert eval2.can_proceed is True

    eval3 = guard.evaluate_tool_call("k8s_get_pods", {"namespace": "kube-system"})
    assert eval3.can_proceed is True


def test_format_termination_instructions():
    guard = BudgetGuardService()
    turn_inst = guard.format_termination_instruction(
        BudgetTripReason.MAX_TURNS_EXCEEDED, "Turn limit reached"
    )
    assert "maximum execution turns" in turn_inst.lower()
    assert "do not call any more tools" in turn_inst.lower()

    budget_inst = guard.format_termination_instruction(
        BudgetTripReason.MAX_BUDGET_EXCEEDED, "Budget reached"
    )
    assert "spend ceiling has been reached" in budget_inst.lower()
