# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ScenarioRunService.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.scenarios import (
    RunScenarioRequest,
    ScenarioRunStatus,
)
from pyrit.backend.services.scenario_run_service import (
    MAX_CONCURRENT_RUNS,
    ScenarioRunService,
    get_scenario_run_service,
)

# The service uses deferred imports inside methods, so we patch at the source module.
_REGISTRY_PATCH_BASE = "pyrit.registry"


@pytest.fixture(autouse=True)
def clear_service_cache():
    """Clear the singleton cache between tests."""
    get_scenario_run_service.cache_clear()
    yield
    get_scenario_run_service.cache_clear()


def _make_request(
    *,
    scenario_name: str = "foundry.red_team_agent",
    target_name: str = "my_target",
    initializers: list[str] | None = None,
    strategies: list[str] | None = None,
    scenario_result_id: str | None = None,
) -> RunScenarioRequest:
    """Create a RunScenarioRequest for testing."""
    return RunScenarioRequest(
        scenario_name=scenario_name,
        target_name=target_name,
        initializers=initializers,
        strategies=strategies,
        scenario_result_id=scenario_result_id,
    )


@pytest.fixture
def mock_scenario_registry():
    """Patch ScenarioRegistry.get_registry_singleton to return a mock."""
    mock_registry = MagicMock()
    mock_registry.get_class.return_value = MagicMock()
    with patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_registry):
        yield mock_registry


@pytest.fixture
def mock_target_registry():
    """Patch TargetRegistry.get_registry_singleton to return a mock."""
    mock_registry = MagicMock()
    mock_registry.get_instance_by_name.return_value = MagicMock()
    mock_registry.get_names.return_value = ["my_target"]
    with patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_registry):
        yield mock_registry


@pytest.fixture
def mock_initializer_registry():
    """Patch InitializerRegistry.get_registry_singleton to return a mock."""
    mock_instance = MagicMock()
    mock_instance.initialize_async = AsyncMock()
    mock_class = MagicMock(return_value=mock_instance)

    mock_registry = MagicMock()
    mock_registry.get_class.return_value = mock_class
    with patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton", return_value=mock_registry):
        yield mock_registry, mock_class, mock_instance


@pytest.fixture
def mock_all_registries():
    """Patch all registries with valid defaults for start_run_async tests."""
    mock_scenario_instance = MagicMock()
    mock_scenario_instance.initialize_async = AsyncMock()
    mock_scenario_instance.run_async = AsyncMock()

    mock_scenario_class = MagicMock(return_value=mock_scenario_instance)
    mock_scenario_class.get_strategy_class.return_value = MagicMock()
    mock_scenario_class.default_dataset_config.return_value = MagicMock()

    mock_sr = MagicMock()
    mock_sr.get_class.return_value = mock_scenario_class

    mock_tr = MagicMock()
    mock_tr.get_instance_by_name.return_value = MagicMock()
    mock_tr.get_names.return_value = ["my_target"]

    mock_ir = MagicMock()
    mock_ir.get_class.return_value = MagicMock(return_value=MagicMock(initialize_async=AsyncMock()))

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
        }


class TestScenarioRunServiceStartRun:
    """Tests for ScenarioRunService.start_run_async."""

    async def test_start_run_returns_running_status(self, mock_all_registries) -> None:
        """Test that starting a run returns RUNNING status with a run_id."""
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        assert response.run_id is not None
        assert response.status == ScenarioRunStatus.RUNNING
        assert response.scenario_name == "foundry.red_team_agent"
        assert response.error is None
        assert response.result is None

    async def test_start_run_invalid_scenario_raises_value_error(self) -> None:
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

    async def test_start_run_invalid_target_raises_value_error(self) -> None:
        """Test that an invalid target name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_sr = MagicMock()
        mock_sr.get_class.return_value = MagicMock()

        mock_tr = MagicMock()
        mock_tr.get_instance_by_name.return_value = None
        mock_tr.get_names.return_value = ["other_target"]

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            with pytest.raises(ValueError, match="my_target.*not found in registry"):
                await service.start_run_async(request=_make_request())

    async def test_start_run_invalid_initializer_raises_value_error(self) -> None:
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

    async def test_start_run_invalid_strategy_raises_value_error(self) -> None:
        """Test that an invalid strategy name raises ValueError immediately."""
        service = ScenarioRunService()

        mock_strategy_class = MagicMock(side_effect=ValueError("not a valid strategy"))
        mock_strategy_class.__iter__ = MagicMock(return_value=iter([MagicMock(value="valid_strat")]))

        mock_scenario_class = MagicMock()
        mock_scenario_class.get_strategy_class.return_value = mock_strategy_class

        mock_sr = MagicMock()
        mock_sr.get_class.return_value = mock_scenario_class

        mock_tr = MagicMock()
        mock_tr.get_instance_by_name.return_value = MagicMock()

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton", return_value=mock_sr),
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton", return_value=mock_tr),
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            with pytest.raises(ValueError, match="Strategy.*not found for scenario"):
                await service.start_run_async(request=_make_request(strategies=["bad_strategy"]))

    async def test_start_run_exceeds_concurrent_limit(self, mock_all_registries) -> None:
        """Test that exceeding concurrent run limit raises ValueError."""
        service = ScenarioRunService()

        # Fill up to the limit
        for _ in range(MAX_CONCURRENT_RUNS):
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

        assert response.status == ScenarioRunStatus.RUNNING
        assert mock_init_instance.initialize_async.await_count == 2

    async def test_start_run_passes_scenario_result_id_for_resume(self, mock_all_registries) -> None:
        """Test that scenario_result_id is passed to the scenario constructor for resumption."""
        service = ScenarioRunService()
        mock_scenario_class = mock_all_registries["scenario_class"]

        response = await service.start_run_async(
            request=_make_request(scenario_result_id="existing-result-uuid")
        )

        assert response.status == ScenarioRunStatus.RUNNING
        mock_scenario_class.assert_called_once_with(scenario_result_id="existing-result-uuid")

    async def test_start_run_omits_scenario_result_id_when_none(self, mock_all_registries) -> None:
        """Test that scenario_result_id is not passed to constructor when not provided."""
        service = ScenarioRunService()
        mock_scenario_class = mock_all_registries["scenario_class"]

        await service.start_run_async(request=_make_request())

        mock_scenario_class.assert_called_once_with()


class TestScenarioRunServiceGetRun:
    """Tests for ScenarioRunService.get_run."""

    async def test_get_run_returns_none_for_unknown_id(self) -> None:
        """Test that get_run returns None for non-existent run_id."""
        service = ScenarioRunService()
        result = service.get_run(run_id="nonexistent-id")
        assert result is None

    async def test_get_run_returns_existing_run(self, mock_all_registries) -> None:
        """Test that get_run returns a started run."""
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        fetched = service.get_run(run_id=response.run_id)
        assert fetched is not None
        assert fetched.run_id == response.run_id
        assert fetched.scenario_name == "foundry.red_team_agent"


class TestScenarioRunServiceListRuns:
    """Tests for ScenarioRunService.list_runs."""

    async def test_list_runs_empty(self) -> None:
        """Test that list_runs returns empty list initially."""
        service = ScenarioRunService()
        result = service.list_runs()
        assert result.items == []

    async def test_list_runs_returns_all_runs(self, mock_all_registries) -> None:
        """Test that list_runs returns all tracked runs."""
        service = ScenarioRunService()

        await service.start_run_async(request=_make_request())
        await service.start_run_async(request=_make_request())

        result = service.list_runs()
        assert len(result.items) == 2


class TestScenarioRunServiceCancelRun:
    """Tests for ScenarioRunService.cancel_run_async."""

    async def test_cancel_run_returns_none_for_unknown_id(self) -> None:
        """Test that cancel returns None for non-existent run_id."""
        service = ScenarioRunService()
        result = await service.cancel_run_async(run_id="nonexistent-id")
        assert result is None

    async def test_cancel_run_sets_cancelled_status(self, mock_all_registries) -> None:
        """Test that cancelling a running scenario sets CANCELLED status."""
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        result = await service.cancel_run_async(run_id=response.run_id)
        assert result is not None
        assert result.status == ScenarioRunStatus.CANCELLED

    async def test_cancel_completed_run_raises_value_error(self, mock_all_registries) -> None:
        """Test that cancelling a completed run raises ValueError."""
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        # Manually set to COMPLETED
        service._runs[response.run_id].status = ScenarioRunStatus.COMPLETED

        with pytest.raises(ValueError, match="Cannot cancel run"):
            await service.cancel_run_async(run_id=response.run_id)


class TestScenarioRunServiceExecution:
    """Tests for the background execution logic."""

    async def test_execute_run_completes_successfully(self) -> None:
        """Test that a successful execution transitions to COMPLETED."""
        service = ScenarioRunService()

        mock_scenario_result = MagicMock()
        mock_scenario_result.id = "result-uuid"
        mock_scenario_result.scenario_run_state = "COMPLETED"
        mock_scenario_result.get_strategies_used.return_value = ["base64"]
        mock_scenario_result.attack_results = {"attack1": []}
        mock_scenario_result.number_tries = 1
        mock_scenario_result.completion_time = None

        mock_scenario_instance = MagicMock()
        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_scenario_result)

        mock_scenario_class = MagicMock(return_value=mock_scenario_instance)
        mock_scenario_class.get_strategy_class.return_value = MagicMock()
        mock_scenario_class.default_dataset_config.return_value = MagicMock()

        mock_target = MagicMock()

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton") as mock_sr,
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton") as mock_tr,
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            mock_sr.return_value.get_class.return_value = mock_scenario_class
            mock_tr.return_value.get_instance_by_name.return_value = mock_target

            response = await service.start_run_async(request=_make_request())

            # Wait for the background task to complete
            task = service._runs[response.run_id].task
            assert task is not None
            await task

        run = service.get_run(run_id=response.run_id)
        assert run is not None
        assert run.status == ScenarioRunStatus.COMPLETED
        assert run.result is not None
        assert run.result.scenario_result_id == "result-uuid"
        assert run.result.strategies_used == ["base64"]

    async def test_execute_run_fails_with_error(self) -> None:
        """Test that a run_async failure transitions to FAILED with error message."""
        service = ScenarioRunService()

        mock_scenario_instance = MagicMock()
        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(side_effect=RuntimeError("scenario exploded"))

        mock_scenario_class = MagicMock(return_value=mock_scenario_instance)
        mock_scenario_class.get_strategy_class.return_value = MagicMock()
        mock_scenario_class.default_dataset_config.return_value = MagicMock()

        with (
            patch(f"{_REGISTRY_PATCH_BASE}.ScenarioRegistry.get_registry_singleton") as mock_sr,
            patch(f"{_REGISTRY_PATCH_BASE}.TargetRegistry.get_registry_singleton") as mock_tr,
            patch(f"{_REGISTRY_PATCH_BASE}.InitializerRegistry.get_registry_singleton"),
        ):
            mock_sr.return_value.get_class.return_value = mock_scenario_class
            mock_tr.return_value.get_instance_by_name.return_value = MagicMock()

            response = await service.start_run_async(request=_make_request())

            # Wait for the background task
            task = service._runs[response.run_id].task
            assert task is not None
            await task

        run = service.get_run(run_id=response.run_id)
        assert run is not None
        assert run.status == ScenarioRunStatus.FAILED
        assert run.error is not None
        assert "scenario exploded" in run.error


class TestScenarioRunServiceGetResults:
    """Tests for ScenarioRunService.get_run_results."""

    def test_get_results_returns_none_for_unknown_id(self) -> None:
        """Test that get_run_results returns None for non-existent run_id."""
        service = ScenarioRunService()
        result = service.get_run_results(run_id="nonexistent-id")
        assert result is None

    async def test_get_results_raises_if_not_completed(self, mock_all_registries) -> None:
        """Test that get_run_results raises ValueError if run is not completed."""
        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        # Run is in RUNNING state
        with pytest.raises(ValueError, match="only available for completed runs"):
            service.get_run_results(run_id=response.run_id)

    async def test_get_results_returns_details_for_completed_run(self, mock_all_registries) -> None:
        """Test that get_run_results returns full details for a completed run."""
        from pyrit.backend.models.scenarios import ScenarioRunResult
        from pyrit.models import AttackOutcome

        service = ScenarioRunService()
        response = await service.start_run_async(request=_make_request())

        # Manually set run to completed with a result
        info = service._runs[response.run_id]
        info.status = ScenarioRunStatus.COMPLETED
        info.result = ScenarioRunResult(
            scenario_result_id="sr-123",
            run_state="COMPLETED",
            strategies_used=["base64"],
            total_attacks=1,
            completed_attacks=1,
            number_tries=1,
            completion_time=None,
        )

        # Mock CentralMemory and ScenarioResult
        mock_attack_result = MagicMock()
        mock_attack_result.attack_result_id = "ar-1"
        mock_attack_result.conversation_id = "conv-1"
        mock_attack_result.objective = "Extract info"
        mock_attack_result.outcome = AttackOutcome.SUCCESS
        mock_attack_result.outcome_reason = "Model complied"
        mock_attack_result.last_response = MagicMock(value="Here is the data")
        mock_attack_result.last_score = MagicMock()
        mock_attack_result.last_score.get_value.return_value = "1.0"
        mock_attack_result.executed_turns = 3
        mock_attack_result.execution_time_ms = 1500
        mock_attack_result.timestamp = None

        mock_scenario_result = MagicMock()
        mock_scenario_result.id = "sr-123"
        mock_scenario_result.scenario_identifier.name = "foundry.red_team_agent"
        mock_scenario_result.scenario_identifier.version = 1
        mock_scenario_result.scenario_run_state = "COMPLETED"
        mock_scenario_result.objective_achieved_rate.return_value = 100
        mock_scenario_result.number_tries = 1
        mock_scenario_result.completion_time = None
        mock_scenario_result.labels = {}
        mock_scenario_result.attack_results = {"base64_attack": [mock_attack_result]}
        mock_scenario_result.get_display_groups.return_value = {"base64_attack": [mock_attack_result]}
        mock_scenario_result._display_group_map = {}

        mock_memory = MagicMock()
        mock_memory.get_scenario_results.return_value = [mock_scenario_result]

        with patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            detail = service.get_run_results(run_id=response.run_id)

        assert detail is not None
        assert detail.scenario_result_id == "sr-123"
        assert detail.objective_achieved_rate == 100
        assert len(detail.attacks) == 1
        assert detail.attacks[0].atomic_attack_name == "base64_attack"
        assert detail.attacks[0].success_count == 1
        assert detail.attacks[0].results[0].objective == "Extract info"
        assert detail.attacks[0].results[0].outcome == "success"
