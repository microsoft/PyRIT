# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve registry-backed presets and suites into ordinary scenario run requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pyrit.models import (
    LaunchScenarioSuiteRequest,
    ScenarioLaunchConfiguration,
    ScenarioSuiteExecutionMetadata,
    ScenarioSuiteReadinessRequest,
)
from pyrit.models.catalog import RunScenarioRequest
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.registry.components.scenario_preset_registry import ScenarioPresetRegistry
from pyrit.registry.components.scenario_suite_registry import ScenarioSuiteRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from collections.abc import Set as AbstractSet
    from uuid import UUID

    from pyrit.models import ScenarioPreset
    from pyrit.models.catalog import ScenarioSuiteRegistryEntry
    from pyrit.models.scenario_readiness import LaunchValueSource
    from pyrit.registry.components.scenario_registry import ScenarioMetadata, ScenarioRegistry


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


_LaunchSource = ScenarioLaunchConfiguration | LaunchScenarioSuiteRequest | ScenarioSuiteReadinessRequest


def bind_registered_scorer(name: str) -> RegisteredComponentReference:
    """
    Bind a preset's portable scorer name to the scorer this deployment registers under it.

    Returns:
        RegisteredComponentReference: The scorer's current registration, as a launch would select it.

    Raises:
        ValueError: If this deployment registers no scorer under the name.
    """
    from pyrit.registry.components.scorer_registry import ScorerRegistry

    scorer = ScorerRegistry.get_registry_singleton().instances.get(name)
    if scorer is None:
        raise ValueError(f"Objective scorer '{name}' is not registered in this deployment.")
    return RegisteredComponentReference(registry_name=name, identifier_hash=scorer.get_identifier().hash)


@dataclass(frozen=True)
class _LaunchDefaultBinding:
    """How one ``launch_defaults`` field reaches a run request in this deployment."""

    bind: Callable[[Any], Any]
    is_supported: Callable[[ScenarioMetadata], bool]


# One entry per ``ScenarioPresetLaunchDefaults`` field, keyed by the run-request field it fills.
# A preset stores portable values; each binder turns one into what this deployment's launch uses.
_LAUNCH_DEFAULT_BINDINGS: Mapping[str, _LaunchDefaultBinding] = MappingProxyType(
    {
        "objective_scorer": _LaunchDefaultBinding(
            bind=bind_registered_scorer,
            is_supported=lambda metadata: metadata.supports_objective_scorer,
        ),
    }
)


@dataclass(frozen=True)
class _ResolvedLaunchDefaults:
    """
    Effective values for a preset's launch-default fields and where each came from.

    ``errors`` names each field whose preset value could not be bound in this deployment;
    such a field keeps the ``"preset"`` source and a ``None`` value.
    """

    values: Mapping[str, Any]
    sources: Mapping[str, LaunchValueSource]
    errors: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


def _resolve_launch_defaults(
    *,
    preset: ScenarioPreset,
    launch: _LaunchSource,
    unsupported_fields: AbstractSet[str] = frozenset(),
) -> _ResolvedLaunchDefaults:
    """
    Apply launch, then preset, then scenario-default precedence to each launch-default field.

    A ``None`` value means the scenario applies its own default. A field named in
    ``unsupported_fields`` is never applied: if the launch or preset set it, its source is
    ``"unsupported"`` so callers can say the scenario keeps its own value. A preset value is
    bound to this deployment here, once, so the same binding serves readiness and execution.

    Returns:
        _ResolvedLaunchDefaults: The effective value and source of every launch-default field.
    """
    preset_defaults = preset.launch_defaults
    values: dict[str, Any] = {}
    sources: dict[str, LaunchValueSource] = {}
    errors: dict[str, str] = {}
    for field_name, binding in _LAUNCH_DEFAULT_BINDINGS.items():
        launch_value = getattr(launch, field_name)
        preset_value = None if preset_defaults is None else getattr(preset_defaults, field_name)
        configured = launch_value is not None or preset_value is not None
        if configured and field_name in unsupported_fields:
            values[field_name], sources[field_name] = None, "unsupported"
        elif launch_value is not None:
            values[field_name], sources[field_name] = launch_value, "launch"
        elif preset_value is not None:
            sources[field_name] = "preset"
            try:
                values[field_name] = binding.bind(preset_value)
            except ValueError as exc:
                values[field_name], errors[field_name] = None, str(exc)
        else:
            values[field_name], sources[field_name] = None, "scenario_default"
    return _ResolvedLaunchDefaults(
        values=MappingProxyType(values), sources=MappingProxyType(sources), errors=MappingProxyType(errors)
    )


def resolve_scenario_preset_run_request(*, preset: ScenarioPreset, launch: _LaunchSource) -> RunScenarioRequest:
    """
    Combine a preset with the target and execution fields it deliberately omits.

    Launch-default fields take the launch value, then the preset's, then the scenario default.

    Returns:
        RunScenarioRequest: An ordinary scenario run request.

    Raises:
        ValueError: If a preset launch default the launch does not override cannot be bound here.
    """
    launch_defaults = _resolve_launch_defaults(preset=preset, launch=launch)
    if launch_defaults.errors:
        raise ValueError(" ".join(launch_defaults.errors.values()))
    return _build_run_request(preset=preset, launch=launch, launch_defaults=launch_defaults)


def _build_run_request(
    *, preset: ScenarioPreset, launch: _LaunchSource, launch_defaults: _ResolvedLaunchDefaults
) -> RunScenarioRequest:
    """
    Build a run request from a preset, its launch, and the launch defaults already resolved for them.

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
        scoring_target_name=launch.scoring_target_name,
        initializers=launch.initializers,
        initializer_args=launch.initializer_args,
        operation_id=launch.operation_id,
        labels=launch.labels,
        **{**launch_defaults.values, **run_defaults},
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
    launch_value_sources: Mapping[str, LaunchValueSource] = field(default_factory=lambda: MappingProxyType({}))
    launch_default_errors: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

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
        scenario_registry: ScenarioRegistry | None = None,
    ) -> None:
        """
        Initialize the resolver with optional registry overrides.

        Args:
            suite_registry (ScenarioSuiteRegistry | None): Suite definitions to resolve.
            preset_registry (ScenarioPresetRegistry | None): Preset definitions to resolve.
            scenario_registry (ScenarioRegistry | None): Scenario capabilities that decide
                which launch defaults an entry accepts.
        """
        self._suite_registry = suite_registry
        self._preset_registry = preset_registry
        self._scenario_registry = scenario_registry

    def resolve(
        self, *, name: str, request: LaunchScenarioSuiteRequest | ScenarioSuiteReadinessRequest
    ) -> ResolvedScenarioSuiteLaunch:
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

            launch_defaults = _resolve_launch_defaults(
                preset=preset.preset,
                launch=request,
                unsupported_fields=self._unsupported_launch_fields(preset=preset.preset, launch=request),
            )
            resolved.append(
                ResolvedScenarioSuiteEntry(
                    entry_id=suite_entry.entry_id,
                    entry_position=position,
                    preset_name=suite_entry.preset_name,
                    preset_origin=preset.origin,
                    preset_version=preset.version,
                    request=_build_run_request(preset=preset.preset, launch=request, launch_defaults=launch_defaults),
                    launch_value_sources=launch_defaults.sources,
                    launch_default_errors=launch_defaults.errors,
                )
            )

        return ResolvedScenarioSuiteLaunch(suite=suite, entries=tuple(resolved))

    def _unsupported_launch_fields(self, *, preset: ScenarioPreset, launch: _LaunchSource) -> frozenset[str]:
        """
        Name the launch defaults this entry's scenario cannot accept.

        One launch value covers every suite entry, so a value a scenario cannot accept is
        skipped for that entry rather than failing the whole suite. A preset naming such a
        value is already rejected by ``check_preset``. The scenario registry is consulted
        only when a launch default is set, and an unregistered scenario is left for
        readiness to report.

        Returns:
            frozenset[str]: Launch-default field names to leave unset for this entry.
        """
        configured = [field_name for field_name in _LAUNCH_DEFAULT_BINDINGS if getattr(launch, field_name) is not None]
        if not configured:
            return frozenset()
        metadata = self._get_scenario_registry().get_registered_class_metadata(preset.scenario_name)
        if metadata is None:
            return frozenset()
        return frozenset(
            field_name for field_name in configured if not _LAUNCH_DEFAULT_BINDINGS[field_name].is_supported(metadata)
        )

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

    def _get_scenario_registry(self) -> ScenarioRegistry:
        """Return the injected scenario registry or process singleton."""
        if self._scenario_registry is not None:
            return self._scenario_registry
        from pyrit.registry.components.scenario_registry import ScenarioRegistry

        return ScenarioRegistry.get_registry_singleton()
