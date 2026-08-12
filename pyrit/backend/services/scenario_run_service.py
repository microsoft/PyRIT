# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario run service for executing scenarios as background tasks.

Manages the lifecycle of scenario runs: starting, tracking status,
retrieving results, and cancellation.
"""

import asyncio
import base64
import contextlib
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.common.utils import to_sha256
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import ScenarioProgressKeysetCursor
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    ComponentIdentifier,
    ScenarioAttackResultDelta,
    ScenarioProgressHeader,
    ScenarioProgressResult,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunProgress,
    ScenarioRunState,
    config_hash,
)
from pyrit.models.catalog.scenario import (
    AttackErrorSummary,
    AttackRetrySummary,
    RunScenarioRequest,
    ScenarioRunSummary,
)
from pyrit.registry import (
    ConverterRegistry,
    InitializerRegistry,
    ScenarioRegistry,
    TargetRegistry,
)
from pyrit.scenario import Scenario

if TYPE_CHECKING:
    from pyrit.converter import Converter
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_RUNS = 3

_CONVERTER_MODIFIER_PREFIX = "converter."


@dataclass
class _ActiveTask:
    """Tracks an in-flight scenario run's asyncio task."""

    scenario_result_id: str
    task: asyncio.Task[None] | None = None
    scenario: Scenario | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _ActiveRunSnapshot:
    """Event-loop-owned state copied before database work moves to a worker thread."""

    error: str | None = None
    active_group_ids: tuple[str, ...] = ()


class ScenarioRunService:
    """
    Service for managing scenario run lifecycle.

    Uses CentralMemory (database) as the source of truth for run state.
    Keeps an in-memory dict only for active asyncio tasks (cancellation support).
    """

    def __init__(self, *, max_concurrent_runs: int = _DEFAULT_MAX_CONCURRENT_RUNS) -> None:
        """Initialize the scenario run service."""
        self._max_concurrent_runs = max_concurrent_runs
        self._memory = CentralMemory.get_memory_instance()
        self._active_tasks: dict[str, _ActiveTask] = {}
        self._run_semaphore = asyncio.Semaphore(max_concurrent_runs)

    async def start_run_async(self, *, request: RunScenarioRequest) -> ScenarioRunSummary:
        """
        Start a new scenario run as a background task.

        Performs all validation and initialization eagerly (initializers, target
        resolution, technique validation, scenario.initialize_async) so errors are
        returned immediately. On success, spawns a background task that only
        executes scenario.run_async.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            ScenarioRunResponse with run_id and RUNNING status.

        Raises:
            ValueError: If scenario, target, initializer, or technique cannot be found,
                or concurrent limit exceeded.
        """
        if self._run_semaphore.locked():
            raise ValueError(
                f"Maximum concurrent runs ({self._max_concurrent_runs}) reached. "
                "Wait for an existing run to complete or cancel one."
            )

        await self._run_semaphore.acquire()

        # Perform all initialization eagerly — errors propagate to caller
        try:
            scenario_class = self._resolve_scenario_class(request=request)
            await self._run_initializers_async(request=request)
            objective_target = self._resolve_target(request=request)
            init_kwargs = self._build_init_kwargs(
                request=request, scenario_class=scenario_class, objective_target=objective_target
            )
            scenario = await self._initialize_scenario_async(request=request, init_kwargs=init_kwargs)
        except Exception:
            self._run_semaphore.release()
            raise

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

        response = self.get_run(scenario_result_id=scenario_result_id)
        if response is None:
            raise RuntimeError(f"Scenario run {scenario_result_id} was not found in the database after initialization.")
        return response

    def get_run(self, *, scenario_result_id: str) -> ScenarioRunSummary | None:
        """
        Get the current status of a scenario run by querying the database.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioRunSummary if found, None otherwise.
        """
        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        return self.get_run_from_storage(scenario_result_id=scenario_result_id, active_error=snapshot.error)

    def get_run_from_storage(
        self,
        *,
        scenario_result_id: str,
        active_error: str | None,
    ) -> ScenarioRunSummary | None:
        """
        Build a run summary using database state plus an event-loop snapshot.

        Args:
            scenario_result_id: The scenario result ID.
            active_error: Error copied from the active asyncio task, if any.

        Returns:
            ScenarioRunSummary | None: The run summary when found.
        """
        return self._build_response(scenario_result_id=scenario_result_id, active_error=active_error)

    def list_runs(self, *, limit: int = 100) -> ScenarioRunListResponse:
        """
        List scenario runs by querying the database (most recent first).

        Args:
            limit (int): Maximum number of runs to return. Defaults to 100.

        Returns:
            ScenarioRunListResponse with runs.
        """
        # This is expensive, and we don't need all the data. At some point
        # we may want to add a lightweight "list" query to the DB layer that only
        results = self._memory.get_scenario_results(limit=limit)
        items = [
            self._build_response_from_db(
                scenario_result=sr,
                active_error=self.snapshot_active_run(scenario_result_id=str(sr.id)).error,
            )
            for sr in results
        ]
        return ScenarioRunListResponse(items=items)

    async def cancel_run_async(self, *, scenario_result_id: str) -> ScenarioRunSummary | None:
        """
        Cancel a running scenario.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            Updated ScenarioRunSummary if found, None if not found.

        Raises:
            ValueError: If the run is already in a terminal state or not active.
        """
        # Verify run exists in DB
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None

        scenario_result = results[0]
        db_status = scenario_result.scenario_run_state

        if db_status in (ScenarioRunState.COMPLETED, ScenarioRunState.FAILED, ScenarioRunState.CANCELLED):
            raise ValueError(f"Cannot cancel run in '{db_status}' state.")

        # Cancel the asyncio task if active and wait for it to finish
        active = self._active_tasks.get(scenario_result_id)
        if active is not None and active.task is not None and not active.task.done():
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(active.task, timeout=5.0)

        # Persist cancelled state to DB
        self._memory.update_scenario_run_state(
            scenario_result_id=scenario_result_id,
            scenario_run_state=ScenarioRunState.CANCELLED,
            error_message="Run was cancelled by user",
            error_type="CancelledError",
        )

        return self.get_run(scenario_result_id=scenario_result_id)

    def _resolve_scenario_class(self, *, request: RunScenarioRequest) -> type[Scenario]:
        """
        Validate and resolve the scenario class from the registry.

        Args:
            request: The run request containing the scenario name.

        Returns:
            The scenario class.

        Raises:
            ValueError: If the scenario name is not found in the registry.
        """
        scenario_registry = ScenarioRegistry.get_registry_singleton()
        try:
            return scenario_registry.get_class(request.scenario_name)
        except KeyError as e:
            raise ValueError(str(e)) from None

    async def _run_initializers_async(self, *, request: RunScenarioRequest) -> None:
        """
        Validate and execute initializers specified in the request.

        Args:
            request: The run request containing initializer names and args.

        Raises:
            ValueError: If an initializer name is not found in the registry.
        """
        if not request.initializers:
            return

        initializer_registry = InitializerRegistry.get_registry_singleton()
        for initializer_name in request.initializers:
            initializer_params = (request.initializer_args or {}).get(initializer_name)
            try:
                instance = initializer_registry.create_and_configure(
                    initializer_name, initializer_params=initializer_params
                )
            except KeyError as e:
                raise ValueError(f"Initializer not found: {e}") from None
            await instance.initialize_async()

    def _resolve_target(self, *, request: RunScenarioRequest) -> "PromptTarget":
        """
        Resolve the objective target from the target registry.

        Args:
            request: The run request containing the target name.

        Returns:
            The resolved PromptTarget instance.

        Raises:
            ValueError: If the target is not found in the registry.
        """
        return self.resolve_target_name(target_name=request.target_name)

    @staticmethod
    def resolve_target_name(*, target_name: str) -> "PromptTarget":
        """
        Resolve one registered target name for launch or configured estimation.

        Args:
            target_name: Registered target instance name.

        Returns:
            PromptTarget: The resolved target.

        Raises:
            ValueError: If the target is not registered.
        """
        target_registry = TargetRegistry.get_registry_singleton()
        objective_target = target_registry.instances.get(target_name)
        if objective_target is None:
            available_names = target_registry.instances.get_names()
            if not available_names:
                raise ValueError(
                    f"Target '{target_name}' not found. The target registry is empty. "
                    "Make sure to include an initializer that registers targets "
                    "(e.g., initializers: ['target'])."
                )
            raise ValueError(
                f"Target '{target_name}' not found in registry. Available targets: {', '.join(available_names)}"
            )
        return objective_target

    def _build_init_kwargs(
        self, *, request: RunScenarioRequest, scenario_class: type[Scenario], objective_target: Any
    ) -> dict[str, Any]:
        """
        Build the kwargs dict for scenario.initialize_async.

        Resolves techniques and dataset configuration from the request.

        Dataset configuration is built so that the scenario's default
        ``DatasetAttackConfiguration`` *subclass* (e.g. ``EncodingDatasetConfiguration``)
        is preserved when the caller overrides ``dataset_names`` or
        ``max_dataset_size``. Subclasses commonly override
        ``_build_attack_groups()`` to shape seeds into scenario-appropriate
        ``AttackSeedGroup`` objects.

        Args:
            request: The run request.
            scenario_class: The resolved scenario class.
            objective_target: The resolved target instance.

        Returns:
            Dict of kwargs to pass to scenario.initialize_async.

        Raises:
            ValueError: If a technique name is invalid for the scenario, or the
                scenario class cannot be instantiated with no arguments when
                introspection is required to resolve techniques or dataset
                configuration.
        """
        return self.resolve_scenario_configuration(
            scenario_name=request.scenario_name,
            scenario_class=scenario_class,
            objective_target=objective_target,
            techniques=request.techniques,
            dataset_names=request.dataset_names,
            max_dataset_size=request.max_dataset_size,
            dataset_filters=request.dataset_filters,
            include_baseline=request.include_baseline,
            max_concurrency=request.max_concurrency,
            max_retries=request.max_retries,
            memory_labels=request.labels,
        )

    @classmethod
    def resolve_scenario_configuration(
        cls,
        *,
        scenario_name: str,
        scenario_class: type[Scenario],
        objective_target: Any | None = None,
        techniques: list[str] | None = None,
        dataset_names: list[str] | None = None,
        max_dataset_size: int | None = None,
        dataset_filters: dict[str, list[str]] | None = None,
        include_baseline: bool | None = None,
        max_concurrency: int | None = None,
        max_retries: int | None = None,
        memory_labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Resolve shared launch/estimate request fields into scenario parameters.

        Args:
            scenario_name: Registered scenario name used in validation errors.
            scenario_class: Scenario class used for technique and dataset introspection.
            objective_target: Optional resolved objective target.
            techniques: Requested technique tokens.
            dataset_names: Requested dataset names.
            max_dataset_size: Requested logical-group selection cap.
            dataset_filters: Validated dataset seed filters.
            include_baseline: Optional baseline policy override.
            max_concurrency: Optional launch concurrency.
            max_retries: Optional launch retry count.
            memory_labels: Optional launch memory labels.

        Returns:
            dict[str, Any]: Values accepted by ``Scenario.set_params_from_args``.

        Raises:
            ValueError: If techniques or dataset overrides are invalid.
        """
        resolved: dict[str, Any] = {}
        if objective_target is not None:
            resolved["objective_target"] = objective_target
        if max_concurrency is not None:
            resolved["max_concurrency"] = max_concurrency
        if max_retries is not None:
            resolved["max_retries"] = max_retries
        if include_baseline is not None:
            resolved["include_baseline"] = include_baseline
        if memory_labels:
            resolved["memory_labels"] = memory_labels

        filters = dataset_filters or {}
        needs_introspection = bool(techniques) or bool(dataset_names) or max_dataset_size is not None or bool(filters)
        if not needs_introspection:
            return resolved

        try:
            introspection_instance = scenario_class()  # type: ignore[ty:missing-argument]
        except Exception as exc:
            raise ValueError(
                f"Cannot resolve runtime configuration for scenario '{scenario_name}': "
                f"scenario class is not instantiable without arguments ({exc})."
            ) from exc

        if techniques:
            technique_class = introspection_instance._technique_class
            technique_enums, technique_converters = cls._resolve_techniques_and_converters(
                tokens=techniques,
                technique_class=technique_class,
                scenario_name=scenario_name,
            )
            resolved["scenario_techniques"] = technique_enums
            if technique_converters:
                resolved["technique_converters"] = technique_converters

        if dataset_names or max_dataset_size is not None or filters:
            default_config = introspection_instance._default_dataset_config

            if dataset_names:
                # Construct a fresh instance of the scenario's own dataset-config
                # class so subclass-specific behavior is preserved.
                default_config_class = type(default_config)
                try:
                    resolved["dataset_config"] = default_config_class(
                        dataset_names=dataset_names,
                        max_dataset_size=max_dataset_size,
                        filters=filters or None,
                    )
                except TypeError as exc:
                    raise ValueError(
                        f"Scenario '{scenario_name}' does not support overriding dataset names through "
                        f"its {default_config_class.__name__} configuration: {exc}"
                    ) from exc
            else:
                # Reuse the scenario's default dataset config (preserves subtype +
                # the scenario's own default dataset names) and override only the
                # sample cap and/or filters. Safe because the introspection instance
                # is throwaway.
                if max_dataset_size is not None:
                    default_config.max_dataset_size = max_dataset_size
                if filters:
                    default_config.update_filters(filters=filters)
                resolved["dataset_config"] = default_config

        return resolved

    @classmethod
    def _resolve_techniques_and_converters(
        cls,
        *,
        tokens: list[str],
        technique_class: type[Any],
        scenario_name: str,
    ) -> tuple[list[Any], dict[str, list["Converter"]]]:
        """
        Resolve ``--techniques`` tokens into technique enums and per-technique converters.

        Each token has the form ``<technique>[:converter.<name>[:converter.<name>...]]``.
        The base ``<technique>`` is resolved to a ``ScenarioTechnique`` enum member (which may
        be an aggregate). Each ``converter.<name>`` modifier is resolved to a registered
        converter instance and appended (in token order) to every concrete technique that the
        base technique expands to.

        Args:
            tokens: The raw technique tokens from the request.
            technique_class: The scenario's ``ScenarioTechnique`` subclass.
            scenario_name: The scenario name, used for error messages.

        Returns:
            A tuple of (technique enums to pass as ``scenario_techniques``, mapping from concrete
            technique name to the list of converters to append for that technique).

        Raises:
            ValueError: If a base technique name is unknown, a modifier is malformed, or a
                converter name is not registered.
        """
        technique_enums: list[Any] = []
        technique_converters: dict[str, list[Converter]] = {}

        for token in tokens:
            base_name, _, remainder = token.partition(":")
            modifiers = [m for m in remainder.split(":") if m] if remainder else []

            try:
                technique_enum = technique_class(base_name)
            except ValueError:
                available_techniques = [s.value for s in technique_class]
                raise ValueError(
                    f"Technique '{base_name}' not found for scenario '{scenario_name}'. "
                    f"Available: {', '.join(available_techniques)}"
                ) from None
            technique_enums.append(technique_enum)

            converters = cls._resolve_converter_modifiers(modifiers=modifiers, token=token)
            if not converters:
                continue

            for concrete in technique_class.expand({technique_enum}):
                technique_converters.setdefault(concrete.value, []).extend(converters)

        return technique_enums, technique_converters

    @staticmethod
    def _resolve_converter_modifiers(*, modifiers: list[str], token: str) -> list["Converter"]:
        """
        Resolve the converter modifiers of a single technique token to converter instances.

        Args:
            modifiers: The modifier segments of the token (everything after the base technique).
            token: The full original token, used for error messages.

        Returns:
            The resolved converter instances in token order.

        Raises:
            ValueError: If a modifier does not use the ``converter.`` prefix or names a
                converter that is not registered.
        """
        if not modifiers:
            return []

        instances = ConverterRegistry.get_registry_singleton().instances
        converters: list[Converter] = []
        for modifier in modifiers:
            if not modifier.startswith(_CONVERTER_MODIFIER_PREFIX):
                raise ValueError(
                    f"Unknown technique modifier '{modifier}' in '{token}'. "
                    f"Supported modifiers must use the '{_CONVERTER_MODIFIER_PREFIX}' prefix "
                    f"(e.g. '{_CONVERTER_MODIFIER_PREFIX}translation_spanish')."
                )
            converter_name = modifier[len(_CONVERTER_MODIFIER_PREFIX) :]
            converter = instances.get(converter_name)
            if converter is None:
                available = instances.get_names()
                available_text = ", ".join(available) if available else "(none registered)"
                raise ValueError(
                    f"Converter '{converter_name}' in '{token}' is not a registered converter "
                    f"instance. Available converters: {available_text}"
                )
            converters.append(converter)
        return converters

    async def _initialize_scenario_async(self, *, request: RunScenarioRequest, init_kwargs: dict[str, Any]) -> Scenario:
        """
        Build and initialize the scenario via the registry.

        Delegates the full create + set-parameters + initialize lifecycle to
        ``ScenarioRegistry.create_and_initialize_async`` so the registry owns
        scenario creation and initialization. The run-specific common parameters
        (target, techniques, dataset config, concurrency) are resolved by
        ``_build_init_kwargs`` and forwarded as ``init_kwargs``.

        Args:
            request: The run request (for scenario_name, scenario_params, and
                scenario_result_id).
            init_kwargs: The resolved common parameters to pass to
                scenario.initialize_async.

        Returns:
            The fully initialized Scenario instance ready for run_async.
        """
        scenario_registry = ScenarioRegistry.get_registry_singleton()
        return await scenario_registry.create_and_initialize_async(
            request.scenario_name,
            scenario_params=request.scenario_params or {},
            scenario_result_id=request.scenario_result_id or None,
            **init_kwargs,
        )

    async def _execute_run_async(self, *, scenario_result_id: str) -> None:
        """
        Execute a scenario run (background task entry point).

        Only calls scenario.run_async on the already-initialized scenario.

        Note: this method intentionally does NOT remove the entry from
        ``_active_tasks`` on completion. The entry must stay so that
        ``_build_response_from_db`` can read ``active.error`` when the
        caller next polls the run status. Cleanup happens lazily there
        once the error has been surfaced.

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

        finally:
            self._run_semaphore.release()

    def _build_response(
        self,
        *,
        scenario_result_id: str,
        active_error: str | None,
    ) -> ScenarioRunSummary | None:
        """
        Build a ScenarioRunResponse by querying the database and merging active task state.

        Args:
            scenario_result_id: The scenario result ID.
            active_error: Error copied from the active asyncio task, if any.

        Returns:
            ScenarioRunResponse if found in the database, None otherwise.
        """
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None
        return self._build_response_from_db(scenario_result=results[0], active_error=active_error)

    def _build_response_from_db(
        self,
        *,
        scenario_result: ScenarioResult,
        active_error: str | None = None,
    ) -> ScenarioRunSummary:
        """
        Build a ScenarioRunResponse from a database ScenarioResult, merged with active task info.

        Args:
            scenario_result: A ScenarioResult retrieved from CentralMemory.
            active_error: Error copied from the active asyncio task, if any.

        Returns:
            The API response model.
        """
        scenario_result_id = str(scenario_result.id)

        # Primary source: DB-persisted error fields
        error = scenario_result.error_message
        error_type = scenario_result.error_type

        # Fallback: look up error from any persisted error AttackResults linked
        # to this scenario via the new attribution_parent_id foreign key.
        if not error:
            error_ars = self._memory.get_attack_results(
                scenario_result_id=scenario_result_id,
                outcome=AttackOutcome.ERROR,
            )
            if error_ars:
                error = error_ars[0].error_message
                error_type = error_ars[0].error_type

        # Fallback: in-memory error for in-flight tasks where DB hasn't been updated yet
        if not error:
            error = active_error

        status = scenario_result.scenario_run_state
        terminal = status in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        plan = self._load_run_plan(scenario_result=scenario_result)

        # Build result fields from DB (always computed so in-progress runs show progress)
        total_attacks, completed_attacks, objective_achieved_rate = self._calculate_progress_counts(
            scenario_result=scenario_result,
            plan=plan,
        )
        techniques_used = (
            list(dict.fromkeys(group.display_group for group in plan.atomic_groups))
            if plan is not None
            else scenario_result.get_techniques_used()
        )

        # Surface per-attack errors and retry pressure regardless of overall run status:
        # a COMPLETED scenario can still hide errored objectives or rate-limit retries.
        failed_attacks: list[AttackErrorSummary] = []
        attack_retries: list[AttackRetrySummary] = []
        total_retries = 0
        attempts_by_unit: dict[tuple[str, str], int] = {}
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
                unit_key = self._result_unit_key(
                    atomic_attack_name=atomic_attack_name,
                    attack_result=attack_result,
                    plan=plan,
                )
                attempts_by_unit[unit_key] = attempts_by_unit.get(unit_key, 0) + 1
                retries = getattr(attack_result, "total_retries", 0)
                if isinstance(retries, int):
                    total_retries += retries

                retry_events = getattr(attack_result, "retry_events", None)
                if isinstance(retry_events, list) and retry_events:
                    attack_retries.append(
                        AttackRetrySummary(
                            attack_result_id=str(attack_result.attack_result_id),
                            atomic_attack_name=atomic_attack_name,
                            retries=retry_events,
                        )
                    )

                if attack_result.outcome == AttackOutcome.ERROR:
                    failed_attacks.append(
                        AttackErrorSummary(
                            atomic_attack_name=atomic_attack_name,
                            objective=attack_result.objective,
                            error_type=attack_result.error_type,
                            error_message=attack_result.error_message,
                            total_retries=retries if isinstance(retries, int) else 0,
                        )
                    )
        total_retries += sum(max(0, attempt_count - 1) for attempt_count in attempts_by_unit.values())

        updated_at = scenario_result.creation_time
        if terminal and scenario_result.completion_time is not None:
            updated_at = scenario_result.completion_time

        return ScenarioRunSummary(
            scenario_result_id=scenario_result_id,
            scenario_name=scenario_result.scenario_name,
            scenario_registry_name=plan.scenario_registry_name if plan else None,
            scenario_version=scenario_result.scenario_version,
            status=status,
            created_at=scenario_result.creation_time,
            updated_at=updated_at,
            error=error,
            error_type=error_type,
            techniques_used=techniques_used,
            total_attacks=total_attacks,
            completed_attacks=completed_attacks,
            objective_achieved_rate=objective_achieved_rate,
            failed_attacks=failed_attacks,
            attack_retries=attack_retries,
            total_retries=total_retries,
            labels=scenario_result.labels,
            completed_at=scenario_result.completion_time if terminal else None,
        )

    def _get_active_task(self, *, scenario_result_id: str) -> _ActiveTask | None:
        """Return a live task and release completed task state."""
        active = self._active_tasks.get(scenario_result_id)
        if active is not None and active.task is not None and active.task.done():
            self._active_tasks.pop(scenario_result_id, None)
        return active

    def snapshot_active_run(self, *, scenario_result_id: str) -> _ActiveRunSnapshot:
        """
        Copy asyncio-owned run state for use by database-only worker-thread methods.

        Returns:
            _ActiveRunSnapshot: An immutable copy of the active state.
        """
        active = self._get_active_task(scenario_result_id=scenario_result_id)
        if active is None:
            return _ActiveRunSnapshot()
        active_group_ids = tuple(sorted(active.scenario.active_atomic_group_ids)) if active.scenario is not None else ()
        return _ActiveRunSnapshot(error=active.error, active_group_ids=active_group_ids)

    @staticmethod
    def _load_run_plan(*, scenario_result: ScenarioResult) -> ScenarioRunPlan | None:
        """
        Load a validated plan from scenario metadata.

        Returns:
            ScenarioRunPlan | None: The stored plan, or None for a legacy row.
        """
        metadata = getattr(scenario_result, "metadata", None)
        raw_plan = (metadata or {}).get(SCENARIO_RUN_PLAN_METADATA_KEY)
        return ScenarioRunPlan.model_validate(raw_plan) if raw_plan is not None else None

    @staticmethod
    def _result_unit_key(
        *,
        atomic_attack_name: str,
        attack_result: Any,
        plan: ScenarioRunPlan | None,
    ) -> tuple[str, str]:
        """
        Resolve one attack attempt to its stable planned-unit key.

        Returns:
            tuple[str, str]: The atomic-group and seed-group IDs.
        """
        atomic_identifier = getattr(attack_result, "atomic_attack_identifier", None)
        typed_identifier = (
            AtomicAttackIdentifier.from_component_identifier(atomic_identifier)
            if isinstance(atomic_identifier, ComponentIdentifier)
            else None
        )
        objective = str(getattr(attack_result, "objective", ""))
        attribution_data = getattr(attack_result, "attribution_data", None)
        attributed_seed_group_id = attribution_data.get("seed_group_id") if isinstance(attribution_data, dict) else None
        seed_group_id = str(attributed_seed_group_id) if attributed_seed_group_id else ""
        if not seed_group_id and typed_identifier is not None and typed_identifier.seed_identifiers:
            seed_group_id = typed_identifier.logical_seed_group_id
        atomic_group_id = atomic_attack_name
        planned_group: ScenarioRunPlanAtomicGroup | None = None
        if plan is not None:
            eval_hash = attribution_data.get("parent_eval_hash") if isinstance(attribution_data, dict) else None
            for group in plan.atomic_groups:
                if group.atomic_attack_name == atomic_attack_name and (
                    eval_hash is None or group.technique_eval_hash == eval_hash
                ):
                    atomic_group_id = group.id
                    planned_group = group
                    break
        if not seed_group_id and plan is not None and planned_group is not None:
            objective_sha256 = str(getattr(attack_result, "objective_sha256", "") or to_sha256(objective))
            matching_seed_ids = [
                seed.id
                for seed in plan.seed_groups
                if seed.id in planned_group.seed_group_ids and seed.objective_sha256 == objective_sha256
            ]
            if len(matching_seed_ids) == 1:
                seed_group_id = matching_seed_ids[0]
        if not seed_group_id:
            seed_group_id = config_hash({"objective": objective})
        return atomic_group_id, seed_group_id

    def _calculate_progress_counts(
        self,
        *,
        scenario_result: ScenarioResult,
        plan: ScenarioRunPlan | None,
    ) -> tuple[int, int, int]:
        """
        Calculate planned-unit totals without inflating retries or error attempts.

        Returns:
            tuple[int, int, int]: Total, completed, and success-rate percentage.
        """
        attempted_units: set[tuple[str, str]] = set()
        latest_non_error_by_unit: dict[tuple[str, str], Any] = {}
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
                unit_key = self._result_unit_key(
                    atomic_attack_name=atomic_attack_name,
                    attack_result=attack_result,
                    plan=plan,
                )
                attempted_units.add(unit_key)
                if attack_result.outcome == AttackOutcome.ERROR:
                    continue
                previous = latest_non_error_by_unit.get(unit_key)
                if previous is None or self._result_order_key(attack_result) > self._result_order_key(previous):
                    latest_non_error_by_unit[unit_key] = attack_result

        planned_units = (
            {(group.id, seed_group_id) for group in plan.atomic_groups for seed_group_id in group.seed_group_ids}
            if plan is not None
            else attempted_units
        )
        total = len(planned_units)
        completed_results = [
            result for unit_key, result in latest_non_error_by_unit.items() if unit_key in planned_units
        ]
        completed = len(completed_results)
        succeeded = sum(result.outcome == AttackOutcome.SUCCESS for result in completed_results)
        rate = int((succeeded / completed) * 100) if completed else 0
        return total, completed, rate

    @staticmethod
    def _result_order_key(attack_result: Any) -> tuple[datetime, str]:
        """Return a deterministic chronological key for one hydrated result attempt."""
        timestamp = getattr(attack_result, "timestamp", None)
        if not isinstance(timestamp, datetime):
            timestamp = datetime.min.replace(tzinfo=timezone.utc)
        return timestamp, str(getattr(attack_result, "attack_result_id", ""))

    def get_run_progress(
        self,
        *,
        scenario_result_id: str,
        since: str | None,
        limit: int,
    ) -> ScenarioRunProgress | None:
        """
        Snapshot live state and return compact incremental progress.

        Returns:
            ScenarioRunProgress | None: Compact progress when the run exists.
        """
        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        return self.get_run_progress_from_storage(
            scenario_result_id=scenario_result_id,
            since=since,
            limit=limit,
            active_group_ids=snapshot.active_group_ids,
        )

    def get_run_progress_from_storage(
        self,
        *,
        scenario_result_id: str,
        since: str | None,
        limit: int,
        active_group_ids: Sequence[str],
    ) -> ScenarioRunProgress | None:
        """Return compact database progress using a previously captured live-state snapshot."""
        header_result = self._memory.get_scenario_result_header(scenario_result_id=scenario_result_id)
        if header_result is None:
            return None

        cursor = self._decode_progress_cursor(since=since, scenario_result_id=scenario_result_id)
        deltas, has_more = self._memory.get_scenario_attack_result_deltas(
            scenario_result_id=scenario_result_id,
            cursor=cursor,
            limit=limit,
        )
        plan = self._load_run_plan(scenario_result=header_result)
        plan_complete = plan is not None
        response_plan = plan if since is None else None
        if plan is None and since is None:
            response_plan = self._synthesize_legacy_plan(deltas=deltas)

        results = [self._map_progress_delta(delta=delta, plan=plan or response_plan) for delta in deltas]
        next_cursor = (
            self._encode_progress_cursor(scenario_result_id=scenario_result_id, delta=deltas[-1]) if deltas else since
        )
        terminal = header_result.scenario_run_state in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        return ScenarioRunProgress(
            run=ScenarioProgressHeader(
                scenario_result_id=scenario_result_id,
                scenario_name=header_result.scenario_name,
                scenario_registry_name=plan.scenario_registry_name if plan else None,
                scenario_version=header_result.scenario_version,
                status=header_result.scenario_run_state,
                created_at=header_result.creation_time,
                completed_at=header_result.completion_time if terminal else None,
            ),
            plan=response_plan,
            reset=False,
            active_atomic_group_ids=list(active_group_ids),
            results=results,
            next_cursor=next_cursor,
            has_more=has_more,
            plan_complete=plan_complete,
        )

    @staticmethod
    def _map_progress_delta(
        *,
        delta: ScenarioAttackResultDelta,
        plan: ScenarioRunPlan | None,
    ) -> ScenarioProgressResult:
        """
        Map a lightweight memory row to its REST progress representation.

        Returns:
            ScenarioProgressResult: The mapped progress delta.
        """
        atomic_attack_name = str(delta.attribution_data.get("parent_collection") or "")
        eval_hash = delta.attribution_data.get("parent_eval_hash")
        atomic_group_id = config_hash(
            {"atomic_attack_name": atomic_attack_name, "technique_eval_hash": eval_hash or ""}
        )
        if plan is not None:
            for group in plan.atomic_groups:
                if group.atomic_attack_name == atomic_attack_name and (
                    eval_hash is None or group.technique_eval_hash == eval_hash
                ):
                    atomic_group_id = group.id
                    break
        attributed_seed_group_id = delta.attribution_data.get("seed_group_id")
        seed_group_id = str(attributed_seed_group_id) if attributed_seed_group_id else ""
        if (
            not seed_group_id
            and delta.atomic_attack_identifier is not None
            and delta.atomic_attack_identifier.seed_identifiers
        ):
            seed_group_id = delta.atomic_attack_identifier.logical_seed_group_id
        if not seed_group_id and plan is not None and delta.objective_sha256:
            matching_seed_ids = [
                seed.id
                for seed in plan.seed_groups
                if seed.objective_sha256 == delta.objective_sha256
                and any(seed.id in group.seed_group_ids for group in plan.atomic_groups if group.id == atomic_group_id)
            ]
            if len(matching_seed_ids) == 1:
                seed_group_id = matching_seed_ids[0]
        if not seed_group_id:
            seed_group_id = config_hash({"objective": delta.objective})
        return ScenarioProgressResult(
            attack_result_id=delta.attack_result_id,
            atomic_group_id=atomic_group_id,
            atomic_attack_name=atomic_attack_name,
            seed_group_id=seed_group_id,
            outcome=delta.outcome,
            execution_time_ms=delta.execution_time_ms,
            timestamp=delta.timestamp,
            total_retries=delta.total_retries,
            retries=delta.retry_events,
            error_type=delta.error_type,
            error_message=delta.error_message,
        )

    @staticmethod
    def _synthesize_legacy_plan(*, deltas: list[ScenarioAttackResultDelta]) -> ScenarioRunPlan:
        """
        Synthesize only known completed legacy units without claiming pending totals.

        Returns:
            ScenarioRunPlan: An incomplete plan containing only known units.
        """
        seeds: dict[str, ScenarioRunPlanSeedGroup] = {}
        groups: dict[str, ScenarioRunPlanAtomicGroup] = {}
        for delta in deltas:
            mapped = ScenarioRunService._map_progress_delta(delta=delta, plan=None)
            seeds.setdefault(
                mapped.seed_group_id,
                ScenarioRunPlanSeedGroup(
                    id=mapped.seed_group_id,
                    objective_sha256=delta.objective_sha256 or to_sha256(delta.objective),
                    objective=delta.objective,
                ),
            )
            group = groups.setdefault(
                mapped.atomic_group_id,
                ScenarioRunPlanAtomicGroup(
                    id=mapped.atomic_group_id,
                    atomic_attack_name=mapped.atomic_attack_name,
                    display_group=mapped.atomic_attack_name,
                    technique_eval_hash=str(delta.attribution_data.get("parent_eval_hash") or ""),
                    seed_group_ids=[],
                ),
            )
            if mapped.seed_group_id not in group.seed_group_ids:
                group.seed_group_ids.append(mapped.seed_group_id)
        return ScenarioRunPlan(atomic_groups=list(groups.values()), seed_groups=list(seeds.values()))

    @staticmethod
    def _encode_progress_cursor(*, scenario_result_id: str, delta: ScenarioAttackResultDelta) -> str:
        payload = {
            "v": 1,
            "run": scenario_result_id,
            "timestamp": delta.timestamp.isoformat(),
            "attack_result_id": delta.attack_result_id,
        }
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_progress_cursor(
        *,
        since: str | None,
        scenario_result_id: str,
    ) -> ScenarioProgressKeysetCursor | None:
        if since is None:
            return None
        try:
            padded = since + "=" * (-len(since) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        except Exception as exc:
            raise ValueError("Malformed scenario progress cursor.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Malformed scenario progress cursor.")
        if payload.get("v") != 1 or payload.get("run") != scenario_result_id:
            raise ValueError("Cursor does not belong to this scenario run.")
        try:
            timestamp = datetime.fromisoformat(payload["timestamp"])
            attack_result_id = str(uuid.UUID(payload["attack_result_id"]))
        except Exception as exc:
            raise ValueError("Malformed scenario progress cursor.") from exc
        if timestamp.tzinfo is None:
            raise ValueError("Cursor timestamp must include a timezone.")
        return ScenarioProgressKeysetCursor(timestamp=timestamp, attack_result_id=attack_result_id)

    def get_run_results(self, *, scenario_result_id: str) -> ScenarioResult | None:
        """
        Get the ScenarioResult for a completed scenario run.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioResult if the run is completed and results exist, None if not found.

        Raises:
            ValueError: If the run is not in a completed state.
        """
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None

        scenario_result = results[0]
        run_response = self._build_response_from_db(scenario_result=scenario_result)

        if run_response.status != ScenarioRunState.COMPLETED:
            raise ValueError(f"Results are only available for completed runs. Current status: '{run_response.status}'.")

        return scenario_result


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
