# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario preset service for CRUD and launch resolution.
"""

import asyncio
import logging
from functools import lru_cache
from typing import Any

from pyrit.backend.models.scenario_presets import (
    PresetIssue,
    ResolveScenarioPresetRequest,
    ScenarioPresetListResponse,
    ScenarioPresetResponse,
)
from pyrit.backend.services.scenario_service import get_scenario_service
from pyrit.models.catalog import RegisteredScenario, RunScenarioRequest, ScenarioPreset, StoredPreset
from pyrit.registry import ScenarioPresetStorage

logger = logging.getLogger(__name__)


class ScenarioPresetNotFoundError(KeyError):
    """No preset is stored under the requested name."""


class ScenarioPresetService:
    """
    Service for reading, writing, and resolving scenario presets.

    Storage is synchronous file or blob I/O, so every call into it is handed to a
    worker thread. Running it inline would block the event loop for the whole
    directory listing on every request.
    """

    def __init__(self) -> None:
        """Initialize the service without touching storage."""
        self._storage: ScenarioPresetStorage | None = None

    def configure_source(self, source: str | None) -> None:
        """
        Configure the local directory or Azure Blob source for scenario presets.

        Args:
            source (str | None): The configured source, or None to use the default directory.
        """
        self._storage = ScenarioPresetStorage(source=source)

    async def list_presets_async(self) -> ScenarioPresetListResponse:
        """
        Read every stored preset, newest advisory issues included.

        Returns:
            ScenarioPresetListResponse: The configured source and every readable preset.
        """
        storage = self._get_storage()
        stored = await asyncio.to_thread(storage.list_presets)

        items = [
            await self._to_response_async(stored_preset) for _, stored_preset in sorted(stored.items(), key=_by_name)
        ]
        return ScenarioPresetListResponse(source=storage.display_source, items=items)

    async def get_preset_async(self, *, name: str) -> ScenarioPresetResponse | None:
        """
        Read one stored preset.

        Args:
            name (str): The preset name.

        Returns:
            ScenarioPresetResponse | None: The preset, or None if nothing is stored under *name*.
        """
        stored = await asyncio.to_thread(self._get_storage().load_preset, name)
        return None if stored is None else await self._to_response_async(stored)

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
            self._get_storage().save_preset, preset=preset, expected_version=expected_version
        )
        logger.info("Saved scenario preset: %s", preset.name)
        return await self._to_response_async(stored)

    async def delete_preset_async(self, *, name: str) -> None:
        """
        Delete one stored preset.

        Args:
            name (str): The preset name.

        Raises:
            ScenarioPresetNotFoundError: If nothing is stored under *name*.
        """
        storage = self._get_storage()
        if await asyncio.to_thread(storage.get_preset_version, name) is None:
            raise ScenarioPresetNotFoundError(name)

        await asyncio.to_thread(storage.delete_preset, name)
        logger.info("Deleted scenario preset: %s", name)

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
            dataset_names=preset.dataset_names,
            max_dataset_size=preset.max_dataset_size,
            dataset_filters=preset.dataset_filters,
            include_baseline=preset.include_baseline,
            scenario_params=preset.scenario_params,
            target_name=launch.target_name,
            adversarial_target_name=launch.adversarial_target_name,
            initializers=launch.initializers,
            initializer_args=launch.initializer_args,
            labels=launch.labels,
            **{name: value for name, value in run_defaults.items() if value is not None},
        )

    def _get_storage(self) -> ScenarioPresetStorage:
        """
        Return storage for the configured preset source.

        Returns:
            ScenarioPresetStorage: The configured storage, defaulting to the standard directory.
        """
        storage = self._storage
        if storage is None:
            storage = ScenarioPresetStorage()
            self._storage = storage
        return storage

    async def _to_response_async(self, stored: StoredPreset) -> ScenarioPresetResponse:
        """
        Attach advisory issues to a stored preset.

        Args:
            stored (StoredPreset): The preset and the version it was read from.

        Returns:
            ScenarioPresetResponse: The wire representation of the preset.
        """
        issues = await self._collect_issues_async(preset=stored.preset)
        return ScenarioPresetResponse(preset=stored.preset, version=stored.version, issues=issues)

    async def _collect_issues_async(self, *, preset: ScenarioPreset) -> list[PresetIssue]:
        """
        Check a preset against the live scenario registry.

        Args:
            preset (ScenarioPreset): The preset to check.

        Returns:
            list[PresetIssue]: Advisory issues, empty when the preset resolves here.
        """
        scenario = await get_scenario_service().get_scenario_async(scenario_name=preset.scenario_name)
        if scenario is None:
            return [
                PresetIssue(
                    field="scenario_name",
                    message=f"Scenario '{preset.scenario_name}' is not registered in this deployment.",
                )
            ]

        return [
            *_unknown_technique_issues(preset=preset, scenario=scenario),
            *_unknown_parameter_issues(preset=preset, scenario=scenario),
            *_forbidden_baseline_issues(preset=preset, scenario=scenario),
        ]


def _by_name(item: tuple[str, StoredPreset]) -> str:
    """
    Sort key for stored presets.

    Args:
        item (tuple[str, StoredPreset]): A storage name and the preset read from it.

    Returns:
        str: The storage name.
    """
    return item[0]


def _unknown_technique_issues(*, preset: ScenarioPreset, scenario: RegisteredScenario) -> list[PresetIssue]:
    """
    Report techniques the scenario does not expose.

    Args:
        preset (ScenarioPreset): The preset to check.
        scenario (RegisteredScenario): The registered scenario it names.

    Returns:
        list[PresetIssue]: One issue naming every unknown technique, or an empty list.
    """
    if not preset.techniques:
        return []

    known = set(scenario.all_techniques) | set(scenario.aggregate_techniques)
    unknown = [technique for technique in preset.techniques if technique not in known]
    if not unknown:
        return []

    return [
        PresetIssue(
            field="techniques",
            message=f"Scenario '{scenario.scenario_name}' does not define: {', '.join(sorted(unknown))}.",
        )
    ]


def _unknown_parameter_issues(*, preset: ScenarioPreset, scenario: RegisteredScenario) -> list[PresetIssue]:
    """
    Report scenario parameters the scenario does not declare.

    Args:
        preset (ScenarioPreset): The preset to check.
        scenario (RegisteredScenario): The registered scenario it names.

    Returns:
        list[PresetIssue]: One issue naming every undeclared parameter, or an empty list.
    """
    if not preset.scenario_params:
        return []

    declared = {parameter.name for parameter in scenario.supported_parameters}
    unknown = [name for name in preset.scenario_params if name not in declared]
    if not unknown:
        return []

    return [
        PresetIssue(
            field="scenario_params",
            message=f"Scenario '{scenario.scenario_name}' does not declare: {', '.join(sorted(unknown))}.",
        )
    ]


def _forbidden_baseline_issues(*, preset: ScenarioPreset, scenario: RegisteredScenario) -> list[PresetIssue]:
    """
    Report a baseline request the scenario forbids.

    Args:
        preset (ScenarioPreset): The preset to check.
        scenario (RegisteredScenario): The registered scenario it names.

    Returns:
        list[PresetIssue]: A single issue when the scenario forbids a requested baseline.
    """
    if not preset.include_baseline or scenario.baseline_policy != "forbidden":
        return []

    return [
        PresetIssue(
            field="include_baseline",
            message=f"Scenario '{scenario.scenario_name}' does not support a baseline run.",
        )
    ]


@lru_cache(maxsize=1)
def get_scenario_preset_service() -> ScenarioPresetService:
    """
    Get the global scenario preset service instance.

    Returns:
        ScenarioPresetService: The singleton scenario preset service instance.
    """
    return ScenarioPresetService()
