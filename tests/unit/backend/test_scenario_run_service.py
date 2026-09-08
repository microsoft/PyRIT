# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ScenarioRunService.
"""

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pyrit.backend.services.scenario_configuration_resolver as _resolver_mod
import pyrit.backend.services.scenario_run_service as _svc_mod
from pyrit.backend.services.scenario_run_service import (
    _DEFAULT_MAX_CONCURRENT_RUNS,
    ScenarioRunService,
)
from pyrit.converter import Converter
from pyrit.memory import ScenarioHistoryAggregate, ScenarioHistoryRunRecord
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    ScenarioAttackResultDelta,
    ScenarioProgressResult,
    ScenarioProgressScore,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
    ScoreStatus,
    SeedObjective,
    config_hash,
)
from pyrit.models.catalog.scenario import RunScenarioRequest
from pyrit.scenario.core import DatasetAttackConfiguration, DatasetConfiguration
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics
from unit.mocks import make_scenario_result


class _StubTechnique(ScenarioTechnique):
    """Minimal concrete ScenarioTechnique used to exercise converter-token parsing."""

    ALL = ("all", {"all"})
    EASY = ("easy", {"easy"})
    ROLE_PLAY = ("role_play", {"easy"})
    SINGLE_TURN = ("single_turn", {"easy"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "easy"}


def _patch_converter_registry(instances: dict[str, Any]):
    """Patch the converter registry singleton so ``.instances`` reflects ``instances``."""
    reg = MagicMock()
    reg.instances.get.side_effect = lambda name: instances.get(name)
    reg.instances.get_names.return_value = list(instances.keys())
    return patch.object(_resolver_mod.ConverterRegistry, "get_registry_singleton", return_value=reg)


_REGISTRY_PATCH_BASE = "pyrit.registry"
_MEMORY_PATCH = "pyrit.memory.CentralMemory.get_memory_instance"


@pytest.fixture(autouse=True)
def clear_service_cache():
    """Clear the singleton instance between tests."""
    _svc_mod._service_instance = None
    yield
    _svc_mod._service_instance = None


def _make_request(
    *,
    scenario_name: str = "foundry.red_team_agent",
    target_name: str = "my_target",
    initializers: list[str] | None = None,
    techniques: list[str] | None = None,
    scenario_result_id: str | None = None,
    dataset_names: list[str] | None = None,
    max_dataset_size: int | None = None,
    dataset_filters: dict[str, list[str]] | None = None,
    include_baseline: bool | None = None,
    scenario_params: dict[str, Any] | None = None,
) -> RunScenarioRequest:
    """Create a RunScenarioRequest for testing."""
    return RunScenarioRequest(
        scenario_name=scenario_name,
        target_name=target_name,
        initializers=initializers,
        techniques=techniques,
        scenario_result_id=scenario_result_id,
        dataset_names=dataset_names,
        max_dataset_size=max_dataset_size,
        dataset_filters=dataset_filters,
        include_baseline=include_baseline,
        scenario_params=scenario_params,
    )


def _make_db_scenario_result(
    *,
    result_id: str = "sr-uuid-1",
    scenario_name: str = "foundry.red_team_agent",
    run_state: ScenarioRunState = ScenarioRunState.IN_PROGRESS,
    attack_results: dict | None = None,
) -> MagicMock:
    """Create a mock ScenarioResult as returned by CentralMemory."""
    sr = MagicMock(spec=ScenarioResult)
    sr.id = result_id
    sr.scenario_name = scenario_name
    sr.scenario_version = 1
    sr.pyrit_version = "0.10.0"
    sr.scenario_run_state = run_state
    sr.scenario_identifier = None
    sr.get_techniques_used.return_value = []
    sr.attack_results = attack_results or {}
    sr.number_tries = 1
    sr.creation_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    sr.completion_time = datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc)
    sr.labels = {}
    sr.objective_achieved_rate.return_value = 0
    sr.get_display_groups.return_value = {}
    sr.display_group_map = {}
    sr.error_message = None
    sr.error_type = None
    return sr


def _make_history_record(
    *,
    result_id: str,
    run_state: ScenarioRunState,
) -> ScenarioHistoryRunRecord:
    scenario_result = make_scenario_result(scenario_name="foundry.red_team_agent", attack_results={})
    return ScenarioHistoryRunRecord(
        scenario_result_id=result_id,
        scenario_name=scenario_result.scenario_name,
        scenario_version=scenario_result.scenario_version,
        pyrit_version=scenario_result.pyrit_version,
        scenario_identifier=scenario_result.scenario_identifier.model_dump(mode="json"),
        objective_target_identifier={},
        status=run_state.value,
        labels={},
        created_at=scenario_result.creation_time,
        completed_at=scenario_result.completion_time,
        error_message=None,
        error_type=None,
        scenario_registry_name=None,
        plan_atomic_groups=None,
        plan_seed_id_map=None,
    )


@pytest.fixture
def mock_memory():
    """Patch CentralMemory.get_memory_instance to return a mock."""
    mock = MagicMock()
    mock.get_scenario_results.return_value = []
    mock.get_scenario_run_history_page.return_value = ([], {}, False)
    mock.get_scenario_history_aggregates.return_value = {}
    # Default: no error AttackResults linked to any scenario. Tests that exercise
    # the error fallback path explicitly set get_attack_results.return_value.
    mock.get_attack_results.return_value = []
    with patch(_MEMORY_PATCH, return_value=mock):
        yield mock


@pytest.fixture
def mock_all_registries(mock_memory):
    """Patch all registries and CentralMemory with valid defaults."""
    mock_scenario_instance = MagicMock()
    mock_scenario_instance.initialize_async = AsyncMock()
    mock_scenario_instance.run_async = AsyncMock()
    mock_scenario_instance._scenario_result_id = "sr-uuid-1"

    mock_scenario_class = MagicMock(return_value=mock_scenario_instance)
    mock_scenario_instance._technique_class = MagicMock()
    mock_scenario_instance._default_dataset_config = MagicMock()

    mock_sr = MagicMock()
    mock_sr.get_class.return_value = mock_scenario_class
    mock_sr.create_instance.return_value = mock_scenario_instance
    mock_sr.create_and_initialize_async = AsyncMock(return_value=mock_scenario_instance)

    mock_tr = MagicMock()
    mock_tr.instances.get.return_value = MagicMock()
    mock_tr.instances.get_names.return_value = ["my_target"]

    mock_ir = MagicMock()
    mock_ir.create_and_configure.return_value = MagicMock(initialize_async=AsyncMock())

    # By default, return a matching DB result for get_run / list_runs queries
    db_result = _make_db_scenario_result()
    mock_memory.get_scenario_results.return_value = [db_result]

    with (
        patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
        patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
        patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton", return_value=mock_ir),
    ):
        yield {
            "scenario_registry": mock_sr,
            "target_registry": mock_tr,
            "initializer_registry": mock_ir,
            "scenario_class": mock_scenario_class,
            "scenario_instance": mock_scenario_instance,
            "memory": mock_memory,
            "db_result": db_result,
        }


class TestScenarioRunServiceStartRun:
    """Tests for ScenarioRunService.start_run_async."""

    async def test_start_run_returns_running_status(self, mock_all_registries) -> None:
        """Test that starting a run returns RUNNING status with run_id = scenario_result_id."""
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        assert response.scenario_result_id == "sr-uuid-1"
        assert response.status == ScenarioRunState.IN_PROGRESS
        assert response.scenario_name == "foundry.red_team_agent"
        assert response.error is None

    async def test_start_run_invalid_scenario_raises_value_error(self, mock_memory) -> None:
        """Test that an invalid scenario name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_sr = MagicMock()
        mock_sr.get_class.side_effect = KeyError("'bad.scenario' not found in registry. Available: foo")
        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton"),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            with pytest.raises(ValueError, match="not found in registry"):
                await service.start_run_async(request=_make_request(scenario_name="bad.scenario"))

    async def test_start_run_invalid_target_raises_value_error(self, mock_memory) -> None:
        """Test that an invalid target name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_sr = MagicMock()
        mock_sr.get_class.return_value = MagicMock()

        mock_tr = MagicMock()
        mock_tr.instances.get.return_value = None
        mock_tr.instances.get_names.return_value = ["other_target"]

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            with pytest.raises(ValueError, match="my_target.*not found in registry"):
                await service.start_run_async(request=_make_request())

    async def test_start_run_invalid_initializer_raises_value_error(self, mock_memory) -> None:
        """Test that an invalid initializer name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_sr = MagicMock()
        mock_sr.get_class.return_value = MagicMock()

        mock_ir = MagicMock()
        mock_ir.create_and_configure.side_effect = KeyError("'bad_init' not found")

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton"),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton", return_value=mock_ir),
        ):
            with pytest.raises(ValueError, match="Initializer not found"):
                await service.start_run_async(request=_make_request(initializers=["bad_init"]))

    async def test_start_run_invalid_technique_raises_value_error(self, mock_memory) -> None:
        """Test that an invalid technique name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_technique_class = MagicMock(side_effect=ValueError("not a valid technique"))
        mock_technique_class.__iter__ = MagicMock(return_value=iter([MagicMock(value="valid_strat")]))

        mock_instance = MagicMock(_technique_class=mock_technique_class)
        mock_scenario_class = MagicMock(return_value=mock_instance)

        mock_sr = MagicMock()
        mock_sr.get_class.return_value = mock_scenario_class

        mock_tr = MagicMock()
        mock_tr.instances.get.return_value = MagicMock()

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            with pytest.raises(ValueError, match="Technique.*not found for scenario"):
                await service.start_run_async(request=_make_request(techniques=["bad_technique"]))

    async def test_start_run_scenario_not_no_arg_instantiable_raises(self, mock_memory) -> None:
        """If introspection is required and ``scenario_class()`` fails, surface a ValueError."""
        service = ScenarioRunService()

        # scenario_class() raises -> introspection fails
        mock_scenario_class = MagicMock(side_effect=TypeError("missing required arg 'foo'"))

        mock_sr = MagicMock()
        mock_sr.get_class.return_value = mock_scenario_class

        mock_tr = MagicMock()
        mock_tr.instances.get.return_value = MagicMock()

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            with pytest.raises(ValueError, match="not instantiable without arguments"):
                # techniques forces the introspection path
                await service.start_run_async(request=_make_request(techniques=["any"]))

    async def test_start_run_passes_valid_techniques_through(self, mock_all_registries) -> None:
        """A valid technique list is converted to enum values and forwarded to initialize_async."""
        technique_a = MagicMock(value="strat_a")
        technique_b = MagicMock(value="strat_b")

        def _lookup(name):
            return {"strat_a": technique_a, "strat_b": technique_b}[name]

        mock_technique_class = MagicMock(side_effect=_lookup)
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._technique_class = mock_technique_class

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(techniques=["strat_a", "strat_b"]))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        assert init_call.kwargs["scenario_techniques"] == [technique_a, technique_b]

    async def test_jailbreak_explicit_selection_and_params_reach_registry_unchanged(self, mock_all_registries) -> None:
        """An explicit Jailbreak technique never adds the default aggregate or other techniques."""

        class _JailbreakTechnique(ScenarioTechnique):
            ALL = ("all", {"all"})
            DEFAULT = ("default", {"default"})
            PROMPT_SENDING = ("prompt_sending", {"default"})
            CONTEXT_COMPLIANCE = ("context_compliance", {"default"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all", "default"}

        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._technique_class = _JailbreakTechnique
        objective_target = mock_all_registries["target_registry"].instances.get.return_value
        scenario_params = {"num_jailbreaks": 2, "num_jailbreak_attempts": 1}

        service = ScenarioRunService()
        await service.start_run_async(
            request=_make_request(
                scenario_name="airt.jailbreak",
                techniques=["prompt_sending"],
                include_baseline=False,
                scenario_params=scenario_params,
            )
        )

        mock_all_registries["scenario_registry"].create_and_initialize_async.assert_awaited_once_with(
            "airt.jailbreak",
            scenario_params=scenario_params,
            scenario_result_id=None,
            objective_target=objective_target,
            max_concurrency=10,
            max_retries=0,
            include_baseline=False,
            scenario_techniques=[_JailbreakTechnique.PROMPT_SENDING],
        )

    async def test_start_run_forwards_include_baseline(self, mock_all_registries) -> None:
        service = ScenarioRunService()
        request = _make_request()
        request.include_baseline = False

        await service.start_run_async(request=request)

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        assert init_call.kwargs["include_baseline"] is False

    async def test_start_run_max_dataset_size_uses_default_config(self, mock_all_registries) -> None:
        """``max_dataset_size`` with no ``dataset_names`` reuses the scenario's default config."""
        default_config = MagicMock()
        default_config.max_dataset_size = 100  # original
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._default_dataset_config = default_config

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(max_dataset_size=5))

        # max_dataset_size on the default config was overridden
        assert default_config.max_dataset_size == 5
        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        assert init_call.kwargs["dataset_config"] is default_config

    async def test_start_run_dataset_names_preserves_subclass_config_type(self, mock_all_registries) -> None:
        """``dataset_names`` rebuilds the config using the scenario's own DatasetConfiguration subclass.

        Regression: passing ``dataset_names`` via the backend used to construct
        a plain ``DatasetConfiguration``, silently losing subclass behavior
        (e.g. ``EncodingDatasetConfiguration``'s objective shaping).
        """

        # Create a marker subclass so we can verify type preservation without
        # depending on any concrete scenario implementation.
        class _MarkerDatasetConfiguration(DatasetConfiguration):
            pass

        default_config = _MarkerDatasetConfiguration(dataset_names=["original"], max_dataset_size=100)
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._default_dataset_config = default_config

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(dataset_names=["custom_a", "custom_b"], max_dataset_size=3))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        built_config = init_call.kwargs["dataset_config"]

        # Type is preserved (this is the regression assertion)
        assert type(built_config) is _MarkerDatasetConfiguration
        # And carries the caller-supplied values, not the scenario defaults
        assert built_config.dataset_names == ["custom_a", "custom_b"]
        assert built_config.max_dataset_size == 3
        # The original default config is not mutated when a fresh dataset_names is supplied
        assert default_config.dataset_names == ["original"]
        assert default_config.max_dataset_size == 100

    async def test_start_run_dataset_names_without_max_dataset_size_preserves_subclass(
        self, mock_all_registries
    ) -> None:
        """``dataset_names`` alone (no ``max_dataset_size``) still preserves the subclass type."""

        class _MarkerDatasetConfiguration(DatasetConfiguration):
            pass

        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._default_dataset_config = _MarkerDatasetConfiguration(dataset_names=["original"])

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(dataset_names=["only_this"]))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        built_config = init_call.kwargs["dataset_config"]
        assert type(built_config) is _MarkerDatasetConfiguration
        assert built_config.dataset_names == ["only_this"]
        assert built_config.max_dataset_size is None

    async def test_start_run_dataset_names_rejects_incompatible_subclass_constructor(self, mock_all_registries) -> None:
        """Reject overrides that cannot preserve scenario-specific dataset configuration."""

        class _RequiresExtraArgConfiguration(DatasetConfiguration):
            def __init__(self, *, required_extra: str, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self._required_extra = required_extra

        scenario_instance = mock_all_registries["scenario_instance"]
        # Build the default with the required kwarg so introspection succeeds.
        scenario_instance._default_dataset_config = _RequiresExtraArgConfiguration(
            required_extra="seeded", dataset_names=["original"]
        )

        service = ScenarioRunService()
        with pytest.raises(
            ValueError,
            match="does not support overriding dataset names.*_RequiresExtraArgConfiguration",
        ):
            await service.start_run_async(request=_make_request(dataset_names=["custom"]))

        mock_all_registries["scenario_registry"].create_and_initialize_async.assert_not_awaited()

    async def test_start_run_dataset_filters_new_config(self, mock_all_registries) -> None:
        """``dataset_filters`` with ``dataset_names`` builds a config carrying the filters."""

        class _MarkerDatasetConfiguration(DatasetConfiguration):
            pass

        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._default_dataset_config = _MarkerDatasetConfiguration(dataset_names=["original"])

        service = ScenarioRunService()
        await service.start_run_async(
            request=_make_request(
                dataset_names=["custom"],
                max_dataset_size=7,
                dataset_filters={"harm_categories": ["cyber"]},
            )
        )

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        built_config = init_call.kwargs["dataset_config"]
        assert built_config.dataset_names == ["custom"]
        assert built_config.max_dataset_size == 7
        assert built_config.filters == {"harm_categories": ["cyber"]}

    async def test_start_run_dataset_filters_updates_default_config(self, mock_all_registries) -> None:
        """``dataset_filters`` with no ``dataset_names`` merges filters into the default config."""
        default_config = DatasetAttackConfiguration(dataset_names=["original"])
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._default_dataset_config = default_config

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(dataset_filters={"harm_categories": ["cyber"]}))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        built_config = init_call.kwargs["dataset_config"]
        assert built_config is default_config
        assert built_config.filters == {"harm_categories": ["cyber"]}

    async def test_start_run_dataset_names_introspection_failure_raises(self, mock_memory) -> None:
        """Passing ``dataset_names`` against a non-no-arg-instantiable scenario fails fast."""
        # Mirrors test_start_run_scenario_not_no_arg_instantiable_raises but for the dataset_names path.
        mock_scenario_class = MagicMock(
            side_effect=[
                TypeError("missing 1 required positional argument: 'objective_target'"),
            ]
        )
        mock_sr = MagicMock()
        mock_sr.get_class.return_value = mock_scenario_class

        mock_tr = MagicMock()
        mock_tr.instances.get.return_value = MagicMock()
        mock_tr.instances.get_names.return_value = ["my_target"]

        mock_ir = MagicMock()

        service = ScenarioRunService()

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton", return_value=mock_ir),
        ):
            with pytest.raises(ValueError, match="not instantiable without arguments"):
                await service.start_run_async(request=_make_request(dataset_names=["custom"]))

    async def test_start_run_max_dataset_size_with_dataset_names_uses_subclass_with_both(
        self, mock_all_registries
    ) -> None:
        """When both ``dataset_names`` and ``max_dataset_size`` are supplied, both flow into the subclass instance."""

        class _MarkerDatasetConfiguration(DatasetConfiguration):
            pass

        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._default_dataset_config = _MarkerDatasetConfiguration(
            dataset_names=["original"], max_dataset_size=99
        )

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(dataset_names=["a", "b"], max_dataset_size=7))

        built_config = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args.kwargs[
            "dataset_config"
        ]
        assert type(built_config) is _MarkerDatasetConfiguration
        assert built_config.dataset_names == ["a", "b"]
        assert built_config.max_dataset_size == 7

    async def test_start_run_exceeds_concurrent_limit(self, mock_all_registries) -> None:
        """Test that exceeding concurrent run limit raises ValueError."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        mock_sr = mock_all_registries["scenario_registry"]

        # A real run holds its permit until it finishes, so the background task has to stay
        # in flight for the limit to be reachable. The default AsyncMock returns immediately
        # and would hand every permit straight back.
        still_running = asyncio.Event()

        async def _block_until_released() -> None:
            await still_running.wait()

        scenario_instance.run_async = _block_until_released

        # Each call needs a unique scenario_result_id
        call_count = 0

        async def _set_unique_id(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            scenario_instance._scenario_result_id = f"sr-uuid-{call_count}"
            return scenario_instance

        mock_sr.create_and_initialize_async = AsyncMock(side_effect=_set_unique_id)

        try:
            # Fill up to the limit
            for _ in range(_DEFAULT_MAX_CONCURRENT_RUNS):
                await service.start_run_async(request=_make_request())

            # Next one should fail
            with pytest.raises(ValueError, match="Maximum concurrent runs"):
                await service.start_run_async(request=_make_request())
        finally:
            still_running.set()

    async def test_start_run_runs_initializers(self, mock_all_registries) -> None:
        """Test that initializers are run during start_run_async."""
        service = ScenarioRunService()
        mock_ir = mock_all_registries["initializer_registry"]
        mock_init_instance = mock_ir.create_and_configure.return_value

        response = await service.start_run_async(
            request=_make_request(initializers=["target", "load_default_datasets"])
        )

        assert response.status == ScenarioRunState.IN_PROGRESS
        assert mock_init_instance.initialize_async.await_count == 2

    async def test_start_run_passes_scenario_result_id_for_resume(self, mock_all_registries) -> None:
        """Test that scenario_result_id is passed to the registry for resumption."""
        service = ScenarioRunService()
        mock_sr = mock_all_registries["scenario_registry"]

        response = await service.start_run_async(request=_make_request(scenario_result_id="existing-result-uuid"))

        assert response.status == ScenarioRunState.IN_PROGRESS
        call = mock_sr.create_and_initialize_async.await_args
        assert call.args[0] == "foundry.red_team_agent"
        assert call.kwargs["scenario_result_id"] == "existing-result-uuid"

    async def test_start_run_omits_scenario_result_id_when_none(self, mock_all_registries) -> None:
        """Test that scenario_result_id is None when not provided in the request."""
        service = ScenarioRunService()
        mock_sr = mock_all_registries["scenario_registry"]

        await service.start_run_async(request=_make_request())

        call = mock_sr.create_and_initialize_async.await_args
        assert call.args[0] == "foundry.red_team_agent"
        assert call.kwargs["scenario_result_id"] is None

    async def test_start_run_keeps_event_loop_responsive(self, mock_all_registries) -> None:
        """Initialization is offloaded, so the loop keeps running while a run starts."""
        service = ScenarioRunService()

        def _slow_prepare(*, request: Any) -> Any:
            time.sleep(0.5)
            return mock_all_registries["scenario_instance"]

        beats = 0

        async def _heartbeat() -> None:
            nonlocal beats
            while True:
                await asyncio.sleep(0.01)
                beats += 1

        with patch.object(service, "_prepare_run_blocking", _slow_prepare):
            heartbeat = asyncio.create_task(_heartbeat())
            try:
                await service.start_run_async(request=_make_request())
            finally:
                heartbeat.cancel()

        # A blocked event loop yields zero heartbeats over the same window.
        assert beats > 10

    async def test_start_run_background_task_survives_handoff(self, mock_all_registries) -> None:
        """The background task must outlive start_run_async and actually execute the run."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        executed = asyncio.Event()

        async def _run_async() -> None:
            executed.set()

        scenario_instance.run_async = _run_async

        response = await service.start_run_async(request=_make_request())

        await asyncio.wait_for(executed.wait(), timeout=5)
        assert executed.is_set()
        assert response.status == ScenarioRunState.IN_PROGRESS

    async def test_start_run_marks_abandoned_prepare_cancelled(self, mock_all_registries) -> None:
        """A preparation that finishes after its caller left must not sit in CREATED forever."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "abandoned-id"
        finished = threading.Event()

        def _slow_prepare(*, request: Any) -> Any:
            time.sleep(0.5)
            finished.set()
            return scenario_instance

        with patch.object(service, "_prepare_run_blocking", _slow_prepare):
            with patch.object(service._memory, "try_update_scenario_run_state") as update_state:
                task = asyncio.create_task(service.start_run_async(request=_make_request()))
                await asyncio.sleep(0.1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

                await asyncio.sleep(1.0)
                assert finished.is_set()
                update_state.assert_called_once()
                assert update_state.call_args.kwargs["scenario_result_id"] == "abandoned-id"
                assert update_state.call_args.kwargs["scenario_run_state"] == ScenarioRunState.CANCELLED
                assert update_state.call_args.kwargs["expected_states"] == {
                    ScenarioRunState.CREATED,
                    ScenarioRunState.IN_PROGRESS,
                }

    async def test_start_run_marks_prepare_cancelled_when_it_finishes_before_cancellation_lands(
        self, mock_all_registries
    ) -> None:
        """A done future never calls back, so this race used to leave the run stuck in CREATED."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "raced-id"

        def _instant_prepare(*, request: Any) -> Any:
            return scenario_instance

        async def _complete_then_cancel(awaitable):
            # The preparation finishes, then the cancellation lands: the exact ordering that
            # leaves ``prepare_task.done()`` True inside the handler.
            await awaitable
            raise asyncio.CancelledError

        with patch.object(service, "_prepare_run_blocking", _instant_prepare):
            with patch("asyncio.shield", _complete_then_cancel):
                with patch.object(service._memory, "try_update_scenario_run_state") as update_state:
                    with pytest.raises(asyncio.CancelledError):
                        await service.start_run_async(request=_make_request())

        update_state.assert_called_once()
        assert update_state.call_args.kwargs["scenario_result_id"] == "raced-id"
        assert update_state.call_args.kwargs["scenario_run_state"] == ScenarioRunState.CANCELLED
        assert update_state.call_args.kwargs["expected_states"] == {
            ScenarioRunState.CREATED,
            ScenarioRunState.IN_PROGRESS,
        }
        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_does_not_run_a_scenario_cancelled_during_initialization(self, mock_all_registries) -> None:
        """A run appears in the run list as soon as it is stored, so it can be cancelled mid-init."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "cancelled-during-init"

        def _prepare(*, request: Any) -> Any:
            return scenario_instance

        cancelled = _make_db_scenario_result(result_id="cancelled-during-init", run_state=ScenarioRunState.CANCELLED)
        mock_all_registries["memory"].get_scenario_results.return_value = [cancelled]
        with patch.object(service, "_prepare_run_blocking", _prepare):
            with patch.object(service, "_execute_run_async") as execute:
                response = await service.start_run_async(request=_make_request())

        assert response.status == ScenarioRunState.CANCELLED
        execute.assert_not_called()
        assert "cancelled-during-init" not in service._active_tasks
        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_resumes_a_run_that_was_already_cancelled(self, mock_all_registries) -> None:
        """Resuming a cancelled run is deliberate, so its starting state must not look like a cancel."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "resumed-cancelled"

        def _prepare(*, request: Any) -> Any:
            return scenario_instance

        cancelled = _make_db_scenario_result(result_id="resumed-cancelled", run_state=ScenarioRunState.CANCELLED)
        mock_all_registries["memory"].get_scenario_results.return_value = [cancelled]
        mock_all_registries["memory"].get_scenario_result_header.return_value = cancelled

        with patch.object(service, "_prepare_run_blocking", _prepare):
            with patch.object(service, "_execute_run_async") as execute:
                response = await service.start_run_async(request=_make_request(scenario_result_id="resumed-cancelled"))

        execute.assert_called_once()
        assert "resumed-cancelled" in service._active_tasks
        # The stored row is still CANCELLED until the scenario moves it on; what matters is
        # that the resume was not mistaken for a cancellation and actually started.
        assert response.scenario_result_id == "resumed-cancelled"

    async def test_start_run_honours_a_cancel_that_lands_while_a_live_run_initializes(
        self, mock_all_registries
    ) -> None:
        """A run that was not already cancelled must still respect a cancel during preparation."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "cancelled-mid-init"

        def _prepare(*, request: Any) -> Any:
            return scenario_instance

        cancelled = _make_db_scenario_result(result_id="cancelled-mid-init", run_state=ScenarioRunState.CANCELLED)
        in_progress = _make_db_scenario_result(result_id="cancelled-mid-init", run_state=ScenarioRunState.IN_PROGRESS)
        mock_all_registries["memory"].get_scenario_results.return_value = [cancelled]
        # The pre-preparation read sees a live run; the cancel lands while the worker prepares.
        mock_all_registries["memory"].get_scenario_result_header.return_value = in_progress

        with patch.object(service, "_prepare_run_blocking", _prepare):
            with patch.object(service, "_execute_run_async") as execute:
                response = await service.start_run_async(request=_make_request(scenario_result_id="cancelled-mid-init"))

        assert response.status == ScenarioRunState.CANCELLED
        execute.assert_not_called()
        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_does_not_read_a_header_for_a_fresh_run(self, mock_all_registries) -> None:
        """A fresh run has no stored state, so it must not pay for an extra query."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "fresh-id"

        def _prepare(*, request: Any) -> Any:
            return scenario_instance

        with patch.object(service, "_prepare_run_blocking", _prepare):
            with patch.object(service, "_execute_run_async"):
                await service.start_run_async(request=_make_request())

        mock_all_registries["memory"].get_scenario_result_header.assert_not_called()

    async def test_start_run_failure_does_not_report_a_cancellation(self, mock_all_registries, caplog) -> None:
        service = ScenarioRunService()

        def _failing_prepare(*, request: Any) -> Any:
            raise ValueError("Scenario 'nope' not found")

        with patch.object(service, "_prepare_run_blocking", _failing_prepare):
            with caplog.at_level(logging.WARNING):
                with pytest.raises(ValueError, match="not found"):
                    await service.start_run_async(request=_make_request())

        assert "cancelled" not in caplog.text.lower()
        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_cleanup_failure_still_propagates_cancellation(self, mock_all_registries) -> None:
        """Cleanup runs inline on this path, so it must not replace the CancelledError."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "raced-id"

        def _instant_prepare(*, request: Any) -> Any:
            return scenario_instance

        async def _complete_then_cancel(awaitable):
            await awaitable
            raise asyncio.CancelledError

        with patch.object(service, "_prepare_run_blocking", _instant_prepare):
            with patch("asyncio.shield", _complete_then_cancel):
                with patch.object(service, "_release_abandoned_prepare", side_effect=RuntimeError("cleanup exploded")):
                    with pytest.raises(asyncio.CancelledError):
                        await service.start_run_async(request=_make_request())

    def test_prepare_executor_serializes_preparations(self, mock_all_registries) -> None:
        """In-memory SQLite shares one connection across threads, so preparations must not overlap."""
        service = ScenarioRunService()
        overlap = []
        active = 0
        lock = threading.Lock()

        def _prepare(*, request: Any) -> Any:
            nonlocal active
            with lock:
                active += 1
                overlap.append(active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_blocking", _prepare):
            futures = [
                service._prepare_executor.submit(lambda: service._prepare_run_blocking(request=_make_request()))
                for _ in range(4)
            ]
            for future in futures:
                future.result()

        assert max(overlap) == 1

    def test_prepare_run_blocking_waits_for_initialization_teardown_tasks(self, mock_all_registries) -> None:
        """Async clients schedule their own teardown, so a benign task must not fail the start."""
        service = ScenarioRunService()

        async def _prepare_with_teardown(*, request: Any) -> Any:
            task = asyncio.create_task(asyncio.sleep(0.05))
            task.set_name("client-teardown-task")
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_async", _prepare_with_teardown):
            assert service._prepare_run_blocking(request=_make_request()) is mock_all_registries["scenario_instance"]

    def test_prepare_run_blocking_fails_when_a_task_outlives_the_drain(self, mock_all_registries) -> None:
        """A task still running when the loop closes is cancelled, so the scenario is unusable."""
        service = ScenarioRunService()

        async def _leaky_prepare(*, request: Any) -> Any:
            task = asyncio.create_task(asyncio.sleep(3600))
            task.set_name("stray-initializer-task")
            await asyncio.sleep(0)
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_async", _leaky_prepare):
            with patch.object(ScenarioRunService, "_INITIALIZATION_DRAIN_TIMEOUT", 0.05):
                with pytest.raises(RuntimeError, match="left background tasks on the initialization loop") as exc_info:
                    service._prepare_run_blocking(request=_make_request())

        assert "stray-initializer-task" in str(exc_info.value)

    def test_prepare_run_blocking_marks_the_run_failed_when_the_drain_fails(self, mock_all_registries) -> None:
        """Initialization already stored the run, so a failed drain must not leave it in CREATED."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "drained-id"

        async def _leaky_prepare(*, request: Any) -> Any:
            task = asyncio.create_task(asyncio.sleep(3600))
            task.set_name("stray-initializer-task")
            await asyncio.sleep(0)
            return scenario_instance

        with patch.object(service, "_prepare_run_async", _leaky_prepare):
            with patch.object(ScenarioRunService, "_INITIALIZATION_DRAIN_TIMEOUT", 0.05):
                with patch.object(service._memory, "try_update_scenario_run_state") as update_state:
                    with pytest.raises(RuntimeError, match="left background tasks"):
                        service._prepare_run_blocking(request=_make_request())

        update_state.assert_called_once()
        assert update_state.call_args.kwargs["scenario_result_id"] == "drained-id"
        assert update_state.call_args.kwargs["scenario_run_state"] == ScenarioRunState.FAILED
        assert update_state.call_args.kwargs["error_type"] == "RuntimeError"
        assert update_state.call_args.kwargs["expected_states"] == {
            ScenarioRunState.CREATED,
            ScenarioRunState.IN_PROGRESS,
        }

    def test_prepare_run_blocking_reports_the_drain_error_when_marking_failed_fails(self, mock_all_registries) -> None:
        """A bookkeeping failure must not replace the error that explains the failed start."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = "drained-id"

        async def _leaky_prepare(*, request: Any) -> Any:
            task = asyncio.create_task(asyncio.sleep(3600))
            task.set_name("stray-initializer-task")
            await asyncio.sleep(0)
            return scenario_instance

        with patch.object(service, "_prepare_run_async", _leaky_prepare):
            with patch.object(ScenarioRunService, "_INITIALIZATION_DRAIN_TIMEOUT", 0.05):
                with patch.object(service._memory, "try_update_scenario_run_state", side_effect=ValueError("gone")):
                    with pytest.raises(RuntimeError, match="left background tasks"):
                        service._prepare_run_blocking(request=_make_request())

    def test_prepare_run_blocking_waits_for_a_task_spawned_during_the_drain(self, mock_all_registries) -> None:
        """A draining task can start another one, which the first snapshot never saw."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        child_finished = threading.Event()

        async def _prepare_spawning_a_child(*, request: Any) -> Any:
            async def _child() -> None:
                await asyncio.sleep(0.05)
                child_finished.set()

            async def _parent() -> None:
                await asyncio.sleep(0.01)
                asyncio.create_task(_child(), name="child-teardown-task")

            asyncio.create_task(_parent(), name="parent-teardown-task")
            await asyncio.sleep(0)
            return scenario_instance

        with patch.object(service, "_prepare_run_async", _prepare_spawning_a_child):
            assert service._prepare_run_blocking(request=_make_request()) is scenario_instance

        assert child_finished.is_set()

    def test_prepare_run_blocking_fails_when_a_spawned_child_outlives_the_drain(self, mock_all_registries) -> None:
        """The child is the one that would be cancelled by the closing loop, so it must be named."""
        service = ScenarioRunService()

        async def _prepare_spawning_a_slow_child(*, request: Any) -> Any:
            async def _parent() -> None:
                await asyncio.sleep(0.01)
                asyncio.create_task(asyncio.sleep(3600), name="child-teardown-task")

            asyncio.create_task(_parent(), name="parent-teardown-task")
            await asyncio.sleep(0)
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_async", _prepare_spawning_a_slow_child):
            with patch.object(ScenarioRunService, "_INITIALIZATION_DRAIN_TIMEOUT", 0.3):
                with pytest.raises(RuntimeError, match="child-teardown-task"):
                    service._prepare_run_blocking(request=_make_request())

    def test_prepare_run_blocking_drain_uses_one_deadline_across_generations(self, mock_all_registries) -> None:
        """Re-scanning must not restart the budget, or a chain of tasks could stall a start forever."""
        service = ScenarioRunService()

        async def _prepare_spawning_a_chain(*, request: Any) -> Any:
            async def _link(depth: int) -> None:
                await asyncio.sleep(0.05)
                asyncio.create_task(_link(depth + 1), name=f"chain-task-{depth + 1}")

            asyncio.create_task(_link(0), name="chain-task-0")
            await asyncio.sleep(0)
            return mock_all_registries["scenario_instance"]

        started = time.monotonic()
        with patch.object(service, "_prepare_run_async", _prepare_spawning_a_chain):
            with patch.object(ScenarioRunService, "_INITIALIZATION_DRAIN_TIMEOUT", 0.3):
                with pytest.raises(RuntimeError, match="chain-task"):
                    service._prepare_run_blocking(request=_make_request())

        assert time.monotonic() - started < 3

    async def test_start_run_releases_semaphore_when_initialization_leaks_a_task(self, mock_all_registries) -> None:
        """Failing the preparation must not strand the permit it was holding."""
        service = ScenarioRunService()

        async def _leaky_prepare(*, request: Any) -> Any:
            task = asyncio.create_task(asyncio.sleep(3600))
            task.set_name("stray-initializer-task")
            await asyncio.sleep(0)
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_async", _leaky_prepare):
            with patch.object(ScenarioRunService, "_INITIALIZATION_DRAIN_TIMEOUT", 0.05):
                with pytest.raises(RuntimeError, match="left background tasks"):
                    await service.start_run_async(request=_make_request())

        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    def test_prepare_run_blocking_is_quiet_when_initialization_is_self_contained(
        self, mock_all_registries, caplog
    ) -> None:
        """The happy path must not warn, otherwise the signal is worthless."""
        service = ScenarioRunService()

        async def _clean_prepare(*, request: Any) -> Any:
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_async", _clean_prepare):
            with caplog.at_level(logging.WARNING):
                service._prepare_run_blocking(request=_make_request())

        assert "left background tasks" not in caplog.text

    async def test_start_run_holds_semaphore_until_abandoned_prepare_finishes(self, mock_all_registries) -> None:
        """A cancelled start must not free capacity while its worker thread is still initializing."""
        service = ScenarioRunService()
        finished = threading.Event()

        def _slow_prepare(*, request: Any) -> Any:
            time.sleep(0.5)
            finished.set()
            return mock_all_registries["scenario_instance"]

        with patch.object(service, "_prepare_run_blocking", _slow_prepare):
            task = asyncio.create_task(service.start_run_async(request=_make_request()))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # The worker thread cannot be killed, so admitting another run here would let
            # two initializations share one permit.
            assert not finished.is_set()
            assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS - 1

            await asyncio.sleep(1.0)
            assert finished.is_set()
            assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_releases_semaphore_when_prepare_fails(self, mock_all_registries) -> None:
        """CancelledError is a BaseException, so it needs an explicit release path."""
        service = ScenarioRunService()

        def _failing_prepare(*, request: Any) -> Any:
            raise ValueError("boom")

        with patch.object(service, "_prepare_run_blocking", _failing_prepare):
            with pytest.raises(ValueError, match="boom"):
                await service.start_run_async(request=_make_request())

        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_releases_semaphore_when_result_id_missing(self, mock_all_registries) -> None:
        """The missing scenario_result_id check used to sit outside the try block."""
        service = ScenarioRunService()
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._scenario_result_id = None

        with pytest.raises(ValueError, match="did not produce a scenario_result_id"):
            await service.start_run_async(request=_make_request())

        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS

    async def test_start_run_cleans_up_when_response_lookup_fails(self, mock_all_registries) -> None:
        """A response failure must not strand a permit or leave an active-task entry."""
        service = ScenarioRunService()

        with patch.object(service, "get_run", return_value=None):
            with pytest.raises(RuntimeError, match="not found in the database"):
                await service.start_run_async(request=_make_request())

        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS
        assert service._active_tasks == {}

    async def test_start_run_releases_semaphore_exactly_once_on_success(self, mock_all_registries) -> None:
        """The background task owns the permit after handoff, so it is not double-released."""
        service = ScenarioRunService()
        released = asyncio.Event()

        async def _run_async() -> None:
            released.set()

        mock_all_registries["scenario_instance"].run_async = _run_async

        await service.start_run_async(request=_make_request())
        await asyncio.wait_for(released.wait(), timeout=5)
        await asyncio.sleep(0)

        assert service._run_semaphore._value == _DEFAULT_MAX_CONCURRENT_RUNS


class TestScenarioRunServiceGetRun:
    """Tests for ScenarioRunService.get_run."""

    def test_get_run_returns_none_for_unknown_id(self, mock_memory) -> None:
        """Test that get_run returns None for non-existent run."""
        mock_memory.get_scenario_results.return_value = []
        service = ScenarioRunService()
        result = service.get_run(scenario_result_id="nonexistent-id")
        assert result is None

    def test_get_run_returns_existing_run(self, mock_memory) -> None:
        """Test that get_run returns a run from the database."""
        db_result = _make_db_scenario_result(result_id="sr-123", run_state=ScenarioRunState.IN_PROGRESS)
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-123")

        assert fetched is not None
        assert fetched.scenario_result_id == "sr-123"
        assert fetched.scenario_name == "foundry.red_team_agent"
        assert fetched.status == ScenarioRunState.IN_PROGRESS

    def test_get_run_maps_typed_scenario_result_state(self, mock_memory) -> None:
        """Test the service boundary with a real ScenarioResult domain model."""
        db_result = make_scenario_result(
            scenario_name="foundry.red_team_agent",
            attack_results={},
            scenario_run_state=ScenarioRunState.FAILED,
            error_message="Scenario failed",
            error_type="RuntimeError",
        )
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id=str(db_result.id))

        assert fetched is not None
        assert fetched.status is ScenarioRunState.FAILED
        assert fetched.error == "Scenario failed"
        assert fetched.error_type == "RuntimeError"

    @pytest.mark.parametrize(
        ("raw_plan", "expected_registry_name", "expected_total", "expected_planned_total", "expected_warning"),
        [
            (
                ScenarioRunPlan(
                    scenario_registry_name="registered.scenario",
                    atomic_groups=[
                        ScenarioRunPlanAtomicGroup(
                            id="group-1",
                            atomic_attack_name="legacy attack",
                            display_group="Attack",
                            technique_eval_hash="eval",
                            seed_group_ids=["seed-1"],
                        )
                    ],
                    seed_groups=[
                        ScenarioRunPlanSeedGroup(
                            id="seed-1",
                            objective_sha256=_svc_mod.to_sha256("objective"),
                            objective="objective",
                        )
                    ],
                ).model_dump(mode="json"),
                "registered.scenario",
                1,
                True,
                False,
            ),
            (None, None, 1, False, False),
            ({"version": 2, "atomic_groups": [], "seed_groups": []}, None, 1, False, True),
            ({"version": 1, "atomic_groups": "malformed", "seed_groups": []}, None, 1, False, True),
        ],
        ids=["valid", "legacy", "forward-version", "malformed"],
    )
    def test_get_run_detail_preserves_readability_across_plan_metadata(
        self,
        mock_memory,
        caplog: pytest.LogCaptureFixture,
        raw_plan: dict[str, Any] | None,
        expected_registry_name: str | None,
        expected_total: int,
        expected_planned_total: bool,
        expected_warning: bool,
    ) -> None:
        metadata = {SCENARIO_RUN_PLAN_METADATA_KEY: raw_plan} if raw_plan is not None else {}
        attack_result = AttackResult(
            conversation_id="conversation-1",
            objective="objective",
            outcome=AttackOutcome.SUCCESS,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            attribution_data={"parent_collection": "legacy attack", "parent_eval_hash": "eval"},
        )
        db_result = make_scenario_result(
            scenario_name="foundry.red_team_agent",
            attack_results={"legacy attack": [attack_result]},
            metadata=metadata,
        )
        mock_memory.get_scenario_results.return_value = [db_result]

        fetched = ScenarioRunService().get_run(scenario_result_id=str(db_result.id))

        assert fetched is not None
        assert fetched.scenario_registry_name == expected_registry_name
        assert fetched.total_attacks == expected_total
        assert fetched.completed_attacks == 1
        assert fetched.planned_total_available is expected_planned_total
        assert fetched.techniques_used == (["Attack"] if expected_planned_total else ["legacy attack"])
        assert ("using legacy run detail fields" in caplog.text) is expected_warning

    def test_get_run_falls_back_to_persisted_error(self, mock_memory) -> None:
        """Test that get_run extracts error from persisted error AttackResult when no active task.

        After the foreign-key-based scenario linkage refactor, error
        AttackResults are located via
        ``get_attack_results(scenario_result_id=..., outcome=ERROR)`` rather
        than via a per-scenario error_attack_result_ids manifest.
        """
        db_result = _make_db_scenario_result(result_id="sr-fail", run_state=ScenarioRunState.FAILED)

        # Mock the error AttackResult lookup
        error_ar = MagicMock()
        error_ar.error_message = "Connection refused"
        error_ar.error_type = "ConnectionError"
        mock_memory.get_scenario_results.return_value = [db_result]
        mock_memory.get_attack_results.return_value = [error_ar]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-fail")

        assert fetched is not None
        assert fetched.error == "Connection refused"
        assert fetched.error_type == "ConnectionError"
        mock_memory.get_attack_results.assert_called_once_with(
            scenario_result_id="sr-fail",
            outcome=AttackOutcome.ERROR,
        )


class TestScenarioRunServiceListRuns:
    """Tests for ScenarioRunService.list_runs."""

    def test_list_runs_empty(self, mock_memory) -> None:
        """Test that list_runs returns empty list when DB has no results."""
        mock_memory.get_scenario_run_history_page.return_value = ([], {}, False)
        service = ScenarioRunService()
        result = service.list_runs()
        assert result.items == []
        assert result.pagination.has_more is False
        mock_memory.get_scenario_results.assert_not_called()

    def test_list_runs_returns_all_runs(self, mock_memory) -> None:
        """Test that list_runs returns all runs from the database."""
        records = [
            _make_history_record(result_id="sr-1", run_state=ScenarioRunState.COMPLETED),
            _make_history_record(result_id="sr-2", run_state=ScenarioRunState.IN_PROGRESS),
        ]
        mock_memory.get_scenario_run_history_page.return_value = (records, {}, False)

        service = ScenarioRunService()
        result = service.list_runs()
        assert len(result.items) == 2
        assert [item.scenario_result_id for item in result.items] == ["sr-1", "sr-2"]
        mock_memory.get_scenario_results.assert_not_called()

    def test_list_runs_passes_custom_limit(self, mock_memory) -> None:
        """Test that list_runs passes a custom limit to the memory query."""
        mock_memory.get_scenario_run_history_page.return_value = ([], {}, False)
        service = ScenarioRunService()
        service.list_runs(limit=10)
        mock_memory.get_scenario_run_history_page.assert_called_once_with(
            scenario_names=[],
            statuses=[],
            labels=None,
            cursor=None,
            limit=10,
        )

    def test_history_cursor_is_filter_bound_and_invalid_values_restart_pagination(self, mock_memory) -> None:
        record = _make_history_record(result_id=str(uuid.uuid4()), run_state=ScenarioRunState.COMPLETED)
        mock_memory.get_scenario_run_history_page.return_value = ([record], {}, True)
        service = ScenarioRunService()

        first_page = service.list_runs(scenario_names=["first"], labels={"operator": ["alice", "bob"]})

        assert first_page.pagination.has_more is True
        assert first_page.pagination.next_cursor is not None
        service.list_runs(scenario_names=["second"], cursor=first_page.pagination.next_cursor)
        assert mock_memory.get_scenario_run_history_page.call_args.kwargs["cursor"] is None
        service.list_runs(cursor="not-a-cursor")
        assert mock_memory.get_scenario_run_history_page.call_args.kwargs["cursor"] is None

    def test_history_uses_plan_and_latest_non_error_attempt_per_unit(self, mock_memory) -> None:
        record = _make_history_record(result_id="sr-aggregate", run_state=ScenarioRunState.COMPLETED)
        plan = ScenarioRunPlan(
            scenario_registry_name="registered.scenario",
            atomic_groups=[
                ScenarioRunPlanAtomicGroup(
                    id="group-1",
                    atomic_attack_name="attack",
                    display_group="Attack",
                    technique_eval_hash="eval-1",
                    seed_group_ids=["seed-1", "seed-2"],
                )
            ],
            seed_groups=[
                ScenarioRunPlanSeedGroup(id="seed-1", objective_sha256="hash-1", objective="first"),
                ScenarioRunPlanSeedGroup(id="seed-2", objective_sha256="hash-2", objective="second"),
            ],
        )
        record = replace(
            record,
            scenario_registry_name=plan.scenario_registry_name,
            plan_atomic_groups=[group.model_dump(mode="json") for group in plan.atomic_groups],
            plan_seed_id_map=[{"id": seed.id, "objective_sha256": seed.objective_sha256} for seed in plan.seed_groups],
        )
        timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
        mock_memory.get_scenario_run_history_page.return_value = (
            [record],
            {
                record.scenario_result_id: ScenarioHistoryAggregate(
                    scenario_result_id=record.scenario_result_id,
                    unit_count=1,
                    completed_units=1,
                    successful_units=1,
                    error_attempts=1,
                    total_retries=3,
                    latest_attempt_timestamp=timestamp,
                    atomic_attack_names=("attack",),
                )
            },
            False,
        )

        summary = ScenarioRunService().list_runs().items[0]

        assert summary.total_attacks == 2
        assert summary.completed_attacks == 1
        assert summary.successful_attacks == 1
        assert summary.error_attacks == 1
        assert summary.total_retries == 3
        assert summary.planned_total_available is True
        assert summary.attack_details_available is False
        assert summary.updated_at >= timestamp

    def test_history_metadata_is_allow_listed_and_secret_free(self, mock_memory) -> None:
        scenario_result = make_scenario_result(
            scenario_name="SafeScenario",
            objective_target_identifier=ComponentIdentifier(
                class_name="OpenAIChatTarget",
                class_module="tests",
                endpoint="https://user:password@example.test/v1?api-key=secret#fragment",
                model_name="gpt-4o",
            ),
            params={
                "max_turns": 5,
                "api_key": "top-secret",
                "connection_string": "AccountKey=connection-secret",
                "headers": {"X-Custom": "header-secret"},
                "nested": {"access_token": "also-secret", "safe": "visible"},
            },
            datasets=["harmbench"],
            attack_results={},
        )
        record = _make_history_record(result_id="sr-safe", run_state=ScenarioRunState.COMPLETED)
        record = replace(
            record,
            scenario_name=scenario_result.scenario_name,
            scenario_identifier=scenario_result.scenario_identifier.model_dump(mode="json"),
        )
        mock_memory.get_scenario_run_history_page.return_value = ([record], {}, False)

        summary = ScenarioRunService().list_runs().items[0]
        serialized = summary.model_dump_json()

        assert summary.target is not None
        assert summary.target.endpoint == "https://example.test"
        assert summary.target.model_name == "gpt-4o"
        assert summary.datasets_used == ["harmbench"]
        assert summary.scenario_parameters["max_turns"] == 5
        assert "connection_string" not in summary.scenario_parameters
        assert "headers" not in summary.scenario_parameters
        assert "nested" not in summary.scenario_parameters
        assert "top-secret" not in serialized
        assert "also-secret" not in serialized
        assert "connection-secret" not in serialized
        assert "header-secret" not in serialized
        assert "/v1" not in serialized
        assert "password" not in serialized

    def test_history_falls_back_honestly_for_incomplete_persisted_plan(self, mock_memory) -> None:
        record = _make_history_record(result_id="sr-legacy", run_state=ScenarioRunState.COMPLETED)
        record = replace(
            record,
            scenario_registry_name="registered.scenario",
            plan_atomic_groups="{}",
            plan_seed_id_map="[]",
        )
        mock_memory.get_scenario_run_history_page.return_value = ([record], {}, False)

        summary = ScenarioRunService().list_runs().items[0]

        assert summary.planned_total_available is False
        assert summary.total_attacks is None
        assert summary.completed_attacks == 0

    def test_history_discards_duplicate_plan_groups_before_legacy_fallback(self, mock_memory) -> None:
        record = _make_history_record(result_id="sr-duplicate-plan", run_state=ScenarioRunState.COMPLETED)
        group = ScenarioRunPlanAtomicGroup(
            id="duplicate",
            atomic_attack_name="attack",
            display_group="Attack",
            technique_eval_hash="eval",
            seed_group_ids=["seed-1"],
        ).model_dump(mode="json")
        record = replace(
            record,
            plan_atomic_groups=[group, group],
            plan_seed_id_map=[{"id": "seed-1", "objective_sha256": "hash-1"}],
        )
        mock_memory.get_scenario_run_history_page.return_value = ([record], {}, False)

        summary = ScenarioRunService().list_runs().items[0]

        assert summary.planned_total_available is False
        assert summary.total_attacks is None

    def test_history_scopes_duplicate_objective_hashes_to_atomic_groups(self, mock_memory) -> None:
        record = _make_history_record(result_id="sr-duplicate-objective", run_state=ScenarioRunState.COMPLETED)
        groups = [
            ScenarioRunPlanAtomicGroup(
                id=f"group-{index}",
                atomic_attack_name=f"attack-{index}",
                display_group=f"Attack {index}",
                technique_eval_hash=f"eval-{index}",
                seed_group_ids=[f"seed-{index}"],
            )
            for index in (1, 2)
        ]
        record = replace(
            record,
            plan_atomic_groups=[group.model_dump(mode="json") for group in groups],
            plan_seed_id_map=[
                {"id": "seed-1", "objective_sha256": "shared-hash"},
                {"id": "seed-2", "objective_sha256": "shared-hash"},
            ],
        )
        timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
        mock_memory.get_scenario_run_history_page.return_value = (
            [record],
            {
                record.scenario_result_id: ScenarioHistoryAggregate(
                    scenario_result_id=record.scenario_result_id,
                    unit_count=2,
                    completed_units=2,
                    successful_units=2,
                    error_attempts=0,
                    total_retries=0,
                    latest_attempt_timestamp=timestamp,
                    atomic_attack_names=("attack-1", "attack-2"),
                )
            },
            False,
        )

        summary = ScenarioRunService().list_runs().items[0]

        assert summary.planned_total_available is True
        assert summary.total_attacks == 2
        assert summary.completed_attacks == 2
        assert summary.successful_attacks == 2
        mock_memory.get_scenario_history_aggregates.assert_not_called()

    def test_history_falls_back_for_duplicate_objective_hashes_within_one_group(self, mock_memory) -> None:
        record = _make_history_record(result_id="sr-ambiguous-objective", run_state=ScenarioRunState.COMPLETED)
        group = ScenarioRunPlanAtomicGroup(
            id="group-1",
            atomic_attack_name="attack",
            display_group="Attack",
            technique_eval_hash="eval",
            seed_group_ids=["seed-1", "seed-2"],
        )
        record = replace(
            record,
            plan_atomic_groups=[group.model_dump(mode="json")],
            plan_seed_id_map=[
                {"id": "seed-1", "objective_sha256": "shared-hash"},
                {"id": "seed-2", "objective_sha256": "shared-hash"},
            ],
        )
        mock_memory.get_scenario_run_history_page.return_value = ([record], {}, False)

        summary = ScenarioRunService().list_runs().items[0]

        assert summary.planned_total_available is False
        assert summary.total_attacks is None

    def test_history_requeries_legacy_aggregates_when_plan_is_rejected(self, mock_memory) -> None:
        """A plan the service cannot trust forces a plan-free aggregate re-query."""
        record = _make_history_record(result_id="sr-rejected-plan", run_state=ScenarioRunState.COMPLETED)
        record = replace(record, plan_atomic_groups="{}", plan_seed_id_map="[]")
        mock_memory.get_scenario_run_history_page.return_value = (
            [record],
            {record.scenario_result_id: ScenarioHistoryAggregate.empty(scenario_result_id=record.scenario_result_id)},
            False,
        )
        mock_memory.get_scenario_history_aggregates.return_value = {
            record.scenario_result_id: ScenarioHistoryAggregate(
                scenario_result_id=record.scenario_result_id,
                unit_count=3,
                completed_units=2,
                successful_units=1,
                error_attempts=1,
                total_retries=4,
                latest_attempt_timestamp=datetime(2026, 8, 7, tzinfo=timezone.utc),
                atomic_attack_names=("attack",),
            )
        }

        summary = ScenarioRunService().list_runs().items[0]

        mock_memory.get_scenario_history_aggregates.assert_called_once_with(
            scenario_result_ids=[record.scenario_result_id]
        )
        assert summary.planned_total_available is False
        assert summary.total_attacks == 3
        assert summary.completed_attacks == 2
        assert summary.successful_attacks == 1
        assert summary.total_retries == 4
        assert summary.techniques_used == ["attack"]

    def test_list_runs_reports_unknown_total_without_plan(self, mock_memory) -> None:
        """Test that legacy runs do not report a false zero planned total."""
        record = _make_history_record(result_id="sr-no-plan", run_state=ScenarioRunState.COMPLETED)
        mock_memory.get_scenario_run_history_page.return_value = ([record], {}, False)

        result = ScenarioRunService().list_runs()

        assert result.items[0].total_attacks is None


class TestScenarioRunServiceCancelRun:
    """Tests for ScenarioRunService.cancel_run_async."""

    async def test_cancel_run_returns_none_for_unknown_id(self, mock_memory) -> None:
        """Test that cancel returns None for non-existent run."""
        mock_memory.get_scenario_results.return_value = []
        service = ScenarioRunService()
        result = await service.cancel_run_async(scenario_result_id="nonexistent-id")
        assert result is None

    async def test_cancel_run_sets_cancelled_status(self, mock_all_registries) -> None:
        """Test that cancelling a running scenario persists CANCELLED to DB."""
        service = ScenarioRunService()
        mock_memory = mock_all_registries["memory"]
        response = await service.start_run_async(request=_make_request())

        # After update_scenario_run_state, the next DB query should return CANCELLED
        running_result = mock_all_registries["db_result"]
        cancelled_result = _make_db_scenario_result(
            result_id=response.scenario_result_id,
            run_state=ScenarioRunState.CANCELLED,
        )
        mock_memory.get_scenario_results.side_effect = [[running_result], [cancelled_result]]

        result = await service.cancel_run_async(scenario_result_id=response.scenario_result_id)

        mock_memory.try_update_scenario_run_state.assert_called_once_with(
            scenario_result_id=response.scenario_result_id,
            expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
            scenario_run_state=ScenarioRunState.CANCELLED,
            error_message="Run was cancelled by user",
            error_type="CancelledError",
        )
        assert result is not None
        assert result.status == ScenarioRunState.CANCELLED

    async def test_cancel_waits_for_final_persisted_progress_delta(self, mock_all_registries) -> None:
        """Cancellation completes task cleanup before callers can fetch terminal progress."""
        mock_memory = mock_all_registries["memory"]
        scenario_instance = mock_all_registries["scenario_instance"]
        delta = ScenarioAttackResultDelta(
            attack_result_id=str(uuid.uuid4()),
            conversation_id="conversation-cancelled",
            objective="persisted during cancellation",
            outcome=AttackOutcome.ERROR,
            execution_time_ms=10,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            error_type="CancelledError",
            error_message="cancelled",
            attribution_data={"parent_collection": "attack"},
        )

        async def run_until_cancelled() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                mock_memory.get_scenario_attack_result_deltas.return_value = ([delta], False)

        scenario_instance.run_async.side_effect = run_until_cancelled
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())
        await asyncio.sleep(0)

        running_result = mock_all_registries["db_result"]
        cancelled_result = _make_db_scenario_result(
            result_id=response.scenario_result_id,
            run_state=ScenarioRunState.CANCELLED,
        )
        cancelled_result.metadata = {}
        mock_memory.get_scenario_results.side_effect = [[running_result], [cancelled_result]]

        await service.cancel_run_async(scenario_result_id=response.scenario_result_id)
        mock_memory.get_scenario_result_header.return_value = cancelled_result
        progress = service.get_run_progress(
            scenario_result_id=response.scenario_result_id,
            since=None,
            limit=25,
        )

        assert progress is not None
        assert progress.run.status is ScenarioRunState.CANCELLED
        assert [result.attack_result_id for result in progress.results] == [delta.attack_result_id]

    async def test_cancel_completed_run_raises_value_error(self, mock_memory) -> None:
        """Test that cancelling a completed run raises ValueError."""
        db_result = _make_db_scenario_result(result_id="sr-done", run_state=ScenarioRunState.COMPLETED)
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        with pytest.raises(ValueError, match="Cannot cancel run"):
            await service.cancel_run_async(scenario_result_id="sr-done")

    async def test_cancel_already_cancelled_run_raises_value_error(self, mock_memory) -> None:
        """Test that cancelling an already-cancelled run raises ValueError."""
        db_result = _make_db_scenario_result(result_id="sr-cancelled", run_state=ScenarioRunState.CANCELLED)
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        with pytest.raises(ValueError, match="Cannot cancel run"):
            await service.cancel_run_async(scenario_result_id="sr-cancelled")


class TestScenarioRunServiceExecution:
    """Tests for the background execution logic."""

    async def test_execute_run_completes_successfully(self, mock_all_registries) -> None:
        """Test that a successful execution removes active task and DB reflects COMPLETED."""
        service = ScenarioRunService()
        mock_instance = mock_all_registries["scenario_instance"]
        mock_memory = mock_all_registries["memory"]

        mock_scenario_result = MagicMock()
        mock_scenario_result.id = "sr-uuid-1"
        mock_scenario_result.scenario_run_state = "COMPLETED"
        mock_scenario_result.get_techniques_used.return_value = ["base64"]
        mock_scenario_result.attack_results = {"attack1": []}
        mock_scenario_result.number_tries = 1
        mock_scenario_result.creation_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        mock_scenario_result.completion_time = datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc)

        mock_instance.run_async = AsyncMock(return_value=mock_scenario_result)

        response = await service.start_run_async(request=_make_request())

        # Wait for the background task to complete
        active = service._active_tasks.get(response.scenario_result_id)
        assert active is not None
        assert active.task is not None
        await active.task

        # Active task is cleaned up on next get_run (deferred cleanup)
        assert response.scenario_result_id in service._active_tasks
        fetched = service.get_run(scenario_result_id=response.scenario_result_id)
        assert fetched is not None
        assert response.scenario_result_id not in service._active_tasks

    async def test_execute_run_fails_with_error(self, mock_all_registries) -> None:
        """Test that a run_async failure stores error and surfaces it via get_run."""
        service = ScenarioRunService()
        mock_instance = mock_all_registries["scenario_instance"]

        mock_instance.run_async = AsyncMock(side_effect=RuntimeError("scenario exploded"))

        response = await service.start_run_async(request=_make_request())

        # Wait for the background task
        active = service._active_tasks.get(response.scenario_result_id)
        assert active is not None
        assert active.task is not None
        await active.task

        # Error is stored on the active task until get_run reads it
        assert active.error == "scenario exploded"
        assert response.scenario_result_id in service._active_tasks

        # get_run should surface the error and clean up
        fetched = service.get_run(scenario_result_id=response.scenario_result_id)
        assert fetched is not None
        assert fetched.error == "scenario exploded"
        assert response.scenario_result_id not in service._active_tasks


class TestScenarioRunServiceGetResults:
    """Tests for ScenarioRunService.get_run_results."""

    def test_get_results_returns_none_for_unknown_id(self, mock_memory) -> None:
        """Test that get_run_results returns None for non-existent run."""
        mock_memory.get_scenario_results.return_value = []
        service = ScenarioRunService()
        result = service.get_run_results(scenario_result_id="nonexistent-id")
        assert result is None

    def test_get_results_raises_if_not_completed(self, mock_memory) -> None:
        """Test that get_run_results raises ValueError if run is not completed."""
        db_result = _make_db_scenario_result(result_id="sr-running", run_state=ScenarioRunState.IN_PROGRESS)
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        with pytest.raises(ValueError, match="only available for completed runs"):
            service.get_run_results(scenario_result_id="sr-running")

    def test_get_results_returns_details_for_completed_run(self, mock_memory) -> None:
        """Test that get_run_results returns the ScenarioResult for a completed run."""
        from pyrit.models import AttackOutcome

        mock_attack_result = MagicMock()
        mock_attack_result.outcome = AttackOutcome.SUCCESS
        mock_attack_result.objective = "Extract info"

        db_result = _make_db_scenario_result(
            result_id="sr-123",
            run_state=ScenarioRunState.COMPLETED,
            attack_results={"base64_attack": [mock_attack_result]},
        )
        db_result.objective_achieved_rate.return_value = 100
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        result = service.get_run_results(scenario_result_id="sr-123")

        assert result is db_result
        assert result.attack_results["base64_attack"][0].outcome == AttackOutcome.SUCCESS


class TestScenarioRunServiceProgressReporting:
    """Tests that in-progress runs expose partial attack counts."""

    def test_in_progress_run_shows_partial_attack_counts(self, mock_memory) -> None:
        """Test that polling an IN_PROGRESS run shows incremental results."""
        from pyrit.models import AttackOutcome

        mock_success = MagicMock()
        mock_success.outcome = AttackOutcome.SUCCESS
        mock_failure = MagicMock()
        mock_failure.outcome = AttackOutcome.FAILURE
        mock_undetermined = MagicMock()
        mock_undetermined.outcome = AttackOutcome.UNDETERMINED

        db_result = _make_db_scenario_result(
            result_id="sr-running",
            run_state=ScenarioRunState.IN_PROGRESS,
            attack_results={
                "attack_a": [mock_success, mock_failure],
                "attack_b": [mock_undetermined],
            },
        )
        db_result.get_techniques_used.return_value = ["attack_a", "attack_b"]
        db_result.objective_achieved_rate.return_value = 33
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-running")

        assert fetched is not None
        assert fetched.status == ScenarioRunState.IN_PROGRESS
        assert fetched.total_attacks == 3
        assert fetched.completed_attacks == 3
        assert fetched.techniques_used == ["attack_a", "attack_b"]
        assert fetched.objective_achieved_rate == 33
        assert fetched.completed_at is None

    def test_created_run_shows_zero_counts(self, mock_memory) -> None:
        """Test that a CREATED run with no results shows zero counts."""
        db_result = _make_db_scenario_result(
            result_id="sr-new",
            run_state=ScenarioRunState.CREATED,
            attack_results={},
        )
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-new")

        assert fetched is not None
        assert fetched.status == ScenarioRunState.CREATED
        assert fetched.total_attacks == 0
        assert fetched.completed_attacks == 0
        assert fetched.techniques_used == []

    def test_completed_run_still_shows_full_counts(self, mock_memory) -> None:
        """Test that COMPLETED runs still show accurate counts after the fix."""
        from pyrit.models import AttackOutcome

        mock_success = MagicMock()
        mock_success.outcome = AttackOutcome.SUCCESS

        db_result = _make_db_scenario_result(
            result_id="sr-done",
            run_state=ScenarioRunState.COMPLETED,
            attack_results={"attack_a": [mock_success]},
        )
        db_result.get_techniques_used.return_value = ["attack_a"]
        db_result.objective_achieved_rate.return_value = 100
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-done")

        assert fetched is not None
        assert fetched.status == ScenarioRunState.COMPLETED
        assert fetched.total_attacks == 1
        assert fetched.completed_attacks == 1
        assert fetched.techniques_used == ["attack_a"]
        assert fetched.objective_achieved_rate == 100
        assert fetched.completed_at == db_result.completion_time


class TestScenarioRunServiceFailedAttackReporting:
    """Tests that per-attack errors and retry pressure surface in the summary."""

    def test_error_attacks_and_retries_are_surfaced(self, mock_memory) -> None:
        from pyrit.models import AttackOutcome

        success = MagicMock()
        success.outcome = AttackOutcome.SUCCESS
        success.total_retries = 2

        errored = MagicMock()
        errored.outcome = AttackOutcome.ERROR
        errored.objective = "do the bad thing"
        errored.error_type = "RateLimitError"
        errored.error_message = "429 Too Many Requests"
        errored.total_retries = 4

        db_result = _make_db_scenario_result(
            result_id="sr-mixed",
            run_state=ScenarioRunState.COMPLETED,
            attack_results={"baseline_airt_hate": [success, errored]},
        )
        db_result.objective_achieved_rate.return_value = 50
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-mixed")

        assert fetched is not None
        assert fetched.total_retries == 6
        assert len(fetched.failed_attacks) == 1
        failed = fetched.failed_attacks[0]
        assert failed.atomic_attack_name == "baseline_airt_hate"
        assert failed.error_type == "RateLimitError"
        assert failed.error_message == "429 Too Many Requests"
        assert failed.total_retries == 4

    def test_no_failed_attacks_when_all_succeed(self, mock_memory) -> None:
        from pyrit.models import AttackOutcome

        success = MagicMock()
        success.outcome = AttackOutcome.SUCCESS
        success.total_retries = 0
        success.retry_events = []

        db_result = _make_db_scenario_result(
            result_id="sr-clean",
            run_state=ScenarioRunState.COMPLETED,
            attack_results={"attack_a": [success]},
        )
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-clean")

        assert fetched is not None
        assert fetched.failed_attacks == []
        assert fetched.total_retries == 0
        assert fetched.attack_retries == []

    def test_retry_events_surface_per_attack(self, mock_memory) -> None:
        from pyrit.models import AttackOutcome
        from pyrit.models.retry_event import RetryEvent

        attack = MagicMock()
        attack.outcome = AttackOutcome.SUCCESS
        attack.total_retries = 2
        attack.attack_result_id = "ar-9"
        attack.retry_events = [
            RetryEvent(
                attempt_number=1,
                exception_type="RateLimitError",
                exception_message="429",
                component_role="objective_scorer",
                component_name="TrueFalseScorer",
                endpoint="https://ep/",
            )
        ]

        db_result = _make_db_scenario_result(
            result_id="sr-retry",
            run_state=ScenarioRunState.COMPLETED,
            attack_results={"baseline_airt_hate": [attack]},
        )
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-retry")

        assert fetched is not None
        assert len(fetched.attack_retries) == 1
        summary = fetched.attack_retries[0]
        assert summary.attack_result_id == "ar-9"
        assert summary.atomic_attack_name == "baseline_airt_hate"
        assert summary.retries[0].endpoint == "https://ep/"
        assert summary.retries[0].component_role == "objective_scorer"


class TestResolveTechniquesAndConverters:
    """Tests for per-technique converter resolution from ``--techniques`` tokens."""

    def test_plain_technique_no_converters(self, mock_memory) -> None:
        with _patch_converter_registry({}):
            enums, converters = _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                tokens=["role_play"], technique_class=_StubTechnique, scenario_name="x"
            )
        assert enums == [_StubTechnique.ROLE_PLAY]
        assert converters == {}

    def test_single_converter_appended(self, mock_memory) -> None:
        conv = MagicMock(spec=Converter)
        with _patch_converter_registry({"translation_spanish": conv}):
            enums, converters = _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                tokens=["role_play:converter.translation_spanish"],
                technique_class=_StubTechnique,
                scenario_name="x",
            )
        assert enums == [_StubTechnique.ROLE_PLAY]
        assert converters == {"role_play": [conv]}

    def test_aggregate_token_applies_converter_to_all_concrete(self, mock_memory) -> None:
        conv = MagicMock(spec=Converter)
        with _patch_converter_registry({"c1": conv}):
            enums, converters = _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                tokens=["easy:converter.c1"], technique_class=_StubTechnique, scenario_name="x"
            )
        assert enums == [_StubTechnique.EASY]
        assert converters == {"role_play": [conv], "single_turn": [conv]}

    def test_multiple_converters_preserve_order(self, mock_memory) -> None:
        c1 = MagicMock(spec=Converter)
        c2 = MagicMock(spec=Converter)
        with _patch_converter_registry({"c1": c1, "c2": c2}):
            _, converters = _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                tokens=["role_play:converter.c1:converter.c2"],
                technique_class=_StubTechnique,
                scenario_name="x",
            )
        assert converters == {"role_play": [c1, c2]}

    def test_overlapping_tokens_append_in_order(self, mock_memory) -> None:
        c1 = MagicMock(spec=Converter)
        c2 = MagicMock(spec=Converter)
        with _patch_converter_registry({"c1": c1, "c2": c2}):
            _, converters = _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                tokens=["easy:converter.c1", "role_play:converter.c2"],
                technique_class=_StubTechnique,
                scenario_name="x",
            )
        # role_play is targeted by both the aggregate token and the concrete token.
        assert converters["role_play"] == [c1, c2]
        assert converters["single_turn"] == [c1]

    def test_unknown_converter_raises(self, mock_memory) -> None:
        with _patch_converter_registry({"known": MagicMock(spec=Converter)}):
            with pytest.raises(ValueError, match="not a registered converter"):
                _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                    tokens=["role_play:converter.missing"],
                    technique_class=_StubTechnique,
                    scenario_name="x",
                )

    def test_unknown_modifier_prefix_raises(self, mock_memory) -> None:
        with _patch_converter_registry({}):
            with pytest.raises(ValueError, match="Unknown technique modifier"):
                _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                    tokens=["role_play:scorer.something"],
                    technique_class=_StubTechnique,
                    scenario_name="x",
                )

    def test_unknown_base_technique_raises(self, mock_memory) -> None:
        with _patch_converter_registry({}):
            with pytest.raises(ValueError, match="not found for scenario"):
                _resolver_mod.ScenarioConfigurationResolver.resolve_techniques_and_converters(
                    tokens=["nope:converter.c1"],
                    technique_class=_StubTechnique,
                    scenario_name="x",
                )

    async def test_start_run_forwards_technique_converters(self, mock_all_registries) -> None:
        """A converter token is resolved and forwarded through the registry as ``technique_converters``."""
        conv = MagicMock(spec=Converter)
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._technique_class = _StubTechnique

        service = ScenarioRunService()
        with _patch_converter_registry({"translation_spanish": conv}):
            await service.start_run_async(request=_make_request(techniques=["role_play:converter.translation_spanish"]))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        assert init_call.kwargs["scenario_techniques"] == [_StubTechnique.ROLE_PLAY]
        assert init_call.kwargs["technique_converters"] == {"role_play": [conv]}


def test_planned_progress_deduplicates_attempts_and_keeps_latest_outcome(mock_memory) -> None:
    seed_group = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
    seed_group_id = seed_group.logical_id
    atomic_group_id = config_hash({"atomic_attack_name": "attack", "technique_eval_hash": "eval"})
    atomic_identifier = AtomicAttackIdentifier.build(
        attack_identifier=ComponentIdentifier(class_name="TestAttack", class_module="tests"),
        seed_group=seed_group,
    )
    plan = ScenarioRunPlan(
        scenario_registry_name="test.scenario",
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=atomic_group_id,
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=[seed_group_id],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(
                id=seed_group_id,
                objective_sha256="objective-sha",
                objective="objective",
            )
        ],
    )
    attempts = [
        AttackResult(
            conversation_id=f"conversation-{index}",
            objective="objective",
            atomic_attack_identifier=atomic_identifier,
            outcome=outcome,
            timestamp=datetime(2025, 1, 1, 0, index, tzinfo=timezone.utc),
            attribution_data={"parent_collection": "attack", "parent_eval_hash": "eval"},
        )
        for index, outcome in enumerate(
            (AttackOutcome.ERROR, AttackOutcome.FAILURE, AttackOutcome.SUCCESS, AttackOutcome.ERROR)
        )
    ]
    scenario_result = make_scenario_result(
        attack_results={"attack": attempts},
        scenario_run_state=ScenarioRunState.COMPLETED,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )

    summary = ScenarioRunService()._build_response_from_db(scenario_result=scenario_result)

    assert summary.total_attacks == 1
    assert summary.completed_attacks == 1
    assert summary.objective_achieved_rate == 0
    assert len(summary.failed_attacks) == 2
    assert summary.total_retries == 3


def test_planned_progress_includes_latest_errors_in_success_rate_denominator(mock_memory) -> None:
    atomic_group_id = config_hash({"atomic_attack_name": "attack", "technique_eval_hash": "eval"})
    plan = ScenarioRunPlan(
        scenario_registry_name="test.scenario",
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=atomic_group_id,
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=["seed-success", "seed-error"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(
                id="seed-success",
                objective_sha256="success-sha",
                objective="success objective",
            ),
            ScenarioRunPlanSeedGroup(
                id="seed-error",
                objective_sha256="error-sha",
                objective="error objective",
            ),
        ],
    )
    results = [
        AttackResult(
            conversation_id="success-conversation",
            objective="success objective",
            outcome=AttackOutcome.SUCCESS,
            attribution_data={
                "parent_collection": "attack",
                "parent_eval_hash": "eval",
                "seed_group_id": "seed-success",
            },
        ),
        AttackResult(
            conversation_id="error-conversation",
            objective="error objective",
            outcome=AttackOutcome.ERROR,
            attribution_data={
                "parent_collection": "attack",
                "parent_eval_hash": "eval",
                "seed_group_id": "seed-error",
            },
        ),
    ]
    scenario_result = make_scenario_result(
        attack_results={"attack": results},
        scenario_run_state=ScenarioRunState.COMPLETED,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )

    summary = ScenarioRunService()._build_response_from_db(scenario_result=scenario_result)

    assert summary.total_attacks == 2
    assert summary.completed_attacks == 2
    assert summary.objective_achieved_rate == 50


def test_planned_progress_maps_legacy_objective_hash_to_logical_seed_id(mock_memory) -> None:
    objective = "legacy resumed objective"
    seed_group = AttackSeedGroup(seeds=[SeedObjective(value=objective)])
    seed_group_id = seed_group.logical_id
    atomic_group_id = config_hash({"atomic_attack_name": "attack", "technique_eval_hash": "eval"})
    plan = ScenarioRunPlan(
        scenario_registry_name="test.scenario",
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=atomic_group_id,
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=[seed_group_id],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(
                id=seed_group_id,
                objective_sha256=_svc_mod.to_sha256(objective),
                objective=objective,
            )
        ],
    )
    legacy_attempt = AttackResult(
        conversation_id="legacy-conversation",
        objective=objective,
        outcome=AttackOutcome.SUCCESS,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        attribution_data={"parent_collection": "attack", "parent_eval_hash": "eval"},
    )
    scenario_result = make_scenario_result(
        attack_results={"attack": [legacy_attempt]},
        scenario_run_state=ScenarioRunState.COMPLETED,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )

    summary = ScenarioRunService()._build_response_from_db(scenario_result=scenario_result)

    assert summary.total_attacks == 1
    assert summary.completed_attacks == 1


def test_get_progress_uses_lightweight_queries_without_full_hydration(mock_memory) -> None:
    plan = ScenarioRunPlan(atomic_groups=[], seed_groups=[], scenario_registry_name="test.scenario")
    header = make_scenario_result(
        attack_results={},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )
    mock_memory.get_scenario_result_header.return_value = header
    mock_memory.get_scenario_attack_result_deltas.return_value = ([], False)
    mock_memory.get_scenario_results.reset_mock()

    service = ScenarioRunService()
    completed_task = MagicMock()
    completed_task.done.return_value = True
    service._active_tasks[str(header.id)] = _svc_mod._ActiveTask(
        scenario_result_id=str(header.id),
        task=completed_task,
        scenario=MagicMock(),
    )

    progress = service.get_run_progress(
        scenario_result_id=str(header.id),
        since=None,
        limit=25,
    )

    assert progress is not None
    assert progress.plan == plan
    assert progress.plan_complete is True
    mock_memory.get_scenario_results.assert_not_called()
    assert str(header.id) not in service._active_tasks


def test_get_progress_cache_only_maps_new_storage_rows(mock_memory) -> None:
    seed_group_ids = ["seed-1", "seed-2"]
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=seed_group_ids,
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id=seed_id, objective_sha256=f"sha-{seed_id}", objective=seed_id)
            for seed_id in seed_group_ids
        ],
    )
    header = make_scenario_result(
        attack_results={},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )
    deltas = [
        ScenarioAttackResultDelta(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=f"conversation-{seed_id}",
            objective=seed_id,
            outcome=AttackOutcome.SUCCESS,
            execution_time_ms=10,
            timestamp=datetime(2025, 1, 1, 0, index, tzinfo=timezone.utc),
            attribution_data={
                "parent_collection": "attack",
                "parent_eval_hash": "eval",
                "seed_group_id": seed_id,
            },
        )
        for index, seed_id in enumerate(seed_group_ids)
    ]
    mock_memory.get_scenario_result_header.return_value = header
    mock_memory.get_scenario_attack_result_deltas.side_effect = [
        ([deltas[0]], False),
        ([deltas[1]], False),
    ]
    service = ScenarioRunService()

    first = service.get_run_progress(
        scenario_result_id=str(header.id),
        since=None,
        limit=25,
    )
    second = service.get_run_progress(
        scenario_result_id=str(header.id),
        since=None,
        limit=25,
    )

    assert first is not None
    assert second is not None
    assert first.summary.overall.completed == 1
    assert second.summary.overall.completed == 2
    assert len(second.results) == 2
    second_cursor = mock_memory.get_scenario_attack_result_deltas.call_args_list[1].kwargs["cursor"]
    assert second_cursor.attack_result_id == deltas[0].attack_result_id


def test_get_progress_cache_refreshes_identifier_enriched_after_insert(mock_memory) -> None:
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=["seed"],
            )
        ],
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed", objective_sha256="sha", objective="objective")],
    )
    header = make_scenario_result(
        attack_results={},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )
    attack_identifier = ComponentIdentifier(class_name="PromptSendingAttack", class_module="tests")
    pre_enrichment = ScenarioAttackResultDelta(
        attack_result_id=str(uuid.uuid4()),
        conversation_id="conversation",
        objective="objective",
        outcome=AttackOutcome.SUCCESS,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        atomic_attack_identifier=AtomicAttackIdentifier.build(attack_identifier=attack_identifier),
        attribution_data={
            "parent_collection": "attack",
            "parent_eval_hash": "eval",
            "seed_group_id": "seed",
        },
    )
    post_enrichment = pre_enrichment.model_copy(
        update={
            "atomic_attack_identifier": AtomicAttackIdentifier.build(
                technique_identifier=ComponentIdentifier(
                    class_name="AttackTechnique",
                    class_module="tests",
                    children={"attack": attack_identifier},
                ),
                seed_group=AttackSeedGroup(seeds=[SeedObjective(value="objective")]),
            )
        }
    )
    mock_memory.get_scenario_result_header.return_value = header
    mock_memory.get_scenario_attack_result_deltas.side_effect = [
        ([pre_enrichment], False),
        ([post_enrichment], False),
    ]
    service = ScenarioRunService()

    first = service.get_run_progress(scenario_result_id=str(header.id), since=None, limit=25)
    second = service.get_run_progress(scenario_result_id=str(header.id), since=None, limit=25)

    assert first is not None
    assert second is not None
    assert first.summary.atomic_groups[0].technique_details is None
    assert second.summary.atomic_groups[0].technique_details is not None
    assert mock_memory.get_scenario_attack_result_deltas.call_args_list[1].kwargs["cursor"] is None


def test_get_progress_treats_duplicate_stored_plan_groups_as_incomplete(mock_memory) -> None:
    group = ScenarioRunPlanAtomicGroup(
        id="duplicate",
        atomic_attack_name="attack",
        display_group="Attack",
        technique_eval_hash="eval",
        seed_group_ids=["seed-1"],
    ).model_dump(mode="json")
    header = make_scenario_result(
        attack_results={},
        metadata={
            SCENARIO_RUN_PLAN_METADATA_KEY: {
                "version": 1,
                "atomic_groups": [group, group],
                "seed_groups": [
                    ScenarioRunPlanSeedGroup(
                        id="seed-1",
                        objective_sha256="objective-sha",
                        objective="objective",
                    ).model_dump(mode="json")
                ],
            }
        },
    )
    mock_memory.get_scenario_result_header.return_value = header
    mock_memory.get_scenario_attack_result_deltas.return_value = ([], False)

    progress = ScenarioRunService().get_run_progress(
        scenario_result_id=str(header.id),
        since=None,
        limit=25,
    )

    assert progress is not None
    assert progress.plan_complete is False
    assert progress.plan is not None
    assert progress.plan.atomic_groups == []


def test_progress_prefers_persisted_logical_seed_group_attribution() -> None:
    delta = ScenarioAttackResultDelta(
        attack_result_id=str(uuid.uuid4()),
        conversation_id="conversation-attributed",
        objective="objective",
        objective_sha256="objective-sha",
        outcome=AttackOutcome.SUCCESS,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        attribution_data={
            "parent_collection": "attack",
            "parent_eval_hash": "eval",
            "seed_group_id": "canonical-seed-id",
        },
    )

    mapped = ScenarioRunService._map_progress_delta(
        delta=delta,
        plan_lookup=_svc_mod._ScenarioPlanLookup.from_plan(plan=None),
    )

    assert mapped.seed_group_id == "canonical-seed-id"


def test_progress_builds_attack_technique_details_once_per_atomic_group() -> None:
    inner_objective_target = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="tests",
        params={
            "endpoint": "https://example.com",
            "model_name": "gpt-4o-deployment",
            "temperature": 0.7,
            "top_p": 0.9,
        },
    )
    objective_target = ComponentIdentifier(
        class_name="RoundRobinTarget",
        class_module="tests",
        params={"endpoint": "https://wrapper.example.com"},
        children={"targets": [inner_objective_target]},
    )
    objective_scorer = ComponentIdentifier(
        class_name="FloatScaleThresholdScorer",
        class_module="tests",
        params={"threshold": 0.1},
    )
    attack_identifier = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="tests",
        params={"max_turns": 1},
        children={
            "objective_target": objective_target,
            "objective_scorer": objective_scorer,
        },
    )
    technique_seed = ComponentIdentifier(
        class_name="SeedPrompt",
        class_module="tests",
        params={
            "value": "Use this jailbreak.",
            "value_sha256": "sha256",
            "data_type": "text",
            "is_general_technique": True,
        },
    )
    technique_identifier = ComponentIdentifier(
        class_name="AttackTechnique",
        class_module="tests",
        children={
            "attack": attack_identifier,
            "technique_seeds": [technique_seed],
        },
    )
    delta = ScenarioAttackResultDelta(
        attack_result_id=str(uuid.uuid4()),
        conversation_id="conversation-identity",
        objective="objective",
        objective_sha256="objective-sha",
        outcome=AttackOutcome.SUCCESS,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            technique_identifier=technique_identifier,
            seed_group=AttackSeedGroup(seeds=[SeedObjective(value="objective")]),
        ),
    )

    mapped = ScenarioRunService._map_progress_delta(
        delta=delta,
        plan_lookup=_svc_mod._ScenarioPlanLookup.from_plan(plan=None),
    )
    details_by_group = ScenarioRunService._build_technique_details_by_group(
        deltas=[delta],
        results=[mapped],
    )
    details = details_by_group[mapped.atomic_group_id]

    assert "technique_details" not in mapped.model_dump()
    assert details.component_name == "AttackTechnique"
    projected_attack = details.children["attack"][0]
    assert projected_attack.component_name == "PromptSendingAttack"
    assert projected_attack.parameters == {"max_turns": 1}
    assert "objective_scorer" not in projected_attack.children
    projected_target = projected_attack.children["objective_target"][0]
    assert projected_target.component_name == "OpenAIChatTarget"
    assert projected_target.parameters == {
        "underlying_model_name": "gpt-4o-deployment",
        "temperature": 0.7,
        "top_p": 0.9,
    }
    assert details.children["technique_seeds"][0].parameters == {
        "value": "Use this jailbreak.",
        "data_type": "text",
    }


def test_technique_details_normalize_every_declared_target_slot() -> None:
    """Operational params are dropped in each target slot, not just objective_target."""
    attack_identifier = ComponentIdentifier(
        class_name="RedTeamingAttack",
        class_module="tests",
        children={
            "objective_target": ComponentIdentifier(
                class_name="OpenAIChatTarget",
                class_module="tests",
                params={"endpoint": "https://objective.example.com", "model_name": "gpt-4o"},
            ),
            "adversarial_chat": ComponentIdentifier(
                class_name="OpenAIChatTarget",
                class_module="tests",
                params={"endpoint": "https://adversarial.example.com", "model_name": "gpt-4o-mini"},
            ),
        },
    )
    technique_identifier = ComponentIdentifier(
        class_name="AttackTechnique",
        class_module="tests",
        children={"attack": attack_identifier},
    )

    details = ScenarioRunService._build_attack_technique_details(technique_identifier=technique_identifier)

    projected_attack = details.children["attack"][0]
    assert projected_attack.children["objective_target"][0].parameters == {"underlying_model_name": "gpt-4o"}
    assert projected_attack.children["adversarial_chat"][0].parameters == {"underlying_model_name": "gpt-4o-mini"}
    assert "endpoint" not in details.model_dump_json()


def test_technique_seeds_child_name_matches_the_identifier_field() -> None:
    """The one display constant left in the service must track the typed field."""
    assert _svc_mod._TECHNIQUE_SEEDS_CHILD in AttackTechniqueIdentifier.model_fields


def test_synthesize_legacy_plan_deduplicates_seed_ids_in_first_seen_order() -> None:
    delta_units = [
        ("attack", "eval", "seed-b", "objective b"),
        ("other attack", "other-eval", "seed-b", "objective b"),
        ("attack", "eval", "seed-a", "objective a"),
        ("attack", "eval", "seed-b", "objective b"),
    ]
    deltas = [
        ScenarioAttackResultDelta(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=f"conversation-{seed_group_id}",
            objective=objective,
            outcome=AttackOutcome.SUCCESS,
            execution_time_ms=10,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            attribution_data={
                "parent_collection": attack_name,
                "parent_eval_hash": eval_hash,
                "seed_group_id": seed_group_id,
            },
        )
        for attack_name, eval_hash, seed_group_id, objective in delta_units
    ]

    plan = ScenarioRunService._synthesize_legacy_plan(deltas=deltas)

    assert [group.atomic_attack_name for group in plan.atomic_groups] == ["attack", "other attack"]
    assert plan.atomic_groups[0].seed_group_ids == ["seed-b", "seed-a"]
    assert plan.atomic_groups[1].seed_group_ids == ["seed-b"]
    assert [seed.id for seed in plan.seed_groups] == ["seed-b", "seed-a"]


def test_get_progress_synthesizes_incomplete_legacy_plan(mock_memory) -> None:
    header = make_scenario_result(
        attack_results={},
        scenario_run_state=ScenarioRunState.COMPLETED,
        metadata={},
    )
    delta = ScenarioAttackResultDelta(
        attack_result_id=str(uuid.uuid4()),
        conversation_id="conversation-legacy",
        objective="legacy objective",
        outcome=AttackOutcome.FAILURE,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        attribution_data={"parent_collection": "legacy attack"},
    )
    mock_memory.get_scenario_result_header.return_value = header
    mock_memory.get_scenario_attack_result_deltas.return_value = ([delta], False)

    progress = ScenarioRunService().get_run_progress(
        scenario_result_id=str(header.id),
        since=None,
        limit=25,
    )

    assert progress is not None
    assert progress.plan_complete is False
    assert progress.plan is not None
    assert len(progress.plan.atomic_groups) == 1
    assert len(progress.results) == 1


def test_get_progress_treats_invalid_persisted_plan_as_incomplete(mock_memory, caplog) -> None:
    header = make_scenario_result(
        attack_results={},
        scenario_run_state=ScenarioRunState.COMPLETED,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: {"atomic_groups": "invalid"}},
    )
    mock_memory.get_scenario_result_header.return_value = header
    mock_memory.get_scenario_attack_result_deltas.return_value = ([], False)

    progress = ScenarioRunService().get_run_progress(
        scenario_result_id=str(header.id),
        since=None,
        limit=25,
    )

    assert progress is not None
    assert progress.plan_complete is False
    assert progress.plan is not None
    assert progress.plan.atomic_groups == []
    assert "treating the plan as unavailable" in caplog.text


def test_progress_summary_uses_latest_attempt_for_backend_owned_counts() -> None:
    plan = ScenarioRunPlan(
        scenario_registry_name="test.scenario",
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group",
                atomic_attack_name="attack",
                display_group="Custom display group",
                technique_name="Technique",
                technique_eval_hash="eval",
                seed_group_ids=["seed-1", "seed-2"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id="seed-1", objective_sha256="sha-1", objective="one"),
            ScenarioRunPlanSeedGroup(id="seed-2", objective_sha256="sha-2", objective="two"),
        ],
    )
    results = [
        ScenarioProgressResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id="conversation-success",
            atomic_group_id="group",
            atomic_attack_name="attack",
            seed_group_id="seed-1",
            outcome=AttackOutcome.SUCCESS,
            execution_time_ms=10,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ),
        ScenarioProgressResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id="conversation-error",
            atomic_group_id="group",
            atomic_attack_name="attack",
            seed_group_id="seed-1",
            outcome=AttackOutcome.ERROR,
            execution_time_ms=10,
            timestamp=datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc),
        ),
        ScenarioProgressResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id="conversation-failure",
            atomic_group_id="group",
            atomic_attack_name="attack",
            seed_group_id="seed-2",
            outcome=AttackOutcome.FAILURE,
            execution_time_ms=10,
            timestamp=datetime(2025, 1, 1, 0, 2, tzinfo=timezone.utc),
            total_retries=2,
            score=ScenarioProgressScore(
                scorer_name="FloatScaleThresholdScorer",
                score_type="true_false",
                status=ScoreStatus.COMPLETE,
                score_value="false",
                score_rationale="The objective was not achieved.",
            ),
        ),
    ]

    target_identifier = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="tests",
        params={
            "endpoint": "https://example.com",
            "model_name": "gpt-test",
            "temperature": 0.2,
            "max_requests_per_minute": 60,
        },
    )
    sub_scorer_identifier = ComponentIdentifier(
        class_name="SubScorer",
        class_module="tests",
    )
    scorer_identifier = ComponentIdentifier(
        class_name="FloatScaleThresholdScorer",
        class_module="tests",
        params={
            "scorer_type": "true_false",
            "score_aggregator": "mean",
            "threshold": 0.1,
            "float_scale_aggregator": "max",
        },
        children={
            "prompt_target": target_identifier,
            "sub_scorers": [sub_scorer_identifier],
        },
    )
    metrics = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=3,
        accuracy=0.95,
        accuracy_standard_error=0.01,
        f1_score=0.94,
        precision=0.93,
        recall=0.92,
        average_score_time_seconds=0.25,
    )
    with patch.object(_svc_mod, "find_objective_metrics_by_eval_hash", return_value=metrics):
        summary = ScenarioRunService._build_progress_summary(
            plan=plan,
            plan_complete=True,
            results=results,
            active_group_ids=["group"],
            terminal=False,
            objective_scorer_identifier=scorer_identifier,
            technique_details_by_group={},
        )

    assert summary.overall.completed == 2
    assert summary.overall.succeeded == 0
    assert summary.overall.errors == 1
    assert summary.overall.retries == 3
    assert summary.atomic_groups[0].status == "RUNNING"
    assert summary.display_groups[0].display_group == "Custom display group"
    assert summary.techniques[0].completed == summary.overall.completed
    assert summary.techniques[0].display_group == "Technique"
    assert summary.seed_groups[0].succeeded == 0
    assert summary.objective_scorer is not None
    assert summary.objective_scorer.component_name == "FloatScaleThresholdScorer"
    assert summary.objective_scorer.parameters == {
        "scorer_type": "true_false",
        "score_aggregator": "mean",
        "threshold": 0.1,
        "float_scale_aggregator": "max",
    }
    assert summary.objective_scorer.children["prompt_target"][0].component_name == "OpenAIChatTarget"
    assert summary.objective_scorer.children["prompt_target"][0].parameters == {
        "underlying_model_name": "gpt-test",
        "temperature": 0.2,
    }
    assert summary.objective_scorer.children["sub_scorers"][0].component_name == "SubScorer"
    assert summary.objective_scorer.metrics is not None
    assert summary.objective_scorer.metrics.accuracy == 0.95
    assert summary.objective_scorer.metrics.f1_score == 0.94


def test_decode_progress_cursor_rejects_cross_run_cursor() -> None:
    delta = ScenarioAttackResultDelta(
        attack_result_id=str(uuid.uuid4()),
        conversation_id="conversation-cursor",
        objective="objective",
        outcome=AttackOutcome.SUCCESS,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    cursor = ScenarioRunService._encode_progress_cursor(scenario_result_id="run-a", delta=delta)

    with pytest.raises(ValueError, match="does not belong"):
        ScenarioRunService._decode_progress_cursor(since=cursor, scenario_result_id="run-b")


def test_decode_progress_cursor_rejects_malformed_cursor() -> None:
    with pytest.raises(ValueError, match="Malformed"):
        ScenarioRunService._decode_progress_cursor(since="not-a-cursor", scenario_result_id="run-a")
