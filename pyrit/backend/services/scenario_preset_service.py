# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario preset service for CRUD and launch resolution.
"""

import asyncio
import logging
from functools import lru_cache
from typing import Any, Literal

from pyrit.backend.models.scenario_presets import (
    ResolveScenarioPresetRequest,
    ScenarioPresetListResponse,
    ScenarioPresetResponse,
)
from pyrit.backend.services.scenario_service import get_scenario_service
from pyrit.models.catalog import (
    RunScenarioRequest,
    ScenarioPreset,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
)
from pyrit.registry import ScenarioPresetRegistry

logger = logging.getLogger(__name__)


class ScenarioPresetService:
    """
    Wire adapter over ``ScenarioPresetRegistry``.

    The registry owns preset storage, validation, and version-checked resolution, so this
    service adds only what the HTTP layer needs on top of it: it runs the registry's
    synchronous file and blob I/O on a worker thread so a directory listing cannot block
    the event loop, and it attaches run-size estimates, which are the one part of a preset
    response that has to be computed by the backend rather than read from storage.
    """

    async def list_presets_async(self, *, include_estimates: bool = False) -> ScenarioPresetListResponse:
        """
        Read every stored preset, newest advisory issues included.

        Args:
            include_estimates (bool): Whether to size each preset's own configuration. Sizing
                constructs every scenario and is opt-in. Callers can estimate the returned
                configurations through the scenario estimate endpoint without re-reading storage.

        Returns:
            ScenarioPresetListResponse: The configured source and every readable preset.
        """
        registry = ScenarioPresetRegistry.get_registry_singleton()
        items = await asyncio.to_thread(self._read_responses, registry)
        if include_estimates:
            estimates = await asyncio.gather(*(self._estimate_run_size_async(preset=item.preset) for item in items))
            items = [
                item.model_copy(update={"run_size": estimate}) for item, estimate in zip(items, estimates, strict=True)
            ]
        return ScenarioPresetListResponse(source=registry.source, items=items)

    async def get_preset_async(self, *, name: str) -> ScenarioPresetResponse | None:
        """
        Read one stored preset.

        Args:
            name (str): The preset name.

        Returns:
            ScenarioPresetResponse | None: The preset, or None if nothing is stored under *name*.
        """
        entry = await asyncio.to_thread(ScenarioPresetRegistry.get_registry_singleton().get_preset, name=name)
        if entry is None:
            return None
        return await self._to_response_async(preset=entry.preset, version=entry.version, origin=entry.origin)

    async def save_preset_async(
        self,
        *,
        preset: ScenarioPreset,
        expected_version: str | None,
    ) -> ScenarioPresetResponse:
        """
        Persist one preset.

        Unresolvable references are reported on the response rather than rejected, so a
        preset authored against one deployment can be saved on another.

        Args:
            preset (ScenarioPreset): The preset to persist.
            expected_version (str | None): None to create a preset that must not already exist,
                or the version returned when the edited preset was read.

        Returns:
            ScenarioPresetResponse: The persisted preset, its new version, and its advisory issues.

        Raises:
            ScenarioPresetConflictError: If the stored version does not match *expected_version*.
        """
        stored = await asyncio.to_thread(
            ScenarioPresetRegistry.get_registry_singleton().save_preset,
            preset=preset,
            expected_version=expected_version,
        )
        return await self._to_response_async(preset=stored.preset, version=stored.version, origin="stored")

    async def delete_preset_async(self, *, name: str) -> bool:
        """
        Delete one stored preset.

        Args:
            name (str): The preset name.

        Returns:
            bool: True if a preset was stored under *name*, False if nothing was.
        """
        return await asyncio.to_thread(ScenarioPresetRegistry.get_registry_singleton().delete_preset, name=name)

    async def resolve_run_request_async(
        self, *, name: str, launch: ResolveScenarioPresetRequest
    ) -> RunScenarioRequest | None:
        """
        Read one preset for launch and combine it with the launch-owned fields.

        The version check belongs to the read, so it is performed by the registry against
        a fresh read rather than by the caller against a response it already holds. A
        caller that re-implemented it would be comparing a version to itself.

        Args:
            name (str): The preset name.
            launch (ResolveScenarioPresetRequest): The target and execution fields for this launch.

        Returns:
            RunScenarioRequest | None: The resolved run request, or None if nothing usable
            is stored under *name*.

        Raises:
            ScenarioPresetConflictError: If the stored version no longer matches the version
                the caller supplied.
        """
        stored = await asyncio.to_thread(
            ScenarioPresetRegistry.get_registry_singleton().resolve_preset,
            name=name,
            expected_version=launch.expected_version,
        )
        return None if stored is None else self.resolve_run_request(preset=stored.preset, launch=launch)

    @staticmethod
    def resolve_run_request(*, preset: ScenarioPreset, launch: ResolveScenarioPresetRequest) -> RunScenarioRequest:
        """
        Combine a preset with the launch-owned fields it omits.

        ``max_concurrency`` and ``max_retries`` are the only run fields that are not
        tri-state, so an unset value is dropped rather than passed as None. Passing it
        through would pin the run default at resolution time instead of letting the
        request model supply it.

        Args:
            preset (ScenarioPreset): The preset supplying the scenario-owned fields.
            launch (ResolveScenarioPresetRequest): The target and execution fields for this launch.

        Returns:
            RunScenarioRequest: The request to post to the existing scenario run endpoint.
        """
        run_defaults: dict[str, Any] = {
            "max_concurrency": launch.max_concurrency,
            "max_retries": launch.max_retries,
        }

        return RunScenarioRequest(
            scenario_name=preset.scenario_name,
            techniques=preset.techniques,
            datasets=preset.datasets,
            include_baseline=preset.include_baseline,
            scenario_params=preset.scenario_params,
            target_name=launch.target_name,
            adversarial_target_name=launch.adversarial_target_name,
            initializers=launch.initializers,
            initializer_args=launch.initializer_args,
            operation_id=launch.operation_id,
            labels=launch.labels,
            **{name: value for name, value in run_defaults.items() if value is not None},
        )

    @staticmethod
    def _read_responses(registry: ScenarioPresetRegistry) -> list[ScenarioPresetResponse]:
        """
        Read and check every stored preset in one worker-thread hop.

        Listing and checking are both synchronous, so they share a hop rather than taking
        one per preset.

        Args:
            registry (ScenarioPresetRegistry): The registry to read through.

        Returns:
            list[ScenarioPresetResponse]: Every readable preset, sorted by name.
        """
        return [
            ScenarioPresetResponse(
                preset=entry.preset,
                origin=entry.origin,
                version=entry.version,
                issues=registry.check_preset(preset=entry.preset),
            )
            for entry in registry.list_presets()
        ]

    @staticmethod
    async def _to_response_async(
        *, preset: ScenarioPreset, version: str | None, origin: Literal["stored", "registered"]
    ) -> ScenarioPresetResponse:
        """
        Attach advisory issues to a preset.

        Args:
            preset (ScenarioPreset): The preset to describe.
            version (str | None): The version of the document it was read from, or None
                for a preset registered in code.
            origin (Literal["stored", "registered"]): Which layer contributed the preset.

        Returns:
            ScenarioPresetResponse: The wire representation of the preset.
        """
        issues = await asyncio.to_thread(ScenarioPresetRegistry.get_registry_singleton().check_preset, preset=preset)
        return ScenarioPresetResponse(preset=preset, origin=origin, version=version, issues=issues)

    async def _estimate_run_size_async(self, *, preset: ScenarioPreset) -> ScenarioRunSizeEstimate | None:
        """
        Size one preset against its own techniques, datasets, and limits.

        A preset is allowed to name things this deployment has not registered, so a
        preset that cannot be sized reports no size rather than failing the listing
        that every other preset shares.

        Args:
            preset (ScenarioPreset): The preset to size.

        Returns:
            ScenarioRunSizeEstimate | None: The estimate, or None when it cannot be computed here.
        """
        request = ScenarioRunSizeEstimateRequest(
            techniques=preset.techniques,
            datasets=preset.datasets,
            include_baseline=preset.include_baseline,
            scenario_params=preset.scenario_params,
        )
        try:
            return await get_scenario_service().estimate_scenario_run_size_async(
                scenario_name=preset.scenario_name, request=request
            )
        except Exception:
            logger.debug("Could not size scenario preset '%s'", preset.name, exc_info=True)
            return None


@lru_cache(maxsize=1)
def get_scenario_preset_service() -> ScenarioPresetService:
    """
    Get the global scenario preset service instance.

    Returns:
        ScenarioPresetService: The singleton scenario preset service instance.
    """
    return ScenarioPresetService()
