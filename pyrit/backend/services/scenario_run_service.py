# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario run service for executing scenarios as background tasks.

Manages the lifecycle of scenario runs: starting, tracking status,
retrieving results, and cancellation.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pyrit.backend.models.scenarios import (
    AtomicAttackResults,
    AttackResultDetail,
    RunScenarioRequest,
    ScenarioResultDetailResponse,
    ScenarioRunListResponse,
    ScenarioRunResponse,
    ScenarioRunResult,
    ScenarioRunStatus,
)
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, ScenarioResult
from pyrit.registry import InitializerRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario import Scenario
from pyrit.scenario.core import DatasetConfiguration

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_RUNS = 3

# Maps DB ScenarioRunState values to API ScenarioRunStatus
_STATE_TO_STATUS: dict[str, ScenarioRunStatus] = {
    "CREATED": ScenarioRunStatus.INITIALIZING,
    "IN_PROGRESS": ScenarioRunStatus.RUNNING,
    "COMPLETED": ScenarioRunStatus.COMPLETED,
    "FAILED": ScenarioRunStatus.FAILED,
    "CANCELLED": ScenarioRunStatus.CANCELLED,
}


@dataclass
class _ActiveTask:
    """Tracks an in-flight scenario run's asyncio task."""

    scenario_result_id: str
    task: asyncio.Task[None] | None = None
    scenario: Scenario | None = None
    error: str | None = None


class ScenarioRunService:
    """
    Service for managing scenario run lifecycle.

    Uses CentralMemory (database) as the source of truth for run state.
    Keeps an in-memory dict only for active asyncio tasks (cancellation support).
    """

    def __init__(self, *, max_concurrent_runs: int = _DEFAULT_MAX_CONCURRENT_RUNS) -> None:
        """Initialize the scenario run service."""
        self._max_concurrent_runs = max_concurrent_runs
        self._active_tasks: dict[str, _ActiveTask] = {}

    async def start_run_async(self, *, request: RunScenarioRequest) -> ScenarioRunResponse:
        """
        Start a new scenario run as a background task.

        Performs all validation and initialization eagerly (initializers, target
        resolution, strategy validation, scenario.initialize_async) so errors are
        returned immediately. On success, spawns a background task that only
        executes scenario.run_async.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            ScenarioRunResponse with run_id and RUNNING status.

        Raises:
            ValueError: If scenario, target, initializer, or strategy cannot be found,
                or concurrent limit exceeded.
        """
        if sum(1 for a in self._active_tasks.values() if a.task is not None and not a.task.done()) >= self._max_concurrent_runs:
            raise ValueError(
                f"Maximum concurrent runs ({self._max_concurrent_runs}) reached. "
                "Wait for an existing run to complete or cancel one."
            )

        # Perform all initialization eagerly — errors propagate to caller
        scenario = await self._initialize_run_async(request=request)

        # scenario_result_id is set during initialize_async
        scenario_result_id = scenario._scenario_result_id
        if scenario_result_id is None:
            raise ValueError("Scenario did not produce a scenario_result_id during initialization.")

        # Track active task
        active = _ActiveTask(scenario_result_id=scenario_result_id, scenario=scenario)
        self._active_tasks[scenario_result_id] = active

        # Spawn background task (only runs scenario.run_async)
        task = asyncio.create_task(self._execute_run_async(scenario_result_id=scenario_result_id))
        active.task = task

        response = self._build_response(scenario_result_id=scenario_result_id)
        assert response is not None  # guaranteed: we just inserted into DB via initialize_async
        return response

    def get_run(self, *, run_id: str) -> ScenarioRunResponse | None:
        """
        Get the current status of a scenario run by querying the database.

        Args:
            run_id: The scenario result ID (run identifier).

        Returns:
            ScenarioRunResponse if found, None otherwise.
        """
        return self._build_response(scenario_result_id=run_id)

    def list_runs(self, *, limit: int = 100) -> ScenarioRunListResponse:
        """
        List scenario runs by querying the database (most recent first).

        Args:
            limit (int): Maximum number of runs to return. Defaults to 100.

        Returns:
            ScenarioRunListResponse with runs.
        """
        memory = CentralMemory.get_memory_instance()

        # This is expensive, and we don't need all the data. At some point
        # we may want to add a lightweight "list" query to the DB layer that only
        results = memory.get_scenario_results(limit=limit)
        items = [self._build_response_from_db(scenario_result=sr) for sr in results]
        return ScenarioRunListResponse(items=items)

    async def cancel_run_async(self, *, run_id: str) -> ScenarioRunResponse | None:
        """
        Cancel a running scenario.

        Args:
            run_id: The scenario result ID (run identifier).

        Returns:
            Updated ScenarioRunResponse if found, None if run_id not found.

        Raises:
            ValueError: If the run is already in a terminal state or not active.
        """
        # Verify run exists in DB
        memory = CentralMemory.get_memory_instance()
        results = memory.get_scenario_results(scenario_result_ids=[run_id])
        if not results:
            return None

        scenario_result = results[0]
        db_status = _STATE_TO_STATUS.get(scenario_result.scenario_run_state, ScenarioRunStatus.FAILED)

        if db_status in (ScenarioRunStatus.COMPLETED, ScenarioRunStatus.FAILED, ScenarioRunStatus.CANCELLED):
            raise ValueError(f"Cannot cancel run in '{db_status}' state.")

        # Cancel the asyncio task if active
        active = self._active_tasks.get(run_id)
        if active is not None:
            if active.task is not None and not active.task.done():
                active.task.cancel()

        # Persist cancelled state to DB
        memory.update_scenario_run_state(scenario_result_id=run_id, scenario_run_state="CANCELLED")

        return self._build_response(scenario_result_id=run_id)

    async def _initialize_run_async(self, *, request: RunScenarioRequest) -> Scenario:
        """
        Validate inputs and initialize the scenario eagerly.

        Performs all validation (scenario, initializers, target, strategies) and
        calls scenario.initialize_async so that any errors are raised immediately
        to the caller. Running initialization on creation simplifies error handling and ensures
        that the scenario is fully ready to run when we spawn the background task.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            The fully initialized Scenario instance ready for run_async.

        Raises:
            ValueError: If any validation fails (bad scenario name, missing target,
                invalid strategy, unknown initializer, etc.).
        """
        # Validate scenario exists
        scenario_registry = ScenarioRegistry.get_registry_singleton()
        try:
            scenario_class = scenario_registry.get_class(request.scenario_name)
        except KeyError as e:
            raise ValueError(str(e)) from None

        # Validate and run initializers
        if request.initializers:
            initializer_registry = InitializerRegistry.get_registry_singleton()
            for initializer_name in request.initializers:
                try:
                    initializer_class = initializer_registry.get_class(initializer_name)
                except KeyError as e:
                    raise ValueError(f"Initializer not found: {e}") from None
                instance = initializer_class()
                if request.initializer_args and initializer_name in request.initializer_args:
                    instance.set_params_from_args(args=request.initializer_args[initializer_name])
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
                f"Target '{request.target_name}' not found in registry. Available targets: {', '.join(available_names)}"
            )

        # Build init kwargs
        init_kwargs: dict[str, Any] = {
            "objective_target": objective_target,
            "max_concurrency": request.max_concurrency,
            "max_retries": request.max_retries,
        }

        if request.memory_labels:
            init_kwargs["memory_labels"] = request.memory_labels

        # Validate and resolve strategies
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

        # Instantiate and initialize scenario
        constructor_kwargs: dict[str, Any] = {}
        if request.scenario_result_id:
            constructor_kwargs["scenario_result_id"] = request.scenario_result_id
        scenario = scenario_class(**constructor_kwargs)  # type: ignore[call-arg]
        scenario.set_params_from_args(args=request.scenario_params or {})
        await scenario.initialize_async(**init_kwargs)
        return scenario

    async def _execute_run_async(self, *, scenario_result_id: str) -> None:
        """
        Execute a scenario run (background task entry point).

        Only calls scenario.run_async on the already-initialized scenario.
        Removes the task from _active_tasks when done.

        Args:
            scenario_result_id: The scenario result ID for this run.
        """
        active = self._active_tasks[scenario_result_id]
        assert active.scenario is not None

        try:
            await active.scenario.run_async()

        except asyncio.CancelledError:
            logger.info(f"Scenario run {scenario_result_id} was cancelled.")

        except Exception as e:
            active.error = str(e)
            logger.exception(f"Scenario run {scenario_result_id} failed: {e}")

    def _build_response(self, *, scenario_result_id: str) -> ScenarioRunResponse | None:
        """
        Build a ScenarioRunResponse by querying the database and merging active task state.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioRunResponse if found in the database, None otherwise.
        """
        memory = CentralMemory.get_memory_instance()
        results = memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None
        return self._build_response_from_db(scenario_result=results[0])

    def _build_response_from_db(self, *, scenario_result: ScenarioResult) -> ScenarioRunResponse:
        """
        Build a ScenarioRunResponse from a database ScenarioResult, merged with active task info.

        Args:
            scenario_result: A ScenarioResult retrieved from CentralMemory.

        Returns:
            The API response model.
        """
        scenario_result_id = str(scenario_result.id)
        active = self._active_tasks.get(scenario_result_id)

        # Clean up finished active tasks after reading the error
        error = None
        if active is not None:
            error = active.error
            if active.task is not None and active.task.done():
                del self._active_tasks[scenario_result_id]

        status = _STATE_TO_STATUS.get(scenario_result.scenario_run_state, ScenarioRunStatus.FAILED)

        # Build result summary for completed runs
        result = None
        if status == ScenarioRunStatus.COMPLETED:
            completed_attacks = sum(
                1
                for results in scenario_result.attack_results.values()
                for ar in results
                if ar.outcome in (AttackOutcome.SUCCESS, AttackOutcome.FAILURE)
            )
            total_attacks = sum(len(results) for results in scenario_result.attack_results.values())
            result = ScenarioRunResult(
                scenario_result_id=scenario_result_id,
                run_state=scenario_result.scenario_run_state,
                strategies_used=scenario_result.get_strategies_used(),
                total_attacks=total_attacks,
                completed_attacks=completed_attacks,
                number_tries=scenario_result.number_tries,
                completion_time=scenario_result.completion_time,
            )

        return ScenarioRunResponse(
            run_id=scenario_result_id,
            scenario_name=scenario_result.scenario_identifier.name,
            status=status,
            created_at=scenario_result.created_at,
            updated_at=scenario_result.completion_time,
            error=error,
            result=result,
        )

    def get_run_results(self, *, run_id: str) -> ScenarioResultDetailResponse | None:
        """
        Get detailed results for a completed scenario run.

        Retrieves the full ScenarioResult from CentralMemory and maps it
        to a detailed response model with per-attack outcomes.

        Args:
            run_id: The scenario result ID (run identifier).

        Returns:
            ScenarioResultDetailResponse if the run is completed and results exist, None if run not found.

        Raises:
            ValueError: If the run is not in a completed state.
        """
        memory = CentralMemory.get_memory_instance()
        results = memory.get_scenario_results(scenario_result_ids=[run_id])
        if not results:
            return None

        scenario_result = results[0]

        if scenario_result.scenario_run_state != "COMPLETED":
            status = _STATE_TO_STATUS.get(scenario_result.scenario_run_state, ScenarioRunStatus.FAILED)
            raise ValueError(f"Results are only available for completed runs. Current status: '{status}'.")

        # Build per-attack detail
        attacks: list[AtomicAttackResults] = []
        display_group_map = scenario_result.display_group_map
        for attack_name, attack_results in scenario_result.attack_results.items():
            details: list[AttackResultDetail] = []
            success_count = 0
            failure_count = 0

            for ar in attack_results:
                score_value = None
                if ar.last_score is not None:
                    score_value = str(ar.last_score.get_value())

                last_response_text = None
                if ar.last_response is not None:
                    last_response_text = str(ar.last_response)

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

            attacks.append(
                AtomicAttackResults(
                    atomic_attack_name=attack_name,
                    display_group=display_group_map.get(attack_name),
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


_service_instance: ScenarioRunService | None = None


def get_scenario_run_service() -> ScenarioRunService:
    """
    Get the global scenario run service instance.

    On first call, reads ``max_concurrent_scenario_runs`` from ``app.state``
    (set by ``pyrit_backend`` CLI) if available, otherwise uses the default.

    Returns:
        The singleton ScenarioRunService instance.
    """
    global _service_instance
    if _service_instance is not None:
        return _service_instance

    max_runs = _DEFAULT_MAX_CONCURRENT_RUNS
    try:
        from pyrit.backend.main import app

        max_runs = getattr(app.state, "max_concurrent_scenario_runs", _DEFAULT_MAX_CONCURRENT_RUNS)
    except Exception:
        pass

    _service_instance = ScenarioRunService(max_concurrent_runs=max_runs)
    return _service_instance
