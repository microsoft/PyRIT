# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# ruff: noqa: E402

import math

import pytest

pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.progressive_schedule",
    reason="GCG optional dependencies not installed",
)

from pyrit.executor.promptgen.gcg.attack.base.progressive_schedule import (
    ProgressiveScheduleController,
    ScheduleTransitionAction,
)


class TestProgressiveScheduleControllerValidation:
    """Tests input validation for ProgressiveScheduleController."""

    def test_raises_on_non_positive_goals(self) -> None:
        with pytest.raises(ValueError, match="total_goals must be positive"):
            ProgressiveScheduleController(total_goals=0, total_workers=2)

        with pytest.raises(ValueError, match="total_goals must be positive"):
            ProgressiveScheduleController(total_goals=-1, total_workers=2)

    def test_raises_on_non_positive_workers(self) -> None:
        with pytest.raises(ValueError, match="total_workers must be positive"):
            ProgressiveScheduleController(total_goals=2, total_workers=0)

        with pytest.raises(ValueError, match="total_workers must be positive"):
            ProgressiveScheduleController(total_goals=2, total_workers=-5)


class TestProgressiveScheduleControllerInit:
    """Tests initialization and state configuration of ProgressiveScheduleController."""

    def test_initial_state_progressive_both(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=3,
            total_workers=2,
            progressive_goals=True,
            progressive_models=True,
            n_steps=100,
        )
        assert controller.active_goal_count == 1
        assert controller.active_worker_count == 1
        assert controller.state.stop_inner_on_success is True
        assert controller.remaining_steps == 100
        assert controller.is_complete is False
        assert controller.is_fully_admitted is False

    def test_initial_state_progressive_goals_only(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=3,
            total_workers=2,
            progressive_goals=True,
            progressive_models=False,
            n_steps=50,
        )
        assert controller.active_goal_count == 1
        assert controller.active_worker_count == 2
        assert controller.state.stop_inner_on_success is True
        assert controller.is_fully_admitted is False

    def test_initial_state_progressive_models_only(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=3,
            total_workers=2,
            progressive_goals=False,
            progressive_models=True,
            n_steps=50,
        )
        assert controller.active_goal_count == 3
        assert controller.active_worker_count == 1
        assert controller.state.stop_inner_on_success is False
        assert controller.is_fully_admitted is False

    def test_initial_state_no_progressive(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=3,
            total_workers=2,
            progressive_goals=False,
            progressive_models=False,
            n_steps=50,
        )
        assert controller.active_goal_count == 3
        assert controller.active_worker_count == 2
        assert controller.state.stop_inner_on_success is False
        assert controller.is_fully_admitted is True


class TestProgressiveScheduleControllerTransitions:
    """Tests state transitions and admission sequencing in ProgressiveScheduleController."""

    def test_goal_admission_before_worker_admission(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=2,
            total_workers=2,
            progressive_goals=True,
            progressive_models=True,
            n_steps=10,
        )
        assert controller.active_goal_count == 1
        assert controller.active_worker_count == 1

        # First inner run completes (budget remaining)
        action = controller.advance_after_inner_run(inner_loss=0.5, inner_steps=2)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.active_goal_count == 2
        assert controller.active_worker_count == 1
        assert math.isinf(controller.state.loss)

        # Second inner run completes (all goals admitted, worker should be admitted next)
        action = controller.advance_after_inner_run(inner_loss=0.4, inner_steps=2)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.active_goal_count == 2
        assert controller.active_worker_count == 2
        assert controller.is_fully_admitted is True

    def test_finalize_and_stop_action_when_fully_admitted_and_stop_on_success(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            progressive_goals=True,
            progressive_models=True,
            n_steps=10,
            stop_on_success=True,
        )
        action = controller.advance_after_inner_run(inner_loss=0.1, inner_steps=3)
        assert action == ScheduleTransitionAction.FINALIZE_AND_STOP
        assert controller.state.steps_completed == 3
        assert controller.state.loss == 0.1

    def test_control_weight_ratchet_increments_and_resets_loss(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            progressive_goals=False,
            progressive_models=False,
            n_steps=10,
            control_weight=0.05,
            incr_control=True,
            stop_on_success=False,
        )
        action = controller.advance_after_inner_run(inner_loss=0.8, inner_steps=2)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.control_weight == pytest.approx(0.06)
        assert math.isinf(controller.state.loss)

    def test_control_weight_above_threshold_disables_stop_inner_on_success(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            progressive_goals=False,
            progressive_models=False,
            n_steps=10,
            control_weight=0.10,
            incr_control=True,
            stop_on_success=False,
        )
        controller.state.stop_inner_on_success = True
        action = controller.advance_after_inner_run(inner_loss=0.8, inner_steps=2)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.control_weight == 0.10
        assert controller.state.stop_inner_on_success is False

    def test_exact_budget_exhaustion_on_goal_boundary_skips_admission(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=2,
            total_workers=1,
            progressive_goals=True,
            progressive_models=True,
            n_steps=3,
        )
        action = controller.advance_after_inner_run(inner_loss=0.75, inner_steps=3)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.is_complete is True
        assert controller.active_goal_count == 1
        assert controller.state.loss == 0.75

    def test_exact_budget_exhaustion_on_worker_boundary_skips_admission(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=2,
            progressive_goals=False,
            progressive_models=True,
            n_steps=3,
        )
        action = controller.advance_after_inner_run(inner_loss=0.6, inner_steps=3)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.is_complete is True
        assert controller.active_worker_count == 1
        assert controller.state.loss == 0.6

    def test_exact_budget_exhaustion_on_control_weight_skips_ratchet(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            progressive_goals=False,
            progressive_models=False,
            n_steps=3,
            control_weight=0.05,
            incr_control=True,
            stop_on_success=False,
        )
        action = controller.advance_after_inner_run(inner_loss=0.8, inner_steps=3)
        assert action == ScheduleTransitionAction.CONTINUE
        assert controller.is_complete is True
        assert controller.control_weight == 0.05
        assert controller.state.loss == 0.8

    def test_non_finite_inner_loss_is_recorded_and_validated(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            n_steps=5,
            stop_on_success=False,
        )
        controller.advance_after_inner_run(inner_loss=float("inf"), inner_steps=5)
        assert controller.state.loss == float("inf")
        # Should not raise AssertionError:
        controller.validate_post_run()

    def test_validate_post_run_raises_when_steps_completed_without_measured_loss(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            n_steps=5,
        )
        controller.state.steps_completed = 3
        with pytest.raises(AssertionError, match="schedule.loss was never updated"):
            controller.validate_post_run()

    def test_before_inner_run_disables_stop_inner_on_success_when_fully_admitted(self) -> None:
        controller = ProgressiveScheduleController(
            total_goals=1,
            total_workers=1,
            progressive_goals=True,
            progressive_models=True,
            n_steps=10,
        )
        controller.state.stop_inner_on_success = True
        controller.before_inner_run()
        assert controller.state.stop_inner_on_success is False
