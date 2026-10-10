# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Progressive schedule controller and state models for Greedy Coordinate Gradient (GCG) attacks."""

import logging
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


@dataclass
class ProgressiveScheduleState:
    """
    Typed schedule state for ``ProgressiveMultiPromptAttack``.

    Tracks how many goals and workers have been admitted so far, together with
    the shared step counter and the loss carried between progressive rounds.
    Exposed as ``ProgressiveMultiPromptAttack.last_schedule_state`` after a call
    to ``ProgressiveMultiPromptAttack.run``.
    """

    goals_admitted: int
    workers_admitted: int
    steps_completed: int = 0
    loss: float = float("inf")
    stop_inner_on_success: bool = False


class ScheduleTransitionAction(Enum):
    """Action to take following an inner attack result in progressive scheduling."""

    CONTINUE = auto()
    FINALIZE_AND_STOP = auto()


class ProgressiveScheduleController:
    """Encapsulates progressive admission, step budget scheduling, and state transitions."""

    def __init__(
        self,
        *,
        total_goals: int,
        total_workers: int,
        progressive_goals: bool = True,
        progressive_models: bool = True,
        n_steps: int = 1000,
        control_weight: float | None = None,
        incr_control: bool = True,
        stop_on_success: bool = True,
        verbose: bool = True,
    ) -> None:
        """
        Initialize the progressive schedule controller.

        Args:
            total_goals: Total number of attack goals available for admission.
            total_workers: Total number of model workers available for admission.
            progressive_goals: Whether goals are admitted progressively one at a time.
            progressive_models: Whether models/workers are admitted progressively one at a time.
            n_steps: Total step budget across all progressive rounds.
            control_weight: Current control weight, or None.
            incr_control: Whether to increment control weight when all goals/models are admitted.
            stop_on_success: Whether to finalize and stop when fully admitted and success is achieved.
            verbose: Whether to log control weight updates.

        Raises:
            ValueError: If total_goals or total_workers is not positive.
        """
        if total_goals <= 0:
            raise ValueError(f"total_goals must be positive, got {total_goals}")
        if total_workers <= 0:
            raise ValueError(f"total_workers must be positive, got {total_workers}")

        self._total_goals = total_goals
        self._total_workers = total_workers
        self._n_steps = n_steps
        self._control_weight = control_weight
        self._incr_control = incr_control
        self._stop_on_success = stop_on_success
        self._verbose = verbose

        self._state = ProgressiveScheduleState(
            goals_admitted=1 if progressive_goals else total_goals,
            workers_admitted=1 if progressive_models else total_workers,
            stop_inner_on_success=progressive_goals,
        )
        self._loss_is_measured = False

    @property
    def state(self) -> ProgressiveScheduleState:
        """The current progressive schedule state."""
        return self._state

    @property
    def is_complete(self) -> bool:
        """Whether the overall step budget has been exhausted."""
        return self._state.steps_completed >= self._n_steps

    @property
    def remaining_steps(self) -> int:
        """The remaining number of optimization steps in the budget."""
        return max(0, self._n_steps - self._state.steps_completed)

    @property
    def active_goal_count(self) -> int:
        """The number of currently admitted goals."""
        return self._state.goals_admitted

    @property
    def active_worker_count(self) -> int:
        """The number of currently admitted workers."""
        return self._state.workers_admitted

    @property
    def control_weight(self) -> float | None:
        """The current control weight."""
        return self._control_weight

    @property
    def is_fully_admitted(self) -> bool:
        """Whether all goals and workers have been admitted."""
        return self._state.goals_admitted == self._total_goals and self._state.workers_admitted == self._total_workers

    def before_inner_run(self) -> None:
        """Prepare schedule state immediately before launching an inner attack."""
        if self.is_fully_admitted:
            self._state.stop_inner_on_success = False

    def advance_after_inner_run(
        self,
        *,
        inner_loss: float,
        inner_steps: int,
    ) -> ScheduleTransitionAction:
        """
        Update schedule state and determine the next action after an inner attack round completes.

        Args:
            inner_loss: Final loss reported by the inner attack.
            inner_steps: Number of steps completed by the inner attack.

        Returns:
            ScheduleTransitionAction indicating whether to continue or finalize and stop.
        """
        self._state.loss = inner_loss
        self._loss_is_measured = True
        self._state.steps_completed += inner_steps

        prepare_next_round = self._state.steps_completed < self._n_steps

        if self._state.goals_admitted < self._total_goals:
            if prepare_next_round:
                self._state.goals_admitted += 1
                self._state.loss = float("inf")
                self._loss_is_measured = False
            return ScheduleTransitionAction.CONTINUE

        if self._state.workers_admitted < self._total_workers:
            if prepare_next_round:
                self._state.workers_admitted += 1
                self._state.loss = float("inf")
                self._loss_is_measured = False
            return ScheduleTransitionAction.CONTINUE

        if self._state.workers_admitted == self._total_workers and self._stop_on_success:
            return ScheduleTransitionAction.FINALIZE_AND_STOP

        if prepare_next_round and isinstance(self._control_weight, (int, float)) and self._incr_control:
            if self._control_weight <= 0.09:
                self._control_weight += 0.01
                self._state.loss = float("inf")
                self._loss_is_measured = False
                if self._verbose:
                    logger.info(f"Control weight increased to {self._control_weight:.5}")
            else:
                self._state.stop_inner_on_success = False

        return ScheduleTransitionAction.CONTINUE

    def validate_post_run(self) -> None:
        """
        Validate post-run invariants.

        Raises:
            AssertionError: If steps were completed but schedule.loss was never updated by an inner run.
        """
        if self._state.steps_completed > 0:
            assert self._loss_is_measured, "schedule.loss was never updated by the inner run"
