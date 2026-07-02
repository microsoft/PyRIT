# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ScenarioRunService.
"""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pyrit.backend.services.scenario_run_service as _svc_mod
from pyrit.backend.services.scenario_run_service import (
    _DEFAULT_MAX_CONCURRENT_RUNS,
    ScenarioRunService,
)
from pyrit.models import AttackOutcome, ScenarioRunState
from pyrit.models.catalog.scenario import RunScenarioRequest
from pyrit.prompt_converter import PromptConverter
from pyrit.scenario.core import DatasetAttackConfiguration, DatasetConfiguration
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy


class _StubStrategy(ScenarioStrategy):
    """Minimal concrete ScenarioStrategy used to exercise converter-token parsing."""

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
    return patch.object(_svc_mod.ConverterRegistry, "get_registry_singleton", return_value=reg)


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
    strategies: list[str] | None = None,
    scenario_result_id: str | None = None,
    dataset_names: list[str] | None = None,
    max_dataset_size: int | None = None,
) -> RunScenarioRequest:
    """Create a RunScenarioRequest for testing."""
    return RunScenarioRequest(
        scenario_name=scenario_name,
        target_name=target_name,
        initializers=initializers,
        strategies=strategies,
        scenario_result_id=scenario_result_id,
        dataset_names=dataset_names,
        max_dataset_size=max_dataset_size,
    )


def _make_db_scenario_result(
    *,
    result_id: str = "sr-uuid-1",
    scenario_name: str = "foundry.red_team_agent",
    run_state: str = "IN_PROGRESS",
    attack_results: dict | None = None,
) -> MagicMock:
    """Create a mock ScenarioResult as returned by CentralMemory."""
    sr = MagicMock()
    sr.id = result_id
    sr.scenario_name = scenario_name
    sr.scenario_version = 1
    sr.scenario_run_state = run_state
    sr.get_strategies_used.return_value = []
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


@pytest.fixture
def mock_memory():
    """Patch CentralMemory.get_memory_instance to return a mock."""
    mock = MagicMock()
    mock.get_scenario_results.return_value = []
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
    mock_scenario_instance._strategy_class = MagicMock()
    mock_scenario_instance._default_dataset_config = MagicMock()

    mock_sr = MagicMock()
    mock_sr.get_class.return_value = mock_scenario_class
    mock_sr.create_instance.return_value = mock_scenario_instance
    mock_sr.create_and_initialize_async = AsyncMock(return_value=mock_scenario_instance)

    mock_tr = MagicMock()
    mock_tr.instances.get.return_value = MagicMock()
    mock_tr.instances.get_names.return_value = ["my_target"]

    mock_ir = MagicMock()
    mock_ir.get_class.return_value = MagicMock(return_value=MagicMock(initialize_async=AsyncMock()))

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
        mock_ir.get_class.side_effect = KeyError("'bad_init' not found")

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton"),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton", return_value=mock_ir),
        ):
            with pytest.raises(ValueError, match="Initializer not found"):
                await service.start_run_async(request=_make_request(initializers=["bad_init"]))

    async def test_start_run_invalid_strategy_raises_value_error(self, mock_memory) -> None:
        """Test that an invalid strategy name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_strategy_class = MagicMock(side_effect=ValueError("not a valid strategy"))
        mock_strategy_class.__iter__ = MagicMock(return_value=iter([MagicMock(value="valid_strat")]))

        mock_instance = MagicMock(_strategy_class=mock_strategy_class)
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
            with pytest.raises(ValueError, match="Strategy.*not found for scenario"):
                await service.start_run_async(request=_make_request(strategies=["bad_strategy"]))

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
                # strategies forces the introspection path
                await service.start_run_async(request=_make_request(strategies=["any"]))

    async def test_start_run_passes_valid_strategies_through(self, mock_all_registries) -> None:
        """A valid strategy list is converted to enum values and forwarded to initialize_async."""
        strategy_a = MagicMock(value="strat_a")
        strategy_b = MagicMock(value="strat_b")

        def _lookup(name):
            return {"strat_a": strategy_a, "strat_b": strategy_b}[name]

        mock_strategy_class = MagicMock(side_effect=_lookup)
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._strategy_class = mock_strategy_class

        service = ScenarioRunService()
        await service.start_run_async(request=_make_request(strategies=["strat_a", "strat_b"]))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        assert init_call.kwargs["scenario_strategies"] == [strategy_a, strategy_b]

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

    async def test_start_run_dataset_names_falls_back_when_subclass_constructor_incompatible(
        self, mock_all_registries, caplog
    ) -> None:
        """If the subclass __init__ rejects standard kwargs, fall back to plain ``DatasetConfiguration``."""

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
        with caplog.at_level("WARNING", logger=_svc_mod.logger.name):
            await service.start_run_async(request=_make_request(dataset_names=["custom"]))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        built_config = init_call.kwargs["dataset_config"]

        # Fallback is the generic base class, not the subclass
        assert type(built_config) is DatasetAttackConfiguration
        assert built_config.dataset_names == ["custom"]
        # Warning was logged so the operator can see the silent degradation
        assert any(
            "_RequiresExtraArgConfiguration" in record.message
            and "Falling back to a generic DatasetAttackConfiguration" in record.message
            for record in caplog.records
        )

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

        # Each call needs a unique scenario_result_id
        call_count = 0

        async def _set_unique_id(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            scenario_instance._scenario_result_id = f"sr-uuid-{call_count}"
            return scenario_instance

        mock_sr.create_and_initialize_async = AsyncMock(side_effect=_set_unique_id)

        # Fill up to the limit
        for _ in range(_DEFAULT_MAX_CONCURRENT_RUNS):
            await service.start_run_async(request=_make_request())

        # Next one should fail
        with pytest.raises(ValueError, match="Maximum concurrent runs"):
            await service.start_run_async(request=_make_request())

    async def test_start_run_runs_initializers(self, mock_all_registries) -> None:
        """Test that initializers are run during start_run_async."""
        service = ScenarioRunService()
        mock_ir = mock_all_registries["initializer_registry"]
        mock_init_instance = mock_ir.get_class.return_value.return_value

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
        db_result = _make_db_scenario_result(result_id="sr-123", run_state="IN_PROGRESS")
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-123")

        assert fetched is not None
        assert fetched.scenario_result_id == "sr-123"
        assert fetched.scenario_name == "foundry.red_team_agent"
        assert fetched.status == ScenarioRunState.IN_PROGRESS

    def test_get_run_falls_back_to_persisted_error(self, mock_memory) -> None:
        """Test that get_run extracts error from persisted error AttackResult when no active task.

        After the foreign-key-based scenario linkage refactor, error
        AttackResults are located via
        ``get_attack_results(scenario_result_id=..., outcome=ERROR)`` rather
        than via a per-scenario error_attack_result_ids manifest.
        """
        db_result = _make_db_scenario_result(result_id="sr-fail", run_state="FAILED")

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
        mock_memory.get_scenario_results.return_value = []
        service = ScenarioRunService()
        result = service.list_runs()
        assert result.items == []
        mock_memory.get_scenario_results.assert_called_once_with(limit=100)

    def test_list_runs_returns_all_runs(self, mock_memory) -> None:
        """Test that list_runs returns all runs from the database."""
        db_results = [
            _make_db_scenario_result(result_id="sr-1", run_state="COMPLETED"),
            _make_db_scenario_result(result_id="sr-2", run_state="IN_PROGRESS"),
        ]
        mock_memory.get_scenario_results.return_value = db_results

        service = ScenarioRunService()
        result = service.list_runs()
        assert len(result.items) == 2
        mock_memory.get_scenario_results.assert_called_once_with(limit=100)

    def test_list_runs_passes_custom_limit(self, mock_memory) -> None:
        """Test that list_runs passes a custom limit to the memory query."""
        mock_memory.get_scenario_results.return_value = []
        service = ScenarioRunService()
        service.list_runs(limit=10)
        mock_memory.get_scenario_results.assert_called_once_with(limit=10)


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
        cancelled_result = _make_db_scenario_result(result_id=response.scenario_result_id, run_state="CANCELLED")
        mock_memory.get_scenario_results.side_effect = [[running_result], [cancelled_result]]

        result = await service.cancel_run_async(scenario_result_id=response.scenario_result_id)

        mock_memory.update_scenario_run_state.assert_called_once_with(
            scenario_result_id=response.scenario_result_id,
            scenario_run_state="CANCELLED",
            error_message="Run was cancelled by user",
            error_type="CancelledError",
        )
        assert result is not None
        assert result.status == ScenarioRunState.CANCELLED

    async def test_cancel_completed_run_raises_value_error(self, mock_memory) -> None:
        """Test that cancelling a completed run raises ValueError."""
        db_result = _make_db_scenario_result(result_id="sr-done", run_state="COMPLETED")
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        with pytest.raises(ValueError, match="Cannot cancel run"):
            await service.cancel_run_async(scenario_result_id="sr-done")

    async def test_cancel_already_cancelled_run_raises_value_error(self, mock_memory) -> None:
        """Test that cancelling an already-cancelled run raises ValueError."""
        db_result = _make_db_scenario_result(result_id="sr-cancelled", run_state="CANCELLED")
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
        mock_scenario_result.get_strategies_used.return_value = ["base64"]
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
        db_result = _make_db_scenario_result(result_id="sr-running", run_state="IN_PROGRESS")
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
            run_state="COMPLETED",
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
            run_state="IN_PROGRESS",
            attack_results={
                "attack_a": [mock_success, mock_failure],
                "attack_b": [mock_undetermined],
            },
        )
        db_result.get_strategies_used.return_value = ["attack_a", "attack_b"]
        db_result.objective_achieved_rate.return_value = 33
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-running")

        assert fetched is not None
        assert fetched.status == ScenarioRunState.IN_PROGRESS
        assert fetched.total_attacks == 3
        assert fetched.completed_attacks == 3
        assert fetched.strategies_used == ["attack_a", "attack_b"]
        assert fetched.objective_achieved_rate == 33

    def test_created_run_shows_zero_counts(self, mock_memory) -> None:
        """Test that a CREATED run with no results shows zero counts."""
        db_result = _make_db_scenario_result(
            result_id="sr-new",
            run_state="CREATED",
            attack_results={},
        )
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-new")

        assert fetched is not None
        assert fetched.status == ScenarioRunState.CREATED
        assert fetched.total_attacks == 0
        assert fetched.completed_attacks == 0
        assert fetched.strategies_used == []

    def test_completed_run_still_shows_full_counts(self, mock_memory) -> None:
        """Test that COMPLETED runs still show accurate counts after the fix."""
        from pyrit.models import AttackOutcome

        mock_success = MagicMock()
        mock_success.outcome = AttackOutcome.SUCCESS

        db_result = _make_db_scenario_result(
            result_id="sr-done",
            run_state="COMPLETED",
            attack_results={"attack_a": [mock_success]},
        )
        db_result.get_strategies_used.return_value = ["attack_a"]
        db_result.objective_achieved_rate.return_value = 100
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        fetched = service.get_run(scenario_result_id="sr-done")

        assert fetched is not None
        assert fetched.status == ScenarioRunState.COMPLETED
        assert fetched.total_attacks == 1
        assert fetched.completed_attacks == 1
        assert fetched.strategies_used == ["attack_a"]
        assert fetched.objective_achieved_rate == 100


class TestResolveStrategiesAndConverters:
    """Tests for per-technique converter resolution from ``--strategies`` tokens."""

    def test_plain_strategy_no_converters(self, mock_memory) -> None:
        service = ScenarioRunService()
        with _patch_converter_registry({}):
            enums, converters = service._resolve_strategies_and_converters(
                tokens=["role_play"], strategy_class=_StubStrategy, scenario_name="x"
            )
        assert enums == [_StubStrategy.ROLE_PLAY]
        assert converters == {}

    def test_single_converter_appended(self, mock_memory) -> None:
        conv = MagicMock(spec=PromptConverter)
        service = ScenarioRunService()
        with _patch_converter_registry({"translation_spanish": conv}):
            enums, converters = service._resolve_strategies_and_converters(
                tokens=["role_play:converter.translation_spanish"],
                strategy_class=_StubStrategy,
                scenario_name="x",
            )
        assert enums == [_StubStrategy.ROLE_PLAY]
        assert converters == {"role_play": [conv]}

    def test_aggregate_token_applies_converter_to_all_concrete(self, mock_memory) -> None:
        conv = MagicMock(spec=PromptConverter)
        service = ScenarioRunService()
        with _patch_converter_registry({"c1": conv}):
            enums, converters = service._resolve_strategies_and_converters(
                tokens=["easy:converter.c1"], strategy_class=_StubStrategy, scenario_name="x"
            )
        assert enums == [_StubStrategy.EASY]
        assert converters == {"role_play": [conv], "single_turn": [conv]}

    def test_multiple_converters_preserve_order(self, mock_memory) -> None:
        c1 = MagicMock(spec=PromptConverter)
        c2 = MagicMock(spec=PromptConverter)
        service = ScenarioRunService()
        with _patch_converter_registry({"c1": c1, "c2": c2}):
            _, converters = service._resolve_strategies_and_converters(
                tokens=["role_play:converter.c1:converter.c2"],
                strategy_class=_StubStrategy,
                scenario_name="x",
            )
        assert converters == {"role_play": [c1, c2]}

    def test_overlapping_tokens_append_in_order(self, mock_memory) -> None:
        c1 = MagicMock(spec=PromptConverter)
        c2 = MagicMock(spec=PromptConverter)
        service = ScenarioRunService()
        with _patch_converter_registry({"c1": c1, "c2": c2}):
            _, converters = service._resolve_strategies_and_converters(
                tokens=["easy:converter.c1", "role_play:converter.c2"],
                strategy_class=_StubStrategy,
                scenario_name="x",
            )
        # role_play is targeted by both the aggregate token and the concrete token.
        assert converters["role_play"] == [c1, c2]
        assert converters["single_turn"] == [c1]

    def test_unknown_converter_raises(self, mock_memory) -> None:
        service = ScenarioRunService()
        with _patch_converter_registry({"known": MagicMock(spec=PromptConverter)}):
            with pytest.raises(ValueError, match="not a registered converter"):
                service._resolve_strategies_and_converters(
                    tokens=["role_play:converter.missing"],
                    strategy_class=_StubStrategy,
                    scenario_name="x",
                )

    def test_unknown_modifier_prefix_raises(self, mock_memory) -> None:
        service = ScenarioRunService()
        with _patch_converter_registry({}):
            with pytest.raises(ValueError, match="Unknown strategy modifier"):
                service._resolve_strategies_and_converters(
                    tokens=["role_play:scorer.something"],
                    strategy_class=_StubStrategy,
                    scenario_name="x",
                )

    def test_unknown_base_strategy_raises(self, mock_memory) -> None:
        service = ScenarioRunService()
        with _patch_converter_registry({}):
            with pytest.raises(ValueError, match="not found for scenario"):
                service._resolve_strategies_and_converters(
                    tokens=["nope:converter.c1"],
                    strategy_class=_StubStrategy,
                    scenario_name="x",
                )

    async def test_start_run_forwards_strategy_converters(self, mock_all_registries) -> None:
        """A converter token is resolved and forwarded through the registry as ``strategy_converters``."""
        conv = MagicMock(spec=PromptConverter)
        scenario_instance = mock_all_registries["scenario_instance"]
        scenario_instance._strategy_class = _StubStrategy

        service = ScenarioRunService()
        with _patch_converter_registry({"translation_spanish": conv}):
            await service.start_run_async(request=_make_request(strategies=["role_play:converter.translation_spanish"]))

        init_call = mock_all_registries["scenario_registry"].create_and_initialize_async.await_args
        assert init_call.kwargs["scenario_strategies"] == [_StubStrategy.ROLE_PLAY]
        assert init_call.kwargs["strategy_converters"] == {"role_play": [conv]}
