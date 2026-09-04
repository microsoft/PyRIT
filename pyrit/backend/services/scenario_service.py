# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario catalog and run-size planning service."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from functools import lru_cache
from time import monotonic

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ListRegisteredScenariosResponse
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.models.catalog.scenario import (
    RegisteredScenario,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
)
from pyrit.registry import ScenarioMetadata, ScenarioRegistry
from pyrit.scenario.core.dataset_configuration import read_only_dataset_resolution

logger = logging.getLogger(__name__)
_ESTIMATE_CACHE_SIZE = 128
_ESTIMATE_CONCURRENCY = 1
_ESTIMATE_INFLIGHT_SIZE = 256
_UNAVAILABLE_CACHE_TTL_SECONDS = 30.0
_EstimateCacheKey = tuple[str, int]
_EstimateCacheValue = tuple[ScenarioRunSizeEstimate, float | None]
_EstimateTask = asyncio.Task[ScenarioRunSizeEstimate]


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
        description=metadata.class_description,
        description_markdown=metadata.description_markdown,
        default_technique=metadata.default_technique,
        default_techniques=list(metadata.default_techniques),
        aggregate_techniques=list(metadata.aggregate_techniques),
        aggregate_technique_expansions={
            aggregate: list(expansion) for aggregate, expansion in metadata.aggregate_technique_expansions
        },
        all_techniques=list(metadata.all_techniques),
        technique_summaries=list(metadata.technique_summaries),
        default_datasets=list(metadata.default_datasets),
        supported_parameters=list(metadata.supported_parameters),
        baseline_policy=metadata.baseline_policy,
        include_baseline_by_default=metadata.include_baseline_by_default,
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
        metadata = self._registry.get_registered_class_metadata(scenario_name)
        if metadata is None:
            return None

        semaphore = getattr(self, "_estimate_semaphore", None)
        if semaphore is None:
            semaphore = asyncio.Semaphore(_ESTIMATE_CONCURRENCY)
            self._estimate_semaphore = semaphore
        async with semaphore:
            return await self._estimate_configured_run_size_async(
                scenario_name=scenario_name,
                request=request,
            )

    async def _get_default_run_size_estimate_async(self, *, metadata: ScenarioMetadata) -> ScenarioRunSizeEstimate:
        """Return a cached, cancellation-safe scenario-owned estimate."""
        cache_key = (metadata.registry_name, metadata.scenario_version)
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
        async with semaphore:
            try:
                scenario = await asyncio.to_thread(self._registry.create_instance, scenario_name)
                with read_only_dataset_resolution():
                    estimate = await scenario.get_default_run_size_estimate_async()
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

    def _clear_estimate_task(self, *, task: _EstimateTask, cache_key: _EstimateCacheKey) -> None:
        """Remove a completed single-flight task without disturbing a replacement."""
        tasks = self._estimate_tasks
        if tasks.get(cache_key) is task:
            del tasks[cache_key]

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
        estimate_kwargs = resolver.resolve_configuration(
            scenario_name=scenario_name,
            scenario_class=scenario_class,
            objective_target=objective_target,
            techniques=request.techniques,
            dataset_names=request.dataset_names,
            max_dataset_size=request.max_dataset_size,
            dataset_filters=request.dataset_filters,
            include_baseline=request.include_baseline,
        )
        return await self._registry.create_and_estimate_async(
            name=scenario_name,
            scenario_params=request.scenario_params or {},
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


@lru_cache(maxsize=1)
def get_scenario_service() -> ScenarioService:
    """
    Get the process-wide Scenario service.

    Returns:
        ScenarioService: The cached service instance.
    """
    return ScenarioService()
