# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario run service for executing scenarios as background tasks.

Manages the lifecycle of scenario runs: starting, tracking status,
retrieving results, and cancellation.
"""

import asyncio
import base64
import contextlib
import json
import logging
import uuid
from bisect import bisect_right
from collections import OrderedDict, deque
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import TypeAdapter, ValidationError

from pyrit.analytics.outcome_statistics import compute_outcome_statistics, success_percentage
from pyrit.analytics.scenario_statistics import (
    ScenarioAttempt,
    ScenarioExecutionUnit,
    count_execution_units,
    resolve_execution_unit,
    select_execution_attack_result_ids,
)
from pyrit.backend.models.common import PaginationInfo, filter_sensitive_fields
from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.backend.services.saved_conversation_read_service import SavedConversationReadService
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_progress_read_model import (
    ScenarioPlanLookup,
    ScenarioProgressReadModel,
    ScenarioProgressSnapshot,
)
from pyrit.common.async_compatibility import run_legacy_sync_async
from pyrit.memory import AttackResultKeysetCursor, CentralMemory, SQLiteMemory
from pyrit.memory.memory_interface import (
    MemoryInterface,
    ScenarioAttemptSeedSummary,
    ScenarioHistoryAggregate,
    ScenarioHistoryKeysetCursor,
    ScenarioHistoryRunRecord,
)
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    SCENARIO_RUN_STARTED_AT_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackResultMetadata,
    ComponentIdentifier,
    ScenarioAttackResultDelta,
    ScenarioHierarchyNode,
    ScenarioHierarchyProjection,
    ScenarioIdentifier,
    ScenarioProducerCategoryCounts,
    ScenarioProducerCounts,
    ScenarioProgressCounts,
    ScenarioProgressHeader,
    ScenarioProgressResult,
    ScenarioQueueEntry,
    ScenarioQueueSnapshot,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunProgress,
    ScenarioRunState,
    TargetIdentifier,
)
from pyrit.models.catalog.scenario import (
    AttackErrorSummary,
    AttackRetrySummary,
    RunScenarioRequest,
    ScenarioOverloadSummary,
    ScenarioRunListItem,
    ScenarioRunSummary,
    ScenarioTargetSummary,
    ScenarioTechniqueSummary,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry import InitializerRegistry, ScenarioRegistry
from pyrit.registry.resolution import reject_non_external_params, resolve_declared_params
from pyrit.scenario import IncompatibleTechniquePolicy, Scenario
from pyrit.scenario.core import override_default_adversarial_target

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_RUNS = 1
_MAX_OVERLOAD_EVENTS = 500
_MAX_OVERLOAD_ROLES = 16
_MAX_TERMINAL_ERRORS = 100
_SCHEDULER_RETRY_INITIAL_SECONDS = 0.05
_SCHEDULER_RETRY_MAX_SECONDS = 1.0
_SCHEDULER_METADATA_KEY = "scheduler_managed_by"
_SCHEDULER_METADATA_VALUE = "ScenarioRunService.process_local_fifo"
_INTERRUPTED_ERROR_TYPE = "ScenarioInterruptedError"
_RESTART_INTERRUPTION_REASON = (
    "The backend process restarted before this scenario run completed; "
    "its executable scenario objects could not be recovered safely."
)
_SHUTDOWN_INTERRUPTION_REASON = "The backend process shut down before this scenario run completed."
_USER_CANCELLATION_REASON = "Run was cancelled by user"
_LAUNCH_REQUEST_METADATA_KEY = "scenario_launch_request_v1"
_RESERVED_INITIAL_METADATA_KEYS = frozenset(
    {
        _SCHEDULER_METADATA_KEY,
        _LAUNCH_REQUEST_METADATA_KEY,
        SCENARIO_RUN_PLAN_METADATA_KEY,
        SCENARIO_RUN_STARTED_AT_METADATA_KEY,
    }
)
_INITIAL_METADATA_CONTEXT: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "scenario_run_initial_metadata",
    default=None,
)
_LAUNCH_REQUEST_FIELDS = (
    "scenario_name",
    "target_name",
    "adversarial_target_name",
    "techniques",
    "technique_override",
    "objective_scorer",
    "datasets",
    "dataset_names",
    "max_dataset_size",
    "dataset_filters",
    "max_concurrency",
    "max_retries",
    "include_baseline",
)
# Fields added after the first saved launch records; older records omit them.
_OPTIONAL_LAUNCH_FIELDS = frozenset(
    {"adversarial_target_name", "datasets", "max_dataset_size", "technique_override", "objective_scorer"}
)

_SAFE_SCENARIO_PARAMETER_NAMES = frozenset(
    {
        "adversarial_targets",
        "jailbreak_names",
        "max_attempts_per_objective",
        "max_turns",
        "num_jailbreak_attempts",
        "num_jailbreaks",
        "sub_harm",
        "incompatible_technique_policy",
        "version",
    }
)
_HISTORY_ATOMIC_GROUPS_ADAPTER = TypeAdapter(list[ScenarioRunPlanAtomicGroup])
_HISTORY_SEED_ID_MAP_ADAPTER = TypeAdapter(list[dict[str, str]])
_STARTED_AT_ADAPTER = TypeAdapter(datetime)


class ScenarioRunConflictError(ValueError):
    """A saved run cannot be admitted to the scheduler in its current state."""


class ScenarioRunNotFoundError(ValueError):
    """The requested saved run is not in the active memory database."""


@dataclass
class _PreparedRun:
    """Scenario and request-scoped default captured on the backend loop."""

    scenario: Scenario
    adversarial_target: PromptTarget | None = None


@dataclass
class _ActiveTask:
    """Tracks an in-flight scenario run's asyncio task."""

    scenario_result_id: str
    task: asyncio.Task[None] | None = None
    scenario: Scenario | None = None
    adversarial_target: PromptTarget | None = None
    error: str | None = None
    scenario_name: str = ""
    scenario_registry_name: str = ""
    created_at: datetime | None = None
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    cancellation_state: ScenarioRunState = ScenarioRunState.CANCELLED
    cancellation_reason: str = _USER_CANCELLATION_REASON
    cancellation_error_type: str = "CancelledError"
    retain_error_on_terminalization: bool = False


@dataclass(frozen=True, slots=True)
class _ActiveRunSnapshot:
    """Event-loop-owned state copied before database awaits allow live state to change."""

    error: str | None = None
    active_group_ids: tuple[str, ...] = ()
    queue_position: int | None = None
    active_scenario_result_id: str | None = None


class ScenarioRunService:
    """
    Service for managing scenario run lifecycle.

    Uses CentralMemory (database) as the source of truth for run state.
    Preparation, execution, and reads run on the backend event loop.
    The preparation gate stays held through abandoned-request cleanup.
    Keeps executable objects in a process-local single-active FIFO scheduler.
    FIFO ordering therefore spans only runs submitted to the same backend
    process. Deploy one backend replica to preserve a global admission order;
    multiple replicas require a shared database-backed scheduler or lease.
    """

    def __init__(
        self,
        *,
        max_concurrent_runs: int = _DEFAULT_MAX_CONCURRENT_RUNS,
        memory: MemoryInterface | None = None,
        progress_read_model: ScenarioProgressReadModel | None = None,
    ) -> None:
        """
        Initialize the scenario run service.

        ``max_concurrent_runs`` remains accepted for configuration compatibility;
        scenario execution is always serialized to one active run.
        """
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1.")
        self._memory = memory if memory is not None else CentralMemory.get_memory_instance()
        self._active_tasks: dict[str, _ActiveTask] = {}
        self._configuration_resolver = ScenarioConfigurationResolver()
        self._progress_read_model = (
            progress_read_model if progress_read_model is not None else ScenarioProgressReadModel(memory=self._memory)
        )
        self._saved_conversation_reader = SavedConversationReadService(memory=self._memory)
        self._technique_metadata_cache: dict[str, dict[str, ScenarioTechniqueSummary]] = {}

        self._preparations: set[asyncio.Task[_PreparedRun]] = set()
        self._preparation_errors: list[Exception] = []
        self._terminal_errors: OrderedDict[str, str] = OrderedDict()
        self._active_scenario_result_id: str | None = None
        self._queued_runs: deque[_ActiveTask] = deque()
        self._handoff_retry_tasks: set[asyncio.Task[None]] = set()
        self._abandoned_prepare_tasks: set[asyncio.Task[None]] = set()
        self._scheduler_lock = asyncio.Lock()
        self._launch_lock = asyncio.Lock()
        self._preparation_lock = asyncio.Lock()
        self._preparing_run_ids: set[str] = set()
        self._pending_resume_requests: set[str] = set()
        self._queue_revision = 0
        self._stopping = False

    def has_active_work(self) -> bool:
        """Return whether scenario scheduling, preparation, or handoff work remains."""
        return bool(
            self._active_scenario_result_id
            or self._queued_runs
            or self._preparations
            or self._abandoned_prepare_tasks
            or self._handoff_retry_tasks
            or self._launch_lock.locked()
        )

    async def close_async(self) -> None:
        """Close a service only after all tracked work has drained."""
        if self.has_active_work():
            raise RuntimeError("Scenario work has not drained.")
        if self._preparation_errors:
            raise ExceptionGroup("Failed to clean up abandoned scenario preparations.", self._preparation_errors)

    def stop_admission(self) -> None:
        """Reject new preparations and prevent queued runs from starting."""
        self._stopping = True

    async def start_run_async(
        self,
        *,
        request: RunScenarioRequest,
        initial_metadata: Mapping[str, Any] | None = None,
    ) -> ScenarioRunSummary:
        """
        Initialize and schedule a new run or an explicitly configured continuation.

        Args:
            request: Scenario launch configuration.
            initial_metadata: Internal caller-owned scenario metadata persisted
                atomically with a fresh result. Reserved run-service keys cannot
                be supplied.

        Returns:
            ScenarioRunSummary: Current scheduled run state.
        """
        metadata = self._validate_initial_metadata(initial_metadata=initial_metadata)
        async with self._reserve_resume_request_async(request.scenario_result_id), self._launch_lock:
            await self._validate_resume_admission_async(scenario_result_id=request.scenario_result_id)
            return await self._start_run_locked_async(request=request, initial_metadata=metadata)

    async def resume_run_async(self, *, scenario_result_id: str) -> ScenarioRunSummary:
        """
        Resume a failed run with its saved launch configuration through the normal scheduler.

        Returns:
            ScenarioRunSummary: Current state under the original result ID.
        """
        async with self._reserve_resume_request_async(scenario_result_id), self._launch_lock:
            stored = await self._validate_resume_admission_async(
                scenario_result_id=scenario_result_id, failed_only=True
            )
            assert stored is not None
            request = await asyncio.to_thread(self._restore_launch_request, stored=stored)
            return await self._start_run_locked_async(request=request, initial_metadata={})

    @contextlib.asynccontextmanager
    async def _reserve_resume_request_async(self, scenario_result_id: str | None) -> AsyncGenerator[None, None]:
        """Reject overlapping resume requests even if the first attempt fails immediately."""
        if scenario_result_id is None:
            yield
            return
        if scenario_result_id in self._pending_resume_requests:
            raise ScenarioRunConflictError(f"Scenario run '{scenario_result_id}' is already being resumed.")
        self._pending_resume_requests.add(scenario_result_id)
        try:
            yield
        finally:
            self._pending_resume_requests.discard(scenario_result_id)

    async def _validate_resume_admission_async(
        self, *, scenario_result_id: str | None, failed_only: bool = False
    ) -> ScenarioResult | None:
        """
        Reject duplicate and non-resumable starts before initialization has side effects.

        Returns:
            ScenarioResult | None: The saved header, or None for a fresh launch.
        """
        if not scenario_result_id:
            return None
        if (
            scenario_result_id in self._preparing_run_ids
            or scenario_result_id in self._active_tasks
            or any(run.scenario_result_id == scenario_result_id for run in self._queued_runs)
        ):
            raise ScenarioRunConflictError(f"Scenario run '{scenario_result_id}' is already scheduled or initializing.")
        stored = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        if stored is None:
            raise ScenarioRunNotFoundError(f"Scenario run '{scenario_result_id}' was not found in this database.")
        eligible_states = {ScenarioRunState.FAILED}
        if not failed_only:
            eligible_states.add(ScenarioRunState.CANCELLED)
        if stored.scenario_run_state not in eligible_states:
            raise ScenarioRunConflictError(
                f"Scenario run '{scenario_result_id}' cannot resume from {stored.scenario_run_state.value}."
            )
        return stored

    def _restore_launch_request(self, *, stored: ScenarioResult) -> RunScenarioRequest:
        """
        Restore persisted inputs, never the browser's or current catalog's defaults.

        Returns:
            RunScenarioRequest: Saved launch inputs and canonical scenario parameters.
        """
        if _LAUNCH_REQUEST_METADATA_KEY not in stored.metadata:
            raise ScenarioRunConflictError(
                "This older run has no saved launch configuration and cannot be resumed through the GUI. "
                "Use the SDK or run API with the original configuration and scenario_result_id."
            )
        raw_request = stored.metadata[_LAUNCH_REQUEST_METADATA_KEY]
        if isinstance(raw_request, dict):
            # Launch records from before per-run target selection used the server default.
            raw_request = {"adversarial_target_name": None, **raw_request}
        if not isinstance(raw_request, dict) or any(
            name not in raw_request for name in _LAUNCH_REQUEST_FIELDS if name not in _OPTIONAL_LAUNCH_FIELDS
        ):
            raise ScenarioRunConflictError("The saved launch configuration is incomplete; resume was not started.")
        try:
            # JSON validation accepts saved enum values as strings while keeping strict type checks.
            request = RunScenarioRequest.model_validate_json(
                json.dumps({name: raw_request[name] for name in _LAUNCH_REQUEST_FIELDS if name in raw_request}),
                strict=True,
            )
        except ValidationError as exc:
            raise ScenarioRunConflictError(
                "The saved launch configuration is invalid; resume was not started."
            ) from exc
        if not request.scenario_name.strip() or (request.target_name is not None and not request.target_name.strip()):
            raise ScenarioRunConflictError("The saved scenario or target registration name is empty.")
        scenario_class = self._configuration_resolver.resolve_scenario_class(scenario_name=request.scenario_name)
        declared_names = {parameter.name for parameter in scenario_class.supported_parameters()}
        if any(
            parameter_name in declared_names and getattr(request, field_name) is None
            for parameter_name, field_name in (
                ("objective_target", "target_name"),
                ("include_baseline", "include_baseline"),
                ("max_concurrency", "max_concurrency"),
                ("max_retries", "max_retries"),
            )
        ):
            raise ScenarioRunConflictError("The saved launch configuration is incomplete; resume was not started.")
        identifier = stored.scenario_identifier
        if (
            "scenario_techniques" in declared_names and request.techniques is None and identifier.techniques is None
        ) or ("dataset_config" in declared_names and request.dataset_request is None and identifier.datasets is None):
            raise ScenarioRunConflictError("The saved scenario identity is missing techniques or datasets.")
        custom_params = {
            name: value
            for name, value in identifier.params.items()
            if name not in {"version", "techniques", "datasets"}
        }
        saved_policy = raw_request.get(
            "incompatible_technique_policy", stored.metadata.get("incompatible_technique_policy")
        )
        if saved_policy is not None:
            try:
                custom_params["incompatible_technique_policy"] = IncompatibleTechniquePolicy(saved_policy).value
            except ValueError as exc:
                raise ScenarioRunConflictError(
                    "The saved incompatible-technique policy is invalid; resume was not started."
                ) from exc
        # Dataset names are not copied from the identity: the saved request reproduces them, and the
        # stored-identity check on initialization rejects any difference.
        return request.model_copy(
            update={
                "scenario_result_id": str(stored.id),
                "initializers": None,
                "initializer_args": None,
                "scenario_params": custom_params,
                "techniques": request.techniques
                if request.techniques is not None or "scenario_techniques" not in declared_names
                else identifier.techniques,
                "labels": dict(stored.labels) if "memory_labels" in declared_names else None,
                "operation_id": stored.operation_id,
            },
            deep=True,
        )

    async def _start_run_locked_async(
        self,
        *,
        request: RunScenarioRequest,
        initial_metadata: Mapping[str, Any],
    ) -> ScenarioRunSummary:
        """
        Initialize and schedule a scenario run.

        Performs all validation and initialization eagerly (initializers, target
        resolution, technique validation, scenario.initialize_async) so errors are
        returned immediately. On success, starts execution when idle or appends
        the initialized run to the FIFO waiting queue.

        Args:
            request: The run request with scenario name, target, and options.
            initial_metadata: Internal caller-owned metadata persisted with a
                fresh scenario result.

        Returns:
            ScenarioRunSummary with a stable ID and current active or queued state.

        Raises:
            ValueError: If scenario, target, initializer, or technique cannot be found.
        """
        if self._stopping:
            raise RuntimeError("Scenario run scheduling is stopping.")
        await self._preparation_lock.acquire()
        prepare_task: asyncio.Task[_PreparedRun] | None = None
        abandoned = False
        try:
            if self._stopping:
                raise RuntimeError("Scenario run scheduling is stopping.")
            request = await self._validate_run_operation_async(request=request)
            resumed_from_cancelled = await self._is_run_cancelled_async(scenario_result_id=request.scenario_result_id)
            if request.scenario_result_id:
                self._preparing_run_ids.add(request.scenario_result_id)
            metadata_token = _INITIAL_METADATA_CONTEXT.set(initial_metadata)
            try:
                prepare_task = asyncio.create_task(self._prepare_run_async(request=request))
            finally:
                _INITIAL_METADATA_CONTEXT.reset(metadata_token)
            self._preparations.add(prepare_task)
            prepared = await asyncio.shield(prepare_task)
            return await self._schedule_prepared_run_async(
                request=request, prepared=prepared, resumed_from_cancelled=resumed_from_cancelled
            )
        except asyncio.CancelledError:
            if prepare_task is not None:
                abandoned = True
                cleanup = asyncio.create_task(
                    self._cleanup_abandoned_prepare_async(prepare_task=prepare_task, request=request)
                )
                self._abandoned_prepare_tasks.add(cleanup)
                cleanup.add_done_callback(self._abandoned_prepare_done)
            raise
        finally:
            if not abandoned:
                self._finish_preparation(prepare_task=prepare_task, request=request)

    async def _schedule_prepared_run_async(
        self, *, request: RunScenarioRequest, prepared: _PreparedRun, resumed_from_cancelled: bool
    ) -> ScenarioRunSummary:
        """
        Validate the persisted header before admission; build details only for the returned response.

        Returns:
            ScenarioRunSummary: The persisted active, queued, or terminal state.
        """
        scenario = prepared.scenario
        scenario_result_id = scenario._scenario_result_id
        if scenario_result_id is None:
            raise ValueError("Scenario did not produce a scenario_result_id during initialization.")
        if request.scenario_result_id and scenario_result_id != request.scenario_result_id:
            raise ValueError("Scenario initialization changed the saved result ID; resume was not started.")
        persisted = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        if persisted is None:
            raise RuntimeError(f"Scenario run {scenario_result_id} was not persisted during initialization.")
        if not resumed_from_cancelled and persisted.scenario_run_state == ScenarioRunState.CANCELLED:
            response = await self._build_response_async(
                scenario_result_id=scenario_result_id,
                active_error=None,
                queue_position=None,
                active_scenario_result_id=self._active_scenario_result_id,
            )
            if response is None:
                raise RuntimeError(
                    f"Scenario run {scenario_result_id} was not found in the database after initialization."
                )
            return response
        scheduled = _ActiveTask(
            scenario_result_id=scenario_result_id,
            scenario=scenario,
            adversarial_target=prepared.adversarial_target,
            scenario_name=persisted.scenario_name,
            scenario_registry_name=request.scenario_name,
            created_at=persisted.creation_time,
            enqueued_at=datetime.now(UTC),
        )
        await self._enqueue_run_async(scheduled=scheduled)

        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        response = await self.get_run_from_storage_async(
            scenario_result_id=scenario_result_id,
            active_error=snapshot.error,
            queue_position=snapshot.queue_position,
            active_scenario_result_id=snapshot.active_scenario_result_id,
        )
        if response is None:
            raise RuntimeError(f"Scenario run {scenario_result_id} was not found in the database after initialization.")
        return response

    def _finish_preparation(
        self, *, prepare_task: asyncio.Task[_PreparedRun] | None, request: RunScenarioRequest
    ) -> None:
        """Release preparation ownership only after admission or abandoned cleanup."""
        if prepare_task is not None:
            self._preparations.discard(prepare_task)
        if request.scenario_result_id:
            self._preparing_run_ids.discard(request.scenario_result_id)
        self._preparation_lock.release()

    async def _is_run_cancelled_async(self, *, scenario_result_id: str | None) -> bool:
        """
        Report whether a stored run is already in the CANCELLED state.

        Reads the header only. A resumed run can have thousands of linked attack results and
        this runs on the event loop, which the rest of this path works to keep free.

        Args:
            scenario_result_id: The run being resumed, or None for a fresh run.

        Returns:
            bool: True when a stored run with this id is CANCELLED.
        """
        if not scenario_result_id:
            return False
        stored = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        return stored is not None and stored.scenario_run_state == ScenarioRunState.CANCELLED

    async def _cleanup_abandoned_prepare_async(
        self, *, prepare_task: asyncio.Task[_PreparedRun], request: RunScenarioRequest
    ) -> None:
        """Retain the preparation gate and resume reservation through cleanup."""
        try:
            await self._release_abandoned_prepare_async(prepare_task)
        finally:
            self._finish_preparation(prepare_task=prepare_task, request=request)

    def _abandoned_prepare_done(self, task: asyncio.Task[None]) -> None:
        """Retrieve cleanup failures and retain them for runtime shutdown."""
        self._abandoned_prepare_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Could not clean up a cancelled scenario preparation.", exc_info=error)
            if isinstance(error, Exception):
                self._preparation_errors.append(error)

    async def _release_abandoned_prepare_async(self, prepare_task: asyncio.Task[_PreparedRun]) -> None:
        """
        Wait for abandoned preparation and conditionally cancel its stored result.

        Keep scheduler-owned runs intact. If cancellation interrupts preparation
        or admission, terminalize its unowned nonterminal result.

        Args:
            prepare_task: The retained backend-loop preparation.
        """
        try:
            prepared = await prepare_task
        except Exception as error:
            logger.warning("Abandoned scenario preparation failed after the request was cancelled: %s", error)
            return

        scenario_result_id = prepared.scenario._scenario_result_id
        if scenario_result_id in self._active_tasks or any(
            run.scenario_result_id == scenario_result_id for run in self._queued_runs
        ):
            return
        if scenario_result_id:
            await self._memory.try_update_scenario_run_state_async(
                scenario_result_id=scenario_result_id,
                # Admission can persist QUEUED before cancellation prevents the queue append.
                expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS, ScenarioRunState.QUEUED},
                scenario_run_state=ScenarioRunState.CANCELLED,
                error_message="The start request was cancelled before the scenario was admitted to the scheduler.",
            )
        logger.warning("Abandoned scenario preparation completed after the request was cancelled.")

    async def _validate_run_operation_async(self, *, request: RunScenarioRequest) -> RunScenarioRequest:
        """
        Check the saved reference before initializers or provider-backed preparation.

        A fresh run's explicit ID wins over a deprecated ``labels.operation`` name, which is then removed.
        Otherwise the name stays for the scenario to resolve.

        Returns:
            The request carrying its stored run attribution when resuming, or its checked ID.

        Raises:
            LookupError: If a supplied Operation ID does not exist.
            ValueError: If continuation attempts to change attribution.
        """
        if request.scenario_result_id:
            stored = await self._memory.get_scenario_result_header_async(scenario_result_id=request.scenario_result_id)
            if stored is None:
                raise ScenarioRunNotFoundError(f"Scenario run '{request.scenario_result_id}' not found.")
            if request.operation_id is not None and request.operation_id != stored.operation_id:
                raise ValueError("Operation attribution is immutable on continuation")
            return request.model_copy(update={"operation_id": stored.operation_id})
        if request.operation_id is None:
            return request
        await self._memory.require_operation_async(operation_id=request.operation_id)
        # TODO(PyRIT 1.4): Remove with Operation name attribution, which the scenario resolves until then.
        labels = {key: value for key, value in (request.labels or {}).items() if key != "operation"}
        return request.model_copy(update={"labels": labels if request.labels is not None else None})

    async def _prepare_run_async(
        self,
        *,
        request: RunScenarioRequest,
        initial_metadata: Mapping[str, Any] | None = None,
    ) -> _PreparedRun:
        """
        Resolve and initialize the scenario for a run request.

        Args:
            request: The run request with scenario name, target, and options.
            initial_metadata: Internal caller-owned metadata persisted with a
                fresh scenario result.

        Returns:
            _PreparedRun: The initialized scenario and its scoped default.

        Raises:
            ValueError: If scenario, target, initializer, or technique cannot be found.
        """
        scenario_class = await run_legacy_sync_async(
            self._configuration_resolver.resolve_scenario_class, scenario_name=request.scenario_name
        )
        if request.scenario_params:
            reject_non_external_params(
                params=request.scenario_params,
                declared=scenario_class.supported_parameters(),
                owner=request.scenario_name,
            )
        await self._run_initializers_async(request=request)
        declared_names = {parameter.name for parameter in scenario_class.supported_parameters()}
        if "objective_target" in declared_names and not request.target_name:
            raise ValueError(f"Scenario '{request.scenario_name}' requires an objective target.")
        objective_target = (
            self._configuration_resolver.resolve_target(target_name=request.target_name)
            if request.target_name is not None
            else None
        )
        adversarial_target = self._configuration_resolver.resolve_adversarial_target(
            target_name=request.adversarial_target_name
        )
        with override_default_adversarial_target(adversarial_target):
            init_kwargs = await run_legacy_sync_async(
                self._configuration_resolver.resolve_configuration,
                scenario_name=request.scenario_name,
                scenario_class=scenario_class,
                objective_target=objective_target,
                techniques=request.techniques,
                technique_override=request.technique_override,
                datasets=request.dataset_request,
                include_baseline=request.include_baseline,
                max_concurrency=request.max_concurrency
                if "max_concurrency" in declared_names or "max_concurrency" in request.model_fields_set
                else None,
                max_retries=request.max_retries
                if "max_retries" in declared_names or "max_retries" in request.model_fields_set
                else None,
                memory_labels=request.labels,
            )
            init_kwargs["operation_id"] = request.operation_id
            scenario = await self._initialize_scenario_async(
                request=request,
                init_kwargs=init_kwargs,
                initial_metadata=(
                    initial_metadata if initial_metadata is not None else (_INITIAL_METADATA_CONTEXT.get() or {})
                ),
            )
        return _PreparedRun(scenario=scenario, adversarial_target=adversarial_target)

    @staticmethod
    def _validate_initial_metadata(*, initial_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        """
        Copy caller metadata after protecting run-service-owned fields.

        Returns:
            dict[str, Any]: A detached mapping safe to merge into scenario metadata.

        Raises:
            ValueError: If the caller attempts to replace a reserved metadata key.
        """
        metadata = dict(initial_metadata or {})
        reserved = sorted(_RESERVED_INITIAL_METADATA_KEYS.intersection(metadata))
        if reserved:
            raise ValueError(f"Initial scenario metadata cannot set reserved keys: {', '.join(reserved)}")
        return metadata

    async def get_run_async(self, *, scenario_result_id: str) -> ScenarioRunSummary | None:
        """
        Get the current status of a scenario run by querying the database.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioRunSummary if found, None otherwise.
        """
        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        return await self.get_run_from_storage_async(
            scenario_result_id=scenario_result_id,
            active_error=snapshot.error,
            queue_position=snapshot.queue_position,
            active_scenario_result_id=snapshot.active_scenario_result_id,
        )

    async def get_run_from_storage_async(
        self,
        *,
        scenario_result_id: str,
        active_error: str | None,
        queue_position: int | None = None,
        active_scenario_result_id: str | None = None,
    ) -> ScenarioRunSummary | None:
        """
        Build a run summary using database state plus an event-loop snapshot.

        Args:
            scenario_result_id: The scenario result ID.
            active_error: Error copied from the active asyncio task, if any.
            queue_position: Current 1-based waiting position, if queued.
            active_scenario_result_id: Currently executing scenario result ID.

        Returns:
            ScenarioRunSummary | None: The run summary when found.
        """
        return await self._build_response_async(
            scenario_result_id=scenario_result_id,
            active_error=active_error,
            queue_position=queue_position,
            active_scenario_result_id=active_scenario_result_id,
        )

    async def list_runs_async(
        self,
        *,
        scenario_names: Sequence[str] | None = None,
        statuses: Sequence[ScenarioRunState | str] | None = None,
        labels: Mapping[str, str | Sequence[str]] | None = None,
        operation_ids: Sequence[uuid.UUID] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ScenarioRunListResponse:
        """
        List scenario runs by querying the database (most recent first).

        Args:
            scenario_names: Registered or persisted scenario names to match.
            statuses: Run states to match.
            labels: Labels with OR-within-key and AND-across-key semantics.
            operation_ids: Saved Operation IDs to match. Legacy runs that only carry an
                operation label never match.
            limit: Maximum number of runs to return.
            cursor: Opaque cursor from the previous page.

        Returns:
            ScenarioRunListResponse with runs.
        """
        normalized_names = sorted({name.strip() for name in scenario_names or [] if name.strip()})
        normalized_statuses = sorted(
            {
                status.value if isinstance(status, ScenarioRunState) else str(status).strip().upper()
                for status in statuses or []
                if str(status).strip()
            }
        )
        normalized_labels = normalize_label_filters(labels=labels)
        fingerprint_values: dict[str, Any] = {
            "scenario_names": normalized_names,
            "statuses": normalized_statuses,
            "labels": normalized_labels,
        }
        if operation_ids:
            fingerprint_values["operation_ids"] = [str(operation_id) for operation_id in operation_ids]
        fingerprint = fingerprint_filters(filters=fingerprint_values)
        decoded_cursor = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        after = (
            ScenarioHistoryKeysetCursor(
                timestamp=decoded_cursor.timestamp,
                scenario_result_id=decoded_cursor.identifier,
            )
            if decoded_cursor is not None
            else None
        )
        records, aggregates, has_more = await self._memory.get_scenario_run_history_page_async(
            scenario_names=normalized_names,
            statuses=normalized_statuses,
            labels=normalized_labels,
            operation_ids=operation_ids,
            cursor=after,
            limit=limit,
        )
        plans = {record.scenario_result_id: self._parse_history_plan(record=record) for record in records}
        # Memory resolves units against every persisted plan. Runs whose plan this service
        # rejects must fall back to legacy unit identity, which needs a plan-free aggregate.
        unusable_plan_ids = [
            record.scenario_result_id
            for record in records
            if record.plan_atomic_groups is not None and plans[record.scenario_result_id] is None
        ]
        if unusable_plan_ids:
            aggregates = {
                **aggregates,
                **(await self._memory.get_scenario_history_aggregates_async(scenario_result_ids=unusable_plan_ids)),
            }
        aggregates = await self._recount_with_sdk_statistics_async(aggregates=aggregates, plans=plans)
        linked_ids = {record.operation_id for record in records if record.operation_id is not None}
        operation_names = (
            {
                operation.id: operation.name
                for operation in await self._memory.get_operations_async(operation_ids=list(linked_ids))
            }
            if linked_ids
            else {}
        )
        items = [
            self._build_history_summary(
                record=record,
                atomic_groups=plans[record.scenario_result_id],
                aggregate=aggregates.get(record.scenario_result_id)
                or ScenarioHistoryAggregate.empty(scenario_result_id=record.scenario_result_id),
                operation_name=operation_names.get(record.operation_id)
                if record.operation_id is not None
                else record.labels.get("operation"),
            )
            for record in records
        ]
        next_cursor = (
            encode_keyset_cursor(
                timestamp=records[-1].created_at,
                identifier=records[-1].scenario_result_id,
                fingerprint=fingerprint,
            )
            if has_more and records
            else None
        )
        return ScenarioRunListResponse(
            items=items,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
        )

    async def cancel_run_async(self, *, scenario_result_id: str) -> ScenarioRunSummary | None:
        """
        Cancel a running scenario.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            Updated ScenarioRunSummary if found, None if not found.

        Raises:
            ValueError: If the run is already in a terminal state or not active.
        """
        header = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        if header is None:
            return None

        db_status = header.scenario_run_state
        if self._is_terminal_state(db_status):
            raise ValueError(f"Cannot cancel run in '{db_status}' state.")

        task: asyncio.Task[None] | None = None
        async with self._scheduler_lock:
            queued = next(
                (run for run in self._queued_runs if run.scenario_result_id == scenario_result_id),
                None,
            )
            if queued is not None:
                await self._memory.try_update_scenario_run_state_async(
                    scenario_result_id=scenario_result_id,
                    expected_states={ScenarioRunState.CREATED, ScenarioRunState.QUEUED},
                    scenario_run_state=ScenarioRunState.CANCELLED,
                    error_message=_USER_CANCELLATION_REASON,
                    error_type="CancelledError",
                )
                self._queued_runs.remove(queued)
                self._queue_revision += 1
            elif self._active_scenario_result_id == scenario_result_id:
                active = self._active_tasks[scenario_result_id]
                active.cancellation_state = ScenarioRunState.CANCELLED
                active.cancellation_reason = _USER_CANCELLATION_REASON
                active.cancellation_error_type = "CancelledError"
                task = active.task
            else:
                latest = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
                if latest is not None and self._is_terminal_state(latest.scenario_run_state):
                    raise ValueError(f"Cannot cancel run in '{latest.scenario_run_state}' state.")
                await self._memory.try_update_scenario_run_state_async(
                    scenario_result_id=scenario_result_id,
                    expected_states={
                        ScenarioRunState.CREATED,
                        ScenarioRunState.QUEUED,
                        ScenarioRunState.IN_PROGRESS,
                    },
                    scenario_run_state=ScenarioRunState.CANCELLED,
                    error_message=_USER_CANCELLATION_REASON,
                    error_type="CancelledError",
                )

        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        result = await self.get_run_from_storage_async(
            scenario_result_id=scenario_result_id,
            active_error=snapshot.error,
            queue_position=snapshot.queue_position,
            active_scenario_result_id=snapshot.active_scenario_result_id,
        )
        if result is not None and result.status != ScenarioRunState.CANCELLED:
            raise ValueError(f"Cannot cancel run in '{result.status}' state.")
        return result

    def get_queue_snapshot(self) -> ScenarioQueueSnapshot:
        """
        Return the current in-process FIFO scheduler state.

        Returns:
            ScenarioQueueSnapshot: Active run and ordered waiting runs.
        """
        snapshot_at = datetime.now(UTC)
        active = None
        if self._active_scenario_result_id is not None:
            active_run = self._active_tasks.get(self._active_scenario_result_id)
            if active_run is not None:
                active = self._build_queue_entry(run=active_run, state=ScenarioRunState.IN_PROGRESS)
        queued = [
            self._build_queue_entry(run=run, state=ScenarioRunState.QUEUED, position=position)
            for position, run in enumerate(self._queued_runs, start=1)
        ]
        return ScenarioQueueSnapshot(
            revision=self._queue_revision,
            snapshot_at=snapshot_at,
            active=active,
            queued=queued,
        )

    async def reconcile_interrupted_runs_async(self) -> int:
        """
        Mark scheduler-managed local rows failed when executable objects were lost.

        Shared and unknown memory backends are intentionally non-destructive because
        another process may still own their runs. File-backed SQLite assumes one
        scheduler process has exclusive ownership of that database file.

        Returns:
            int: Number of reconciled rows.
        """
        if not isinstance(self._memory, SQLiteMemory):
            logger.info(
                "Skipping interrupted Scenario run reconciliation for shared or unsupported %s memory.",
                type(self._memory).__name__,
            )
            return 0

        states = (ScenarioRunState.CREATED, ScenarioRunState.QUEUED, ScenarioRunState.IN_PROGRESS)
        after_id = None
        reconciled = 0
        while True:
            interrupted, has_more = await self._memory.get_scenario_run_state_page_async(
                states=states, after_id=after_id, limit=500
            )
            for result in interrupted:
                header = await self._memory.get_scenario_result_header_async(
                    scenario_result_id=result.scenario_result_id
                )
                if header is None or header.metadata.get(_SCHEDULER_METADATA_KEY) != _SCHEDULER_METADATA_VALUE:
                    continue
                await self._memory.update_scenario_run_state_async(
                    scenario_result_id=result.scenario_result_id,
                    scenario_run_state=ScenarioRunState.FAILED,
                    error_message=_RESTART_INTERRUPTION_REASON,
                    error_type=_INTERRUPTED_ERROR_TYPE,
                )
                reconciled += 1
            if not has_more:
                return reconciled
            if not interrupted:
                raise RuntimeError(
                    "Scenario run state projection reported another page without returning a cursor row."
                )
            after_id = interrupted[-1].scenario_result_id

    async def shutdown_async(self) -> None:
        """Stop scheduling and terminalize active and queued runs for process shutdown."""
        self.stop_admission()
        task: asyncio.Task[None] | None = None
        retry_tasks: list[asyncio.Task[None]] = []
        errors: list[Exception] = []
        async with self._launch_lock:
            async with self._scheduler_lock:
                retry_tasks = list(self._handoff_retry_tasks)
                queued = list(self._queued_runs)
                self._queued_runs.clear()
                if queued:
                    self._queue_revision += 1
                for run in queued:
                    try:
                        await self._memory.update_scenario_run_state_async(
                            scenario_result_id=run.scenario_result_id,
                            scenario_run_state=ScenarioRunState.FAILED,
                            error_message=_SHUTDOWN_INTERRUPTION_REASON,
                            error_type=_INTERRUPTED_ERROR_TYPE,
                        )
                    except Exception as exc:
                        errors.append(exc)
                if self._active_scenario_result_id is not None:
                    active = self._active_tasks[self._active_scenario_result_id]
                    active.cancellation_state = ScenarioRunState.FAILED
                    active.cancellation_reason = _SHUTDOWN_INTERRUPTION_REASON
                    active.cancellation_error_type = _INTERRUPTED_ERROR_TYPE
                    task = active.task
                    if task is None or task.done():
                        try:
                            await self._memory.update_scenario_run_state_async(
                                scenario_result_id=active.scenario_result_id,
                                scenario_run_state=ScenarioRunState.FAILED,
                                error_message=_SHUTDOWN_INTERRUPTION_REASON,
                                error_type=_INTERRUPTED_ERROR_TYPE,
                            )
                        except Exception as exc:
                            errors.append(exc)
                        self._active_scenario_result_id = None
                        self._release_completed_task(scenario_result_id=active.scenario_result_id)
                        self._queue_revision += 1
        if self._abandoned_prepare_tasks:
            await asyncio.gather(*self._abandoned_prepare_tasks, return_exceptions=True)
        errors.extend(self._preparation_errors)
        self._preparation_errors.clear()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                errors.append(exc)
        for retry_task in retry_tasks:
            retry_task.cancel()
        if retry_tasks:
            await asyncio.gather(*retry_tasks, return_exceptions=True)
        if errors:
            raise ExceptionGroup("Failed to persist one or more scenario shutdown transitions.", errors)

    async def _enqueue_run_async(self, *, scheduled: _ActiveTask) -> None:
        """Atomically enqueue a persisted initialized run or start it immediately."""
        async with self._scheduler_lock:
            if self._stopping:
                await self._memory.try_update_scenario_run_state_async(
                    scenario_result_id=scheduled.scenario_result_id,
                    expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
                    scenario_run_state=ScenarioRunState.FAILED,
                    error_message=_SHUTDOWN_INTERRUPTION_REASON,
                    error_type=_INTERRUPTED_ERROR_TYPE,
                )
                return
            scheduled_ids = {
                *(run.scenario_result_id for run in self._queued_runs),
                *self._active_tasks.keys(),
            }
            if scheduled.scenario_result_id in scheduled_ids:
                raise ValueError(f"Scenario run '{scheduled.scenario_result_id}' is already scheduled.")
            self._terminal_errors.pop(scheduled.scenario_result_id, None)
            if self._active_scenario_result_id is None:
                await self._start_scheduled_run_locked_async(scheduled=scheduled)
                return
            await self._memory.update_scenario_run_state_and_metadata_fields_async(
                scenario_result_id=scheduled.scenario_result_id,
                scenario_run_state=ScenarioRunState.QUEUED,
                metadata_fields={
                    _SCHEDULER_METADATA_KEY: _SCHEDULER_METADATA_VALUE,
                    SCENARIO_RUN_STARTED_AT_METADATA_KEY: None,
                },
            )
            self._queued_runs.append(scheduled)
            self._queue_revision += 1

    async def _start_scheduled_run_locked_async(self, *, scheduled: _ActiveTask) -> None:
        """Start one run while the scheduler lock guarantees exclusive ownership."""
        scheduled.started_at = datetime.now(UTC)
        await self._memory.update_scenario_run_state_and_metadata_fields_async(
            scenario_result_id=scheduled.scenario_result_id,
            scenario_run_state=ScenarioRunState.IN_PROGRESS,
            metadata_fields={
                _SCHEDULER_METADATA_KEY: _SCHEDULER_METADATA_VALUE,
                SCENARIO_RUN_STARTED_AT_METADATA_KEY: scheduled.started_at.isoformat(),
            },
        )
        self._active_scenario_result_id = scheduled.scenario_result_id
        self._active_tasks[scheduled.scenario_result_id] = scheduled
        scheduled.task = asyncio.create_task(self._execute_run_async(scenario_result_id=scheduled.scenario_result_id))
        self._queue_revision += 1

    async def _handoff_scheduler_async(self, *, scenario_result_id: str) -> None:
        """Release one terminal active run and start the next valid queued run once."""
        async with self._scheduler_lock:
            if self._active_scenario_result_id != scenario_result_id:
                return
            if self._stopping:
                self._active_scenario_result_id = None
                self._release_completed_task(scenario_result_id=scenario_result_id)
                self._queue_revision += 1
                return
            while self._queued_runs:
                next_run = self._queued_runs[0]
                persisted = await self._memory.get_scenario_result_header_async(
                    scenario_result_id=next_run.scenario_result_id
                )
                if persisted is None or persisted.scenario_run_state != ScenarioRunState.QUEUED:
                    self._queued_runs.popleft()
                    self._queue_revision += 1
                    continue
                await self._start_scheduled_run_locked_async(scheduled=next_run)
                self._queued_runs.popleft()
                self._release_completed_task(scenario_result_id=scenario_result_id)
                return
            self._active_scenario_result_id = None
            self._release_completed_task(scenario_result_id=scenario_result_id)
            self._queue_revision += 1

    def _release_completed_task(self, *, scenario_result_id: str) -> None:
        """Release executable state while retaining bounded terminal error evidence."""
        completed = self._active_tasks.pop(scenario_result_id, None)
        if completed is None or completed.error is None:
            return
        self._terminal_errors[scenario_result_id] = completed.error
        self._terminal_errors.move_to_end(scenario_result_id)
        while len(self._terminal_errors) > _MAX_TERMINAL_ERRORS:
            self._terminal_errors.popitem(last=False)

    def _schedule_handoff_retry(self, *, scenario_result_id: str) -> None:
        """Retry a failed terminal handoff without permitting another active run."""
        retry_task = asyncio.create_task(self._retry_handoff_async(scenario_result_id=scenario_result_id))
        self._handoff_retry_tasks.add(retry_task)
        retry_task.add_done_callback(self._handoff_retry_tasks.discard)

    def _schedule_terminalization_retry(self, *, active: _ActiveTask) -> None:
        """Retry cancellation persistence before releasing the active slot."""
        retry_task = asyncio.create_task(self._retry_terminalization_async(active=active))
        self._handoff_retry_tasks.add(retry_task)
        retry_task.add_done_callback(self._handoff_retry_tasks.discard)

    def _can_retry_active_run(self, *, scenario_result_id: str) -> bool:
        """Return whether retry work may continue for the active run."""
        return not self._stopping and self._active_scenario_result_id == scenario_result_id

    async def _retry_handoff_async(self, *, scenario_result_id: str) -> None:
        """Retry scheduler handoff with bounded exponential delay until it succeeds or shutdown begins."""
        delay = _SCHEDULER_RETRY_INITIAL_SECONDS
        while self._can_retry_active_run(scenario_result_id=scenario_result_id):
            await asyncio.sleep(delay)
            try:
                await self._handoff_scheduler_async(scenario_result_id=scenario_result_id)
            except Exception:
                logger.exception("Scenario scheduler handoff retry failed for %s.", scenario_result_id)
                delay = min(delay * 2, _SCHEDULER_RETRY_MAX_SECONDS)
            else:
                return

    async def _retry_terminalization_async(self, *, active: _ActiveTask) -> None:
        """Retry a failed cancellation transition, then perform the terminal handoff."""
        delay = _SCHEDULER_RETRY_INITIAL_SECONDS
        while self._can_retry_active_run(scenario_result_id=active.scenario_result_id):
            await asyncio.sleep(delay)
            try:
                async with self._scheduler_lock:
                    if not self._can_retry_active_run(scenario_result_id=active.scenario_result_id):
                        return
                    await self._memory.try_update_scenario_run_state_async(
                        scenario_result_id=active.scenario_result_id,
                        expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
                        scenario_run_state=active.cancellation_state,
                        error_message=active.cancellation_reason,
                        error_type=active.cancellation_error_type,
                    )
                    if not active.retain_error_on_terminalization:
                        active.error = None
                await self._handoff_scheduler_async(scenario_result_id=active.scenario_result_id)
            except Exception:
                logger.exception(
                    "Scenario terminal transition retry failed for %s.",
                    active.scenario_result_id,
                )
                delay = min(delay * 2, _SCHEDULER_RETRY_MAX_SECONDS)
            else:
                return

    async def _complete_handoff_async(self, *, scenario_result_id: str) -> None:
        """Complete terminal handoff even if the execution task is cancelled while waiting for the scheduler lock."""
        handoff_task = asyncio.create_task(self._handoff_scheduler_async(scenario_result_id=scenario_result_id))
        self._handoff_retry_tasks.add(handoff_task)
        handoff_task.add_done_callback(self._handoff_retry_tasks.discard)
        try:
            await asyncio.shield(handoff_task)
        except asyncio.CancelledError:
            try:
                await handoff_task
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Scenario scheduler handoff failed for %s; retrying.", scenario_result_id)
                if not self._stopping:
                    self._schedule_handoff_retry(scenario_result_id=scenario_result_id)
        except Exception:
            logger.exception("Scenario scheduler handoff failed for %s; retrying.", scenario_result_id)
            if not self._stopping:
                self._schedule_handoff_retry(scenario_result_id=scenario_result_id)

    @staticmethod
    def _build_queue_entry(
        *,
        run: _ActiveTask,
        state: ScenarioRunState,
        position: int | None = None,
    ) -> ScenarioQueueEntry:
        """
        Map event-loop scheduler state to the canonical queue DTO.

        Returns:
            ScenarioQueueEntry: Canonical active or queued entry.
        """
        if run.created_at is None or run.enqueued_at is None:
            raise RuntimeError(f"Scenario run '{run.scenario_result_id}' has incomplete queue timestamps.")
        return ScenarioQueueEntry(
            scenario_result_id=run.scenario_result_id,
            scenario_name=run.scenario_name,
            scenario_registry_name=run.scenario_registry_name,
            created_at=run.created_at,
            enqueued_at=run.enqueued_at,
            started_at=run.started_at,
            state=state,
            position=position,
        )

    @staticmethod
    def _is_terminal_state(state: ScenarioRunState) -> bool:
        """Return whether a scenario state is terminal."""
        return state in (ScenarioRunState.COMPLETED, ScenarioRunState.FAILED, ScenarioRunState.CANCELLED)

    async def _run_initializers_async(self, *, request: RunScenarioRequest) -> None:
        """
        Validate and execute initializers specified in the request.

        Args:
            request: The run request containing initializer names and args.

        Raises:
            ValueError: If an initializer name is not found in the registry.
        """
        if not request.initializers:
            return

        initializer_registry = await run_legacy_sync_async(InitializerRegistry.get_registry_singleton)
        for initializer_name in request.initializers:
            initializer_params = (request.initializer_args or {}).get(initializer_name)
            try:
                instance = await run_legacy_sync_async(
                    initializer_registry.create_and_configure, initializer_name, initializer_params=initializer_params
                )
            except KeyError as e:
                raise ValueError(f"Initializer not found: {e}") from None
            await instance.initialize_async()

    async def _initialize_scenario_async(
        self,
        *,
        request: RunScenarioRequest,
        init_kwargs: dict[str, Any],
        initial_metadata: Mapping[str, Any] | None = None,
    ) -> Scenario:
        """
        Build and initialize the scenario via the registry.

        Delegates the full create + set-parameters + initialize lifecycle to
        ``ScenarioRegistry.create_and_initialize_async`` so the registry owns
        scenario creation and initialization. The run-specific common parameters
        are resolved before this method and forwarded as ``init_kwargs``.
        The saved launch record retains effective numeric values so resume does
        not depend on current parameter defaults.

        Args:
            request: The run request (for scenario_name, scenario_params, and
                scenario_result_id).
            init_kwargs: The resolved common parameters to pass to
                scenario.initialize_async.
            initial_metadata: Internal caller-owned metadata persisted with a
                fresh scenario result.

        Returns:
            The fully initialized Scenario instance ready for run_async.
        """
        scenario_registry = ScenarioRegistry.get_registry_singleton()
        scenario_class = scenario_registry.get_class(request.scenario_name)
        declared = scenario_class.supported_parameters()
        declared_names = {parameter.name for parameter in declared}
        launch_request = request.model_dump(mode="json", include=set(_LAUNCH_REQUEST_FIELDS))
        policy_parameter = next(
            (parameter for parameter in declared if parameter.name == "incompatible_technique_policy"),
            None,
        )
        policy = (
            resolve_declared_params(
                declared=[policy_parameter],
                raw_args={
                    "incompatible_technique_policy": (request.scenario_params or {}).get(
                        "incompatible_technique_policy"
                    )
                },
                owner=f"Scenario '{request.scenario_name}'",
            )["incompatible_technique_policy"]
            if policy_parameter is not None
            else None
        )
        if policy_parameter is not None:
            launch_request["incompatible_technique_policy"] = (
                policy or scenario_class.INCOMPATIBLE_TECHNIQUE_POLICY
            ).value
        numeric_parameters = [
            parameter for parameter in declared if parameter.name in {"max_concurrency", "max_retries"}
        ]
        merged_args = {**(request.scenario_params or {}), **init_kwargs}
        resolved_numeric = resolve_declared_params(
            declared=numeric_parameters,
            raw_args={parameter.name: merged_args.get(parameter.name) for parameter in numeric_parameters},
            owner=f"Scenario '{request.scenario_name}'",
        )
        for name in ("max_concurrency", "max_retries"):
            if name in declared_names:
                launch_request[name] = resolved_numeric[name]
            elif name not in request.model_fields_set:
                launch_request[name] = None
        if launch_request["include_baseline"] is None and "include_baseline" in declared_names:
            baseline_parameter = next(
                (parameter for parameter in declared if parameter.name == "include_baseline"),
                None,
            )
            baseline = (
                resolve_declared_params(
                    declared=[baseline_parameter],
                    raw_args={"include_baseline": (request.scenario_params or {}).get("include_baseline")},
                    owner=f"Scenario '{request.scenario_name}'",
                )["include_baseline"]
                if baseline_parameter is not None
                else None
            )
            launch_request["include_baseline"] = (
                baseline if baseline is not None else scenario_class.BASELINE_ATTACK_POLICY.value == "enabled"
            )
        return await scenario_registry.create_and_initialize_async(
            request.scenario_name,
            scenario_params=request.scenario_params or {},
            scenario_result_id=request.scenario_result_id or None,
            constructor_kwargs=self._configuration_resolver.resolve_constructor_kwargs(
                scenario_class=scenario_class, objective_scorer=request.objective_scorer
            ),
            initial_metadata={
                **(initial_metadata or {}),
                _SCHEDULER_METADATA_KEY: _SCHEDULER_METADATA_VALUE,
                _LAUNCH_REQUEST_METADATA_KEY: launch_request,
            },
            **init_kwargs,
        )

    async def _execute_run_async(self, *, scenario_result_id: str) -> None:
        """
        Execute a scenario run (background task entry point).

        Only calls scenario.run_async on the already-initialized scenario.

        Terminal handoff releases executable objects. Bounded error evidence is
        retained separately for later status polling.

        Args:
            scenario_result_id: The scenario result ID for this run.
        """
        active = self._active_tasks[scenario_result_id]
        assert active.scenario is not None
        handoff_ready = True

        try:
            with override_default_adversarial_target(active.adversarial_target):
                await active.scenario.run_async()

        except asyncio.CancelledError:
            try:
                await self._memory.try_update_scenario_run_state_async(
                    scenario_result_id=scenario_result_id,
                    expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
                    scenario_run_state=active.cancellation_state,
                    error_message=active.cancellation_reason,
                    error_type=active.cancellation_error_type,
                )
            except Exception as exc:
                handoff_ready = False
                active.error = str(exc)
                if not self._stopping:
                    self._schedule_terminalization_retry(active=active)
                raise
            logger.info("Scenario run %s stopped in state %s.", scenario_result_id, active.cancellation_state.value)

        except Exception as e:
            active.error = str(e)
            active.cancellation_state = ScenarioRunState.FAILED
            active.cancellation_reason = str(e)
            active.cancellation_error_type = type(e).__name__
            active.retain_error_on_terminalization = True
            try:
                await self._memory.try_update_scenario_run_state_async(
                    scenario_result_id=scenario_result_id,
                    expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
                    scenario_run_state=ScenarioRunState.FAILED,
                    error_message=str(e),
                    error_type=type(e).__name__,
                )
            except Exception:
                handoff_ready = False
                if not self._stopping:
                    self._schedule_terminalization_retry(active=active)
                logger.exception("Failed to persist terminal state for scenario run %s.", scenario_result_id)
            logger.exception(f"Scenario run {scenario_result_id} failed: {e}")

        finally:
            if handoff_ready:
                await self._complete_handoff_async(scenario_result_id=scenario_result_id)

    async def _build_response_async(
        self,
        *,
        scenario_result_id: str,
        active_error: str | None,
        queue_position: int | None,
        active_scenario_result_id: str | None,
    ) -> ScenarioRunSummary | None:
        """
        Build a ScenarioRunResponse by querying the database and merging active task state.

        Args:
            scenario_result_id: The scenario result ID.
            active_error: Error copied from the active asyncio task, if any.
            queue_position: Current 1-based waiting position, if queued.
            active_scenario_result_id: Currently executing scenario result ID.

        Returns:
            ScenarioRunResponse if found in the database, None otherwise.
        """
        results = await self._memory.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
        if not results:
            return None
        return await self._build_response_from_db_async(
            scenario_result=results[0],
            active_error=active_error,
            queue_position=queue_position,
            active_scenario_result_id=active_scenario_result_id,
        )

    async def _build_response_from_db_async(
        self,
        *,
        scenario_result: ScenarioResult,
        active_error: str | None = None,
        queue_position: int | None = None,
        active_scenario_result_id: str | None = None,
    ) -> ScenarioRunSummary:
        error_results = (
            await self._memory.get_attack_results_async(
                scenario_result_id=str(scenario_result.id),
                outcome=AttackOutcome.ERROR,
            )
            if not scenario_result.error_message and scenario_result.scenario_run_state == ScenarioRunState.FAILED
            else []
        )
        return self._build_run_summary(
            scenario_result=scenario_result,
            error_results=error_results,
            active_error=active_error,
            queue_position=queue_position,
            active_scenario_result_id=active_scenario_result_id,
        )

    def _build_run_summary(
        self,
        *,
        scenario_result: ScenarioResult,
        error_results: Sequence[AttackResult],
        active_error: str | None = None,
        queue_position: int | None = None,
        active_scenario_result_id: str | None = None,
    ) -> ScenarioRunSummary:
        """
        Build a ScenarioRunResponse from a database ScenarioResult, merged with active task info.

        Args:
            scenario_result: A ScenarioResult retrieved from CentralMemory.
            error_results: Failed attack results used when the scenario has no stored error message.
            active_error: Error copied from the active asyncio task, if any.
            queue_position: Current 1-based waiting position, if queued.
            active_scenario_result_id: Currently executing scenario result ID.

        Returns:
            The API response model.
        """
        scenario_result_id = str(scenario_result.id)
        status = scenario_result.scenario_run_state

        # Primary source: DB-persisted error fields
        error = scenario_result.error_message
        error_type = scenario_result.error_type

        # Historical attack errors remain after a successful resume; only use them for failed runs.
        if not error and status == ScenarioRunState.FAILED:
            error_ars = error_results
            if error_ars:
                error = error_ars[0].error_message
                error_type = error_ars[0].error_type

        # Fallback: in-memory error for in-flight tasks where DB hasn't been updated yet
        if not error:
            error = active_error

        terminal = status in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        try:
            plan = self._load_run_plan(scenario_result=scenario_result)
        except (ValidationError, ValueError):
            logger.warning(
                "Scenario run %s has invalid persisted plan metadata; using legacy run detail fields.",
                scenario_result_id,
            )
            plan = None

        # Build result fields from DB (always computed so in-progress runs show progress)
        overall_counts = self._progress_read_model.calculate_progress_counts(
            scenario_result=scenario_result,
            plan=plan,
        )
        total_attacks = overall_counts.planned if overall_counts.planned is not None else overall_counts.completed
        completed_attacks = overall_counts.completed
        objective_achieved_rate = overall_counts.success_percentage or 0
        successful_attacks = overall_counts.succeeded
        techniques_used = (
            list(dict.fromkeys(group.technique_name or group.display_group for group in plan.atomic_groups))
            if plan is not None
            else scenario_result.get_techniques_used()
        )
        target, datasets_used, scenario_parameters = self._safe_run_metadata(
            scenario_identifier=getattr(scenario_result, "scenario_identifier", None)
        )

        # Surface per-attack errors and retry pressure regardless of overall run status:
        # a COMPLETED scenario can still hide errored objectives or rate-limit retries.
        failed_attacks: list[AttackErrorSummary] = []
        attack_retries: list[AttackRetrySummary] = []
        overload_events: deque[Any] = deque(maxlen=_MAX_OVERLOAD_EVENTS)
        execution_ids = select_execution_attack_result_ids(scenario_result, plan=plan, use_saved_plan=False)
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
                if str(attack_result.attack_result_id) not in execution_ids:
                    continue
                retries = getattr(attack_result, "total_retries", 0)

                retry_events = getattr(attack_result, "retry_events", None)
                if isinstance(retry_events, list) and retry_events:
                    overload_events.extend(retry_events)
                    truncated_retries = retry_events[:100]
                    retries_truncated = len(retry_events) > 100
                    total_r = max(0, retries) if isinstance(retries, int) else len(retry_events)
                    attack_retries.append(
                        AttackRetrySummary(
                            attack_result_id=str(attack_result.attack_result_id),
                            atomic_attack_name=atomic_attack_name,
                            total_retries=total_r,
                            retries=truncated_retries,
                            retries_truncated=retries_truncated,
                        )
                    )

                if attack_result.outcome == AttackOutcome.ERROR:
                    failed_attacks.append(
                        AttackErrorSummary(
                            atomic_attack_name=atomic_attack_name,
                            objective=attack_result.objective,
                            error_type=attack_result.error_type,
                            error_message=attack_result.error_message,
                            total_retries=max(0, retries) if isinstance(retries, int) else 0,
                        )
                    )
        total_retries = overall_counts.retries

        updated_at = scenario_result.creation_time
        if terminal and scenario_result.completion_time is not None:
            updated_at = scenario_result.completion_time

        return ScenarioRunSummary(
            scenario_result_id=scenario_result_id,
            scenario_name=scenario_result.scenario_name,
            scenario_registry_name=plan.scenario_registry_name if plan else None,
            scenario_version=scenario_result.scenario_version,
            status=status,
            created_at=scenario_result.creation_time,
            started_at=self._load_started_at(scenario_result=scenario_result),
            updated_at=updated_at,
            error=error,
            error_type=error_type,
            techniques_used=techniques_used,
            total_attacks=total_attacks,
            completed_attacks=completed_attacks,
            objective_achieved_rate=objective_achieved_rate,
            failed_attacks=failed_attacks,
            attack_retries=attack_retries,
            total_retries=total_retries,
            labels=scenario_result.labels,
            completed_at=scenario_result.completion_time if terminal else None,
            pyrit_version=(
                scenario_result.pyrit_version
                if isinstance(getattr(scenario_result, "pyrit_version", None), str)
                else None
            ),
            target=target,
            datasets_used=datasets_used,
            scenario_parameters=scenario_parameters,
            planned_total_available=plan is not None,
            successful_attacks=successful_attacks,
            error_attacks=len(failed_attacks),
            queue_position=queue_position,
            active_scenario_result_id=active_scenario_result_id,
            overload_summaries=self._build_overload_summaries(retry_events=overload_events),
            producer_counts=overall_counts.producer_counts,
        )

    @staticmethod
    def _parse_history_plan(*, record: ScenarioHistoryRunRecord) -> list[ScenarioRunPlanAtomicGroup] | None:
        """
        Validate the compact persisted run plan projected onto one history row.

        Returns:
            list[ScenarioRunPlanAtomicGroup] | None: Planned atomic groups, or None when the
                run has no plan or the persisted plan cannot identify units unambiguously.
        """
        if record.plan_atomic_groups is None:
            return None
        try:
            raw_atomic_groups = (
                json.loads(record.plan_atomic_groups)
                if isinstance(record.plan_atomic_groups, str)
                else record.plan_atomic_groups
            )
            atomic_groups = _HISTORY_ATOMIC_GROUPS_ADAPTER.validate_python(raw_atomic_groups)
            group_ids = [group.id for group in atomic_groups]
            if len(group_ids) != len(set(group_ids)):
                raise ValueError("duplicate atomic group IDs")
            raw_seed_map = (
                json.loads(record.plan_seed_id_map)
                if isinstance(record.plan_seed_id_map, str)
                else record.plan_seed_id_map or []
            )
            seed_hash_by_id: dict[str, str] = {}
            for seed in _HISTORY_SEED_ID_MAP_ADAPTER.validate_python(raw_seed_map):
                seed_id = seed.get("id")
                objective_sha256 = seed.get("objective_sha256")
                if not seed_id or not objective_sha256:
                    raise ValueError("seed projection is missing required identity fields")
                previous_hash = seed_hash_by_id.get(seed_id)
                if previous_hash is not None and previous_hash != objective_sha256:
                    raise ValueError("conflicting objective hashes for seed group")
                seed_hash_by_id[seed_id] = objective_sha256
            for group in atomic_groups:
                if len(group.seed_group_ids) != len(set(group.seed_group_ids)):
                    raise ValueError("duplicate seed group IDs within atomic group")
                if set(group.seed_group_ids) - seed_hash_by_id.keys():
                    raise ValueError("atomic group references unknown seed groups")
            return atomic_groups
        except (json.JSONDecodeError, ValidationError, ValueError):
            logger.warning(
                "Scenario run %s has an incomplete persisted plan; using legacy history totals.",
                record.scenario_result_id,
            )
            return None

    async def _recount_with_sdk_statistics_async(
        self,
        *,
        aggregates: dict[str, ScenarioHistoryAggregate],
        plans: Mapping[str, list[ScenarioRunPlanAtomicGroup] | None],
        max_deltas: int | None = None,
    ) -> dict[str, ScenarioHistoryAggregate]:
        """
        Recount runs the SQL aggregate can't resolve with the shared statistics.

        Attempts identified only by their atomic identifier's seeds need the logical seed group derived
        from those seeds, which only ``pyrit.analytics.scenario_statistics`` can do. Those runs are loaded
        and counted the same way as run detail, so the history list never disagrees with it.

        Returns:
            dict[str, ScenarioHistoryAggregate]: The aggregates, with flagged runs recounted.
        """
        run_ids = [run_id for run_id, aggregate in aggregates.items() if aggregate.needs_sdk_statistics]
        if not run_ids:
            return aggregates
        recounted = dict(aggregates)
        for run_id in run_ids:
            cursor: AttackResultKeysetCursor | None = None
            all_summaries: list[ScenarioAttemptSeedSummary] = []
            while True:
                remaining_limit = min(500, max_deltas - len(all_summaries)) if max_deltas is not None else 500
                if remaining_limit <= 0:
                    break
                page, has_more = await self._memory.get_scenario_recount_summaries_async(
                    scenario_result_id=run_id,
                    cursor=cursor,
                    limit=remaining_limit,
                )
                all_summaries.extend(page)
                if not has_more or not page or (max_deltas is not None and len(all_summaries) >= max_deltas):
                    break
                cursor = AttackResultKeysetCursor(
                    timestamp=page[-1].timestamp, attack_result_id=page[-1].attack_result_id
                )

            saved_plan: ScenarioRunPlan | None = None
            header = await self._memory.get_scenario_result_header_async(scenario_result_id=run_id)
            if header is not None:
                try:
                    saved_plan = self._load_run_plan(scenario_result=header)
                except (ValidationError, ValueError):
                    logger.warning(
                        "Scenario run %s has an invalid saved run plan; recounting as legacy run.",
                        run_id,
                    )
            plan_lookup = ScenarioPlanLookup.from_plan(plan=saved_plan)
            attempts: list[ScenarioAttempt] = []
            for summary in all_summaries:
                atomic_name = str(summary.attribution_data.get("parent_collection") or "")
                eval_hash = summary.attribution_data.get("parent_eval_hash")
                attr_seed = summary.attribution_data.get("seed_group_id")
                typed_identifier = (
                    AtomicAttackIdentifier.from_component_identifier(summary.atomic_attack_identifier)
                    if isinstance(summary.atomic_attack_identifier, ComponentIdentifier)
                    else None
                )
                unit = resolve_execution_unit(
                    atomic_attack_name=atomic_name,
                    technique_eval_hash=str(eval_hash) if eval_hash is not None else None,
                    attributed_seed_group_id=str(attr_seed) if attr_seed else None,
                    atomic_attack_identifier=typed_identifier,
                    objective=summary.objective,
                    objective_sha256=summary.objective_sha256,
                    plan_lookup=plan_lookup,
                )
                res_metadata = AttackResultMetadata.from_metadata(metadata=summary.attribution_data)
                attempts.append(
                    ScenarioAttempt(
                        unit=unit,
                        atomic_attack_name=atomic_name,
                        outcome=summary.outcome,
                        timestamp=summary.timestamp,
                        attempt_id=summary.attack_result_id,
                        total_retries=summary.total_retries,
                        result_role=res_metadata.result_role,
                        attempt_index=res_metadata.attempt_index,
                        child_attack_result_ids=summary.child_attack_result_ids,
                    )
                )
            attempts.sort(key=lambda a: (a.timestamp, a.attempt_id))
            attempts_by_unit: dict[ScenarioExecutionUnit, list[ScenarioAttempt]] = {}
            for a in attempts:
                attempts_by_unit.setdefault(a.unit, []).append(a)

            if saved_plan is not None:
                planned_units = list(
                    dict.fromkeys(
                        ScenarioExecutionUnit(atomic_group_id=group.id, seed_group_id=seed_id)
                        for group in saved_plan.atomic_groups
                        for seed_id in group.seed_group_ids
                    )
                )
                counted_units = planned_units
                planned = len(planned_units)
            else:
                counted_units = list(attempts_by_unit.keys())
                planned = None

            overall = count_execution_units(
                units=counted_units,
                attempts_by_unit=attempts_by_unit,
                planned=planned,
                producer_attempts=attempts,
            )

            recounted_group_counts: dict[str, ScenarioProgressCounts] = {}
            if saved_plan is not None:
                for group in saved_plan.atomic_groups:
                    g_units = [
                        ScenarioExecutionUnit(atomic_group_id=group.id, seed_group_id=seed_id)
                        for seed_id in group.seed_group_ids
                    ]
                    g_producer_attempts = [a for a in attempts if a.unit.atomic_group_id == group.id]
                    g_counts = count_execution_units(
                        units=g_units,
                        attempts_by_unit=attempts_by_unit,
                        planned=len(g_units),
                        producer_attempts=g_producer_attempts,
                    )
                    recounted_group_counts[group.id] = g_counts

                unplanned_group_ids = {a.unit.atomic_group_id for a in attempts} - {
                    g.id for g in saved_plan.atomic_groups
                }
                for gid in unplanned_group_ids:
                    g_units = [u for u in attempts_by_unit if u.atomic_group_id == gid]
                    g_producer_attempts = [a for a in attempts if a.unit.atomic_group_id == gid]
                    g_counts = count_execution_units(
                        units=g_units,
                        attempts_by_unit=attempts_by_unit,
                        planned=None,
                        producer_attempts=g_producer_attempts,
                    )
                    recounted_group_counts[gid] = g_counts
            else:
                for gid in {a.unit.atomic_group_id for a in attempts}:
                    g_units = [u for u in attempts_by_unit if u.atomic_group_id == gid]
                    g_producer_attempts = [a for a in attempts if a.unit.atomic_group_id == gid]
                    g_counts = count_execution_units(
                        units=g_units,
                        attempts_by_unit=attempts_by_unit,
                        planned=None,
                        producer_attempts=g_producer_attempts,
                    )
                    recounted_group_counts[gid] = g_counts

            is_truncated = max_deltas is not None and len(all_summaries) >= max_deltas
            raw_total_errors = (
                aggregates[run_id].raw_total_errors
                if is_truncated
                else sum(1 for d in all_summaries if d.outcome == AttackOutcome.ERROR)
            )
            raw_total_retries = (
                aggregates[run_id].raw_total_retries
                if is_truncated
                else ScenarioProgressReadModel.total_retry_pressure(
                    attempts_per_unit=(len(a) for a in attempts_by_unit.values()),
                    persisted_retries=(d.total_retries for d in all_summaries),
                )
            )
            raw_retrying_attacks_count = (
                aggregates[run_id].raw_retrying_attacks_count
                if is_truncated
                else sum(1 for d in all_summaries if d.has_retries or d.total_retries > 0)
            )
            merged_group_counts = {**aggregates[run_id].group_counts, **recounted_group_counts}

            recounted[run_id] = replace(
                aggregates[run_id],
                unit_count=overall.completed,
                completed_units=overall.completed,
                successful_units=overall.succeeded,
                error_attempts=overall.errors,
                total_retries=overall.retries,
                outcome_successes=overall.outcomes.successes if overall.outcomes else 0,
                outcome_failures=overall.outcomes.failures if overall.outcomes else 0,
                outcome_errors=overall.outcomes.errors if overall.outcomes else 0,
                outcome_undetermined=overall.outcomes.undetermined if overall.outcomes else 0,
                raw_total_errors=raw_total_errors,
                raw_total_retries=raw_total_retries,
                raw_retrying_attacks_count=raw_retrying_attacks_count,
                group_counts=merged_group_counts,
                target_facing_attempts=overall.producer_counts.target_facing.attempts,
                target_facing_error_attempts=overall.producer_counts.target_facing.errors,
                target_facing_retries=overall.producer_counts.target_facing.retries,
                orchestration_attempts=overall.producer_counts.orchestration.attempts,
                orchestration_error_attempts=overall.producer_counts.orchestration.errors,
                orchestration_retries=overall.producer_counts.orchestration.retries,
                unknown_role_attempts=overall.producer_counts.unknown.attempts,
                unknown_role_error_attempts=overall.producer_counts.unknown.errors,
                unknown_role_retries=overall.producer_counts.unknown.retries,
            )
        return recounted

    def _build_history_summary(
        self,
        *,
        record: ScenarioHistoryRunRecord,
        atomic_groups: list[ScenarioRunPlanAtomicGroup] | None,
        aggregate: ScenarioHistoryAggregate,
        operation_name: str | None = None,
    ) -> ScenarioRunListItem:
        """
        Map lightweight persisted history projections to the public summary DTO.

        Returns:
            ScenarioRunListItem: Safe, aggregated history summary.
        """
        if atomic_groups is not None:
            atomic_groups = self._enrich_legacy_group_techniques(
                atomic_groups=atomic_groups,
                scenario_name=record.scenario_registry_name,
            )
        scenario_identifier = None
        try:
            scenario_identifier = ScenarioIdentifier.from_component_identifier(
                ComponentIdentifier.model_validate(
                    {**record.scenario_identifier, "pyrit_version": record.pyrit_version}
                )
            )
        except (ValidationError, ValueError):
            logger.warning(
                "Scenario run %s has invalid persisted identifier metadata; using legacy history fields.",
                record.scenario_result_id,
            )
        target, datasets_used, scenario_parameters = self._safe_run_metadata(scenario_identifier=scenario_identifier)
        if target is None and record.objective_target_identifier:
            try:
                target = self._safe_target_metadata(
                    target_identifier=TargetIdentifier.from_component_identifier(
                        ComponentIdentifier.model_validate(record.objective_target_identifier)
                    )
                )
            except ValidationError:
                logger.warning(
                    "Scenario run %s has invalid persisted target metadata; omitting the target summary.",
                    record.scenario_result_id,
                )

        planned_total = (
            len({(group.id, seed_group_id) for group in atomic_groups for seed_group_id in group.seed_group_ids})
            if atomic_groups is not None
            else aggregate.unit_count
        )
        completed = aggregate.completed_units
        successful = aggregate.successful_units
        status = ScenarioRunState(record.status)
        terminal = status in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        timestamps = [record.created_at]
        if aggregate.latest_attempt_timestamp is not None:
            timestamps.append(aggregate.latest_attempt_timestamp)
        if terminal and record.completed_at is not None:
            timestamps.append(record.completed_at)
        techniques = (
            list(dict.fromkeys(group.technique_name or group.display_group for group in atomic_groups))
            if atomic_groups is not None
            else list(aggregate.atomic_attack_names)
        )
        from pyrit.models import ScenarioProducerCategoryCounts, ScenarioProducerCounts

        producer_counts = ScenarioProducerCounts(
            target_facing=ScenarioProducerCategoryCounts(
                attempts=aggregate.target_facing_attempts,
                errors=aggregate.target_facing_error_attempts,
                retries=aggregate.target_facing_retries,
            ),
            orchestration=ScenarioProducerCategoryCounts(
                attempts=aggregate.orchestration_attempts,
                errors=aggregate.orchestration_error_attempts,
                retries=aggregate.orchestration_retries,
            ),
            unknown=ScenarioProducerCategoryCounts(
                attempts=aggregate.unknown_role_attempts,
                errors=aggregate.unknown_role_error_attempts,
                retries=aggregate.unknown_role_retries,
            ),
        )
        return ScenarioRunListItem(
            scenario_result_id=record.scenario_result_id,
            scenario_name=record.scenario_name,
            scenario_registry_name=record.scenario_registry_name,
            scenario_version=record.scenario_version,
            status=status,
            created_at=record.created_at,
            started_at=record.started_at,
            updated_at=max(timestamps),
            error=record.error_message,
            error_type=record.error_type,
            techniques_used=techniques,
            total_attacks=planned_total if atomic_groups is not None or planned_total else None,
            completed_attacks=completed,
            objective_achieved_rate=success_percentage(succeeded=successful, completed=completed) or 0,
            total_retries=aggregate.total_retries,
            labels=record.labels,
            operation_id=record.operation_id,
            operation_name=operation_name,
            completed_at=record.completed_at if terminal else None,
            pyrit_version=record.pyrit_version,
            target=target,
            datasets_used=datasets_used,
            scenario_parameters=scenario_parameters,
            planned_total_available=atomic_groups is not None,
            successful_attacks=successful,
            error_attacks=aggregate.error_attempts,
            attack_details_available=False,
            producer_counts=producer_counts,
        )

    @staticmethod
    def _load_started_at(*, scenario_result: ScenarioResult) -> datetime | None:
        """
        Load the persisted aware execution start timestamp from scenario metadata.

        Returns:
            datetime | None: The execution start, or None for legacy or invalid metadata.
        """
        raw_value = (getattr(scenario_result, "metadata", None) or {}).get(SCENARIO_RUN_STARTED_AT_METADATA_KEY)
        if raw_value is None:
            return None
        try:
            started_at = _STARTED_AT_ADAPTER.validate_python(raw_value)
        except ValidationError:
            return None
        return started_at if started_at.tzinfo is not None else None

    @staticmethod
    def _identifier_techniques(scenario_identifier: ScenarioIdentifier | None) -> list[str]:
        """
        Read techniques when legacy persisted metadata has an identifier.

        Returns:
            list[str]: Stored techniques or an empty list.
        """
        return list(scenario_identifier.techniques or []) if scenario_identifier is not None else []

    @staticmethod
    def _safe_run_metadata(
        *,
        scenario_identifier: ScenarioIdentifier | None,
    ) -> tuple[ScenarioTargetSummary | None, list[str], dict[str, Any]]:
        """
        Project canonical identifiers to an allow-listed, secret-free API shape.

        Returns:
            tuple[ScenarioTargetSummary | None, list[str], dict[str, Any]]:
                Safe target, datasets, and scenario parameters.
        """
        if scenario_identifier is None:
            return None, [], {}

        target = ScenarioRunService._safe_target_metadata(target_identifier=scenario_identifier.objective_target)
        return (
            target,
            list(scenario_identifier.datasets or []),
            ScenarioRunService._safe_scenario_parameters(parameters=dict(scenario_identifier.params)),
        )

    @staticmethod
    def _build_overload_summaries(*, retry_events: Sequence[Any]) -> list[ScenarioOverloadSummary]:
        """
        Aggregate bounded HTTP overload evidence by component role.

        Returns:
            list[ScenarioOverloadSummary]: Most recently affected roles first.
        """
        aggregates: dict[str, dict[str, Any]] = {}
        for event in retry_events:
            status_code = getattr(event, "status_code", None)
            if not isinstance(status_code, int) or (status_code != 429 and not 500 <= status_code <= 599):
                continue
            role = str(getattr(event, "component_role", "") or "unknown")
            timestamp = getattr(event, "timestamp", None)
            if not isinstance(timestamp, datetime):
                continue
            aggregate = aggregates.setdefault(
                role,
                {
                    "count": 0,
                    "rate_limit_count": 0,
                    "server_error_count": 0,
                    "status_codes": set(),
                    "latest_timestamp": timestamp,
                },
            )
            aggregate["count"] += 1
            aggregate["rate_limit_count"] += status_code == 429
            aggregate["server_error_count"] += 500 <= status_code <= 599
            aggregate["status_codes"].add(status_code)
            aggregate["latest_timestamp"] = max(aggregate["latest_timestamp"], timestamp)
        ordered = sorted(
            aggregates.items(),
            key=lambda item: item[1]["latest_timestamp"],
            reverse=True,
        )[:_MAX_OVERLOAD_ROLES]
        return [
            ScenarioOverloadSummary(
                component_role=role,
                count=aggregate["count"],
                rate_limit_count=aggregate["rate_limit_count"],
                server_error_count=aggregate["server_error_count"],
                status_codes=sorted(aggregate["status_codes"]),
                latest_timestamp=aggregate["latest_timestamp"],
            )
            for role, aggregate in ordered
        ]

    @staticmethod
    def _safe_target_metadata(*, target_identifier: TargetIdentifier | None) -> ScenarioTargetSummary | None:
        """
        Project a target identifier to the secret-free public shape.

        Returns:
            ScenarioTargetSummary | None: Safe target metadata when available.
        """
        if target_identifier is None:
            return None
        return ScenarioTargetSummary(
            target_type=target_identifier.class_name,
            endpoint=ScenarioRunService._safe_endpoint(target_identifier.endpoint),
            model_name=target_identifier.model_name or target_identifier.underlying_model_name,
            identifier_hash=target_identifier.hash,
        )

    @staticmethod
    def _safe_scenario_parameters(*, parameters: dict[str, Any]) -> dict[str, Any]:
        """
        Return only explicitly approved, JSON-safe scenario configuration fields.

        Returns:
            dict[str, Any]: Allow-listed scenario parameters with sensitive keys removed.
        """
        filtered = filter_sensitive_fields(parameters)
        return {
            key: value
            for key, value in filtered.items()
            if key in _SAFE_SCENARIO_PARAMETER_NAMES
            and (
                value is None
                or isinstance(value, (bool, int, float, str))
                or (
                    isinstance(value, list)
                    and all(item is None or isinstance(item, (bool, int, float, str)) for item in value)
                )
            )
        }

    @staticmethod
    def _safe_endpoint(endpoint: str | None) -> str | None:
        """
        Remove endpoint credentials, query parameters, and fragments.

        Returns:
            str | None: Sanitized endpoint.
        """
        if not endpoint:
            return None
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        host = parsed.hostname or ""
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            host = f"{host}:{port}"
        return urlunsplit((parsed.scheme, host, "", "", ""))

    def _get_active_task(self, *, scenario_result_id: str) -> _ActiveTask | None:
        """Return executable state for an active run."""
        active = self._active_tasks.get(scenario_result_id)
        if (
            active is not None
            and active.task is not None
            and active.task.done()
            and self._active_scenario_result_id != scenario_result_id
        ):
            self._release_completed_task(scenario_result_id=scenario_result_id)
            return None
        return active

    def snapshot_active_run(self, *, scenario_result_id: str) -> _ActiveRunSnapshot:
        """
        Copy live run state before storage awaits can change it.

        Returns:
            _ActiveRunSnapshot: An immutable copy of the active state.
        """
        active_scenario_result_id = self._active_scenario_result_id
        queue_position = next(
            (
                position
                for position, queued in enumerate(self._queued_runs, start=1)
                if queued.scenario_result_id == scenario_result_id
            ),
            None,
        )
        active = self._get_active_task(scenario_result_id=scenario_result_id)
        if active is None:
            return _ActiveRunSnapshot(
                error=self._terminal_errors.get(scenario_result_id),
                queue_position=queue_position,
                active_scenario_result_id=active_scenario_result_id,
            )
        active_group_ids = tuple(sorted(active.scenario.active_atomic_group_ids)) if active.scenario is not None else ()
        return _ActiveRunSnapshot(
            error=active.error,
            active_group_ids=active_group_ids,
            queue_position=queue_position,
            active_scenario_result_id=active_scenario_result_id,
        )

    def _load_run_plan(self, *, scenario_result: ScenarioResult) -> ScenarioRunPlan | None:
        """
        Load a validated plan from scenario metadata.

        Returns:
            ScenarioRunPlan | None: The stored plan, or None for a legacy row.
        """
        metadata = getattr(scenario_result, "metadata", None)
        raw_plan = (metadata or {}).get(SCENARIO_RUN_PLAN_METADATA_KEY)
        if raw_plan is None:
            return None
        plan = ScenarioRunPlan.model_validate(raw_plan)
        atomic_groups = self._enrich_legacy_group_techniques(
            atomic_groups=plan.atomic_groups,
            scenario_name=plan.scenario_registry_name,
        )
        return plan.model_copy(update={"atomic_groups": atomic_groups})

    def _enrich_legacy_group_techniques(
        self,
        *,
        atomic_groups: list[ScenarioRunPlanAtomicGroup],
        scenario_name: str | None,
    ) -> list[ScenarioRunPlanAtomicGroup]:
        """
        Recover technique metadata for legacy groups in full plans and compact history projections.

        Returns:
            list[ScenarioRunPlanAtomicGroup]: The original groups or copies with recovered technique metadata.
        """
        if scenario_name is None or all(group.technique_name for group in atomic_groups):
            return atomic_groups

        technique_summaries = self._get_scenario_technique_summaries(scenario_name=scenario_name)
        if not technique_summaries:
            return atomic_groups

        candidate_names = sorted(technique_summaries, key=len, reverse=True)
        enriched_groups: list[ScenarioRunPlanAtomicGroup] = []
        for group in atomic_groups:
            technique_name = group.technique_name
            if technique_name is None:
                technique_name = next(
                    (
                        candidate
                        for candidate in candidate_names
                        if group.display_group == candidate
                        or group.atomic_attack_name == candidate
                        or group.atomic_attack_name.startswith(f"{candidate}_")
                        or group.atomic_attack_name.startswith(f"{candidate}__")
                    ),
                    None,
                )
            summary = technique_summaries.get(technique_name) if technique_name else None
            enriched_groups.append(
                group.model_copy(
                    update={
                        "technique_name": technique_name,
                        "description": group.description or (summary.description if summary else None),
                        "tags": group.tags or (list(summary.tags) if summary else []),
                    }
                )
            )
        return enriched_groups

    def _get_scenario_technique_summaries(
        self,
        *,
        scenario_name: str,
    ) -> dict[str, ScenarioTechniqueSummary]:
        """
        Get cached technique metadata without materializing unrelated scenarios.

        Returns:
            dict[str, ScenarioTechniqueSummary]: Technique metadata keyed by name.
        """
        from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

        revision = AttackTechniqueRegistry.get_registry_singleton().catalog_revision
        if getattr(self, "_technique_metadata_revision", None) != revision:
            self._technique_metadata_cache.clear()
            self._technique_metadata_revision = revision
        cached = self._technique_metadata_cache.get(scenario_name)
        if cached is not None:
            return cached

        registry = ScenarioRegistry.get_registry_singleton()
        if scenario_name not in registry:
            summaries: dict[str, ScenarioTechniqueSummary] = {}
        else:
            scenario_class = registry.get_class(scenario_name)
            metadata = registry.get_class_metadata(scenario_class)
            summaries = {summary.name: summary for summary in metadata.technique_summaries}
        self._technique_metadata_cache[scenario_name] = summaries
        return summaries

    async def get_run_progress_async(
        self,
        *,
        scenario_result_id: str,
        since: str | None,
        limit: int,
    ) -> ScenarioRunProgress | None:
        """
        Snapshot live state and return compact incremental progress.

        Returns:
            ScenarioRunProgress | None: Compact progress when the run exists.
        """
        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        return await self.get_run_progress_from_storage_async(
            scenario_result_id=scenario_result_id,
            since=since,
            limit=limit,
            active_group_ids=snapshot.active_group_ids,
            queue_position=snapshot.queue_position,
            active_scenario_result_id=snapshot.active_scenario_result_id,
        )

    async def get_run_progress_from_storage_async(
        self,
        *,
        scenario_result_id: str,
        since: str | None,
        limit: int,
        active_group_ids: Sequence[str],
        queue_position: int | None = None,
        active_scenario_result_id: str | None = None,
    ) -> ScenarioRunProgress | None:
        """Return compact database progress using a previously captured live-state snapshot."""
        header_result = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        if header_result is None:
            return None

        try:
            plan = self._load_run_plan(scenario_result=header_result)
        except (ValidationError, ValueError):
            logger.warning(
                "Scenario run %s has invalid persisted plan metadata; treating the plan as unavailable.",
                scenario_result_id,
            )
            plan = None
        plan_complete = plan is not None
        cursor = self._decode_progress_cursor(since=since, scenario_result_id=scenario_result_id)
        terminal = header_result.scenario_run_state in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        objective_scorer_identifier = header_result.objective_scorer_identifier
        if not isinstance(objective_scorer_identifier, ComponentIdentifier):
            objective_scorer_identifier = None
        progress_snapshot = await self._progress_read_model.get_snapshot_async(
            scenario_result_id=scenario_result_id,
            plan=plan,
            plan_complete=plan_complete,
            active_group_ids=active_group_ids,
            terminal=terminal,
            objective_scorer_identifier=objective_scorer_identifier,
        )
        return self._build_progress_response(
            header_result=header_result,
            plan=plan,
            progress_snapshot=progress_snapshot,
            scenario_result_id=scenario_result_id,
            since=since,
            limit=limit,
            cursor=cursor,
            terminal=terminal,
            queue_position=queue_position,
            active_scenario_result_id=active_scenario_result_id,
        )

    def _build_progress_response(
        self,
        *,
        header_result: ScenarioResult,
        plan: ScenarioRunPlan | None,
        progress_snapshot: ScenarioProgressSnapshot,
        scenario_result_id: str,
        since: str | None,
        limit: int,
        cursor: AttackResultKeysetCursor | None,
        terminal: bool,
        queue_position: int | None,
        active_scenario_result_id: str | None,
    ) -> ScenarioRunProgress:
        if len(progress_snapshot.deltas) != len(progress_snapshot.results):
            raise ValueError("Scenario progress deltas and results have different lengths.")
        overload_events: deque[Any] = deque(maxlen=_MAX_OVERLOAD_EVENTS)

        for delta in progress_snapshot.deltas:
            overload_events.extend(delta.retry_events)
        # Use binary search to locate the bookmark in results sorted by timestamp and ID.
        start = (
            bisect_right(
                progress_snapshot.deltas,
                (cursor.timestamp, uuid.UUID(cursor.attack_result_id)),
                key=lambda delta: (delta.timestamp, uuid.UUID(delta.attack_result_id)),
            )
            if cursor is not None
            else 0
        )
        end = start + limit
        deltas = progress_snapshot.deltas[start:end]
        results = list(progress_snapshot.results[start:end])
        has_more = end < len(progress_snapshot.deltas)
        response_plan = progress_snapshot.plan if since is None else None
        next_cursor = (
            self._encode_progress_cursor(scenario_result_id=scenario_result_id, delta=deltas[-1]) if deltas else since
        )
        scenario_identifier = header_result.scenario_identifier
        target, datasets_used, scenario_parameters = self._safe_run_metadata(scenario_identifier=scenario_identifier)
        if plan is not None:
            techniques_used = list(
                dict.fromkeys(group.technique_name or group.display_group for group in plan.atomic_groups)
            )
        else:
            techniques_used = self._identifier_techniques(scenario_identifier)
        return ScenarioRunProgress(
            run=ScenarioProgressHeader(
                scenario_result_id=scenario_result_id,
                scenario_name=header_result.scenario_name,
                scenario_registry_name=plan.scenario_registry_name if plan else None,
                scenario_version=header_result.scenario_version,
                status=header_result.scenario_run_state,
                created_at=header_result.creation_time,
                started_at=self._load_started_at(scenario_result=header_result),
                completed_at=header_result.completion_time if terminal else None,
                error=header_result.error_message,
                error_type=header_result.error_type,
                pyrit_version=header_result.pyrit_version,
                target=target,
                techniques_used=techniques_used,
                datasets_used=datasets_used,
                scenario_parameters=scenario_parameters,
                labels=header_result.labels,
                queue_position=queue_position,
                active_scenario_result_id=active_scenario_result_id,
                overload_summaries=self._build_overload_summaries(retry_events=overload_events),
            ),
            plan=response_plan,
            results=results,
            summary=progress_snapshot.summary,
            next_cursor=next_cursor,
            has_more=has_more,
            plan_complete=plan is not None,
        )

    @staticmethod
    def _encode_progress_cursor(*, scenario_result_id: str, delta: ScenarioAttackResultDelta) -> str:
        payload = {
            "v": 1,
            "run": scenario_result_id,
            "timestamp": delta.timestamp.isoformat(),
            "attack_result_id": delta.attack_result_id,
        }
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_progress_cursor(
        *,
        since: str | None,
        scenario_result_id: str,
    ) -> AttackResultKeysetCursor | None:
        if since is None:
            return None
        try:
            padded = since + "=" * (-len(since) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        except Exception as exc:
            raise ValueError("Malformed scenario progress cursor.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Malformed scenario progress cursor.")
        if payload.get("v") != 1 or payload.get("run") != scenario_result_id:
            raise ValueError("Cursor does not belong to this scenario run.")
        try:
            timestamp = datetime.fromisoformat(payload["timestamp"])
            attack_result_id = str(uuid.UUID(payload["attack_result_id"]))
        except Exception as exc:
            raise ValueError("Malformed scenario progress cursor.") from exc
        if timestamp.tzinfo is None:
            raise ValueError("Cursor timestamp must include a timezone.")
        return AttackResultKeysetCursor(timestamp=timestamp, attack_result_id=attack_result_id)

    async def _get_hierarchy_aggregate_async(
        self, *, scenario_result_id: str, plan: ScenarioRunPlan | None
    ) -> ScenarioHistoryAggregate:
        """
        Read exact shared run statistics independently of the selected hierarchy page.

        Returns:
            ScenarioHistoryAggregate: The same SQL/SDK aggregate used by saved-run history.
        """
        aggregates = await self._memory.get_scenario_history_aggregates_async(
            scenario_result_ids=[scenario_result_id],
            plan_scenario_ids=[scenario_result_id] if plan is not None else [],
        )
        aggregates = await self._recount_with_sdk_statistics_async(
            aggregates=aggregates,
            plans={scenario_result_id: plan.atomic_groups if plan is not None else None},
        )
        return aggregates[scenario_result_id]

    @staticmethod
    def _hierarchy_counts(*, aggregate: ScenarioHistoryAggregate, planned: int | None) -> ScenarioProgressCounts:
        """
        Present the shared aggregate without choosing a second counting population.

        Returns:
            ScenarioProgressCounts: Exact logical counts, outcomes, and producer diagnostics.
        """
        return ScenarioProgressCounts(
            planned=planned,
            completed=aggregate.completed_units,
            succeeded=aggregate.successful_units,
            success_percentage=success_percentage(
                succeeded=aggregate.successful_units, completed=aggregate.completed_units
            ),
            errors=aggregate.error_attempts,
            retries=aggregate.total_retries,
            producer_counts=ScenarioProducerCounts(
                target_facing=ScenarioProducerCategoryCounts(
                    attempts=aggregate.target_facing_attempts,
                    errors=aggregate.target_facing_error_attempts,
                    retries=aggregate.target_facing_retries,
                ),
                orchestration=ScenarioProducerCategoryCounts(
                    attempts=aggregate.orchestration_attempts,
                    errors=aggregate.orchestration_error_attempts,
                    retries=aggregate.orchestration_retries,
                ),
                unknown=ScenarioProducerCategoryCounts(
                    attempts=aggregate.unknown_role_attempts,
                    errors=aggregate.unknown_role_error_attempts,
                    retries=aggregate.unknown_role_retries,
                ),
            ),
            outcomes=compute_outcome_statistics(
                {
                    AttackOutcome.SUCCESS: aggregate.outcome_successes,
                    AttackOutcome.FAILURE: aggregate.outcome_failures,
                    AttackOutcome.ERROR: aggregate.outcome_errors,
                    AttackOutcome.UNDETERMINED: aggregate.outcome_undetermined,
                }
            ),
        )

    async def _load_hierarchy_preview_async(
        self, *, scenario_result_id: str, roots: Sequence[ScenarioAttackResultDelta], max_nodes: int
    ) -> list[ScenarioAttackResultDelta]:
        """
        Resolve only the bounded preview's descendants in breadth-first batches.

        Returns:
            list[ScenarioAttackResultDelta]: Roots and saved descendants, never more than the node budget.
        """
        deltas = {delta.attack_result_id: delta for delta in roots}
        queried_ids = set(deltas)
        frontier = list(roots)
        for _ in range(10):
            if not frontier or len(deltas) >= max_nodes:
                break
            pending = (
                child_id
                for delta in frontier
                for child_id in self._progress_read_model._read_child_attack_result_ids(
                    attack_metadata=delta.attack_metadata
                )[:500]
            )
            batch: list[str] = []
            batch_limit = min(500, max_nodes - len(deltas))
            for child_id in pending:
                if child_id in queried_ids:
                    continue
                queried_ids.add(child_id)
                batch.append(child_id)
                if len(batch) >= batch_limit:
                    break
            if not batch:
                break
            frontier = list(
                await self._memory.get_scenario_attack_result_deltas_by_ids_async(
                    scenario_result_id=scenario_result_id, attack_result_ids=batch
                )
            )
            deltas.update((child.attack_result_id, child) for child in frontier)
        return list(deltas.values())

    async def get_run_hierarchy_async(
        self,
        *,
        scenario_result_id: str,
        max_nodes: int = 1000,
        cursor: str | None = None,
    ) -> ScenarioHierarchyProjection | None:
        """
        Get the bounded result hierarchy projection for a scenario run.

        Args:
            scenario_result_id: The persisted scenario run ID.
            max_nodes: Maximum nodes to assemble in the hierarchy tree.
            cursor: Optional continuation for the ascending page of true roots.

        Returns:
            ScenarioHierarchyProjection | None: The hierarchy projection, or None if the run does not exist.
        """
        header_result = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        if header_result is None:
            return None

        try:
            plan = self._load_run_plan(scenario_result=header_result)
        except (ValidationError, ValueError):
            logger.warning(
                "Scenario run %s has invalid persisted plan metadata; treating the plan as unavailable.",
                scenario_result_id,
            )
            plan = None
        if not 1 <= max_nodes <= 5000:
            raise ValueError("Hierarchy node budget must be between 1 and 5000.")
        fingerprint = fingerprint_filters(filters={"kind": "hierarchy-roots", "run": scenario_result_id})
        anchor = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        if cursor is not None and (anchor is None or anchor.position is not None):
            raise ValueError("Invalid hierarchy root cursor.")
        root_cursor = (
            AttackResultKeysetCursor(timestamp=anchor.timestamp, attack_result_id=anchor.identifier)
            if anchor is not None
            else None
        )
        roots, has_more = await self._memory.get_scenario_root_deltas_async(
            scenario_result_id=scenario_result_id, cursor=root_cursor, limit=min(500, max_nodes)
        )
        deltas = await self._load_hierarchy_preview_async(
            scenario_result_id=scenario_result_id, roots=roots, max_nodes=max_nodes
        )
        aggregate = await self._get_hierarchy_aggregate_async(scenario_result_id=scenario_result_id, plan=plan)
        summary_plan = plan or self._progress_read_model._synthesize_legacy_plan(deltas=deltas)
        plan_lookup = ScenarioPlanLookup.from_plan(plan=summary_plan)
        results = tuple(
            self._progress_read_model._map_progress_delta(delta=delta, plan_lookup=plan_lookup) for delta in deltas
        )
        summary = self._progress_read_model._build_progress_summary(
            plan=summary_plan,
            plan_complete=plan is not None,
            results=results,
            active_group_ids=(),
            terminal=header_result.scenario_run_state
            in (ScenarioRunState.COMPLETED, ScenarioRunState.FAILED, ScenarioRunState.CANCELLED),
            objective_scorer_identifier=None,
            technique_details_by_group=self._progress_read_model._build_technique_details_by_group(
                deltas=deltas, results=results
            ),
        )
        raw_attempts = (
            aggregate.target_facing_attempts + aggregate.orchestration_attempts + aggregate.unknown_role_attempts
        )
        progress_snapshot = ScenarioProgressSnapshot(
            deltas=tuple(deltas),
            results=results,
            summary=summary,
            plan=summary_plan,
            has_more=has_more or raw_attempts > len(deltas),
        )

        hierarchy = self._progress_read_model.build_scenario_hierarchy(
            scenario_result_id=scenario_result_id,
            scenario_name=header_result.scenario_name,
            scenario_version=header_result.scenario_version,
            status=header_result.scenario_run_state,
            snapshot=progress_snapshot,
            max_nodes=max_nodes,
            overall_counts=self._hierarchy_counts(aggregate=aggregate, planned=summary.overall.planned),
            exact_total_errors=aggregate.raw_total_errors,
            exact_total_retries=aggregate.raw_total_retries,
            exact_retrying_attacks_count=aggregate.raw_retrying_attacks_count,
            exact_group_counts=aggregate.group_counts,
            root_result_ids=[root.attack_result_id for root in roots],
        )
        hierarchy.has_more = has_more
        if has_more and roots:
            hierarchy.next_cursor = encode_keyset_cursor(
                timestamp=roots[-1].timestamp,
                identifier=roots[-1].attack_result_id,
                fingerprint=fingerprint,
            )
        if cursor is None and not roots and raw_attempts:
            rootless, clipped = await self._memory.get_scenario_recount_summaries_async(
                scenario_result_id=scenario_result_id, limit=min(100, max_nodes)
            )
            hierarchy.diagnostics.rootless_result_ids = [result.attack_result_id for result in rootless]
            hierarchy.diagnostics.rootless_results_count = raw_attempts
            hierarchy.diagnostics.rootless_results_truncated = clipped

        return hierarchy

    async def get_run_hierarchy_node_async(
        self,
        *,
        scenario_result_id: str,
        attack_result_id: str,
        max_immediate_children: int = 200,
        children_cursor: str | None = None,
    ) -> ScenarioHierarchyNode | None:
        """
        Get bounded single-node hierarchy details and generic conversation projections without full-run hydration.

        Args:
            scenario_result_id: The persisted scenario run ID.
            attack_result_id: The target attack result ID.
            max_immediate_children: Maximum number of immediate child nodes to resolve.
            children_cursor: Continuation bound to this run, node, and stored child ordering.

        Returns:
            ScenarioHierarchyNode | None: The node details, or None if the node is not found in this run.
        """
        if not 1 <= max_immediate_children <= 500:
            raise ValueError("Hierarchy child page size must be between 1 and 500.")
        deltas = await self._memory.get_scenario_attack_result_deltas_by_ids_async(
            scenario_result_id=scenario_result_id,
            attack_result_ids=[attack_result_id],
        )
        if not deltas:
            return None

        target_delta = deltas[0]

        header_result = await self._memory.get_scenario_result_header_async(scenario_result_id=scenario_result_id)
        plan = None
        if header_result is not None:
            try:
                plan = self._load_run_plan(scenario_result=header_result)
            except (ValidationError, ValueError):
                logger.warning(
                    "Scenario run %s has invalid persisted plan metadata; treating the plan as unavailable.",
                    scenario_result_id,
                )
                plan = None

        plan_lookup = ScenarioPlanLookup.from_plan(plan=plan)
        target_result = self._progress_read_model._map_progress_delta(
            delta=target_delta,
            plan_lookup=plan_lookup,
        )

        child_ids = target_result.child_attack_result_ids
        fingerprint = fingerprint_filters(
            filters={
                "kind": "hierarchy-children",
                "run": scenario_result_id,
                "node": attack_result_id,
                "ordered_references": json.dumps(child_ids, separators=(",", ":")),
            }
        )
        anchor = decode_keyset_cursor(cursor=children_cursor, fingerprint=fingerprint)
        if children_cursor is not None and (
            anchor is None
            or anchor.position is None
            or not 0 <= anchor.position < len(child_ids)
            or anchor.identifier != target_delta.attack_result_id
            or anchor.timestamp != target_delta.timestamp
        ):
            raise ValueError("Invalid or stale hierarchy child cursor.")
        child_offset = anchor.position if anchor is not None and anchor.position is not None else 0
        page_child_ids = child_ids[child_offset : child_offset + max_immediate_children]
        next_offset = child_offset + len(page_child_ids)
        next_children_cursor = (
            encode_keyset_cursor(
                timestamp=target_delta.timestamp,
                identifier=target_delta.attack_result_id,
                position=next_offset,
                fingerprint=fingerprint,
            )
            if next_offset < len(child_ids)
            else None
        )
        children_deltas: list[ScenarioAttackResultDelta] = []
        children_results: list[ScenarioProgressResult] = []
        if page_child_ids:
            children_deltas = list(
                await self._memory.get_scenario_attack_result_deltas_by_ids_async(
                    scenario_result_id=scenario_result_id,
                    attack_result_ids=page_child_ids,
                )
            )
            children_results = [
                self._progress_read_model._map_progress_delta(delta=d, plan_lookup=plan_lookup) for d in children_deltas
            ]

        conversations = await self._saved_conversation_reader.list_conversations_async(
            attack_result_id=attack_result_id
        )
        if conversations is None:
            return None

        return self._progress_read_model.build_hierarchy_node(
            delta=target_delta,
            result=target_result,
            children_deltas=children_deltas,
            children_results=children_results,
            conversations=conversations.conversations,
            conversations_count=conversations.total_count,
            conversations_truncated=(
                conversations.pagination.has_more if conversations.pagination is not None else False
            ),
            children_truncated=child_offset > 0 or next_offset < len(child_ids),
            child_offset=child_offset,
            child_limit=max_immediate_children,
            next_children_cursor=next_children_cursor,
        )

    async def get_run_results_async(self, *, scenario_result_id: str) -> ScenarioResult | None:
        """
        Get the ScenarioResult for a completed scenario run.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioResult if the run is completed and results exist, None if not found.

        Raises:
            ValueError: If the run is not in a completed state.
        """
        results = await self._memory.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
        if not results:
            return None

        scenario_result = results[0]
        run_response = await self._build_response_from_db_async(scenario_result=scenario_result)

        if run_response.status != ScenarioRunState.COMPLETED:
            raise ValueError(f"Results are only available for completed runs. Current status: '{run_response.status}'.")

        return scenario_result


_service_instance: ScenarioRunService | None = None


def peek_scenario_run_service() -> ScenarioRunService | None:
    """Return the existing service without constructing one for lifecycle inspection."""
    return _service_instance


async def reset_scenario_run_service_async() -> None:
    """Close and discard the drained singleton."""
    global _service_instance
    if _service_instance is not None:
        await _service_instance.close_async()
        _service_instance = None


def get_scenario_run_service() -> ScenarioRunService:
    """
    Get the global scenario run service instance.

    On first call, reads ``max_concurrent_scenario_runs`` from ``app.state``
    (set by ``pyrit_backend`` CLI) if available, otherwise uses the default.

    Returns:
        The singleton ScenarioRunService instance.
    """
    global _service_instance
    if _service_instance is not None:
        return _service_instance

    max_runs = _DEFAULT_MAX_CONCURRENT_RUNS
    try:
        from pyrit.backend.main import app

        max_runs = getattr(app.state, "max_concurrent_scenario_runs", _DEFAULT_MAX_CONCURRENT_RUNS)
    except Exception:
        pass

    _service_instance = ScenarioRunService(max_concurrent_runs=max_runs)
    return _service_instance
