# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Transient coordinator for launching registry-resolved scenario suites."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from pyrit.backend.models.scenario_presets import ResolveScenarioPresetRequest
from pyrit.backend.models.scenario_suites import (
    LaunchScenarioSuiteRequest,
    ScenarioSuiteExecutionEntryState,
    ScenarioSuiteExecutionEntrySummary,
    ScenarioSuiteExecutionStatus,
    ScenarioSuiteExecutionSummary,
)
from pyrit.backend.services.scenario_preset_service import ScenarioPresetService
from pyrit.backend.services.scenario_run_service import ScenarioRunService, get_scenario_run_service
from pyrit.models import ScenarioSuiteExecutionMetadata
from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteRegistry

if TYPE_CHECKING:
    from pyrit.models.catalog import RunScenarioRequest, ScenarioRunSummary, ScenarioSuiteRegistryEntry

logger = logging.getLogger(__name__)

_MAX_RETAINED_EXECUTIONS = 256
_CAPACITY_MESSAGE = (
    "Too many scenario suites are still active. Wait for an active suite to finish before launching another."
)
_TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ScenarioSuiteExecutionStatus.COMPLETED,
        ScenarioSuiteExecutionStatus.FAILED,
        ScenarioSuiteExecutionStatus.CANCELLED,
    }
)


class ScenarioSuiteExecutionNotFoundError(LookupError):
    """A requested suite definition or transient execution does not exist."""


class ScenarioSuiteExecutionCapacityError(RuntimeError):
    """Active suite executions fill the transient coordinator capacity."""


class ScenarioSuiteLaunchConflictError(ValueError):
    """A suite or referenced preset changed after the operator reviewed it."""


class ScenarioSuiteNotRunnableError(ValueError):
    """A suite cannot launch because one or more entries are unresolved."""


@dataclass(frozen=True)
class _ResolvedLaunchEntry:
    """One preflighted suite entry ready for scenario submission."""

    entry_id: UUID
    entry_position: int
    preset_name: str
    preset_origin: Literal["stored", "registered"]
    preset_version: str | None
    request: RunScenarioRequest

    def metadata(self, *, suite_execution_id: UUID, suite: ScenarioSuiteRegistryEntry) -> dict[str, Any]:
        """Return the scenario metadata fields for this child run."""
        return ScenarioSuiteExecutionMetadata(
            suite_execution_id=suite_execution_id,
            suite_name=suite.suite.name,
            suite_version=suite.version,
            suite_entry_id=self.entry_id,
            entry_position=self.entry_position,
            preset_name=self.preset_name,
            preset_version=self.preset_version,
        ).to_metadata_fields()


@dataclass
class _ExecutionEntryState:
    """Mutable process-local submission state for one resolved entry."""

    resolved: _ResolvedLaunchEntry
    state: ScenarioSuiteExecutionEntryState = ScenarioSuiteExecutionEntryState.PENDING
    scenario_result_id: str | None = None
    error: str | None = None
    error_type: str | None = None
    submission_task: asyncio.Task[ScenarioRunSummary] | None = None


@dataclass
class _ExecutionState:
    """Mutable process-local state for one accepted suite execution."""

    execution_id: UUID
    suite: ScenarioSuiteRegistryEntry
    entries: list[_ExecutionEntryState]
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool = False
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class _ExecutionEntrySnapshot:
    """Detached entry state used while querying child runs."""

    resolved: _ResolvedLaunchEntry
    state: ScenarioSuiteExecutionEntryState
    scenario_result_id: str | None
    error: str | None
    error_type: str | None


@dataclass(frozen=True)
class _ExecutionSnapshot:
    """Detached execution state used while querying child runs."""

    execution_id: UUID
    suite: ScenarioSuiteRegistryEntry
    entries: tuple[_ExecutionEntrySnapshot, ...]
    created_at: datetime
    updated_at: datetime


class ScenarioSuiteExecutionService:
    """
    Preflight suites and coordinate ordinary scenario runs without a persisted parent.

    Definitions remain owned by the suite and preset registries. This service owns only
    the transient identity and submission progress for one launch; each successful child
    remains an ordinary persisted ``ScenarioResult`` managed by ``ScenarioRunService``.
    """

    SUBMISSION_FAILURE_MESSAGE = "The scenario run could not be created. Check server logs before retrying."
    CHILD_STATUS_FAILURE_MESSAGE = "The scenario run status could not be loaded. Refresh the page or check server logs."

    def __init__(
        self,
        *,
        suite_registry: ScenarioSuiteRegistry | None = None,
        preset_registry: ScenarioPresetRegistry | None = None,
        run_service: ScenarioRunService | None = None,
    ) -> None:
        """Initialize the coordinator with injectable registries and run service."""
        self._suite_registry = suite_registry
        self._preset_registry = preset_registry
        self._run_service = run_service
        self._executions: OrderedDict[UUID, _ExecutionState] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()
        self._admission_lock = asyncio.Lock()
        self._stopping = False

    def has_active_work(self) -> bool:
        """Return whether accepted suite submissions are still running."""
        return any(not task.done() for task in self._tasks)

    def stop_admission(self) -> None:
        """Reject new launches while allowing retained progress to remain readable."""
        self._stopping = True

    async def launch_async(
        self,
        *,
        name: str,
        request: LaunchScenarioSuiteRequest,
    ) -> ScenarioSuiteExecutionSummary:
        """
        Preflight one suite and begin ordered child-run submission.

        Returns:
            ScenarioSuiteExecutionSummary: Accepted transient execution identity
                and initial entry states.
        """
        if self._stopping:
            raise RuntimeError("Scenario suite launch scheduling is stopping.")

        suite, resolved_entries = await asyncio.to_thread(
            self._resolve_launch,
            name=name,
            request=request,
        )
        if self._stopping:
            raise RuntimeError("Scenario suite launch scheduling is stopping.")

        async with self._admission_lock:
            if self._stopping:
                raise RuntimeError("Scenario suite launch scheduling is stopping.")
            await self._ensure_execution_capacity_async()

            now = datetime.now(UTC)
            state = _ExecutionState(
                execution_id=uuid4(),
                suite=suite,
                entries=[_ExecutionEntryState(resolved=entry) for entry in resolved_entries],
                created_at=now,
                updated_at=now,
            )
            self._executions[state.execution_id] = state
            task = asyncio.create_task(self._submit_entries_async(state=state))
            state.task = task
            self._tasks.add(task)
            task.add_done_callback(self._submission_done)
        return await self._build_summary_async(state=state)

    async def get_execution_async(self, *, execution_id: UUID) -> ScenarioSuiteExecutionSummary | None:
        """
        Read current process-local progress for one suite execution.

        Returns:
            ScenarioSuiteExecutionSummary | None: Current grouped progress, or
                None when the transient execution is unavailable.
        """
        state = self._executions.get(execution_id)
        return None if state is None else await self._build_summary_async(state=state)

    async def cancel_execution_async(self, *, execution_id: UUID) -> ScenarioSuiteExecutionSummary | None:
        """
        Best-effort cancel pending submissions and already-created child runs.

        Returns:
            ScenarioSuiteExecutionSummary | None: Updated grouped progress, or
                None when the transient execution is unavailable.
        """
        state = self._executions.get(execution_id)
        if state is None:
            return None

        current = await self._build_summary_async(state=state)
        if current.status in _TERMINAL_EXECUTION_STATUSES:
            return current

        await self._request_cancellation_async(state=state)
        return await self._build_summary_async(state=state)

    async def shutdown_async(self) -> None:
        """Request child cancellation and finish in-flight admission handoffs."""
        self.stop_admission()
        await asyncio.gather(
            *(self._request_cancellation_async(state=state) for state in list(self._executions.values())),
            return_exceptions=True,
        )
        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_async(self) -> None:
        """Close a drained coordinator."""
        if self.has_active_work():
            raise RuntimeError("Scenario suite execution work has not drained.")

    def _resolve_launch(
        self,
        *,
        name: str,
        request: LaunchScenarioSuiteRequest,
    ) -> tuple[ScenarioSuiteRegistryEntry, list[_ResolvedLaunchEntry]]:
        """
        Resolve and validate the complete launch before any scenario is submitted.

        Returns:
            tuple[ScenarioSuiteRegistryEntry, list[_ResolvedLaunchEntry]]: The
                exact suite and ordered, runnable child requests.
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
        launch_fields = ResolveScenarioPresetRequest(
            expected_version=None,
            target_name=request.target_name,
            adversarial_target_name=request.adversarial_target_name,
            initializers=request.initializers,
            initializer_args=request.initializer_args,
            max_concurrency=request.max_concurrency,
            max_retries=request.max_retries,
            operation_id=request.operation_id,
            labels=request.labels,
        )
        presets = {
            preset_name: preset_registry.get_preset(name=preset_name)
            for preset_name in dict.fromkeys(entry.preset_name for entry in suite.suite.entries)
        }

        resolved: list[_ResolvedLaunchEntry] = []
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
                _ResolvedLaunchEntry(
                    entry_id=suite_entry.entry_id,
                    entry_position=position,
                    preset_name=suite_entry.preset_name,
                    preset_origin=preset.origin,
                    preset_version=preset.version,
                    request=ScenarioPresetService.resolve_run_request(
                        preset=preset.preset,
                        launch=launch_fields,
                    ),
                )
            )
        return suite, resolved

    async def _submit_entries_async(self, *, state: _ExecutionState) -> None:
        """Submit every resolved entry in declared order through the FIFO run service."""
        try:
            for entry in state.entries:
                async with state.lock:
                    if state.cancel_requested:
                        self._mark_unstarted_entries_cancelled(state=state)
                        state.updated_at = datetime.now(UTC)
                        return
                    entry.state = ScenarioSuiteExecutionEntryState.SUBMITTING
                    task = asyncio.create_task(
                        self._get_run_service().start_run_async(
                            request=entry.resolved.request,
                            initial_metadata=entry.resolved.metadata(
                                suite_execution_id=state.execution_id,
                                suite=state.suite,
                            ),
                        )
                    )
                    entry.submission_task = task
                    state.updated_at = datetime.now(UTC)

                try:
                    result = await asyncio.shield(task)
                except Exception as exc:
                    logger.warning(
                        "Scenario suite execution %s entry %s could not be submitted.",
                        state.execution_id,
                        entry.resolved.entry_id,
                        exc_info=True,
                    )
                    async with state.lock:
                        entry.state = ScenarioSuiteExecutionEntryState.SUBMISSION_FAILED
                        entry.error = self.SUBMISSION_FAILURE_MESSAGE
                        entry.error_type = type(exc).__name__
                        state.updated_at = datetime.now(UTC)
                    continue

                await self._record_submission_result_async(state=state, entry=entry, result=result)
        except asyncio.CancelledError:
            async with state.lock:
                state.cancel_requested = True
                self._mark_unstarted_entries_cancelled(state=state)
                in_flight = [
                    (entry, entry.submission_task)
                    for entry in state.entries
                    if entry.state == ScenarioSuiteExecutionEntryState.SUBMITTING
                    and entry.submission_task is not None
                    and entry.scenario_result_id is None
                ]
                state.updated_at = datetime.now(UTC)
            for entry, task in in_flight:
                try:
                    result = await asyncio.shield(task)
                except asyncio.CancelledError:
                    async with state.lock:
                        if entry.scenario_result_id is None:
                            entry.state = ScenarioSuiteExecutionEntryState.CANCELLED
                            state.updated_at = datetime.now(UTC)
                except Exception as exc:
                    async with state.lock:
                        entry.state = ScenarioSuiteExecutionEntryState.SUBMISSION_FAILED
                        entry.error = self.SUBMISSION_FAILURE_MESSAGE
                        entry.error_type = type(exc).__name__
                        state.updated_at = datetime.now(UTC)
                else:
                    await self._record_submission_result_async(state=state, entry=entry, result=result)
            raise

    async def _build_summary_async(self, *, state: _ExecutionState) -> ScenarioSuiteExecutionSummary:
        """
        Build a coherent response while querying child runs outside the state lock.

        Returns:
            ScenarioSuiteExecutionSummary: Current grouped execution progress.
        """
        async with state.lock:
            snapshot = _ExecutionSnapshot(
                execution_id=state.execution_id,
                suite=state.suite,
                entries=tuple(
                    _ExecutionEntrySnapshot(
                        resolved=entry.resolved,
                        state=entry.state,
                        scenario_result_id=entry.scenario_result_id,
                        error=entry.error,
                        error_type=entry.error_type,
                    )
                    for entry in state.entries
                ),
                created_at=state.created_at,
                updated_at=state.updated_at,
            )
        return await self._build_summary_from_snapshot_async(snapshot=snapshot)

    async def _build_summary_from_snapshot_async(
        self,
        *,
        snapshot: _ExecutionSnapshot,
    ) -> ScenarioSuiteExecutionSummary:
        """
        Build a response from detached coordinator state and live child runs.

        Returns:
            ScenarioSuiteExecutionSummary: Current grouped execution progress.
        """
        run_entries = [entry for entry in snapshot.entries if entry.scenario_result_id is not None]
        results = await asyncio.gather(
            *(
                self._get_run_service().get_run_async(scenario_result_id=entry.scenario_result_id or "")
                for entry in run_entries
            ),
            return_exceptions=True,
        )
        runs_by_entry: dict[UUID, ScenarioRunSummary | BaseException | None] = {
            entry.resolved.entry_id: result for entry, result in zip(run_entries, results, strict=True)
        }

        summaries: list[ScenarioSuiteExecutionEntrySummary] = []
        updated_at = snapshot.updated_at
        for entry in snapshot.entries:
            has_run = entry.resolved.entry_id in runs_by_entry
            run_or_error = runs_by_entry.get(entry.resolved.entry_id)
            run = None
            error = entry.error
            error_type = entry.error_type
            entry_state = entry.state
            if isinstance(run_or_error, BaseException):
                logger.warning(
                    "Scenario suite execution %s entry %s child run status could not be loaded.",
                    snapshot.execution_id,
                    entry.resolved.entry_id,
                    exc_info=(type(run_or_error), run_or_error, run_or_error.__traceback__),
                )
                error = self.CHILD_STATUS_FAILURE_MESSAGE
                error_type = type(run_or_error).__name__
            elif run_or_error is not None:
                run = run_or_error
                entry_state = ScenarioSuiteExecutionEntryState(run.status.value)
                updated_at = max(updated_at, run.updated_at)
            elif has_run:
                entry_state = ScenarioSuiteExecutionEntryState.FAILED
                error = "The submitted child scenario run is no longer available."
                error_type = "ScenarioRunNotFoundError"

            summaries.append(
                ScenarioSuiteExecutionEntrySummary(
                    entry_id=entry.resolved.entry_id,
                    entry_position=entry.resolved.entry_position,
                    preset_name=entry.resolved.preset_name,
                    preset_origin=entry.resolved.preset_origin,
                    preset_version=entry.resolved.preset_version,
                    state=entry_state,
                    run=run,
                    error=error,
                    error_type=error_type,
                )
            )

        return ScenarioSuiteExecutionSummary(
            suite_execution_id=snapshot.execution_id,
            suite_name=snapshot.suite.suite.name,
            suite_origin=snapshot.suite.origin,
            suite_version=snapshot.suite.version,
            status=self._execution_status(entries=summaries),
            created_at=snapshot.created_at,
            updated_at=updated_at,
            entries=summaries,
        )

    async def _request_cancellation_async(self, *, state: _ExecutionState) -> None:
        """Cancel known children without interrupting an in-flight admission handoff."""
        async with state.lock:
            state.cancel_requested = True
            self._mark_unstarted_entries_cancelled(state=state)
            admitted_entries = [
                (entry, entry.scenario_result_id) for entry in state.entries if entry.scenario_result_id is not None
            ]
            state.updated_at = datetime.now(UTC)

        await asyncio.gather(
            *(
                self._cancel_child_run_async(
                    state=state,
                    entry=entry,
                    scenario_result_id=scenario_result_id,
                )
                for entry, scenario_result_id in admitted_entries
            )
        )

    async def _record_submission_result_async(
        self,
        *,
        state: _ExecutionState,
        entry: _ExecutionEntryState,
        result: ScenarioRunSummary,
    ) -> None:
        """Record an admitted child and honor cancellation requested during admission."""
        async with state.lock:
            entry.scenario_result_id = result.scenario_result_id
            entry.state = ScenarioSuiteExecutionEntryState(result.status.value)
            entry.error = result.error
            entry.error_type = result.error_type
            should_cancel = state.cancel_requested
            state.updated_at = datetime.now(UTC)

        if should_cancel:
            await self._cancel_child_run_async(
                state=state,
                entry=entry,
                scenario_result_id=result.scenario_result_id,
            )

    async def _cancel_child_run_async(
        self,
        *,
        state: _ExecutionState,
        entry: _ExecutionEntryState,
        scenario_result_id: str,
    ) -> None:
        """Cancel one admitted child and retain the scheduler's authoritative state."""
        try:
            result = await self._get_run_service().cancel_run_async(scenario_result_id=scenario_result_id)
        except Exception:
            logger.warning(
                "Scenario suite execution %s child run %s could not be cancelled.",
                state.execution_id,
                scenario_result_id,
                exc_info=True,
            )
            return
        if result is None:
            return

        async with state.lock:
            entry.state = ScenarioSuiteExecutionEntryState(result.status.value)
            entry.error = result.error
            entry.error_type = result.error_type
            state.updated_at = datetime.now(UTC)

    @staticmethod
    def _mark_unstarted_entries_cancelled(*, state: _ExecutionState) -> None:
        """Mark entries that have not begun admission as cancelled."""
        for entry in state.entries:
            if entry.state == ScenarioSuiteExecutionEntryState.PENDING:
                entry.state = ScenarioSuiteExecutionEntryState.CANCELLED

    async def _ensure_execution_capacity_async(self) -> None:
        """Evict only terminal executions or reject admission when active work fills capacity."""
        while len(self._executions) >= _MAX_RETAINED_EXECUTIONS:
            evicted = False
            for execution_id, state in list(self._executions.items()):
                if state.task is None or not state.task.done():
                    continue
                summary = await self._build_summary_async(state=state)
                if summary.status not in _TERMINAL_EXECUTION_STATUSES:
                    continue
                if self._executions.get(execution_id) is state:
                    self._executions.pop(execution_id)
                    evicted = True
                    break
            if not evicted:
                raise ScenarioSuiteExecutionCapacityError(_CAPACITY_MESSAGE)

    @staticmethod
    def _execution_status(
        *,
        entries: list[ScenarioSuiteExecutionEntrySummary],
    ) -> ScenarioSuiteExecutionStatus:
        """
        Reduce ordered entry states to one process-local execution status.

        Returns:
            ScenarioSuiteExecutionStatus: Aggregate execution state.
        """
        states = {entry.state for entry in entries}
        if states.intersection(
            {
                ScenarioSuiteExecutionEntryState.PENDING,
                ScenarioSuiteExecutionEntryState.SUBMITTING,
            }
        ):
            return ScenarioSuiteExecutionStatus.QUEUING
        if states.intersection(
            {
                ScenarioSuiteExecutionEntryState.CREATED,
                ScenarioSuiteExecutionEntryState.QUEUED,
                ScenarioSuiteExecutionEntryState.IN_PROGRESS,
            }
        ):
            return ScenarioSuiteExecutionStatus.IN_PROGRESS
        if ScenarioSuiteExecutionEntryState.CANCELLED in states:
            return ScenarioSuiteExecutionStatus.CANCELLED
        if states.intersection(
            {
                ScenarioSuiteExecutionEntryState.SUBMISSION_FAILED,
                ScenarioSuiteExecutionEntryState.FAILED,
            }
        ):
            return ScenarioSuiteExecutionStatus.FAILED
        return ScenarioSuiteExecutionStatus.COMPLETED

    def _submission_done(self, task: asyncio.Task[None]) -> None:
        """Retrieve unexpected task failures and release active-task ownership."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Scenario suite submission coordinator failed.",
                exc_info=(type(error), error, error.__traceback__),
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

    def _get_run_service(self) -> ScenarioRunService:
        """Return the injected run service or process singleton."""
        return self._run_service if self._run_service is not None else get_scenario_run_service()


_service_instance: ScenarioSuiteExecutionService | None = None


def peek_scenario_suite_execution_service() -> ScenarioSuiteExecutionService | None:
    """Return the existing coordinator without constructing one."""
    return _service_instance


async def reset_scenario_suite_execution_service_async() -> None:
    """Close and discard the drained coordinator singleton."""
    global _service_instance
    if _service_instance is not None:
        await _service_instance.close_async()
        _service_instance = None


def get_scenario_suite_execution_service() -> ScenarioSuiteExecutionService:
    """Return the process-local suite execution coordinator."""
    global _service_instance
    if _service_instance is None:
        _service_instance = ScenarioSuiteExecutionService()
    return _service_instance
