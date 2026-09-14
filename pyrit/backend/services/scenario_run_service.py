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
import functools
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import TypeAdapter, ValidationError

from pyrit.backend.models.common import PaginationInfo, filter_sensitive_fields
from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_progress_read_model import ResultUnitIdentity, ScenarioProgressReadModel
from pyrit.memory import AttackResultKeysetCursor, CentralMemory
from pyrit.memory.memory_interface import (
    ScenarioHistoryAggregate,
    ScenarioHistoryKeysetCursor,
    ScenarioHistoryRunRecord,
)
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    ComponentIdentifier,
    ScenarioAttackResultDelta,
    ScenarioIdentifier,
    ScenarioProgressHeader,
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
    ScenarioRunListItem,
    ScenarioRunSummary,
    ScenarioTargetSummary,
    ScenarioTechniqueSummary,
)
from pyrit.registry import InitializerRegistry, ScenarioRegistry
from pyrit.scenario import Scenario

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_RUNS = 3

_SAFE_SCENARIO_PARAMETER_NAMES = frozenset(
    {
        "adversarial_targets",
        "jailbreak_names",
        "max_attempts_per_objective",
        "max_turns",
        "num_jailbreak_attempts",
        "num_jailbreaks",
        "sub_harm",
        "version",
    }
)
_HISTORY_ATOMIC_GROUPS_ADAPTER = TypeAdapter(list[ScenarioRunPlanAtomicGroup])
_HISTORY_SEED_ID_MAP_ADAPTER = TypeAdapter(list[dict[str, str]])


@dataclass
class _ActiveTask:
    """Tracks an in-flight scenario run's asyncio task."""

    scenario_result_id: str
    task: asyncio.Task[None] | None = None
    scenario: Scenario | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _ActiveRunSnapshot:
    """Event-loop-owned state copied before database work moves to a worker thread."""

    error: str | None = None
    active_group_ids: tuple[str, ...] = ()


class ScenarioRunService:
    """
    Service for managing scenario run lifecycle.

    Uses CentralMemory (database) as the source of truth for run state.
    Keeps an in-memory dict only for active asyncio tasks (cancellation support).
    """

    #: Seconds to let initialization's own background tasks (for example HTTP client teardown
    #: scheduled from ``__del__``) finish before the initialization loop is torn down. This is
    #: headroom for incidental teardown, not a waiter for real long-running work.
    _INITIALIZATION_DRAIN_TIMEOUT = 5.0

    def __init__(self, *, max_concurrent_runs: int = _DEFAULT_MAX_CONCURRENT_RUNS) -> None:
        """Initialize the scenario run service."""
        self._max_concurrent_runs = max_concurrent_runs
        self._memory = CentralMemory.get_memory_instance()
        self._active_tasks: dict[str, _ActiveTask] = {}
        self._run_semaphore = asyncio.Semaphore(max_concurrent_runs)
        self._configuration_resolver = ScenarioConfigurationResolver()
        self._progress_read_model = ScenarioProgressReadModel(memory=self._memory)
        self._technique_metadata_cache: dict[str, dict[str, ScenarioTechniqueSummary]] = {}
        self._technique_metadata_lock = Lock()

        # Initialization writes to CentralMemory, and the in-memory SQLite backend shares one
        # DBAPI connection across every thread (StaticPool, sqlite_memory.py). Two preparations
        # running at once would use that connection concurrently and lose or corrupt writes, so
        # they are serialized onto a single worker. The event loop is still free while they run,
        # which is the point of the offload.
        self._prepare_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pyrit-scenario-prep")

    async def start_run_async(self, *, request: RunScenarioRequest) -> ScenarioRunSummary:
        """
        Start a new scenario run as a background task.

        Performs all validation and initialization eagerly (initializers, target
        resolution, technique validation, scenario.initialize_async) so errors are
        returned immediately. On success, spawns a background task that only
        executes scenario.run_async.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            ScenarioRunResponse with run_id and RUNNING status.

        Raises:
            ValueError: If scenario, target, initializer, or technique cannot be found,
                or concurrent limit exceeded.
        """
        if self._run_semaphore.locked():
            raise ValueError(
                f"Maximum concurrent runs ({self._max_concurrent_runs}) reached. "
                "Wait for an existing run to complete or cancel one."
            )

        await self._run_semaphore.acquire()

        # This frame owns the permit until the background task is created; every exit path
        # before that hand-off has to release it, including cancellation, which is a
        # BaseException and so is not caught by ``except Exception``.
        release_on_exit = True
        registered_run_id: str | None = None
        try:
            # A resumed run keeps the state its previous run left behind, so one that was
            # cancelled and is now being resumed on purpose is still CANCELLED while it
            # initializes. Read that before preparation: the check afterwards otherwise
            # cannot tell an intentional resume from a cancellation that landed while the
            # worker thread was still initializing, and would refuse to restart it.
            resumed_from_cancelled = self._is_run_cancelled(scenario_result_id=request.scenario_result_id)

            # Initialization loads the default datasets, which takes minutes, and is mostly
            # synchronous work. Run it on a worker thread so the event loop stays free to
            # answer health checks and status polls while a run is starting.
            prepare_task = asyncio.get_running_loop().run_in_executor(
                self._prepare_executor, functools.partial(self._prepare_run_blocking, request=request)
            )
            try:
                scenario = await asyncio.shield(prepare_task)
            except BaseException as exc:
                # A worker thread cannot be killed, so it keeps initializing after this frame
                # unwinds. Keep holding the permit until it actually finishes, otherwise the
                # next caller is admitted while this run is still loading datasets and
                # ``max_concurrent_runs`` stops bounding the work that is really running.
                if not prepare_task.done():
                    prepare_task.add_done_callback(self._release_abandoned_prepare)
                    release_on_exit = False
                elif isinstance(exc, asyncio.CancelledError):
                    # The thread can finish just as the cancellation lands. A done future never
                    # calls back, so cleaning up here is the only chance to release the permit
                    # and terminalize the run that initialization already stored.
                    release_on_exit = False
                    try:
                        self._release_abandoned_prepare(prepare_task)
                    except Exception as cleanup_error:
                        # The permit is released first, so it is already back even if the rest
                        # failed. Never let cleanup replace the cancellation being propagated.
                        logger.warning(f"Could not clean up after a cancelled scenario preparation: {cleanup_error}")
                raise

            # scenario_result_id is set during initialize_async
            scenario_result_id = scenario._scenario_result_id
            if scenario_result_id is None:
                raise ValueError("Scenario did not produce a scenario_result_id during initialization.")

            # Track active task
            active = _ActiveTask(scenario_result_id=scenario_result_id, scenario=scenario)
            self._active_tasks[scenario_result_id] = active
            registered_run_id = scenario_result_id

            # Build the response before spawning the task so that a failure here cannot leave
            # a run executing that the caller never received an id for.
            response = self.get_run(scenario_result_id=scenario_result_id)
            if response is None:
                raise RuntimeError(
                    f"Scenario run {scenario_result_id} was not found in the database after initialization."
                )

            # A run can be cancelled through its id while initialization is still on the worker
            # thread: a resume already knows the id, and a fresh run appears in the run list as
            # soon as initialization stores it. Nothing has run yet, so honour that instead of
            # starting a scenario the caller gave up on. The finally block returns the permit
            # and drops the tracking entry.
            if response.status == ScenarioRunState.CANCELLED and not resumed_from_cancelled:
                logger.info(f"Scenario run {scenario_result_id} was cancelled while it was being initialized.")
                return response

            # Spawn background task (only runs scenario.run_async). It releases the permit in
            # its own finally, so ownership transfers here and this frame must not release it.
            task = asyncio.create_task(self._execute_run_async(scenario_result_id=scenario_result_id))
            active.task = task
            release_on_exit = False
            registered_run_id = None
        finally:
            if registered_run_id is not None:
                self._active_tasks.pop(registered_run_id, None)
            if release_on_exit:
                self._run_semaphore.release()

        return response

    def _is_run_cancelled(self, *, scenario_result_id: str | None) -> bool:
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
        stored = self._memory.get_scenario_result_header(scenario_result_id=scenario_result_id)
        return stored is not None and stored.scenario_run_state == ScenarioRunState.CANCELLED

    def _release_abandoned_prepare(self, prepare_task: "asyncio.Future[Scenario]") -> None:
        """
        Clean up after an abandoned preparation thread has finished.

        ``start_run_async`` hands ownership of the permit to this callback when it is
        cancelled while the worker thread is still initializing, so the permit is only
        released after the thread has genuinely stopped using the slot. A preparation that
        succeeds anyway leaves behind a scenario result nobody will run, which is marked
        cancelled here rather than left waiting in ``CREATED``.

        Args:
            prepare_task: The future wrapping the abandoned ``_prepare_run_blocking`` call.
        """
        self._run_semaphore.release()

        if prepare_task.cancelled():
            return
        error = prepare_task.exception()
        if error is not None:
            logger.warning(f"Abandoned scenario preparation failed after the request was cancelled: {error}")
            return

        # Initialization already stored a CREATED scenario result, and nothing is going to run
        # it now, so terminalize it rather than leaving a run that never starts. A run that
        # already reached a terminal state keeps it, so a real failure is not relabelled.
        scenario_result_id = prepare_task.result()._scenario_result_id
        if scenario_result_id:
            try:
                self._memory.try_update_scenario_run_state(
                    scenario_result_id=scenario_result_id,
                    expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
                    scenario_run_state=ScenarioRunState.CANCELLED,
                    error_message="The start request was cancelled while the scenario was being initialized.",
                )
            except Exception as update_error:
                logger.warning(
                    f"Could not mark abandoned scenario run {scenario_result_id} as cancelled: {update_error}"
                )
        logger.warning("Abandoned scenario preparation completed after the request was cancelled.")

    def _prepare_run_blocking(self, *, request: RunScenarioRequest) -> Scenario:
        """
        Run the eager initialization for a scenario run on the calling thread.

        Exists so ``start_run_async`` can offload initialization onto a worker thread.
        The scenario is executed later on the caller's event loop, so initialization must not
        leave anything bound to the throwaway loop used here. Clients that schedule their own
        teardown are given a moment to finish; anything still running after that would be
        cancelled when the loop closes, so the start fails rather than handing back a scenario
        that holds dead async resources.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            Scenario: The initialized scenario.

        Raises:
            RuntimeError: If tasks are still running on the initialization loop after the drain.
        """

        async def prepare_async() -> Scenario:
            scenario = await self._prepare_run_async(request=request)
            try:
                await self._drain_initialization_tasks_async()
            except RuntimeError as drain_error:
                # Initialization already stored a CREATED row and this start is over, so
                # terminalize it here rather than leaving a run that never begins. A cancel
                # can land while the drain is running, so keep whatever terminal state won.
                scenario_result_id = scenario._scenario_result_id
                if scenario_result_id:
                    try:
                        self._memory.try_update_scenario_run_state(
                            scenario_result_id=scenario_result_id,
                            expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
                            scenario_run_state=ScenarioRunState.FAILED,
                            error_message=str(drain_error),
                            error_type=type(drain_error).__name__,
                        )
                    except Exception as update_error:
                        logger.warning(f"Could not mark scenario run {scenario_result_id} as failed: {update_error}")
                raise
            return scenario

        return asyncio.run(prepare_async())

    async def _drain_initialization_tasks_async(self) -> None:
        """
        Let initialization's background tasks finish before the initialization loop closes.

        Initialization builds throwaway async clients, and some of them schedule their own
        teardown from ``__del__``, so a task can appear purely because a garbage collection
        landed late. Waiting for those is the difference between a scenario that starts and
        one that fails at random. A draining task can also start another one, so the set is
        rebuilt after every wait and the whole drain shares a single deadline.

        Raises:
            RuntimeError: If any task is still running after the drain timeout.
        """
        loop = asyncio.get_running_loop()
        current_task = asyncio.current_task()
        deadline = loop.time() + self._INITIALIZATION_DRAIN_TIMEOUT

        while True:
            pending = [task for task in asyncio.all_tasks() if task is not current_task]
            if not pending:
                return

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    "Scenario initialization left background tasks on the initialization loop, which is "
                    "about to close. They would be cancelled and the scenario would hold dead async "
                    f"resources: {', '.join(sorted(task.get_name() for task in pending))}"
                )

            done, _ = await asyncio.wait(pending, timeout=remaining)
            for task in done:
                # Retrieve outcomes so a failed teardown task does not log "never retrieved" noise.
                if not task.cancelled() and task.exception() is not None:
                    logger.debug(f"A scenario initialization task failed during teardown: {task.exception()}")

    async def _prepare_run_async(self, *, request: RunScenarioRequest) -> Scenario:
        """
        Resolve and initialize the scenario for a run request.

        Args:
            request: The run request with scenario name, target, and options.

        Returns:
            Scenario: The initialized scenario.

        Raises:
            ValueError: If scenario, target, initializer, or technique cannot be found.
        """
        scenario_class = self._configuration_resolver.resolve_scenario_class(scenario_name=request.scenario_name)
        await self._run_initializers_async(request=request)
        objective_target = self._configuration_resolver.resolve_target(target_name=request.target_name)
        init_kwargs = self._configuration_resolver.resolve_configuration(
            scenario_name=request.scenario_name,
            scenario_class=scenario_class,
            objective_target=objective_target,
            techniques=request.techniques,
            dataset_names=request.dataset_names,
            max_dataset_size=request.max_dataset_size,
            dataset_filters=request.dataset_filters,
            include_baseline=request.include_baseline,
            max_concurrency=request.max_concurrency,
            max_retries=request.max_retries,
            memory_labels=request.labels,
        )
        return await self._initialize_scenario_async(request=request, init_kwargs=init_kwargs)

    def get_run(self, *, scenario_result_id: str) -> ScenarioRunSummary | None:
        """
        Get the current status of a scenario run by querying the database.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioRunSummary if found, None otherwise.
        """
        snapshot = self.snapshot_active_run(scenario_result_id=scenario_result_id)
        return self.get_run_from_storage(scenario_result_id=scenario_result_id, active_error=snapshot.error)

    def get_run_from_storage(
        self,
        *,
        scenario_result_id: str,
        active_error: str | None,
    ) -> ScenarioRunSummary | None:
        """
        Build a run summary using database state plus an event-loop snapshot.

        Args:
            scenario_result_id: The scenario result ID.
            active_error: Error copied from the active asyncio task, if any.

        Returns:
            ScenarioRunSummary | None: The run summary when found.
        """
        return self._build_response(scenario_result_id=scenario_result_id, active_error=active_error)

    def list_runs(
        self,
        *,
        scenario_names: Sequence[str] | None = None,
        statuses: Sequence[ScenarioRunState | str] | None = None,
        labels: Mapping[str, str | Sequence[str]] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ScenarioRunListResponse:
        """
        List scenario runs by querying the database (most recent first).

        Args:
            scenario_names: Registered or persisted scenario names to match.
            statuses: Run states to match.
            labels: Labels with OR-within-key and AND-across-key semantics.
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
        fingerprint = fingerprint_filters(
            filters={
                "scenario_names": normalized_names,
                "statuses": normalized_statuses,
                "labels": normalized_labels,
            }
        )
        decoded_cursor = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        after = (
            ScenarioHistoryKeysetCursor(
                timestamp=decoded_cursor.timestamp,
                scenario_result_id=decoded_cursor.identifier,
            )
            if decoded_cursor is not None
            else None
        )
        records, aggregates, has_more = self._memory.get_scenario_run_history_page(
            scenario_names=normalized_names,
            statuses=normalized_statuses,
            labels=normalized_labels,
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
                **self._memory.get_scenario_history_aggregates(scenario_result_ids=unusable_plan_ids),
            }
        items = [
            self._build_history_summary(
                record=record,
                atomic_groups=plans[record.scenario_result_id],
                aggregate=aggregates.get(record.scenario_result_id)
                or ScenarioHistoryAggregate.empty(scenario_result_id=record.scenario_result_id),
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
        # Verify run exists in DB
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None

        scenario_result = results[0]
        db_status = scenario_result.scenario_run_state

        if db_status in (ScenarioRunState.COMPLETED, ScenarioRunState.FAILED, ScenarioRunState.CANCELLED):
            raise ValueError(f"Cannot cancel run in '{db_status}' state.")

        # Cancel the asyncio task if active and wait for it to finish
        active = self._active_tasks.get(scenario_result_id)
        if active is not None and active.task is not None and not active.task.done():
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(active.task, timeout=5.0)

        # The run can reach a terminal state during the await above, so only cancel a run that
        # is still going. The re-read below reports whichever state actually won.
        self._memory.try_update_scenario_run_state(
            scenario_result_id=scenario_result_id,
            expected_states={ScenarioRunState.CREATED, ScenarioRunState.IN_PROGRESS},
            scenario_run_state=ScenarioRunState.CANCELLED,
            error_message="Run was cancelled by user",
            error_type="CancelledError",
        )

        return self.get_run(scenario_result_id=scenario_result_id)

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

        initializer_registry = InitializerRegistry.get_registry_singleton()
        for initializer_name in request.initializers:
            initializer_params = (request.initializer_args or {}).get(initializer_name)
            try:
                instance = initializer_registry.create_and_configure(
                    initializer_name, initializer_params=initializer_params
                )
            except KeyError as e:
                raise ValueError(f"Initializer not found: {e}") from None
            await instance.initialize_async()

    async def _initialize_scenario_async(self, *, request: RunScenarioRequest, init_kwargs: dict[str, Any]) -> Scenario:
        """
        Build and initialize the scenario via the registry.

        Delegates the full create + set-parameters + initialize lifecycle to
        ``ScenarioRegistry.create_and_initialize_async`` so the registry owns
        scenario creation and initialization. The run-specific common parameters
        are resolved before this method and forwarded as ``init_kwargs``.

        Args:
            request: The run request (for scenario_name, scenario_params, and
                scenario_result_id).
            init_kwargs: The resolved common parameters to pass to
                scenario.initialize_async.

        Returns:
            The fully initialized Scenario instance ready for run_async.
        """
        scenario_registry = ScenarioRegistry.get_registry_singleton()
        return await scenario_registry.create_and_initialize_async(
            request.scenario_name,
            scenario_params=request.scenario_params or {},
            scenario_result_id=request.scenario_result_id or None,
            **init_kwargs,
        )

    async def _execute_run_async(self, *, scenario_result_id: str) -> None:
        """
        Execute a scenario run (background task entry point).

        Only calls scenario.run_async on the already-initialized scenario.

        Note: this method intentionally does NOT remove the entry from
        ``_active_tasks`` on completion. The entry must stay so that
        ``_build_response_from_db`` can read ``active.error`` when the
        caller next polls the run status. Cleanup happens lazily there
        once the error has been surfaced.

        Args:
            scenario_result_id: The scenario result ID for this run.
        """
        active = self._active_tasks[scenario_result_id]
        assert active.scenario is not None

        try:
            await active.scenario.run_async()

        except asyncio.CancelledError:
            logger.info(f"Scenario run {scenario_result_id} was cancelled.")

        except Exception as e:
            active.error = str(e)
            logger.exception(f"Scenario run {scenario_result_id} failed: {e}")

        finally:
            self._run_semaphore.release()

    def _build_response(
        self,
        *,
        scenario_result_id: str,
        active_error: str | None,
    ) -> ScenarioRunSummary | None:
        """
        Build a ScenarioRunResponse by querying the database and merging active task state.

        Args:
            scenario_result_id: The scenario result ID.
            active_error: Error copied from the active asyncio task, if any.

        Returns:
            ScenarioRunResponse if found in the database, None otherwise.
        """
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None
        return self._build_response_from_db(scenario_result=results[0], active_error=active_error)

    def _build_response_from_db(
        self,
        *,
        scenario_result: ScenarioResult,
        active_error: str | None = None,
    ) -> ScenarioRunSummary:
        """
        Build a ScenarioRunResponse from a database ScenarioResult, merged with active task info.

        Args:
            scenario_result: A ScenarioResult retrieved from CentralMemory.
            active_error: Error copied from the active asyncio task, if any.

        Returns:
            The API response model.
        """
        scenario_result_id = str(scenario_result.id)

        # Primary source: DB-persisted error fields
        error = scenario_result.error_message
        error_type = scenario_result.error_type

        # Fallback: look up error from any persisted error AttackResults linked
        # to this scenario via the new attribution_parent_id foreign key.
        if not error:
            error_ars = self._memory.get_attack_results(
                scenario_result_id=scenario_result_id,
                outcome=AttackOutcome.ERROR,
            )
            if error_ars:
                error = error_ars[0].error_message
                error_type = error_ars[0].error_type

        # Fallback: in-memory error for in-flight tasks where DB hasn't been updated yet
        if not error:
            error = active_error

        status = scenario_result.scenario_run_state
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
        plan_lookup = self._progress_read_model.build_plan_lookup(plan=plan)

        # Build result fields from DB (always computed so in-progress runs show progress)
        total_attacks, completed_attacks, objective_achieved_rate, successful_attacks = (
            self._progress_read_model.calculate_progress_counts(
                scenario_result=scenario_result,
                plan=plan,
                plan_lookup=plan_lookup,
            )
        )
        techniques_used = (
            list(dict.fromkeys(group.display_group for group in plan.atomic_groups))
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
        persisted_retries: list[int] = []
        attempts_by_unit: dict[ResultUnitIdentity, int] = {}
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
                unit_identity = self._progress_read_model.resolve_result_unit_identity(
                    atomic_attack_name=atomic_attack_name,
                    attack_result=attack_result,
                    plan_lookup=plan_lookup,
                )
                attempts_by_unit[unit_identity] = attempts_by_unit.get(unit_identity, 0) + 1
                retries = getattr(attack_result, "total_retries", 0)
                if isinstance(retries, int):
                    persisted_retries.append(retries)

                retry_events = getattr(attack_result, "retry_events", None)
                if isinstance(retry_events, list) and retry_events:
                    attack_retries.append(
                        AttackRetrySummary(
                            attack_result_id=str(attack_result.attack_result_id),
                            atomic_attack_name=atomic_attack_name,
                            retries=retry_events,
                        )
                    )

                if attack_result.outcome == AttackOutcome.ERROR:
                    failed_attacks.append(
                        AttackErrorSummary(
                            atomic_attack_name=atomic_attack_name,
                            objective=attack_result.objective,
                            error_type=attack_result.error_type,
                            error_message=attack_result.error_message,
                            total_retries=retries if isinstance(retries, int) else 0,
                        )
                    )
        total_retries = self._progress_read_model.total_retry_pressure(
            attempts_per_unit=attempts_by_unit.values(),
            persisted_retries=persisted_retries,
        )

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
                objective_hashes = [
                    seed_hash_by_id[seed_id] for seed_id in group.seed_group_ids if seed_id in seed_hash_by_id
                ]
                if len(objective_hashes) != len(set(objective_hashes)):
                    raise ValueError("ambiguous objective hash within atomic group")
            return atomic_groups
        except (json.JSONDecodeError, ValidationError, ValueError):
            logger.warning(
                "Scenario run %s has an incomplete persisted plan; using legacy history totals.",
                record.scenario_result_id,
            )
            return None

    def _build_history_summary(
        self,
        *,
        record: ScenarioHistoryRunRecord,
        atomic_groups: list[ScenarioRunPlanAtomicGroup] | None,
        aggregate: ScenarioHistoryAggregate,
    ) -> ScenarioRunListItem:
        """
        Map lightweight persisted history projections to the public summary DTO.

        Returns:
            ScenarioRunListItem: Safe, aggregated history summary.
        """
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
            list(dict.fromkeys(group.display_group for group in atomic_groups))
            if atomic_groups is not None
            else list(aggregate.atomic_attack_names)
        )
        return ScenarioRunListItem(
            scenario_result_id=record.scenario_result_id,
            scenario_name=record.scenario_name,
            scenario_registry_name=record.scenario_registry_name,
            scenario_version=record.scenario_version,
            status=status,
            created_at=record.created_at,
            updated_at=max(timestamps),
            error=record.error_message,
            error_type=record.error_type,
            techniques_used=techniques,
            total_attacks=planned_total if atomic_groups is not None or planned_total else None,
            completed_attacks=completed,
            objective_achieved_rate=int((successful / completed) * 100) if completed else 0,
            total_retries=aggregate.total_retries,
            labels=record.labels,
            completed_at=record.completed_at if terminal else None,
            pyrit_version=record.pyrit_version,
            target=target,
            datasets_used=datasets_used,
            scenario_parameters=scenario_parameters,
            planned_total_available=atomic_groups is not None,
            successful_attacks=successful,
            error_attacks=aggregate.error_attempts,
            attack_details_available=False,
        )

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
        """Return a live task and release completed task state."""
        active = self._active_tasks.get(scenario_result_id)
        if active is not None and active.task is not None and active.task.done():
            self._active_tasks.pop(scenario_result_id, None)
        return active

    def snapshot_active_run(self, *, scenario_result_id: str) -> _ActiveRunSnapshot:
        """
        Copy asyncio-owned run state for use by database-only worker-thread methods.

        Returns:
            _ActiveRunSnapshot: An immutable copy of the active state.
        """
        active = self._get_active_task(scenario_result_id=scenario_result_id)
        if active is None:
            return _ActiveRunSnapshot()
        active_group_ids = tuple(sorted(active.scenario.active_atomic_group_ids)) if active.scenario is not None else ()
        return _ActiveRunSnapshot(error=active.error, active_group_ids=active_group_ids)

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
        return self._enrich_legacy_plan_techniques(plan=plan)

    def _enrich_legacy_plan_techniques(self, *, plan: ScenarioRunPlan) -> ScenarioRunPlan:
        """
        Add technique identity and metadata to plans stored before those fields existed.

        Returns:
            ScenarioRunPlan: The original plan or a copy with recovered technique metadata.
        """
        scenario_name = plan.scenario_registry_name
        if scenario_name is None or all(group.technique_name for group in plan.atomic_groups):
            return plan

        technique_summaries = self._get_scenario_technique_summaries(scenario_name=scenario_name)
        if not technique_summaries:
            return plan

        candidate_names = sorted(technique_summaries, key=len, reverse=True)
        enriched_groups: list[ScenarioRunPlanAtomicGroup] = []
        for group in plan.atomic_groups:
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
        return plan.model_copy(update={"atomic_groups": enriched_groups})

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
        with self._technique_metadata_lock:
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

    def get_run_progress(
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
        return self.get_run_progress_from_storage(
            scenario_result_id=scenario_result_id,
            since=since,
            limit=limit,
            active_group_ids=snapshot.active_group_ids,
        )

    def get_run_progress_from_storage(
        self,
        *,
        scenario_result_id: str,
        since: str | None,
        limit: int,
        active_group_ids: Sequence[str],
    ) -> ScenarioRunProgress | None:
        """Return compact database progress using a previously captured live-state snapshot."""
        header_result = self._memory.get_scenario_result_header(scenario_result_id=scenario_result_id)
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
        progress_snapshot = self._progress_read_model.get_snapshot(
            scenario_result_id=scenario_result_id,
            plan=plan,
            plan_complete=plan_complete,
            active_group_ids=active_group_ids,
            terminal=terminal,
            objective_scorer_identifier=objective_scorer_identifier,
        )
        available = [
            (delta, result)
            for delta, result in zip(progress_snapshot.deltas, progress_snapshot.results, strict=True)
            if cursor is None
            or (delta.timestamp, uuid.UUID(delta.attack_result_id))
            > (cursor.timestamp, uuid.UUID(cursor.attack_result_id))
        ]
        page = available[:limit]
        deltas = [delta for delta, _ in page]
        results = [result for _, result in page]
        has_more = len(available) > limit
        response_plan = progress_snapshot.plan if since is None else None
        next_cursor = (
            self._encode_progress_cursor(scenario_result_id=scenario_result_id, delta=deltas[-1]) if deltas else since
        )
        scenario_identifier = header_result.scenario_identifier
        target, datasets_used, scenario_parameters = self._safe_run_metadata(scenario_identifier=scenario_identifier)
        if plan is not None:
            techniques_used = list(dict.fromkeys(group.display_group for group in plan.atomic_groups))
        elif scenario_identifier is not None:
            techniques_used = list(scenario_identifier.techniques or [])
        else:
            techniques_used = []
        return ScenarioRunProgress(
            run=ScenarioProgressHeader(
                scenario_result_id=scenario_result_id,
                scenario_name=header_result.scenario_name,
                scenario_registry_name=plan.scenario_registry_name if plan else None,
                scenario_version=header_result.scenario_version,
                status=header_result.scenario_run_state,
                created_at=header_result.creation_time,
                completed_at=header_result.completion_time if terminal else None,
                pyrit_version=header_result.pyrit_version,
                target=target,
                techniques_used=techniques_used,
                datasets_used=datasets_used,
                scenario_parameters=scenario_parameters,
                labels=header_result.labels,
            ),
            plan=response_plan,
            results=results,
            summary=progress_snapshot.summary,
            next_cursor=next_cursor,
            has_more=has_more,
            plan_complete=plan_complete,
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

    def get_run_results(self, *, scenario_result_id: str) -> ScenarioResult | None:
        """
        Get the ScenarioResult for a completed scenario run.

        Args:
            scenario_result_id: The scenario result ID.

        Returns:
            ScenarioResult if the run is completed and results exist, None if not found.

        Raises:
            ValueError: If the run is not in a completed state.
        """
        results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if not results:
            return None

        scenario_result = results[0]
        run_response = self._build_response_from_db(scenario_result=scenario_result)

        if run_response.status != ScenarioRunState.COMPLETED:
            raise ValueError(f"Results are only available for completed runs. Current status: '{run_response.status}'.")

        return scenario_result


_service_instance: ScenarioRunService | None = None


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
