# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario run service for executing scenarios as background tasks.

Manages the lifecycle of scenario runs: starting, tracking status,
retrieving results, and cancellation.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, ScenarioResult, ScenarioRunState
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
from pyrit.scenario.core import DatasetAttackConfiguration

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

        response = self._build_response(scenario_result_id=scenario_result_id)
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
        return self._build_response(scenario_result_id=scenario_result_id)

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
        items = [self._build_response_from_db(scenario_result=sr) for sr in results]
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
        db_status = ScenarioRunState(scenario_result.scenario_run_state)

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
            scenario_run_state="CANCELLED",
            error_message="Run was cancelled by user",
            error_type="CancelledError",
        )

        return self._build_response(scenario_result_id=scenario_result_id)

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
        target_registry = TargetRegistry.get_registry_singleton()
        objective_target = target_registry.instances.get(request.target_name)
        if objective_target is None:
            available_names = target_registry.instances.get_names()
            if not available_names:
                raise ValueError(
                    f"Target '{request.target_name}' not found. The target registry is empty. "
                    "Make sure to include an initializer that registers targets "
                    "(e.g., initializers: ['target'])."
                )
            raise ValueError(
                f"Target '{request.target_name}' not found in registry. Available targets: {', '.join(available_names)}"
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
        init_kwargs: dict[str, Any] = {
            "objective_target": objective_target,
            "max_concurrency": request.max_concurrency,
            "max_retries": request.max_retries,
        }

        if request.labels:
            init_kwargs["memory_labels"] = request.labels

        # The request model has already validated the filter keys and coerced values into
        # lists, so the service can consume them directly.
        dataset_filters = request.dataset_filters or {}

        # Resolve techniques and dataset config from a temporary instance of the
        # scenario. The downstream _initialize_scenario_async builds its own
        # instance (so scenario_result_id can be passed), so this is a cheap
        # throwaway used only for introspection. Introspection is required
        # whenever the caller wants to override techniques, dataset names, the
        # sample cap, or dataset filters, because each of those needs the
        # scenario's own technique enum or dataset-config subclass to be resolved
        # correctly.
        needs_introspection = (
            bool(request.techniques)
            or bool(request.dataset_names)
            or request.max_dataset_size is not None
            or bool(dataset_filters)
        )
        if not needs_introspection:
            return init_kwargs

        try:
            introspection_instance = scenario_class()  # type: ignore[ty:missing-argument]
        except Exception as exc:
            raise ValueError(
                f"Cannot resolve runtime configuration for scenario '{request.scenario_name}': "
                f"scenario class is not instantiable without arguments ({exc})."
            ) from exc

        if request.techniques:
            technique_class = introspection_instance._technique_class
            technique_enums, technique_converters = self._resolve_techniques_and_converters(
                tokens=request.techniques,
                technique_class=technique_class,
                scenario_name=request.scenario_name,
            )
            init_kwargs["scenario_techniques"] = technique_enums
            if technique_converters:
                init_kwargs["technique_converters"] = technique_converters

        if request.dataset_names or request.max_dataset_size is not None or dataset_filters:
            default_config = introspection_instance._default_dataset_config

            if request.dataset_names:
                # Construct a fresh instance of the scenario's own dataset-config
                # class so subclass-specific behavior is preserved.
                default_config_class = type(default_config)
                try:
                    init_kwargs["dataset_config"] = default_config_class(
                        dataset_names=request.dataset_names,
                        max_dataset_size=request.max_dataset_size,
                        filters=dataset_filters or None,
                    )
                except TypeError as exc:
                    # The subclass __init__ takes extra required kwargs we cannot
                    # supply from a backend request. Fall back to the base
                    # DatasetAttackConfiguration so the run can still proceed; downstream
                    # scenarios that strictly require the subclass should either
                    # define a no-extra-required-args constructor or surface the
                    # incompatibility through their own initialize_async validation.
                    logger.warning(
                        "Cannot construct %s(dataset_names=..., max_dataset_size=..., filters=...) (%s). "
                        "Falling back to a generic DatasetAttackConfiguration; scenario-specific "
                        "dataset-config behavior may be lost.",
                        default_config_class.__name__,
                        exc,
                    )
                    init_kwargs["dataset_config"] = DatasetAttackConfiguration(
                        dataset_names=request.dataset_names,
                        max_dataset_size=request.max_dataset_size,
                        filters=dataset_filters or None,
                    )
            else:
                # Reuse the scenario's default dataset config (preserves subtype +
                # the scenario's own default dataset names) and override only the
                # sample cap and/or filters. Safe because the introspection instance
                # is throwaway.
                if request.max_dataset_size is not None:
                    default_config.max_dataset_size = request.max_dataset_size
                if dataset_filters:
                    default_config.update_filters(filters=dataset_filters)
                init_kwargs["dataset_config"] = default_config

        return init_kwargs

    def _resolve_techniques_and_converters(
        self,
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

            converters = self._resolve_converter_modifiers(modifiers=modifiers, token=token)
            if not converters:
                continue

            for concrete in technique_class.expand({technique_enum}):
                technique_converters.setdefault(concrete.value, []).extend(converters)

        return technique_enums, technique_converters

    def _resolve_converter_modifiers(self, *, modifiers: list[str], token: str) -> list["Converter"]:
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

    def _build_response(self, *, scenario_result_id: str) -> ScenarioRunSummary | None:
        """
        Build a ScenarioRunResponse by querying the database and merging active task state.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioRunResponse if found in the database, None otherwise.
        """
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None
        return self._build_response_from_db(scenario_result=results[0])

    def _build_response_from_db(self, *, scenario_result: ScenarioResult) -> ScenarioRunSummary:
        """
        Build a ScenarioRunResponse from a database ScenarioResult, merged with active task info.

        Args:
            scenario_result: A ScenarioResult retrieved from CentralMemory.

        Returns:
            The API response model.
        """
        scenario_result_id = str(scenario_result.id)
        active = self._active_tasks.get(scenario_result_id)

        # Clean up finished active tasks
        if active is not None and active.task is not None and active.task.done():
            del self._active_tasks[scenario_result_id]

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
        if not error and active is not None:
            error = active.error

        status = ScenarioRunState(scenario_result.scenario_run_state)

        # Build result fields from DB (always computed so in-progress runs show progress)
        total_attacks = sum(len(results) for results in scenario_result.attack_results.values())
        completed_attacks = total_attacks
        techniques_used = scenario_result.get_techniques_used()

        # Surface per-attack errors and retry pressure regardless of overall run status:
        # a COMPLETED scenario can still hide errored objectives or rate-limit retries.
        failed_attacks: list[AttackErrorSummary] = []
        attack_retries: list[AttackRetrySummary] = []
        total_retries = 0
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
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

        return ScenarioRunSummary(
            scenario_result_id=scenario_result_id,
            scenario_name=scenario_result.scenario_name,
            scenario_version=scenario_result.scenario_version,
            status=status,
            created_at=scenario_result.creation_time,
            updated_at=scenario_result.completion_time or scenario_result.creation_time,
            error=error,
            error_type=error_type,
            techniques_used=techniques_used,
            total_attacks=total_attacks,
            completed_attacks=completed_attacks,
            objective_achieved_rate=scenario_result.objective_achieved_rate(),
            failed_attacks=failed_attacks,
            attack_retries=attack_retries,
            total_retries=total_retries,
            labels=scenario_result.labels,
            completed_at=scenario_result.completion_time,
        )

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
