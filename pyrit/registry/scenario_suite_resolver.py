# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve registry-backed presets and suites into ordinary scenario run requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pyrit.models.catalog.scenario import RunScenarioRequest, ScenarioLaunchConfiguration
from pyrit.models.scenario_suite_execution import (
    LaunchScenarioSuiteRequest,
    ScenarioSuiteExecutionMetadata,
)
from pyrit.registry.components.scenario_preset_registry import ScenarioPresetRegistry
from pyrit.registry.components.scenario_suite_registry import ScenarioSuiteRegistry

if TYPE_CHECKING:
    from uuid import UUID

    from pyrit.models.catalog.scenario_preset import ScenarioPreset
    from pyrit.models.catalog.scenario_suite import ScenarioSuiteRegistryEntry


class _RunDefaults(TypedDict, total=False):
    """Optional ordinary-run defaults supplied by a launch."""

    max_concurrency: int
    max_retries: int


class ScenarioSuiteExecutionNotFoundError(LookupError):
    """A requested suite definition does not exist."""


class ScenarioSuiteLaunchConflictError(ValueError):
    """A suite or referenced preset changed after the operator reviewed it."""


class ScenarioSuiteNotRunnableError(ValueError):
    """A suite cannot launch because one or more entries are unresolved."""


def resolve_scenario_preset_run_request(
    *,
    preset: ScenarioPreset,
    launch: ScenarioLaunchConfiguration,
) -> RunScenarioRequest:
    """
    Combine a preset with the target and execution fields it deliberately omits.

    Returns:
        RunScenarioRequest: An ordinary scenario run request.
    """
    run_defaults = _RunDefaults()
    if launch.max_concurrency is not None:
        run_defaults["max_concurrency"] = launch.max_concurrency
    if launch.max_retries is not None:
        run_defaults["max_retries"] = launch.max_retries
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
        **run_defaults,
    )


@dataclass(frozen=True)
class ResolvedScenarioSuiteEntry:
    """One preflighted suite entry ready for ordinary scenario submission."""

    entry_id: UUID
    entry_position: int
    preset_name: str
    preset_origin: Literal["stored", "registered"]
    preset_version: str | None
    request: RunScenarioRequest

    def metadata(self, *, suite_execution_id: UUID, suite: ScenarioSuiteRegistryEntry) -> dict[str, Any]:
        """
        Build the child attribution metadata for this resolved entry.

        Returns:
            dict[str, Any]: JSON-compatible fields for ``ScenarioResult.metadata``.
        """
        return ScenarioSuiteExecutionMetadata(
            suite_execution_id=suite_execution_id,
            suite_name=suite.suite.name,
            suite_version=suite.version,
            suite_entry_id=self.entry_id,
            entry_position=self.entry_position,
            preset_name=self.preset_name,
            preset_version=self.preset_version,
        ).to_metadata_fields()


@dataclass(frozen=True)
class ResolvedScenarioSuiteLaunch:
    """A version-checked suite and its ordered ordinary scenario run requests."""

    suite: ScenarioSuiteRegistryEntry
    entries: tuple[ResolvedScenarioSuiteEntry, ...]


class ScenarioSuiteLaunchResolver:
    """Resolve suites and presets without owning backend task lifecycle."""

    def __init__(
        self,
        *,
        suite_registry: ScenarioSuiteRegistry | None = None,
        preset_registry: ScenarioPresetRegistry | None = None,
    ) -> None:
        """
        Initialize the resolver with optional registry overrides.

        Args:
            suite_registry (ScenarioSuiteRegistry | None): Suite definitions to resolve.
            preset_registry (ScenarioPresetRegistry | None): Preset definitions to resolve.
        """
        self._suite_registry = suite_registry
        self._preset_registry = preset_registry

    def resolve(self, *, name: str, request: LaunchScenarioSuiteRequest) -> ResolvedScenarioSuiteLaunch:
        """
        Resolve and validate the complete suite before any child is submitted.

        Returns:
            ResolvedScenarioSuiteLaunch: The exact suite and ordered child requests.

        Raises:
            ScenarioSuiteExecutionNotFoundError: If the suite does not exist.
            ScenarioSuiteLaunchConflictError: If the reviewed suite or preset state changed.
            ScenarioSuiteNotRunnableError: If a referenced preset is unavailable or invalid.
        """
        suite = self._get_suite_registry().resolve_suite(name=name, expected_version=request.expected_version)
        if suite is None:
            raise ScenarioSuiteExecutionNotFoundError(f"Scenario suite '{name}' not found.")
        if suite.origin == "stored" and request.expected_version is None:
            raise ScenarioSuiteLaunchConflictError(
                f"Scenario suite '{name}' must be refreshed before launch because no reviewed version was supplied."
            )
        if len(suite.suite.entries) != len(request.entries):
            raise ScenarioSuiteLaunchConflictError(
                f"Scenario suite '{name}' changed after it was reviewed; refresh it before launching."
            )

        preset_registry = self._get_preset_registry()
        presets = {
            preset_name: preset_registry.get_preset(name=preset_name)
            for preset_name in dict.fromkeys(entry.preset_name for entry in suite.suite.entries)
        }
        resolved: list[ResolvedScenarioSuiteEntry] = []
        for position, (suite_entry, reviewed) in enumerate(
            zip(suite.suite.entries, request.entries, strict=True),
            start=1,
        ):
            if suite_entry.entry_id != reviewed.entry_id or suite_entry.preset_name != reviewed.preset_name:
                raise ScenarioSuiteLaunchConflictError(
                    f"Scenario suite '{name}' changed after it was reviewed; refresh it before launching."
                )

            preset = presets[suite_entry.preset_name]
            if preset is None:
                raise ScenarioSuiteNotRunnableError(
                    f"Entry {position} references unavailable scenario preset '{suite_entry.preset_name}'."
                )
            if preset.origin != reviewed.origin or preset.version != reviewed.preset_version:
                raise ScenarioSuiteLaunchConflictError(
                    f"Scenario preset '{suite_entry.preset_name}' changed after the suite was reviewed; "
                    "refresh the suite before launching."
                )

            issues = preset_registry.check_preset(preset=preset.preset)
            if issues:
                details = "; ".join(issue.message for issue in issues)
                raise ScenarioSuiteNotRunnableError(
                    f"Entry {position} scenario preset '{suite_entry.preset_name}' is not runnable: {details}"
                )

            resolved.append(
                ResolvedScenarioSuiteEntry(
                    entry_id=suite_entry.entry_id,
                    entry_position=position,
                    preset_name=suite_entry.preset_name,
                    preset_origin=preset.origin,
                    preset_version=preset.version,
                    request=resolve_scenario_preset_run_request(
                        preset=preset.preset,
                        launch=request,
                    ),
                )
            )

        return ResolvedScenarioSuiteLaunch(suite=suite, entries=tuple(resolved))

    def _get_suite_registry(self) -> ScenarioSuiteRegistry:
        """Return the injected suite registry or process singleton."""
        return (
            self._suite_registry if self._suite_registry is not None else ScenarioSuiteRegistry.get_registry_singleton()
        )

    def _get_preset_registry(self) -> ScenarioPresetRegistry:
        """Return the injected preset registry or process singleton."""
        return (
            self._preset_registry
            if self._preset_registry is not None
            else ScenarioPresetRegistry.get_registry_singleton()
        )
