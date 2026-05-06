# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario run service for executing scenarios as background tasks.

Manages the lifecycle of scenario runs: starting, tracking status,
retrieving results, and cancellation.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from pyrit.backend.models.scenarios import (
    RunScenarioRequest,
    ScenarioRunListResponse,
    ScenarioRunResponse,
    ScenarioRunResult,
    ScenarioRunStatus,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_RUNS = 3
MAX_COMPLETED_RUNS = 50


@dataclass
class _RunInfo:
    """Internal tracking state for a scenario run."""

    run_id: str
    request: RunScenarioRequest
    status: ScenarioRunStatus = ScenarioRunStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task: asyncio.Task[None] | None = None
    error: str | None = None
    result: ScenarioRunResult | None = None


class ScenarioRunService:
    """
    Service for managing scenario run lifecycle.

    Runs are tracked in-memory and executed as background asyncio tasks.
    """

    def __init__(self) -> None:
        """Initialize the scenario run service."""
        self._runs: dict[str, _RunInfo] = {}

    async def start_run_async(self, *, request: RunScenarioRequest) -> ScenarioRunResponse:
        """
        Start a new scenario run as a background task.

        Validates inputs synchronously, then spawns an asyncio task for execution.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            ScenarioRunResponse with run_id and PENDING status.

        Raises:
            ValueError: If scenario or target cannot be found, or concurrent limit exceeded.
        """
        # Check concurrent run limit
        active_count = sum(
            1
            for r in self._runs.values()
            if r.status in (ScenarioRunStatus.PENDING, ScenarioRunStatus.INITIALIZING, ScenarioRunStatus.RUNNING)
        )
        if active_count >= MAX_CONCURRENT_RUNS:
            raise ValueError(
                f"Maximum concurrent runs ({MAX_CONCURRENT_RUNS}) reached. "
                "Wait for an existing run to complete or cancel one."
            )

        # Validate scenario exists
        from pyrit.registry import ScenarioRegistry

        scenario_registry = ScenarioRegistry.get_registry_singleton()
        try:
            scenario_registry.get_class(request.scenario_name)
        except KeyError as e:
            raise ValueError(str(e)) from None

        # Create run info
        run_id = str(uuid.uuid4())
        info = _RunInfo(run_id=run_id, request=request)
        self._runs[run_id] = info

        # Evict old completed runs if over limit
        self._evict_completed_runs()

        # Spawn background task
        task = asyncio.create_task(self._execute_run_async(run_id=run_id))
        info.task = task

        return self._to_response(info)

    def get_run(self, *, run_id: str) -> ScenarioRunResponse | None:
        """
        Get the current status of a scenario run.

        Args:
            run_id: The unique run identifier.

        Returns:
            ScenarioRunResponse if found, None otherwise.
        """
        info = self._runs.get(run_id)
        if info is None:
            return None
        return self._to_response(info)

    def list_runs(self) -> ScenarioRunListResponse:
        """
        List all tracked scenario runs (most recent first).

        Returns:
            ScenarioRunListResponse with all runs.
        """
        items = [self._to_response(info) for info in reversed(self._runs.values())]
        return ScenarioRunListResponse(items=items)

    async def cancel_run_async(self, *, run_id: str) -> ScenarioRunResponse | None:
        """
        Cancel a running scenario.

        Args:
            run_id: The unique run identifier.

        Returns:
            Updated ScenarioRunResponse if found, None if run_id not found.

        Raises:
            ValueError: If the run is already in a terminal state.
        """
        info = self._runs.get(run_id)
        if info is None:
            return None

        terminal_states = (ScenarioRunStatus.COMPLETED, ScenarioRunStatus.FAILED, ScenarioRunStatus.CANCELLED)
        if info.status in terminal_states:
            raise ValueError(f"Cannot cancel run in '{info.status}' state.")

        # Cancel the asyncio task
        if info.task is not None and not info.task.done():
            info.task.cancel()

        info.status = ScenarioRunStatus.CANCELLED
        info.updated_at = datetime.now(timezone.utc)
        return self._to_response(info)

    async def _execute_run_async(self, *, run_id: str) -> None:
        """
        Execute a scenario run (background task entry point).

        Mirrors the flow in pyrit.cli.frontend_core.run_scenario_async.

        Args:
            run_id: The run to execute.
        """
        info = self._runs[run_id]
        request = info.request

        try:
            # --- Phase 1: Initialize ---
            info.status = ScenarioRunStatus.INITIALIZING
            info.updated_at = datetime.now(timezone.utc)

            from pyrit.registry import InitializerRegistry, ScenarioRegistry, TargetRegistry
            from pyrit.scenario.core import DatasetConfiguration

            # Run initializers if requested
            if request.initializers:
                initializer_registry = InitializerRegistry.get_registry_singleton()
                for initializer_name in request.initializers:
                    try:
                        initializer_class = initializer_registry.get_class(initializer_name)
                    except KeyError as e:
                        raise ValueError(f"Initializer not found: {e}") from None
                    instance = initializer_class()
                    await instance.initialize_async()

            # Resolve target
            target_registry = TargetRegistry.get_registry_singleton()
            objective_target = target_registry.get_instance_by_name(request.target_name)
            if objective_target is None:
                available_names = target_registry.get_names()
                if not available_names:
                    raise ValueError(
                        f"Target '{request.target_name}' not found. The target registry is empty. "
                        "Make sure to include an initializer that registers targets "
                        "(e.g., initializers: ['target'])."
                    )
                raise ValueError(
                    f"Target '{request.target_name}' not found in registry. "
                    f"Available targets: {', '.join(available_names)}"
                )

            # Resolve scenario class
            scenario_registry = ScenarioRegistry.get_registry_singleton()
            scenario_class = scenario_registry.get_class(request.scenario_name)

            # --- Phase 2: Run ---
            info.status = ScenarioRunStatus.RUNNING
            info.updated_at = datetime.now(timezone.utc)

            # Build init kwargs
            init_kwargs: dict[str, Any] = {
                "objective_target": objective_target,
                "max_concurrency": request.max_concurrency,
                "max_retries": request.max_retries,
            }

            if request.memory_labels:
                init_kwargs["memory_labels"] = request.memory_labels

            # Resolve strategies
            if request.strategies:
                strategy_class = scenario_class.get_strategy_class()
                strategy_enums = []
                for name in request.strategies:
                    try:
                        strategy_enums.append(strategy_class(name))
                    except ValueError:
                        available_strategies = [s.value for s in strategy_class]
                        raise ValueError(
                            f"Strategy '{name}' not found for scenario '{request.scenario_name}'. "
                            f"Available: {', '.join(available_strategies)}"
                        ) from None
                init_kwargs["scenario_strategies"] = strategy_enums

            # Build dataset config
            if request.dataset_names:
                init_kwargs["dataset_config"] = DatasetConfiguration(
                    dataset_names=request.dataset_names,
                    max_dataset_size=request.max_dataset_size,
                )
            elif request.max_dataset_size is not None:
                default_config = scenario_class.default_dataset_config()
                default_config.max_dataset_size = request.max_dataset_size
                init_kwargs["dataset_config"] = default_config

            # Instantiate and execute
            scenario = scenario_class()  # type: ignore[call-arg]
            await scenario.initialize_async(**init_kwargs)
            scenario_result = await scenario.run_async()

            # --- Phase 3: Store result ---
            info.status = ScenarioRunStatus.COMPLETED
            info.updated_at = datetime.now(timezone.utc)
            info.result = ScenarioRunResult(
                scenario_result_id=str(scenario_result.id),
                run_state=scenario_result.scenario_run_state,
                strategies_used=scenario_result.get_strategies_used(),
                total_attacks=len(scenario_result.attack_results),
                completed_attacks=len(scenario_result.attack_results),
                number_tries=scenario_result.number_tries,
                completion_time=scenario_result.completion_time,
            )

        except asyncio.CancelledError:
            info.status = ScenarioRunStatus.CANCELLED
            info.updated_at = datetime.now(timezone.utc)
            logger.info(f"Scenario run {run_id} was cancelled.")

        except Exception as e:
            info.status = ScenarioRunStatus.FAILED
            info.updated_at = datetime.now(timezone.utc)
            info.error = str(e)
            logger.exception(f"Scenario run {run_id} failed: {e}")

    def _evict_completed_runs(self) -> None:
        """Remove oldest completed runs if over the retention limit."""
        terminal_states = (ScenarioRunStatus.COMPLETED, ScenarioRunStatus.FAILED, ScenarioRunStatus.CANCELLED)
        completed = [r for r in self._runs.values() if r.status in terminal_states]
        if len(completed) > MAX_COMPLETED_RUNS:
            # Sort by creation time, remove oldest
            completed.sort(key=lambda r: r.created_at)
            for run_info in completed[: len(completed) - MAX_COMPLETED_RUNS]:
                del self._runs[run_info.run_id]

    def get_run_results(self, *, run_id: str) -> "ScenarioResultDetailResponse | None":
        """
        Get detailed results for a completed scenario run.

        Retrieves the full ScenarioResult from CentralMemory and maps it
        to a detailed response model with per-attack outcomes.

        Args:
            run_id: The unique run identifier.

        Returns:
            ScenarioResultDetailResponse if the run is completed and results exist, None if run not found.

        Raises:
            ValueError: If the run is not in a completed state or results not found in memory.
        """
        from pyrit.backend.models.scenarios import (
            AtomicAttackResults,
            AttackResultDetail,
            ScenarioResultDetailResponse,
        )
        from pyrit.memory import CentralMemory
        from pyrit.models import AttackOutcome

        info = self._runs.get(run_id)
        if info is None:
            return None

        if info.status != ScenarioRunStatus.COMPLETED or info.result is None:
            raise ValueError(
                f"Results are only available for completed runs. Current status: '{info.status}'."
            )

        # Retrieve from CentralMemory
        memory = CentralMemory.get_memory_instance()
        results = memory.get_scenario_results(scenario_result_ids=[info.result.scenario_result_id])
        if not results:
            raise ValueError(
                f"Scenario result '{info.result.scenario_result_id}' not found in memory."
            )

        scenario_result = results[0]
        display_groups = scenario_result.get_display_groups()

        # Build per-attack detail
        attacks: list[AtomicAttackResults] = []
        for attack_name, attack_results in scenario_result.attack_results.items():
            details: list[AttackResultDetail] = []
            success_count = 0
            failure_count = 0

            for ar in attack_results:
                score_value = None
                if ar.last_score is not None:
                    score_value = ar.last_score.get_value()

                last_response_text = None
                if ar.last_response is not None:
                    last_response_text = ar.last_response.value if hasattr(ar.last_response, "value") else str(ar.last_response)

                details.append(
                    AttackResultDetail(
                        attack_result_id=ar.attack_result_id,
                        conversation_id=ar.conversation_id,
                        objective=ar.objective,
                        outcome=ar.outcome.value,
                        outcome_reason=ar.outcome_reason,
                        last_response=last_response_text,
                        score_value=score_value,
                        executed_turns=ar.executed_turns,
                        execution_time_ms=ar.execution_time_ms,
                        timestamp=ar.timestamp,
                    )
                )

                if ar.outcome == AttackOutcome.SUCCESS:
                    success_count += 1
                elif ar.outcome == AttackOutcome.FAILURE:
                    failure_count += 1

            # Find display group for this attack
            display_group = None
            if hasattr(scenario_result, "_display_group_map") and scenario_result._display_group_map:
                display_group = scenario_result._display_group_map.get(attack_name)

            attacks.append(
                AtomicAttackResults(
                    atomic_attack_name=attack_name,
                    display_group=display_group,
                    results=details,
                    success_count=success_count,
                    failure_count=failure_count,
                    total_count=len(details),
                )
            )

        return ScenarioResultDetailResponse(
            scenario_result_id=str(scenario_result.id),
            scenario_name=scenario_result.scenario_identifier.name,
            scenario_version=scenario_result.scenario_identifier.version,
            run_state=scenario_result.scenario_run_state,
            objective_achieved_rate=scenario_result.objective_achieved_rate(),
            number_tries=scenario_result.number_tries,
            completion_time=scenario_result.completion_time,
            labels=scenario_result.labels,
            attacks=attacks,
        )

    @staticmethod
    def _to_response(info: _RunInfo) -> ScenarioRunResponse:
        """Convert internal run info to API response model."""
        return ScenarioRunResponse(
            run_id=info.run_id,
            scenario_name=info.request.scenario_name,
            status=info.status,
            created_at=info.created_at,
            updated_at=info.updated_at,
            error=info.error,
            result=info.result,
        )


@lru_cache(maxsize=1)
def get_scenario_run_service() -> ScenarioRunService:
    """
    Get the global scenario run service instance.

    Returns:
        The singleton ScenarioRunService instance.
    """
    return ScenarioRunService()
