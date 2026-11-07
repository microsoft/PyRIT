# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset service."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services import scenario_preset_service as preset_service_module
from pyrit.backend.services.scenario_preset_service import (
    ScenarioPresetService,
    get_scenario_preset_service,
)
from pyrit.models import Parameter
from pyrit.models.catalog import PresetIssue, RegisteredScenario, ScenarioPreset
from pyrit.models.catalog.scenario import ScenarioRunSizeComponent, ScenarioRunSizeEstimate
from pyrit.models.scenario_dataset import DatasetSourceInput, ScenarioDatasetRequest
from pyrit.registry import (
    ScenarioPresetConflictError,
    ScenarioPresetNameRegisteredError,
    ScenarioPresetRegistry,
    ScenarioPresetStorage,
)

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
    parameters = [
        Parameter(name=name, description="", param_type=str) for name in (supported_parameters or ["max_turns"])
    ]
    return RegisteredScenario(
        scenario_name=SCENARIO_NAME,
        scenario_type="RedTeamAgentScenario",
        description="Test scenario",
        default_technique=techniques[0],
        all_techniques=techniques,
        aggregate_techniques=aggregate_techniques or [],
        default_datasets=["harmbench"],
        baseline_policy=baseline_policy,
        supported_parameters=parameters,
        supported_parameter_names=[parameter.name for parameter in parameters],
        default_run_size=ScenarioRunSizeEstimate.unavailable(),
    )


def _estimate(count: int) -> ScenarioRunSizeEstimate:
    """Build an exact run-size estimate of *count* attacks."""
    return ScenarioRunSizeEstimate(
        total_attack_count=count,
        components=[ScenarioRunSizeComponent(label="Preset sweep", count=count)],
    )


@pytest.fixture
def preset_registry(tmp_path: Path) -> Iterator[ScenarioPresetRegistry]:
    """
    Install a preset registry backed by an isolated directory as the process singleton.

    Validation belongs to the registry and is tested against the real token grammar there,
    so it is stubbed out here. These tests cover what the service does with the result.
    """
    instance = ScenarioPresetRegistry(storage=ScenarioPresetStorage(source=str(tmp_path)))
    with (
        patch.object(ScenarioPresetRegistry, "get_registry_singleton", return_value=instance),
        patch.object(instance, "check_preset", return_value=[]),
    ):
        yield instance


@pytest.fixture
def service(preset_registry: ScenarioPresetRegistry) -> ScenarioPresetService:
    """Create a service reading through the isolated preset registry."""
    return ScenarioPresetService()


@pytest.fixture
def registered_scenario() -> Iterator[RegisteredScenario]:
    """Patch the scenario lookup so presets resolve against a known scenario."""
    scenario = _registered_scenario()
    with patch("pyrit.backend.services.scenario_preset_service.get_scenario_service") as mock_factory:
        mock_factory.return_value.get_scenario_async = AsyncMock(return_value=scenario)
        mock_factory.return_value.estimate_scenario_run_size_async = AsyncMock(return_value=_estimate(12))
        yield scenario


@pytest.fixture
def scenario_service(registered_scenario: RegisteredScenario) -> MagicMock:
    """Return the scenario service the preset service calls while it is patched."""
    return cast("MagicMock", preset_service_module.get_scenario_service())


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
            author="Grace Hopper",
            description="Nightly smoke",
            techniques=["crescendo"],
            datasets=ScenarioDatasetRequest(
                sources=[DatasetSourceInput(name="harmbench", max_size=10)],
                max_total=25,
                filters={"harm_categories": ["violence"]},
            ),
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
        assert await service.delete_preset_async(name="missing") is False


class TestRegisteredPresets:
    """A preset contributed by code has no stored document behind it."""

    async def test_listing_a_registered_preset_reports_no_version(
        self,
        service: ScenarioPresetService,
        preset_registry: ScenarioPresetRegistry,
        registered_scenario: RegisteredScenario,
    ) -> None:
        preset_registry.register_preset(preset=_preset("builtin_smoke"))

        response = await service.list_presets_async(include_estimates=False)

        assert [(item.preset.name, item.origin, item.version) for item in response.items] == [
            ("builtin_smoke", "registered", None)
        ]

    async def test_getting_a_registered_preset_reports_no_version(
        self,
        service: ScenarioPresetService,
        preset_registry: ScenarioPresetRegistry,
        registered_scenario: RegisteredScenario,
    ) -> None:
        preset_registry.register_preset(preset=_preset("builtin_smoke"))

        response = await service.get_preset_async(name="builtin_smoke")

        assert response is not None
        assert response.origin == "registered"
        assert response.version is None

    async def test_a_stored_preset_is_still_reported_as_stored_and_versioned(
        self,
        service: ScenarioPresetService,
        preset_registry: ScenarioPresetRegistry,
        registered_scenario: RegisteredScenario,
    ) -> None:
        preset_registry.register_preset(preset=_preset("builtin_smoke"))
        await service.save_preset_async(preset=_preset("nightly"), expected_version=None)

        response = await service.list_presets_async(include_estimates=False)
        by_name = {item.preset.name: item for item in response.items}

        assert by_name["nightly"].origin == "stored"
        assert by_name["nightly"].version is not None
        assert by_name["builtin_smoke"].version is None

    async def test_saving_over_a_registered_name_is_rejected(
        self,
        service: ScenarioPresetService,
        preset_registry: ScenarioPresetRegistry,
        registered_scenario: RegisteredScenario,
    ) -> None:
        preset_registry.register_preset(preset=_preset("builtin_smoke"))

        with pytest.raises(ScenarioPresetNameRegisteredError):
            await service.save_preset_async(preset=_preset("builtin_smoke"), expected_version=None)


class TestAdvisoryIssues:
    """The service attaches the registry's advisory issues to the wire response."""

    async def test_registry_issues_reach_a_saved_preset(
        self, service: ScenarioPresetService, preset_registry: ScenarioPresetRegistry
    ) -> None:
        issue = PresetIssue(field="scenario_name", message="Scenario 'nope' is not registered in this deployment.")

        with patch.object(preset_registry, "check_preset", return_value=[issue]):
            saved = await service.save_preset_async(preset=_preset(), expected_version=None)

        assert saved.issues == [issue]

    async def test_registry_issues_reach_a_listed_preset(
        self, service: ScenarioPresetService, preset_registry: ScenarioPresetRegistry
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)
        issue = PresetIssue(field="techniques", message="Technique 'nope' is not available.")

        with patch.object(preset_registry, "check_preset", return_value=[issue]):
            response = await service.list_presets_async(include_estimates=False)

        assert response.items[0].issues == [issue]

    async def test_an_unresolvable_preset_is_still_saved(
        self, service: ScenarioPresetService, preset_registry: ScenarioPresetRegistry
    ) -> None:
        issue = PresetIssue(field="scenario_name", message="Scenario 'nope' is not registered in this deployment.")

        with patch.object(preset_registry, "check_preset", return_value=[issue]):
            await service.save_preset_async(preset=_preset(), expected_version=None)

        assert await service.get_preset_async(name="quick_scan") is not None


class TestRunSizeEstimates:
    """Sizing a preset against its own configuration rather than the scenario default."""

    async def test_every_listed_preset_carries_its_own_size(
        self, service: ScenarioPresetService, scenario_service: MagicMock
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)

        response = await service.list_presets_async(include_estimates=True)

        assert response.items[0].run_size is not None
        assert response.items[0].run_size.total_attack_count == 12

    async def test_the_preset_configuration_is_what_gets_sized(
        self, service: ScenarioPresetService, scenario_service: MagicMock
    ) -> None:
        preset = _preset(
            techniques=["crescendo"],
            datasets=ScenarioDatasetRequest(
                sources=[DatasetSourceInput(name="harmbench", max_size=10)],
                max_total=25,
                filters={"harm_categories": ["violence"]},
            ),
            include_baseline=True,
            scenario_params={"max_turns": 3},
        )
        await service.save_preset_async(preset=preset, expected_version=None)

        await service.list_presets_async(include_estimates=True)

        estimate_call = scenario_service.estimate_scenario_run_size_async.call_args
        assert estimate_call.kwargs["scenario_name"] == SCENARIO_NAME
        request = estimate_call.kwargs["request"]
        assert request.techniques == ["crescendo"]
        assert request.datasets is not None
        assert request.datasets.sources is not None
        assert request.datasets.sources[0].name == "harmbench"
        assert request.datasets.max_total == 25
        assert request.datasets.filters == {"harm_categories": ["violence"]}
        assert request.include_baseline is True
        assert request.scenario_params == {"max_turns": 3}

    async def test_a_preset_that_sets_nothing_is_sized_without_overrides(
        self, service: ScenarioPresetService, scenario_service: MagicMock
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)

        await service.list_presets_async(include_estimates=True)

        request = scenario_service.estimate_scenario_run_size_async.call_args.kwargs["request"]
        assert request.techniques is None
        assert request.datasets is None
        assert request.target_name is None

    async def test_estimates_are_off_by_default(
        self, service: ScenarioPresetService, scenario_service: MagicMock
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)

        response = await service.list_presets_async()

        assert response.items[0].run_size is None
        scenario_service.estimate_scenario_run_size_async.assert_not_called()

    async def test_one_preset_that_cannot_be_sized_does_not_hide_the_others(
        self, service: ScenarioPresetService, scenario_service: MagicMock
    ) -> None:
        for name in ("alpha", "broken"):
            await service.save_preset_async(preset=_preset(name), expected_version=None)
        scenario_service.estimate_scenario_run_size_async = AsyncMock(
            side_effect=[_estimate(7), ValueError("unknown technique 'nope'")]
        )

        response = await service.list_presets_async(include_estimates=True)

        assert [item.preset.name for item in response.items] == ["alpha", "broken"]
        assert response.items[0].run_size is not None
        assert response.items[0].run_size.total_attack_count == 7
        assert response.items[1].run_size is None

    async def test_a_sizer_that_returns_nothing_leaves_the_size_unset(
        self, service: ScenarioPresetService, scenario_service: MagicMock
    ) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)
        scenario_service.estimate_scenario_run_size_async = AsyncMock(return_value=None)

        response = await service.list_presets_async(include_estimates=True)

        assert response.items[0].run_size is None

    async def test_a_single_read_stays_cheap(self, service: ScenarioPresetService, scenario_service: MagicMock) -> None:
        await service.save_preset_async(preset=_preset(), expected_version=None)
        scenario_service.estimate_scenario_run_size_async.reset_mock()

        read_back = await service.get_preset_async(name="quick_scan")

        assert read_back is not None
        assert read_back.run_size is None
        scenario_service.estimate_scenario_run_size_async.assert_not_called()


class TestServiceConfiguration:
    """Storage source selection."""

    def test_the_service_is_a_singleton(self) -> None:
        assert get_scenario_preset_service() is get_scenario_preset_service()

    async def test_the_listed_source_is_the_registry_source(
        self, service: ScenarioPresetService, tmp_path: Path
    ) -> None:
        response = await service.list_presets_async(include_estimates=False)

        assert response.source == str(tmp_path)
