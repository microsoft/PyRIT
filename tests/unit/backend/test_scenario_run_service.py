# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ScenarioRunService.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pyrit.backend.services.scenario_run_service as _svc_mod
from pyrit.backend.models.scenarios import (
    RunScenarioRequest,
    ScenarioRunStatus,
)
from pyrit.backend.services.scenario_run_service import (
    _DEFAULT_MAX_CONCURRENT_RUNS,
    ScenarioRunService,
)

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
) -> RunScenarioRequest:
    """Create a RunScenarioRequest for testing."""
    return RunScenarioRequest(
        scenario_name=scenario_name,
        target_name=target_name,
        initializers=initializers,
        strategies=strategies,
        scenario_result_id=scenario_result_id,
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
    sr.scenario_identifier.name = scenario_name
    sr.scenario_identifier.version = 1
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
    sr.error_attack_result_ids = []
    return sr


@pytest.fixture
def mock_memory():
    """Patch CentralMemory.get_memory_instance to return a mock."""
    mock = MagicMock()
    mock.get_scenario_results.return_value = []
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
    mock_scenario_class.get_strategy_class.return_value = MagicMock()
    mock_scenario_class.default_dataset_config.return_value = MagicMock()

    mock_sr = MagicMock()
    mock_sr.get_class.return_value = mock_scenario_class

    mock_tr = MagicMock()
    mock_tr.get_instance_by_name.return_value = MagicMock()
    mock_tr.get_names.return_value = ["my_target"]

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
        assert response.status == ScenarioRunStatus.IN_PROGRESS
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
        mock_tr.get_instance_by_name.return_value = None
        mock_tr.get_names.return_value = ["other_target"]

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
        scenario_instance = mock_all_registries["scenario_instance"]

        # Each call needs a unique scenario_result_id
        call_count = 0
        original_init = scenario_instance.initialize_async

        async def _set_unique_id(**kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            scenario_instance._scenario_result_id = f"sr-uuid-{call_count}"

        scenario_instance.initialize_async = AsyncMock(side_effect=_set_unique_id)

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

        assert response.status == ScenarioRunStatus.IN_PROGRESS
        assert mock_init_instance.initialize_async.await_count == 2

    async def test_start_run_passes_scenario_result_id_for_resume(self, mock_all_registries) -> None:
        """Test that scenario_result_id is passed to the scenario constructor for resumption."""
        service = ScenarioRunService()
        mock_scenario_class = mock_all_registries["scenario_class"]

        response = await service.start_run_async(request=_make_request(scenario_result_id="existing-result-uuid"))

        assert response.status == ScenarioRunStatus.IN_PROGRESS
        mock_scenario_class.assert_called_once_with(scenario_result_id="existing-result-uuid")

    async def test_start_run_omits_scenario_result_id_when_none(self, mock_all_registries) -> None:
        """Test that scenario_result_id is not passed to constructor when not provided."""
        service = ScenarioRunService()
        mock_scenario_class = mock_all_registries["scenario_class"]

        await service.start_run_async(request=_make_request())

        mock_scenario_class.assert_called_once_with()


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
        assert fetched.status == ScenarioRunStatus.IN_PROGRESS

    def test_get_run_falls_back_to_persisted_error(self, mock_memory) -> None:
        """Test that get_run extracts error from persisted error AttackResult when no active task."""
        db_result = _make_db_scenario_result(result_id="sr-fail", run_state="FAILED")
        db_result.error_attack_result_ids = ["err-ar-1"]

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
        mock_memory.get_attack_results.assert_called_once_with(attack_result_ids=["err-ar-1"])


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
        assert result.status == ScenarioRunStatus.CANCELLED

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
        """Test that get_run_results returns full details for a completed run."""
        from pyrit.models import AttackOutcome

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
        mock_attack_result.error_message = None
        mock_attack_result.error_type = None
        mock_attack_result.error_traceback = None
        mock_attack_result.total_retries = 0
        mock_attack_result.retry_events = []

        db_result = _make_db_scenario_result(
            result_id="sr-123",
            run_state="COMPLETED",
            attack_results={"base64_attack": [mock_attack_result]},
        )
        db_result.objective_achieved_rate.return_value = 100
        mock_memory.get_scenario_results.return_value = [db_result]

        service = ScenarioRunService()
        detail = service.get_run_results(scenario_result_id="sr-123")

        assert detail is not None
        assert detail.run.scenario_result_id == "sr-123"
        assert detail.run.objective_achieved_rate == 100
        assert len(detail.attacks) == 1
        assert detail.attacks[0].atomic_attack_name == "base64_attack"
        assert detail.attacks[0].success_count == 1
        assert detail.attacks[0].results[0].objective == "Extract info"
        assert detail.attacks[0].results[0].outcome == "success"


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
        assert fetched.status == ScenarioRunStatus.IN_PROGRESS
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
        assert fetched.status == ScenarioRunStatus.CREATED
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
        assert fetched.status == ScenarioRunStatus.COMPLETED
        assert fetched.total_attacks == 1
        assert fetched.completed_attacks == 1
        assert fetched.strategies_used == ["attack_a"]
        assert fetched.objective_achieved_rate == 100
