# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for transient scenario suite execution coordination."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import pyrit.backend.services.scenario_configuration_resolver as _configuration_mod
import pyrit.backend.services.scenario_suite_execution_service as _execution_mod
from pyrit.backend.services import (
    ScenarioRunService,
    ScenarioSuiteExecutionService,
)
from pyrit.backend.services.scenario_suite_execution_service import (
    ScenarioSuiteExecutionCapacityError,
)
from pyrit.models import (
    SCENARIO_SUITE_EXECUTION_METADATA_KEY,
    ComponentIdentifier,
    LaunchScenarioSuiteRequest,
    PresetEntry,
    PresetIssue,
    ScenarioPreset,
    ScenarioPresetLaunchDefaults,
    ScenarioRunState,
    ScenarioSuite,
    ScenarioSuiteEntry,
    ScenarioSuiteExecutionStatus,
    ScenarioSuiteLaunchEntrySnapshot,
    ScenarioSuiteSubmissionState,
)
from pyrit.models.catalog import (
    RunScenarioRequest,
    ScenarioRunSummary,
)
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.scenario_readiness import ScenarioConfigurationIssue, ScenarioReadiness, ScenarioRoleBinding
from pyrit.registry import (
    ScenarioPresetRegistry,
    ScenarioRegistry,
    ScenarioSuiteLaunchConflictError,
    ScenarioSuiteNotRunnableError,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
    ScorerRegistry,
)
from pyrit.scenario.scenarios.garak import ApiKey
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")
OPERATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_REAL_RESOLVE_READINESS = _configuration_mod.ScenarioConfigurationResolver.resolve_readiness


class _BuiltInScorerScenario(ApiKey):
    def __init__(self) -> None:
        super().__init__()


def _suite(*, name: str = "weekly") -> ScenarioSuite:
    """Build a two-entry suite with stable identities."""
    return ScenarioSuite(
        name=name,
        entries=[
            ScenarioSuiteEntry(entry_id=ENTRY_ONE, preset_name="first"),
            ScenarioSuiteEntry(entry_id=ENTRY_TWO, preset_name="second"),
        ],
    )


def _preset_entry(
    *,
    name: str,
    scenario_name: str,
    origin: Literal["stored", "registered"] = "stored",
    version: str | None = None,
) -> PresetEntry:
    """Build a registry entry for one runnable preset."""
    return PresetEntry(
        preset=ScenarioPreset(name=name, scenario_name=scenario_name),
        origin=origin,
        version=version,
    )


def _run_summary(
    *,
    run_id: str,
    scenario_name: str,
    state: ScenarioRunState = ScenarioRunState.QUEUED,
) -> ScenarioRunSummary:
    """Build a minimal child run summary."""
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return ScenarioRunSummary(
        scenario_result_id=run_id,
        scenario_name=scenario_name,
        status=state,
        created_at=now,
        updated_at=now,
    )


def _launch_request(
    *,
    suite_version: str | None,
    first_origin: str | None = "stored",
    first_version: str | None = "first-v1",
    second_origin: str | None = "stored",
    second_version: str | None = "second-v1",
) -> LaunchScenarioSuiteRequest:
    """Build the reviewed suite and preset snapshot sent by the UI."""
    return LaunchScenarioSuiteRequest(
        expected_version=suite_version,
        entries=[
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_ONE,
                preset_name="first",
                origin=first_origin,
                preset_version=first_version,
            ),
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_TWO,
                preset_name="second",
                origin=second_origin,
                preset_version=second_version,
            ),
        ],
        target_name="objective-target",
        adversarial_target_name="adversarial-target",
        max_concurrency=7,
        max_retries=3,
        operation_id=OPERATION_ID,
        labels={"team": "purple"},
    )


async def _wait_for_submissions(service: ScenarioSuiteExecutionService) -> None:
    """Wait for immediate mock submissions without sleeping wall-clock time."""
    for _ in range(100):
        if not service.has_active_work():
            return
        await asyncio.sleep(0)
    raise AssertionError("Suite submission coordinator did not finish")


@pytest.fixture
async def execution_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[
    tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
    None,
]:
    """Create isolated registries and a controllable run service."""
    monkeypatch.setattr(
        _execution_mod.ScenarioConfigurationResolver,
        "resolve_readiness",
        MagicMock(return_value=ScenarioReadiness()),
    )
    suite_registry = ScenarioSuiteRegistry(storage=ScenarioSuiteStorage(source=str(tmp_path)))
    preset_registry = MagicMock(spec=ScenarioPresetRegistry)
    presets = {
        "first": _preset_entry(
            name="first",
            scenario_name="scenario.first",
            version="first-v1",
        ),
        "second": _preset_entry(
            name="second",
            scenario_name="scenario.second",
            version="second-v1",
        ),
    }
    preset_registry.get_preset.side_effect = lambda *, name: presets.get(name)
    preset_registry.check_preset.return_value = []

    calls: list[tuple[RunScenarioRequest, dict[str, Any]]] = []
    runs: dict[str, ScenarioRunSummary] = {}

    async def start_run_async(
        *,
        request: RunScenarioRequest,
        initial_metadata: dict[str, Any],
    ) -> ScenarioRunSummary:
        calls.append((request, initial_metadata))
        run_id = f"run-{len(calls)}"
        summary = _run_summary(run_id=run_id, scenario_name=request.scenario_name)
        runs[run_id] = summary
        return summary

    async def get_run_async(*, scenario_result_id: str) -> ScenarioRunSummary | None:
        return runs.get(scenario_result_id)

    async def cancel_run_async(*, scenario_result_id: str) -> ScenarioRunSummary | None:
        current = runs.get(scenario_result_id)
        if current is None:
            return None
        cancelled = current.model_copy(update={"status": ScenarioRunState.CANCELLED})
        runs[scenario_result_id] = cancelled
        return cancelled

    run_service = MagicMock(spec=ScenarioRunService)
    run_service.start_run_async = AsyncMock(side_effect=start_run_async)
    run_service.get_run_async = AsyncMock(side_effect=get_run_async)
    run_service.cancel_run_async = AsyncMock(side_effect=cancel_run_async)
    scenario_registry = MagicMock(spec=ScenarioRegistry)
    scenario_registry.get_registered_class_metadata.return_value = None

    service = ScenarioSuiteExecutionService(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
        scenario_registry=scenario_registry,
        run_service=run_service,
    )
    try:
        yield service, suite_registry, preset_registry, run_service, calls, runs
    finally:
        await service.shutdown_async()


async def test_launch_queues_entries_in_order_and_stamps_versioned_metadata(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test server-owned identity, launch propagation, ordering, and child metadata."""
    service, suite_registry, _, _, calls, _ = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    bindings = [
        [
            ScenarioRoleBinding(
                role=role, component=f"child-{index}", identifier=ComponentIdentifier(class_name=f"{role}-{index}")
            )
            for role in (
                "objective_target",
                "objective_scorer",
                "scoring_target",
                "adversarial_target",
                "converter:flip:0",
            )
        ]
        for index in (1, 2)
    ]
    _execution_mod.ScenarioConfigurationResolver.resolve_readiness.side_effect = [
        ScenarioReadiness(bindings=child_bindings) for child_bindings in bindings
    ]

    accepted = await service.launch_async(
        name="weekly",
        request=_launch_request(suite_version=stored.version),
    )
    await _wait_for_submissions(service)
    summary = await service.get_execution_async(execution_id=accepted.suite_execution_id)

    assert summary is not None
    assert summary.status == ScenarioSuiteExecutionStatus.IN_PROGRESS
    assert [entry.preset_name for entry in summary.entries] == ["first", "second"]
    assert [entry.entry_position for entry in summary.entries] == [1, 2]
    assert [request.scenario_name for request, _ in calls] == [
        "scenario.first",
        "scenario.second",
    ]
    for request, _ in calls:
        assert request.target_name == "objective-target"
        assert request.adversarial_target_name == "adversarial-target"
        assert request.max_concurrency == 7
        assert request.max_retries == 3
        assert request.operation_id == OPERATION_ID
        assert request.labels == {"team": "purple"}
    for (_, metadata), child_bindings in zip(calls, bindings, strict=True):
        assert metadata["scenario_expected_role_references_v1"] == {
            f"{binding.role}_hash": binding.identifier.hash for binding in child_bindings
        }

    first_metadata = calls[0][1][SCENARIO_SUITE_EXECUTION_METADATA_KEY]
    second_metadata = calls[1][1][SCENARIO_SUITE_EXECUTION_METADATA_KEY]
    assert first_metadata == {
        "version": 1,
        "suite_execution_id": str(accepted.suite_execution_id),
        "suite_name": "weekly",
        "suite_version": stored.version,
        "suite_entry_id": str(ENTRY_ONE),
        "entry_position": 1,
        "preset_name": "first",
        "preset_version": "first-v1",
    }
    assert second_metadata["suite_execution_id"] == str(accepted.suite_execution_id)
    assert second_metadata["suite_entry_id"] == str(ENTRY_TWO)
    assert second_metadata["entry_position"] == 2


async def test_invalid_later_child_rejects_all_admission_async(execution_context) -> None:
    service, suite_registry, _, run_service, calls, _ = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    _execution_mod.ScenarioConfigurationResolver.resolve_readiness.side_effect = [
        ScenarioReadiness(),
        ScenarioReadiness(
            issues=[
                ScenarioConfigurationIssue(
                    role="scoring_target",
                    component="scenario.second",
                    reason="No scoring model.",
                    remediation="Select a scoring target.",
                )
            ]
        ),
    ]
    with pytest.raises(ScenarioSuiteNotRunnableError, match="second.*scoring_target"):
        await service.launch_async(name="weekly", request=_launch_request(suite_version=stored.version))
    run_service.start_run_async.assert_not_awaited()
    assert calls == []
    assert service._executions == {}


async def test_registered_suite_and_presets_launch_with_nullable_versions(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test code-defined definitions use the same launch path with null versions."""
    service, suite_registry, preset_registry, _, calls, _ = execution_context
    suite_registry.register_suite(suite=_suite())
    preset_registry.get_preset.side_effect = lambda *, name: _preset_entry(
        name=name,
        scenario_name=f"scenario.{name}",
        origin="registered",
        version=None,
    )

    accepted = await service.launch_async(
        name="weekly",
        request=_launch_request(
            suite_version=None,
            first_origin="registered",
            first_version=None,
            second_origin="registered",
            second_version=None,
        ),
    )
    await _wait_for_submissions(service)

    summary = await service.get_execution_async(execution_id=accepted.suite_execution_id)
    assert summary is not None
    assert summary.suite_origin == "registered"
    assert summary.suite_version is None
    assert [entry.preset_version for entry in summary.entries] == [None, None]
    assert calls[0][1][SCENARIO_SUITE_EXECUTION_METADATA_KEY]["suite_version"] is None
    assert calls[0][1][SCENARIO_SUITE_EXECUTION_METADATA_KEY]["preset_version"] is None


async def test_stored_suite_requires_the_reviewed_version(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test a stored suite cannot silently launch without optimistic concurrency."""
    service, suite_registry, _, run_service, _, _ = execution_context
    suite_registry.save_suite(suite=_suite(), expected_version=None)

    with pytest.raises(ScenarioSuiteLaunchConflictError, match="no reviewed version"):
        await service.launch_async(name="weekly", request=_launch_request(suite_version=None))

    run_service.start_run_async.assert_not_awaited()


async def test_changed_preset_snapshot_rejects_the_whole_launch_before_submission(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test referenced preset drift is a hard preflight conflict."""
    service, suite_registry, _, run_service, _, _ = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)

    with pytest.raises(ScenarioSuiteLaunchConflictError, match="first"):
        await service.launch_async(
            name="weekly",
            request=_launch_request(suite_version=stored.version, first_version="stale"),
        )

    run_service.start_run_async.assert_not_awaited()


async def test_advisory_preset_issue_becomes_a_hard_launch_gate(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test suite editing remains permissive while execution rejects invalid entries."""
    service, suite_registry, preset_registry, run_service, _, _ = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    preset_registry.check_preset.side_effect = lambda *, preset: (
        [PresetIssue(field="scenario_name", message="Scenario is unavailable.")] if preset.name == "second" else []
    )

    with pytest.raises(ScenarioSuiteNotRunnableError, match="Entry 2"):
        await service.launch_async(
            name="weekly",
            request=_launch_request(suite_version=stored.version),
        )

    run_service.start_run_async.assert_not_awaited()


async def test_submission_failure_does_not_prevent_later_entries_from_queueing(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test accepted entries retain independent ordinary-run failure semantics."""
    service, suite_registry, _, run_service, calls, runs = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)

    async def start_run_async(
        *,
        request: RunScenarioRequest,
        initial_metadata: dict[str, Any],
    ) -> ScenarioRunSummary:
        calls.append((request, initial_metadata))
        if len(calls) == 1:
            raise ValueError(
                "(sqlite3.OperationalError) database is locked "
                "[SQL: INSERT INTO ScenarioResultEntries ...] "
                "[parameters: ('secret-token',)]"
            )
        summary = _run_summary(
            run_id="run-2",
            scenario_name=request.scenario_name,
            state=ScenarioRunState.COMPLETED,
        )
        runs[summary.scenario_result_id] = summary
        return summary

    run_service.start_run_async.side_effect = start_run_async

    accepted = await service.launch_async(
        name="weekly",
        request=_launch_request(suite_version=stored.version),
    )
    await _wait_for_submissions(service)
    summary = await service.get_execution_async(execution_id=accepted.suite_execution_id)

    assert summary is not None
    assert summary.status == ScenarioSuiteExecutionStatus.FAILED
    assert [entry.state for entry in summary.entries] == [
        ScenarioSuiteSubmissionState.SUBMISSION_FAILED,
        ScenarioRunState.COMPLETED,
    ]
    assert summary.entries[0].error == service.SUBMISSION_FAILURE_MESSAGE
    assert summary.entries[0].error_type == "ValueError"
    assert "database is locked" not in summary.model_dump_json()
    assert "secret-token" not in summary.model_dump_json()
    assert summary.entries[1].run is not None
    assert len(calls) == 2


async def test_child_status_failure_is_nonterminal_and_recovers(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test a temporary child status error stays nonterminal and clears after recovery."""
    service, suite_registry, _, run_service, _, runs = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)

    accepted = await service.launch_async(
        name="weekly",
        request=_launch_request(suite_version=stored.version),
    )
    await _wait_for_submissions(service)

    failed_once = False

    async def get_run_async(*, scenario_result_id: str) -> ScenarioRunSummary | None:
        nonlocal failed_once
        if scenario_result_id == "run-1" and not failed_once:
            failed_once = True
            raise RuntimeError("SELECT secret_column FROM private_table")
        return runs.get(scenario_result_id)

    runs["run-2"] = runs["run-2"].model_copy(update={"status": ScenarioRunState.COMPLETED})
    run_service.get_run_async.side_effect = get_run_async
    unavailable = await service.get_execution_async(execution_id=accepted.suite_execution_id)
    recovered = await service.get_execution_async(execution_id=accepted.suite_execution_id)

    assert unavailable is not None
    assert unavailable.status == ScenarioSuiteExecutionStatus.IN_PROGRESS
    assert unavailable.entries[0].state == ScenarioRunState.QUEUED
    assert unavailable.entries[0].error == service.CHILD_STATUS_FAILURE_MESSAGE
    assert unavailable.entries[0].error_type == "RuntimeError"
    assert "secret_column" not in unavailable.model_dump_json()
    assert unavailable.entries[1].run is not None
    assert recovered is not None
    assert recovered.status == ScenarioSuiteExecutionStatus.IN_PROGRESS
    assert recovered.entries[0].run is not None
    assert recovered.entries[0].error is None
    assert recovered.entries[0].error_type is None


async def test_child_status_failure_does_not_suppress_cancellation(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test cancellation remains available while a child status read is failing."""
    service, suite_registry, _, run_service, _, runs = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)

    accepted = await service.launch_async(
        name="weekly",
        request=_launch_request(suite_version=stored.version),
    )
    await _wait_for_submissions(service)

    fail_reads = True

    async def get_run_async(*, scenario_result_id: str) -> ScenarioRunSummary | None:
        if scenario_result_id == "run-1" and fail_reads:
            raise TimeoutError("temporary read timeout")
        return runs.get(scenario_result_id)

    run_service.get_run_async.side_effect = get_run_async
    cancelling = await service.cancel_execution_async(execution_id=accepted.suite_execution_id)

    assert cancelling is not None
    assert cancelling.status == ScenarioSuiteExecutionStatus.CANCELLED
    assert run_service.cancel_run_async.await_count == 2

    fail_reads = False
    recovered = await service.get_execution_async(execution_id=accepted.suite_execution_id)

    assert recovered is not None
    assert recovered.status == ScenarioSuiteExecutionStatus.CANCELLED
    assert [entry.state for entry in recovered.entries] == [
        ScenarioRunState.CANCELLED,
        ScenarioRunState.CANCELLED,
    ]


async def test_cancel_recovers_and_cancels_child_admitted_before_response(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test cancellation retains an admitted child ID until the response handoff completes."""
    service, suite_registry, _, run_service, calls, runs = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    admitted = asyncio.Event()
    release_response = asyncio.Event()

    async def start_run_async(
        *,
        request: RunScenarioRequest,
        initial_metadata: dict[str, Any],
    ) -> ScenarioRunSummary:
        calls.append((request, initial_metadata))
        summary = _run_summary(
            run_id=f"run-{len(calls)}",
            scenario_name=request.scenario_name,
        )
        runs[summary.scenario_result_id] = summary
        admitted.set()
        await release_response.wait()
        return summary

    run_service.start_run_async.side_effect = start_run_async

    summary = None
    try:
        accepted = await service.launch_async(
            name="weekly",
            request=_launch_request(suite_version=stored.version),
        )
        await asyncio.wait_for(admitted.wait(), timeout=5)

        cancelling = await service.cancel_execution_async(execution_id=accepted.suite_execution_id)

        assert cancelling is not None
        assert cancelling.status == ScenarioSuiteExecutionStatus.QUEUING
        assert [entry.state for entry in cancelling.entries] == [
            ScenarioSuiteSubmissionState.SUBMITTING,
            ScenarioRunState.CANCELLED,
        ]
        run_service.cancel_run_async.assert_not_awaited()

        release_response.set()
        await _wait_for_submissions(service)
        summary = await service.get_execution_async(execution_id=accepted.suite_execution_id)
    finally:
        release_response.set()

    assert summary is not None
    assert summary.status == ScenarioSuiteExecutionStatus.CANCELLED
    assert [entry.state for entry in summary.entries] == [
        ScenarioRunState.CANCELLED,
        ScenarioRunState.CANCELLED,
    ]
    run_service.cancel_run_async.assert_awaited_once_with(scenario_result_id="run-1")
    assert len(calls) == 1


def test_execution_status_surfaces_failures_behind_cancellations() -> None:
    """A failed entry outranks cancelled entries in the aggregate."""
    entries = [
        MagicMock(state=ScenarioRunState.FAILED),
        MagicMock(state=ScenarioRunState.CANCELLED),
    ]
    assert ScenarioSuiteExecutionService._execution_status(entries=entries) == ScenarioSuiteExecutionStatus.FAILED
    # Pure cancellations keep reporting CANCELLED.
    assert (
        ScenarioSuiteExecutionService._execution_status(entries=[MagicMock(state=ScenarioRunState.CANCELLED)])
        == ScenarioSuiteExecutionStatus.CANCELLED
    )


async def test_retention_rejects_active_capacity_and_evicts_terminal_execution(
    execution_context: tuple[
        ScenarioSuiteExecutionService,
        ScenarioSuiteRegistry,
        MagicMock,
        MagicMock,
        list[tuple[RunScenarioRequest, dict[str, Any]]],
        dict[str, ScenarioRunSummary],
    ],
) -> None:
    """Test the actual retention boundary never evicts queued or running children."""
    service, suite_registry, _, _, _, runs = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    request = _launch_request(suite_version=stored.version)

    accepted = [
        await service.launch_async(name="weekly", request=request)
        for _ in range(_execution_mod._MAX_RETAINED_EXECUTIONS)
    ]
    await _wait_for_submissions(service)

    with pytest.raises(ScenarioSuiteExecutionCapacityError, match="still active"):
        await service.launch_async(name="weekly", request=request)

    first = await service.get_execution_async(execution_id=accepted[0].suite_execution_id)
    assert first is not None
    assert first.status == ScenarioSuiteExecutionStatus.IN_PROGRESS

    for entry in first.entries:
        assert entry.run is not None
        runs[entry.run.scenario_result_id] = entry.run.model_copy(update={"status": ScenarioRunState.COMPLETED})

    replacement = await service.launch_async(name="weekly", request=request)

    assert await service.get_execution_async(execution_id=accepted[0].suite_execution_id) is None
    assert await service.get_execution_async(execution_id=replacement.suite_execution_id) is not None


REFUSE_SCORER = RegisteredComponentReference(registry_name="inverted_refusal", identifier_hash="a" * 64)
OVERRIDE_SCORER = RegisteredComponentReference(registry_name="override", identifier_hash="c" * 64)


def _pin_first_preset_scorer(preset_registry: MagicMock) -> None:
    """Make the first preset pin a scorer while the second keeps the scenario default."""
    presets = {
        "first": PresetEntry(
            preset=ScenarioPreset(
                name="first",
                scenario_name="scenario.first",
                launch_defaults=ScenarioPresetLaunchDefaults(objective_scorer=REFUSE_SCORER),
            ),
            origin="stored",
            version="first-v1",
        ),
        "second": _preset_entry(name="second", scenario_name="scenario.second", version="second-v1"),
    }
    preset_registry.get_preset.side_effect = lambda *, name: presets.get(name)


def _scorer_issue(*, component: str) -> ScenarioConfigurationIssue:
    return ScenarioConfigurationIssue(
        role="objective_scorer",
        component=component,
        reason="Objective scorer 'inverted_refusal' has changed.",
        remediation="Select the objective scorer again.",
    )


async def test_children_launch_with_their_own_preset_scorer_async(execution_context) -> None:
    service, suite_registry, preset_registry, _, calls, _ = execution_context
    _pin_first_preset_scorer(preset_registry)
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)

    await service.launch_async(name="weekly", request=_launch_request(suite_version=stored.version))
    await _wait_for_submissions(service)

    assert [request.objective_scorer for request, _ in calls] == [REFUSE_SCORER, None]
    readiness_requests = [
        call.kwargs["request"] for call in _execution_mod.ScenarioConfigurationResolver.resolve_readiness.call_args_list
    ]
    assert [request.objective_scorer for request in readiness_requests] == [REFUSE_SCORER, None]


async def test_unresolved_preset_scorer_is_attributed_to_its_entry_and_preset_async(execution_context) -> None:
    service, suite_registry, preset_registry, run_service, _, _ = execution_context
    _pin_first_preset_scorer(preset_registry)
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    _execution_mod.ScenarioConfigurationResolver.resolve_readiness.side_effect = [
        ScenarioReadiness(issues=[_scorer_issue(component="scenario.first")]),
        ScenarioReadiness(),
    ]

    readiness = await service.get_readiness_async(name="weekly", request=_launch_request(suite_version=stored.version))

    [issue] = readiness.issues
    assert issue.entry_id == ENTRY_ONE
    assert issue.source == "preset"
    assert "launch_defaults.objective_scorer" in issue.remediation
    assert "'first'" in issue.remediation
    run_service.start_run_async.assert_not_awaited()


async def test_unresolved_preset_scorer_blocks_the_whole_launch_async(execution_context) -> None:
    service, suite_registry, preset_registry, run_service, _, _ = execution_context
    _pin_first_preset_scorer(preset_registry)
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    _execution_mod.ScenarioConfigurationResolver.resolve_readiness.side_effect = [
        ScenarioReadiness(issues=[_scorer_issue(component="scenario.first")]),
        ScenarioReadiness(),
    ]

    with pytest.raises(ScenarioSuiteNotRunnableError, match="Entry 1 \\(first\\).*objective_scorer"):
        await service.launch_async(name="weekly", request=_launch_request(suite_version=stored.version))
    run_service.start_run_async.assert_not_awaited()


async def test_launch_scorer_override_replaces_preset_scorers_async(execution_context) -> None:
    service, suite_registry, preset_registry, _, calls, _ = execution_context
    _pin_first_preset_scorer(preset_registry)
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    _execution_mod.ScenarioConfigurationResolver.resolve_readiness.side_effect = [
        ScenarioReadiness(
            bindings=[
                ScenarioRoleBinding(
                    role="objective_scorer",
                    component=f"child-{index}",
                    identifier=ComponentIdentifier(class_name="OverrideScorer"),
                )
            ]
        )
        for index in (1, 2, 1, 2)
    ]
    request = _launch_request(suite_version=stored.version).model_copy(update={"objective_scorer": OVERRIDE_SCORER})

    readiness = await service.get_readiness_async(name="weekly", request=request)
    await service.launch_async(name="weekly", request=request)
    await _wait_for_submissions(service)

    assert [binding.source for binding in readiness.bindings] == ["launch", "launch"]
    assert [binding.entry_id for binding in readiness.bindings] == [ENTRY_ONE, ENTRY_TWO]
    assert [request.objective_scorer for request, _ in calls] == [OVERRIDE_SCORER, OVERRIDE_SCORER]


@pytest.mark.usefixtures("patch_central_database")
async def test_launch_scorer_skips_entries_whose_scenario_uses_a_built_in_scorer_async(execution_context) -> None:
    _, suite_registry, preset_registry, run_service, calls, _ = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    scenario_registry = MagicMock(spec=ScenarioRegistry)
    scenario_registry.get_registered_class_metadata.side_effect = lambda name: MagicMock(
        supports_objective_scorer=name != "scenario.second"
    )
    service = ScenarioSuiteExecutionService(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
        scenario_registry=scenario_registry,
        run_service=run_service,
    )
    scorer = SubStringScorer(substring="expected")
    scorer_registry = ScorerRegistry()
    scorer_registry.instances.register(scorer, name="override")
    override = RegisteredComponentReference(registry_name="override", identifier_hash=scorer.get_identifier().hash)
    scenario_classes = {"scenario.first": ApiKey, "scenario.second": _BuiltInScorerScenario}
    configured_requests: list[RunScenarioRequest] = []

    def create_configured_scenario(*, scenario_name: str, request: RunScenarioRequest) -> MagicMock:
        configured_requests.append(request)
        scenario = MagicMock()
        scenario.get_readiness.return_value = ScenarioReadiness(
            bindings=[
                ScenarioRoleBinding(
                    role="objective_scorer",
                    component=scenario_name,
                    identifier=ComponentIdentifier(class_name="SubStringScorer"),
                )
            ]
        )
        return scenario

    request = _launch_request(suite_version=stored.version).model_copy(update={"objective_scorer": override})
    resolver = _execution_mod.ScenarioConfigurationResolver
    try:
        with (
            patch.object(resolver, "resolve_readiness", _REAL_RESOLVE_READINESS),
            patch.object(resolver, "resolve_target", return_value=MockPromptTarget()),
            patch.object(
                resolver, "resolve_scenario_class", side_effect=lambda *, scenario_name: scenario_classes[scenario_name]
            ),
            patch.object(resolver, "create_configured_scenario", side_effect=create_configured_scenario),
            patch.object(_configuration_mod, "validate_default_adversarial_target"),
            patch.object(ScorerRegistry, "get_registry_singleton", return_value=scorer_registry),
        ):
            readiness = await service.get_readiness_async(name="weekly", request=request)
            await service.launch_async(name="weekly", request=request)
            await _wait_for_submissions(service)
    finally:
        await service.shutdown_async()

    assert readiness.issues == []
    assert [(binding.entry_id, binding.source) for binding in readiness.bindings] == [
        (ENTRY_ONE, "launch"),
        (ENTRY_TWO, "unsupported"),
    ]
    assert [configured.objective_scorer for configured in configured_requests] == [override, None, override, None]
    assert [child.objective_scorer for child, _ in calls] == [override, None]


async def test_scenario_default_scorer_issue_keeps_the_generic_remediation_async(execution_context) -> None:
    service, suite_registry, _, _, _, _ = execution_context
    stored = suite_registry.save_suite(suite=_suite(), expected_version=None)
    _execution_mod.ScenarioConfigurationResolver.resolve_readiness.side_effect = [
        ScenarioReadiness(issues=[_scorer_issue(component="scenario.first")]),
        ScenarioReadiness(),
    ]

    readiness = await service.get_readiness_async(name="weekly", request=_launch_request(suite_version=stored.version))

    [issue] = readiness.issues
    assert issue.entry_id == ENTRY_ONE
    assert issue.source == "scenario_default"
    assert issue.remediation == "Select the objective scorer again."
