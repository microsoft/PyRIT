# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset service."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.backend.models.scenario_presets import ResolveScenarioPresetRequest
from pyrit.backend.services.scenario_preset_service import (
    ScenarioPresetNotFoundError,
    ScenarioPresetService,
    get_scenario_preset_service,
)
from pyrit.models import Parameter
from pyrit.models.catalog import RegisteredScenario, ScenarioPreset
from pyrit.models.catalog.scenario import ScenarioRunSizeEstimate
from pyrit.registry import ScenarioPresetConflictError

SCENARIO_NAME = "foundry.red_team_agent"


def _registered_scenario(
    *,
    all_techniques: list[str] | None = None,
    aggregate_techniques: list[str] | None = None,
    supported_parameters: list[str] | None = None,
    baseline_policy: str = "enabled",
) -> RegisteredScenario:
    """Build a registered scenario with only the fields preset validation reads."""
    techniques = all_techniques if all_techniques is not None else ["crescendo", "flip"]
    return RegisteredScenario(
        scenario_name=SCENARIO_NAME,
        scenario_type="RedTeamAgentScenario",
        description="Test scenario",
        default_technique=techniques[0],
        all_techniques=techniques,
        aggregate_techniques=aggregate_techniques or [],
        default_datasets=["harmbench"],
        baseline_policy=baseline_policy,
        supported_parameters=[
            Parameter(name=name, description="", param_type=str) for name in (supported_parameters or ["max_turns"])
        ],
        default_run_size=ScenarioRunSizeEstimate.unavailable(),
    )


@pytest.fixture
def service(tmp_path: Path) -> ScenarioPresetService:
    """Create a service backed by an isolated preset directory."""
    instance = ScenarioPresetService()
    instance.configure_source(str(tmp_path))
    return instance


@pytest.fixture
def registered_scenario() -> RegisteredScenario:
    """Patch the scenario lookup so presets resolve against a known scenario."""
    scenario = _registered_scenario()
    with patch("pyrit.backend.services.scenario_preset_service.get_scenario_service") as mock_factory:
        mock_factory.return_value.get_scenario_async = AsyncMock(return_value=scenario)
        yield scenario


def _preset(name: str = "quick_scan", **overrides: object) -> ScenarioPreset:
    """Build a preset that resolves cleanly against the fixture scenario."""
    fields: dict[str, object] = {"name": name, "scenario_name": SCENARIO_NAME}
    fields.update(overrides)
    return ScenarioPreset(**fields)  # type: ignore[arg-type]


class TestPresetCrud:
    """CRUD behavior over the configured storage source."""

    async def test_list_presets_is_empty_when_nothing_is_stored(
        self, service: ScenarioPresetService, tmp_path: Path, registered_scenario: RegisteredScenario
    ) -> None:
        response = await service.list_presets_async()

        assert response.items == []
        assert response.source == str(tmp_path)

    async def test_list_presets_is_sorted_by_name(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        for name in ("zebra", "alpha", "middle"):
            await service.save_preset_async(preset=_preset(name), expected_version=None)

        response = await service.list_presets_async()

        assert [item.preset.name for item in response.items] == ["alpha", "middle", "zebra"]

    async def test_get_preset_returns_none_when_not_stored(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        assert await service.get_preset_async(name="missing") is None

    async def test_saved_preset_round_trips_every_field(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        preset = _preset(
            description="Nightly smoke",
            techniques=["crescendo"],
            dataset_names=["harmbench"],
            max_dataset_size=25,
            dataset_filters={"harm_categories": ["violence"]},
            include_baseline=True,
            scenario_params={"max_turns": 3},
        )

        await service.save_preset_async(preset=preset, expected_version=None)
        read_back = await service.get_preset_async(name=preset.name)

        assert read_back is not None
        assert read_back.preset == preset

    async def test_update_with_the_version_from_a_read_succeeds(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        created = await service.save_preset_async(preset=_preset(), expected_version=None)

        updated = await service.save_preset_async(
            preset=_preset(description="changed"), expected_version=created.version
        )

        assert updated.preset.description == "changed"
        assert updated.version != created.version

    async def test_update_with_a_stale_version_conflicts(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        created = await service.save_preset_async(preset=_preset(), expected_version=None)
        await service.save_preset_async(preset=_preset(description="first"), expected_version=created.version)

        with pytest.raises(ScenarioPresetConflictError):
            await service.save_preset_async(preset=_preset(description="second"), expected_version=created.version)

    async def test_create_over_an_existing_preset_conflicts(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)

        with pytest.raises(ScenarioPresetConflictError):
            await service.save_preset_async(preset=_preset(), expected_version=None)

    async def test_delete_removes_the_stored_preset(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)

        await service.delete_preset_async(name="quick_scan")

        assert await service.get_preset_async(name="quick_scan") is None

    async def test_delete_reports_a_missing_preset_rather_than_succeeding_silently(
        self, service: ScenarioPresetService
    ) -> None:
        with pytest.raises(ScenarioPresetNotFoundError):
            await service.delete_preset_async(name="missing")


class TestAdvisoryValidation:
    """Unresolvable references are reported, never enforced."""

    async def test_a_resolvable_preset_has_no_issues(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        saved = await service.save_preset_async(
            preset=_preset(techniques=["crescendo"], scenario_params={"max_turns": 2}), expected_version=None
        )

        assert saved.issues == []

    async def test_an_unregistered_scenario_is_saved_and_reported(self, service: ScenarioPresetService) -> None:
        with patch("pyrit.backend.services.scenario_preset_service.get_scenario_service") as mock_factory:
            mock_factory.return_value.get_scenario_async = AsyncMock(return_value=None)
            saved = await service.save_preset_async(preset=_preset(), expected_version=None)

            read_back = await service.get_preset_async(name="quick_scan")

        assert read_back is not None
        assert [issue.field for issue in saved.issues] == ["scenario_name"]
        assert "not registered" in saved.issues[0].message

    async def test_unknown_techniques_are_reported_without_blocking_the_save(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        saved = await service.save_preset_async(
            preset=_preset(techniques=["crescendo", "not_a_technique"]), expected_version=None
        )

        assert [issue.field for issue in saved.issues] == ["techniques"]
        assert "not_a_technique" in saved.issues[0].message
        assert await service.get_preset_async(name="quick_scan") is not None

    async def test_an_aggregate_technique_is_not_reported_as_unknown(self, service: ScenarioPresetService) -> None:
        scenario = _registered_scenario(aggregate_techniques=["all"])
        with patch("pyrit.backend.services.scenario_preset_service.get_scenario_service") as mock_factory:
            mock_factory.return_value.get_scenario_async = AsyncMock(return_value=scenario)
            saved = await service.save_preset_async(preset=_preset(techniques=["all"]), expected_version=None)

        assert saved.issues == []

    async def test_undeclared_scenario_parameters_are_reported(
        self, service: ScenarioPresetService, registered_scenario: RegisteredScenario
    ) -> None:
        saved = await service.save_preset_async(
            preset=_preset(scenario_params={"max_turns": 1, "mystery": 2}), expected_version=None
        )

        assert [issue.field for issue in saved.issues] == ["scenario_params"]
        assert "mystery" in saved.issues[0].message

    async def test_a_baseline_the_scenario_forbids_is_reported(self, service: ScenarioPresetService) -> None:
        scenario = _registered_scenario(baseline_policy="forbidden")
        with patch("pyrit.backend.services.scenario_preset_service.get_scenario_service") as mock_factory:
            mock_factory.return_value.get_scenario_async = AsyncMock(return_value=scenario)
            saved = await service.save_preset_async(preset=_preset(include_baseline=True), expected_version=None)

        assert [issue.field for issue in saved.issues] == ["include_baseline"]

    async def test_an_omitted_baseline_is_not_reported_when_the_scenario_forbids_one(
        self, service: ScenarioPresetService
    ) -> None:
        scenario = _registered_scenario(baseline_policy="forbidden")
        with patch("pyrit.backend.services.scenario_preset_service.get_scenario_service") as mock_factory:
            mock_factory.return_value.get_scenario_async = AsyncMock(return_value=scenario)
            saved = await service.save_preset_async(preset=_preset(), expected_version=None)

        assert saved.issues == []


class TestRunRequestResolution:
    """A preset plus launch fields becomes an ordinary run request."""

    def test_unset_run_fields_fall_back_to_the_request_defaults(self) -> None:
        resolved = ScenarioPresetService.resolve_run_request(
            preset=_preset(), launch=ResolveScenarioPresetRequest(target_name="gpt4")
        )

        assert resolved.max_concurrency == 10
        assert resolved.max_retries == 0

    def test_explicit_run_fields_are_applied(self) -> None:
        resolved = ScenarioPresetService.resolve_run_request(
            preset=_preset(),
            launch=ResolveScenarioPresetRequest(target_name="gpt4", max_concurrency=4, max_retries=2),
        )

        assert resolved.max_concurrency == 4
        assert resolved.max_retries == 2

    def test_unset_preset_fields_stay_unset_so_the_scenario_default_still_applies(self) -> None:
        resolved = ScenarioPresetService.resolve_run_request(
            preset=_preset(), launch=ResolveScenarioPresetRequest(target_name="gpt4")
        )

        assert resolved.techniques is None
        assert resolved.dataset_names is None
        assert resolved.max_dataset_size is None
        assert resolved.dataset_filters is None
        assert resolved.include_baseline is None
        assert resolved.scenario_params is None

    def test_a_baseline_explicitly_disabled_by_the_preset_is_preserved(self) -> None:
        resolved = ScenarioPresetService.resolve_run_request(
            preset=_preset(include_baseline=False), launch=ResolveScenarioPresetRequest(target_name="gpt4")
        )

        assert resolved.include_baseline is False

    def test_every_preset_field_reaches_the_run_request(self) -> None:
        preset = _preset(
            techniques=["crescendo"],
            dataset_names=["harmbench"],
            max_dataset_size=25,
            dataset_filters={"harm_categories": ["violence"]},
            include_baseline=True,
            scenario_params={"max_turns": 3},
        )

        resolved = ScenarioPresetService.resolve_run_request(
            preset=preset, launch=ResolveScenarioPresetRequest(target_name="gpt4")
        )

        assert resolved.scenario_name == preset.scenario_name
        assert resolved.techniques == preset.techniques
        assert resolved.dataset_names == preset.dataset_names
        assert resolved.max_dataset_size == preset.max_dataset_size
        assert resolved.dataset_filters == preset.dataset_filters
        assert resolved.include_baseline == preset.include_baseline
        assert resolved.scenario_params == preset.scenario_params

    def test_launch_fields_reach_the_run_request(self) -> None:
        launch = ResolveScenarioPresetRequest(
            target_name="gpt4",
            adversarial_target_name="adversary",
            initializers=["scorer"],
            initializer_args={"scorer": {"threshold": 0.5}},
            labels={"operator": "red"},
        )

        resolved = ScenarioPresetService.resolve_run_request(preset=_preset(), launch=launch)

        assert resolved.target_name == "gpt4"
        assert resolved.adversarial_target_name == "adversary"
        assert resolved.initializers == ["scorer"]
        assert resolved.initializer_args == {"scorer": {"threshold": 0.5}}
        assert resolved.labels == {"operator": "red"}


class TestServiceConfiguration:
    """Storage source selection."""

    def test_the_service_is_a_singleton(self) -> None:
        assert get_scenario_preset_service() is get_scenario_preset_service()

    def test_configure_source_replaces_the_storage_location(self, tmp_path: Path) -> None:
        instance = ScenarioPresetService()

        instance.configure_source(str(tmp_path))

        assert instance._get_storage().display_source == str(tmp_path)

    def test_an_unconfigured_service_falls_back_to_the_default_directory(self) -> None:
        instance = ScenarioPresetService()

        assert instance._get_storage().display_source.endswith("scenario_presets")
