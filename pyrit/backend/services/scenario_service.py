# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario catalog and run-size planning service."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from functools import lru_cache
from time import monotonic

from pyrit.backend.models import DatasetListResponse, ListRegisteredScenariosResponse, PaginationInfo
from pyrit.backend.models.datasets import ScenarioDatasetLoadResponse
from pyrit.backend.services.dataset_service import get_dataset_service
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.memory import CentralMemory
from pyrit.models import (
    DatasetSelectionMode,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
    SeedGroupRequirements,
)
from pyrit.models.catalog import (
    RegisteredScenario,
)
from pyrit.models.dataset_compatibility import (
    DatasetCompatibility,
    DatasetCompatibilityReason,
    DatasetCompatibilityStatus,
    ScenarioDatasetCompatibility,
)
from pyrit.registry import AttackTechniqueRegistry, ScenarioMetadata, ScenarioRegistry
from pyrit.registry.resolution import reject_non_external_params
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.core import Scenario, override_default_adversarial_target
from pyrit.scenario.core.dataset_compatibility import assess_candidate_profiles, describe_requirements

logger = logging.getLogger(__name__)
_ESTIMATE_CACHE_SIZE = 128
_ESTIMATE_CONCURRENCY = 4
_CONFIGURED_ESTIMATE_CONCURRENCY = 4
_DEFAULT_ESTIMATE_TIMEOUT_SECONDS = 3.0
_ESTIMATE_INFLIGHT_SIZE = 256
_UNAVAILABLE_CACHE_TTL_SECONDS = 30.0
_EstimateCacheKey = tuple[str, int, tuple[object, int]]
_EstimateCacheValue = tuple[ScenarioRunSizeEstimate, float | None]
_EstimateTask = asyncio.Task[ScenarioRunSizeEstimate]
_ConfiguredEstimateKey = tuple[str, type[Scenario], str, tuple[object, int]]


def _metadata_to_registered_scenario(
    *,
    metadata: ScenarioMetadata,
    default_run_size: ScenarioRunSizeEstimate | None = None,
) -> RegisteredScenario:
    """
    Convert a ScenarioMetadata dataclass to a ScenarioSummary Pydantic model.

    Args:
        metadata: The registry metadata for a scenario.
        default_run_size: Scenario-owned default-run estimate.

    Returns:
        RegisteredScenario: Public catalog projection.
    """
    estimate = default_run_size or ScenarioRunSizeEstimate.unavailable()
    return RegisteredScenario(
        scenario_name=metadata.registry_name,
        scenario_type=metadata.class_name,
        scenario_version=metadata.scenario_version,
        supports_objective_scorer=metadata.supports_objective_scorer,
        description=metadata.class_description,
        description_markdown=metadata.description_markdown,
        default_technique=metadata.default_technique,
        default_techniques=list(metadata.default_techniques),
        aggregate_techniques=list(metadata.aggregate_techniques),
        aggregate_technique_expansions={
            aggregate: list(expansion) for aggregate, expansion in metadata.aggregate_technique_expansions
        },
        all_techniques=list(metadata.all_techniques),
        max_concrete_techniques=metadata.max_concrete_techniques,
        technique_summaries=list(metadata.technique_summaries),
        default_datasets=list(metadata.default_datasets),
        dataset_options=metadata.dataset_options,
        dataset_defaults=metadata.dataset_defaults,
        supported_parameters=[
            parameter.for_external_catalog()
            for parameter in metadata.supported_parameters
            if parameter.is_external_input
        ],
        supported_parameter_names=[parameter.name for parameter in metadata.supported_parameters],
        baseline_policy=metadata.baseline_policy,
        include_baseline_by_default=metadata.include_baseline_by_default,
        uses_default_adversarial_target=metadata.uses_default_adversarial_target,
        default_run_size=estimate,
    )


class ScenarioService:
    """Expose Scenario metadata and scenario-owned run-size planning."""

    def __init__(self) -> None:
        """Initialize registry access and the per-scenario default-estimate cache."""
        self._registry = ScenarioRegistry.get_registry_singleton()
        self._estimate_cache: OrderedDict[_EstimateCacheKey, _EstimateCacheValue] = OrderedDict()
        self._estimate_tasks: OrderedDict[_EstimateCacheKey, _EstimateTask] = OrderedDict()
        self._estimate_task_lock = asyncio.Lock()
        self._estimate_semaphore = asyncio.Semaphore(_ESTIMATE_CONCURRENCY)
        self._timed_out_estimate_workers: set[_EstimateTask] = set()
        self._configured_estimate_tasks: dict[_ConfiguredEstimateKey, _EstimateTask] = {}
        self._configured_estimate_task_lock = asyncio.Lock()
        self._configured_estimate_semaphore = asyncio.Semaphore(_CONFIGURED_ESTIMATE_CONCURRENCY)

    def outstanding_estimates(self) -> int:
        """Return the number of owned background estimates still running."""
        tasks = {
            *self._estimate_tasks.values(),
            *self._configured_estimate_tasks.values(),
            *self._timed_out_estimate_workers,
        }
        return sum(not task.done() for task in tasks)

    async def close_async(self) -> None:
        """Drain shielded estimates before discarding the registry and cache."""
        tasks = {
            *self._estimate_tasks.values(),
            *self._configured_estimate_tasks.values(),
            *self._timed_out_estimate_workers,
        }
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._estimate_cache.clear()

    async def list_scenarios_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        include_estimates: bool = True,
    ) -> ListRegisteredScenariosResponse:
        """
        List scenarios with optional default estimates and cursor pagination.

        Args:
            limit: Maximum number of scenarios to return.
            cursor: Scenario name after which the page starts.
            include_estimates: Whether to wait for default run-size estimates.

        Returns:
            ListRegisteredScenariosResponse: The requested catalog page.
        """
        all_metadata = self._registry.get_all_registered_class_metadata()
        all_summaries = [_metadata_to_registered_scenario(metadata=metadata) for metadata in all_metadata]

        page, has_more = self._paginate(items=all_summaries, cursor=cursor, limit=limit)
        if include_estimates:
            metadata_by_name = {metadata.registry_name: metadata for metadata in all_metadata}
            estimates = await asyncio.gather(
                *(
                    self._get_default_run_size_estimate_async(metadata=metadata_by_name[item.scenario_name])
                    for item in page
                )
            )
            page = [
                item.model_copy(
                    update={
                        "default_run_size": estimate,
                    }
                )
                for item, estimate in zip(page, estimates, strict=True)
            ]
        next_cursor = page[-1].scenario_name if has_more and page else None
        return ListRegisteredScenariosResponse(
            items=page,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
        )

    async def get_scenario_async(self, *, scenario_name: str) -> RegisteredScenario | None:
        """
        Get one scenario and its cached default estimate.

        Returns:
            RegisteredScenario | None: The catalog entry, or None when it is not registered.
        """
        metadata = self._registry.get_registered_class_metadata(scenario_name)
        if metadata is not None:
            estimate = await self._get_default_run_size_estimate_async(metadata=metadata)
            return _metadata_to_registered_scenario(metadata=metadata, default_run_size=estimate)
        return None

    async def estimate_scenario_run_size_async(
        self,
        *,
        scenario_name: str,
        request: ScenarioRunSizeEstimateRequest,
    ) -> ScenarioRunSizeEstimate | None:
        """
        Estimate one configured scenario without creating a run.

        Args:
            scenario_name: Registered scenario name.
            request: Request-specific techniques, datasets, baseline, and parameters.

        Returns:
            ScenarioRunSizeEstimate | None: Estimate, or ``None`` when the scenario is unknown.
        """
        try:
            scenario_class = self._registry.get_class(scenario_name)
        except KeyError:
            return None
        if request.scenario_params:
            reject_non_external_params(
                params=request.scenario_params,
                declared=scenario_class.supported_parameters(),
                owner=scenario_name,
            )

        estimate_key = self._build_configured_estimate_key(
            scenario_name=scenario_name, scenario_class=scenario_class, request=request
        )
        task_lock = getattr(self, "_configured_estimate_task_lock", None)
        if task_lock is None:
            task_lock = asyncio.Lock()
            self._configured_estimate_task_lock = task_lock
        async with task_lock:
            tasks = getattr(self, "_configured_estimate_tasks", None)
            if tasks is None:
                tasks = {}
                self._configured_estimate_tasks = tasks
            task = tasks.get(estimate_key)
            if task is None:
                task = asyncio.create_task(
                    self._run_configured_estimate_with_capacity_async(
                        scenario_name=scenario_name,
                        request=request,
                    )
                )
                tasks[estimate_key] = task

                def clear_estimate_task(completed_task: _EstimateTask) -> None:
                    self._clear_configured_estimate_task(task=completed_task, estimate_key=estimate_key)

                task.add_done_callback(clear_estimate_task)

        await asyncio.wait({task})
        estimate = task.result()
        assert isinstance(estimate, ScenarioRunSizeEstimate)
        return estimate

    async def load_datasets_async(
        self, *, scenario_name: str, request: ScenarioRunSizeEstimateRequest
    ) -> ScenarioDatasetLoadResponse | None:
        """
        Resolve the same source selection as launch and load only its missing datasets.

        Returns:
            ScenarioDatasetLoadResponse | None: Selected metadata, or None for an unknown scenario.
        """
        try:
            scenario_class = self._registry.get_class(scenario_name)
        except KeyError:
            return None
        reject_non_external_params(
            params=request.scenario_params or {},
            declared=scenario_class.supported_parameters(),
            owner=scenario_name,
        )
        resolver = ScenarioConfigurationResolver()
        adversarial_target = resolver.resolve_adversarial_target(target_name=request.adversarial_target_name)
        objective_target = resolver.resolve_target(target_name=request.target_name) if request.target_name else None
        with override_default_adversarial_target(adversarial_target):
            kwargs = await asyncio.to_thread(
                resolver.resolve_configuration,
                scenario_name=scenario_name,
                scenario_class=scenario_class,
                techniques=request.techniques,
                technique_override=request.technique_override,
                datasets=request.dataset_request,
                objective_target=objective_target,
                scoring_target_name=request.scoring_target_name,
                include_baseline=request.include_baseline,
            )
            constructor_kwargs = resolver.resolve_constructor_kwargs(
                scenario_class=scenario_class, objective_scorer=request.objective_scorer
            )
            scenario = await asyncio.to_thread(self._registry.create_instance, scenario_name, **constructor_kwargs)
            scenario.set_params_from_args(args={**(request.scenario_params or {}), **kwargs})
            settings = await scenario.load_datasets_async()
        metadata = await get_dataset_service().list_datasets_async()
        names = {source.name for source in settings.sources}
        return ScenarioDatasetLoadResponse(
            settings=settings, items=[item for item in metadata.items if item.name in names]
        )

    async def assess_datasets_async(
        self,
        *,
        scenario_name: str,
        request: ScenarioRunSizeEstimateRequest,
        validate_selected: bool = False,
    ) -> ScenarioDatasetCompatibility | None:
        """
        List candidates from stored facts; fully check only an explicitly requested selection.

        Returns:
            ScenarioDatasetCompatibility | None: Assessment, or None for an unknown scenario.
        """
        try:
            scenario_class = self._registry.get_class(scenario_name)
        except KeyError:
            return None
        reject_non_external_params(
            params=request.scenario_params or {},
            declared=scenario_class.supported_parameters(),
            owner=scenario_name,
        )
        resolver = ScenarioConfigurationResolver()
        adversarial_target = resolver.resolve_adversarial_target(target_name=request.adversarial_target_name)
        with override_default_adversarial_target(adversarial_target):
            scenario = await self._create_assessment_scenario_async(
                scenario_name=scenario_name, scenario_class=scenario_class, request=request
            )
            result = await self._list_dataset_candidates_async(scenario)
            if validate_selected:
                try:
                    async with self._configured_estimate_semaphore:
                        selected = await scenario.get_dataset_compatibility_async(allow_unloaded=True)
                except ValueError as error:
                    reason = DatasetCompatibilityReason(code="invalid_dataset", message=str(error))
                    selected = ScenarioDatasetCompatibility(
                        selection_compatible=False,
                        selection_reasons=[reason],
                        items=[
                            DatasetCompatibility(
                                name=name,
                                selected=True,
                                status=DatasetCompatibilityStatus.INCOMPATIBLE,
                                reasons=[reason],
                            )
                            for name in scenario._dataset_config.dataset_names
                        ],
                    )
                checked = {item.name: item for item in selected.items}
                result.items = [checked.pop(item.name, item) for item in result.items]
                result.items.extend(checked.values())
                result.selection_compatible = selected.selection_compatible
                result.selection_reasons = selected.selection_reasons
        return result

    async def list_compatible_datasets_async(self, *, scenario_name: str) -> DatasetListResponse | None:
        """
        List datasets satisfying the scenario's contract, independently of launch-form settings.

        Stored facts narrow the candidates; a schema hint never certifies a dataset. Providers are
        not fetched, and only potentially suitable stored contents are fully checked.

        Returns:
            DatasetListResponse | None: Eligible named datasets, or None for an unknown scenario.
        """
        try:
            scenario_class = self._registry.get_class(scenario_name)
        except KeyError:
            return None
        scenario = await self._create_assessment_scenario_async(
            scenario_name=scenario_name,
            scenario_class=scenario_class,
            request=ScenarioRunSizeEstimateRequest(),
        )
        metadata = await get_dataset_service().list_datasets_async()
        allowed = self._permitted_dataset_names(scenario)
        named = [
            item
            for item in metadata.items
            if item.selection_key != "dataset:unnamed" and (allowed is None or item.name in allowed)
        ]
        if scenario.get_dataset_requirements() == SeedGroupRequirements():
            return DatasetListResponse(items=named)
        hints = await self._list_dataset_candidates_async(scenario, metadata=metadata)
        candidates = {item.name for item in hints.items if item.status is not DatasetCompatibilityStatus.INCOMPATIBLE}
        compatible = []
        async with self._configured_estimate_semaphore:
            for item in named:
                if not item.loaded or item.name not in candidates:
                    continue
                configuration = scenario._dataset_config.with_overrides(
                    sources=[DatasetSource(name=item.name, max_size="all")],
                    max_per_dataset="all",
                    max_total="all",
                )
                try:
                    population = await configuration.get_attack_groups_by_dataset_async(apply_sampling=False)
                    await asyncio.to_thread(scenario._validate_dataset_population, population)
                except ValueError as error:
                    logger.debug("Dataset '%s' is ineligible for '%s': %s", item.name, scenario_name, error)
                    continue
                compatible.append(item)
        return DatasetListResponse(items=compatible)

    async def _create_assessment_scenario_async(
        self,
        *,
        scenario_name: str,
        scenario_class: type[Scenario],
        request: ScenarioRunSizeEstimateRequest,
    ) -> Scenario:
        resolver = ScenarioConfigurationResolver()
        kwargs = await asyncio.to_thread(
            resolver.resolve_configuration,
            scenario_name=scenario_name,
            scenario_class=scenario_class,
            techniques=request.techniques,
            technique_override=request.technique_override,
            datasets=request.dataset_request,
            objective_target=resolver.resolve_target(target_name=request.target_name) if request.target_name else None,
            scoring_target_name=request.scoring_target_name,
            include_baseline=request.include_baseline,
        )
        constructor = resolver.resolve_constructor_kwargs(
            scenario_class=scenario_class, objective_scorer=request.objective_scorer
        )
        scenario = await asyncio.to_thread(self._registry.create_instance, scenario_name, **constructor)
        scenario.set_params_from_args(args={**(request.scenario_params or {}), **kwargs})
        await asyncio.to_thread(scenario._resolve_runtime_configuration, require_objective_target=False)
        return scenario

    @staticmethod
    def _permitted_dataset_names(scenario: Scenario) -> set[str] | None:
        """Return source restrictions including required ingredients, or None for a general catalog."""
        options = scenario.get_dataset_options()
        if options.mode is DatasetSelectionMode.ANY:
            return None
        allowed = set(options.allowed_names)
        if options.mode is DatasetSelectionMode.FIXED:
            allowed = {source.name for source in scenario.get_default_dataset_settings().sources}
        if scenario._required_data is not None:
            allowed.update(scenario._required_data.dataset_names)
        return allowed

    async def _list_dataset_candidates_async(
        self, scenario: Scenario, *, metadata: DatasetListResponse | None = None
    ) -> ScenarioDatasetCompatibility:
        requirements = scenario.get_dataset_requirements()
        if metadata is None:
            metadata = await get_dataset_service().list_datasets_async()
        profiles = await CentralMemory.get_memory_instance().get_seed_dataset_group_profiles_async(
            metadata_keys=[requirement.key for requirement in requirements.metadata]
        )
        by_name = {}
        for profile in profiles:
            by_name.setdefault(profile.dataset_name, []).append(profile)
        selected = set(scenario._dataset_config.dataset_names)
        required = scenario._required_data
        if required is not None:
            selected.update(required.dataset_names)
        allowed = self._permitted_dataset_names(scenario)
        candidates = {item.name for item in metadata.items if item.selection_key != "dataset:unnamed"} | selected
        items = []
        for name in sorted(candidates):
            if allowed is not None and name not in allowed:
                continue
            item = assess_candidate_profiles(name=name, profiles=by_name.get(name, []), requirements=requirements)
            if item.status is DatasetCompatibilityStatus.INCOMPATIBLE and (
                scenario._dataset_config.filters or type(scenario._dataset_config) is not DatasetAttackConfiguration
            ):
                item.status = DatasetCompatibilityStatus.NOT_VERIFIED
                item.reasons = [
                    DatasetCompatibilityReason(
                        code="not_verified",
                        message="Stored facts do not apply the selected filters or custom grouping. "
                        "Validate the selected contents.",
                    )
                ]
            item.selected = name in selected
            if name not in by_name:
                item.group_count = None
                item.reasons = [
                    DatasetCompatibilityReason(
                        code="not_loaded", message="Not verified. Load this provider's data and validate it first."
                    )
                ]
            items.append(item)
        return ScenarioDatasetCompatibility(requirements=describe_requirements(requirements), items=items)

    async def _get_default_run_size_estimate_async(self, *, metadata: ScenarioMetadata) -> ScenarioRunSizeEstimate:
        """Return a cached, cancellation-safe scenario-owned estimate."""
        cache_key = (
            metadata.registry_name,
            metadata.scenario_version,
            AttackTechniqueRegistry.get_registry_singleton().catalog_revision,
        )
        cache = getattr(self, "_estimate_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._estimate_cache = cache
        while True:
            cached = self._read_estimate_cache(cache_key=cache_key)
            if cached is not None:
                return cached

            task_lock = getattr(self, "_estimate_task_lock", None)
            if task_lock is None:
                task_lock = asyncio.Lock()
                self._estimate_task_lock = task_lock
            wait_for_capacity: _EstimateTask | None = None
            task: _EstimateTask | None = None
            async with task_lock:
                cached = self._read_estimate_cache(cache_key=cache_key)
                if cached is not None:
                    return cached

                tasks = getattr(self, "_estimate_tasks", None)
                if tasks is None:
                    tasks = OrderedDict()
                    self._estimate_tasks = tasks
                for completed_key in [key for key, candidate in tasks.items() if candidate.done()]:
                    del tasks[completed_key]
                task = tasks.get(cache_key)
                if task is None:
                    if len(tasks) >= _ESTIMATE_INFLIGHT_SIZE:
                        wait_for_capacity = next(iter(tasks.values()))
                    else:
                        task = asyncio.create_task(
                            self._compute_default_run_size_estimate_async(
                                scenario_name=metadata.registry_name,
                                cache_key=cache_key,
                            )
                        )
                        tasks[cache_key] = task

                        def clear_estimate_task(completed_task: _EstimateTask) -> None:
                            self._clear_estimate_task(task=completed_task, cache_key=cache_key)

                        task.add_done_callback(clear_estimate_task)

            if task is not None:
                estimate = await asyncio.shield(task)
                assert isinstance(estimate, ScenarioRunSizeEstimate)
                return estimate
            if wait_for_capacity is not None:
                await asyncio.shield(wait_for_capacity)

    def _read_estimate_cache(self, *, cache_key: _EstimateCacheKey) -> ScenarioRunSizeEstimate | None:
        """Return a live cached estimate and discard expired unavailable entries."""
        cache = self._estimate_cache
        cached = cache.get(cache_key)
        if cached is None:
            return None
        estimate, expires_at = cached
        if expires_at is not None and monotonic() >= expires_at:
            del cache[cache_key]
            return None
        cache.move_to_end(cache_key)
        return estimate

    async def _compute_default_run_size_estimate_async(
        self,
        *,
        scenario_name: str,
        cache_key: _EstimateCacheKey,
    ) -> ScenarioRunSizeEstimate:
        """
        Construct and estimate one scenario on the owning event loop.

        Returns:
            ScenarioRunSizeEstimate: Scenario-owned estimate.
        """
        semaphore = getattr(self, "_estimate_semaphore", None)
        if semaphore is None:
            semaphore = asyncio.Semaphore(_ESTIMATE_CONCURRENCY)
            self._estimate_semaphore = semaphore
        await semaphore.acquire()
        construction_complete = asyncio.Event()
        execution_timed_out = asyncio.Event()
        try:
            worker_task = asyncio.create_task(
                self._run_default_estimate_with_capacity_async(
                    scenario_name=scenario_name,
                    semaphore=semaphore,
                    construction_complete=construction_complete,
                    execution_timed_out=execution_timed_out,
                )
            )
        except Exception:
            semaphore.release()
            raise
        try:
            completed, _ = await asyncio.wait(
                {worker_task},
                timeout=_DEFAULT_ESTIMATE_TIMEOUT_SECONDS,
            )
            if completed:
                estimate = worker_task.result()
            else:
                raise TimeoutError
        except TimeoutError:
            execution_timed_out.set()
            if construction_complete.is_set():
                worker_task.cancel()
            self._track_timed_out_estimate_worker(worker_task)
            logger.warning(
                "Default-run estimate timed out for scenario '%s' after %.1f seconds",
                scenario_name,
                _DEFAULT_ESTIMATE_TIMEOUT_SECONDS,
            )
            estimate = ScenarioRunSizeEstimate.unavailable(
                note="The default estimate timed out; open the scenario to calculate the configured run size."
            )
        except asyncio.CancelledError:
            execution_timed_out.set()
            if construction_complete.is_set():
                worker_task.cancel()
            self._track_timed_out_estimate_worker(worker_task)
            raise
        except Exception as exc:
            logger.warning("Default-run estimate failed for scenario '%s': %s", scenario_name, exc)
            estimate = ScenarioRunSizeEstimate.unavailable(
                note=f"The scenario could not resolve its default inputs for estimation ({type(exc).__name__})."
            )

        expires_at = monotonic() + _UNAVAILABLE_CACHE_TTL_SECONDS if estimate.estimated_attack_count is None else None
        cache = self._estimate_cache
        cache[cache_key] = (estimate, expires_at)
        cache.move_to_end(cache_key)
        while len(cache) > _ESTIMATE_CACHE_SIZE:
            cache.popitem(last=False)
        return estimate

    async def _run_default_estimate_with_capacity_async(
        self,
        *,
        scenario_name: str,
        semaphore: asyncio.Semaphore,
        construction_complete: asyncio.Event,
        execution_timed_out: asyncio.Event,
    ) -> ScenarioRunSizeEstimate:
        """
        Run one default estimate and release its capacity only after the worker exits.

        Returns:
            ScenarioRunSizeEstimate: The authoritative scenario estimate.
        """
        try:
            return await self._run_default_estimate_async(
                scenario_name=scenario_name,
                construction_complete=construction_complete,
                execution_timed_out=execution_timed_out,
            )
        finally:
            semaphore.release()

    async def _run_default_estimate_async(
        self,
        *,
        scenario_name: str,
        construction_complete: asyncio.Event,
        execution_timed_out: asyncio.Event,
    ) -> ScenarioRunSizeEstimate:
        """
        Run one default estimate.

        Returns:
            ScenarioRunSizeEstimate: The authoritative scenario estimate.
        """
        try:
            scenario = await asyncio.to_thread(self._registry.create_instance, scenario_name)
        finally:
            construction_complete.set()
        if execution_timed_out.is_set():
            raise asyncio.CancelledError
        return await scenario.get_default_run_size_estimate_async()

    def _clear_estimate_task(self, *, task: _EstimateTask, cache_key: _EstimateCacheKey) -> None:
        """Remove a completed single-flight task without disturbing a replacement."""
        tasks = self._estimate_tasks
        if tasks.get(cache_key) is task:
            del tasks[cache_key]

    def _track_timed_out_estimate_worker(self, task: _EstimateTask) -> None:
        """Retain a timed-out worker until its non-cancellable blocking work exits."""
        workers = getattr(self, "_timed_out_estimate_workers", None)
        if workers is None:
            workers = set()
            self._timed_out_estimate_workers = workers
        workers.add(task)
        task.add_done_callback(self._clear_timed_out_estimate_worker)

    def _clear_timed_out_estimate_worker(self, task: _EstimateTask) -> None:
        """Release a completed timed-out worker reference and retrieve any exception."""
        self._timed_out_estimate_workers.discard(task)
        if not task.cancelled():
            task.exception()

    async def _run_configured_estimate_with_capacity_async(
        self,
        *,
        scenario_name: str,
        request: ScenarioRunSizeEstimateRequest,
    ) -> ScenarioRunSizeEstimate:
        """
        Run one configured estimate within its independent capacity bound.

        Returns:
            ScenarioRunSizeEstimate: The configured scenario estimate.
        """
        semaphore = getattr(self, "_configured_estimate_semaphore", None)
        if semaphore is None:
            semaphore = asyncio.Semaphore(_CONFIGURED_ESTIMATE_CONCURRENCY)
            self._configured_estimate_semaphore = semaphore
        async with semaphore:
            return await self._estimate_configured_run_size_async(
                scenario_name=scenario_name,
                request=request,
            )

    def _clear_configured_estimate_task(
        self,
        *,
        task: _EstimateTask,
        estimate_key: _ConfiguredEstimateKey,
    ) -> None:
        """Remove a completed configured estimate task without disturbing a replacement."""
        tasks = self._configured_estimate_tasks
        if tasks.get(estimate_key) is task:
            del tasks[estimate_key]
        if not task.cancelled():
            task.exception()

    async def _estimate_configured_run_size_async(
        self,
        *,
        scenario_name: str,
        request: ScenarioRunSizeEstimateRequest,
    ) -> ScenarioRunSizeEstimate:
        """
        Resolve and estimate one request on the owning event loop.

        Returns:
            ScenarioRunSizeEstimate: Request-specific scenario estimate.
        """
        scenario_class = self._registry.get_class(scenario_name)
        resolver = ScenarioConfigurationResolver()
        objective_target = resolver.resolve_target(target_name=request.target_name) if request.target_name else None
        adversarial_target = resolver.resolve_adversarial_target(target_name=request.adversarial_target_name)
        with override_default_adversarial_target(adversarial_target):
            estimate_kwargs = await asyncio.to_thread(
                resolver.resolve_configuration,
                scenario_name=scenario_name,
                scenario_class=scenario_class,
                objective_target=objective_target,
                scoring_target_name=request.scoring_target_name,
                techniques=request.techniques,
                technique_override=request.technique_override,
                datasets=request.dataset_request,
                include_baseline=request.include_baseline,
            )
            return await self._registry.create_and_estimate_async(
                name=scenario_name,
                scenario_params=request.scenario_params or {},
                constructor_kwargs=resolver.resolve_constructor_kwargs(
                    scenario_class=scenario_class, objective_scorer=request.objective_scorer
                ),
                **estimate_kwargs,
            )

    @staticmethod
    def _paginate(
        *,
        items: list[RegisteredScenario],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[RegisteredScenario], bool]:
        """
        Apply scenario-name cursor pagination.

        Returns:
            tuple[list[RegisteredScenario], bool]: The page and whether another page exists.
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.scenario_name == cursor:
                    start_idx = i + 1
                    break
        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more

    @staticmethod
    def _build_configured_estimate_key(
        *,
        scenario_name: str,
        scenario_class: type[Scenario],
        request: ScenarioRunSizeEstimateRequest,
    ) -> _ConfiguredEstimateKey:
        """
        Build a stable key for semantically identical configured estimate requests.

        Returns:
            _ConfiguredEstimateKey: Scenario identity and canonical request JSON.
        """
        request_json = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            scenario_name,
            scenario_class,
            request_json,
            AttackTechniqueRegistry.get_registry_singleton().catalog_revision,
        )


@lru_cache(maxsize=1)
def get_scenario_service() -> ScenarioService:
    """
    Get the process-wide Scenario service.

    Returns:
        ScenarioService: The cached service instance.
    """
    return ScenarioService()
