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
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultKeysetCursor, CentralMemory
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ScenarioAttackResultDelta,
    ScenarioProgressHeader,
    ScenarioProgressResult,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunProgress,
    ScenarioRunState,
    config_hash,
)
from pyrit.models.catalog.scenario import (
    AttackErrorSummary,
    AttackRetrySummary,
    RunScenarioRequest,
    ScenarioRunListItem,
    ScenarioRunSummary,
)
from pyrit.registry import InitializerRegistry, ScenarioRegistry
from pyrit.scenario import Scenario

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_RUNS = 3


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


@dataclass(frozen=True, slots=True)
class _ResultUnitIdentity:
    """Stable identity of one planned scenario execution unit."""

    atomic_group_id: str
    seed_group_id: str


@dataclass(frozen=True, slots=True)
class _ScenarioPlanLookup:
    """Pre-indexed run-plan data used while mapping many attack results."""

    groups_by_identity: dict[tuple[str, str], ScenarioRunPlanAtomicGroup]
    groups_by_name: dict[str, tuple[ScenarioRunPlanAtomicGroup, ...]]
    seed_ids_by_group_and_objective: dict[tuple[str, str], tuple[str, ...]]
    planned_units: frozenset[_ResultUnitIdentity]

    @classmethod
    def from_plan(cls, *, plan: ScenarioRunPlan | None) -> "_ScenarioPlanLookup":
        """
        Build constant-time lookup tables for one run plan.

        Returns:
            _ScenarioPlanLookup: Indexed plan data.
        """
        if plan is None:
            return cls(
                groups_by_identity={},
                groups_by_name={},
                seed_ids_by_group_and_objective={},
                planned_units=frozenset(),
            )

        groups_by_identity: dict[tuple[str, str], ScenarioRunPlanAtomicGroup] = {}
        grouped_by_name: dict[str, list[ScenarioRunPlanAtomicGroup]] = {}
        seeds_by_id = {seed.id: seed for seed in plan.seed_groups}
        seed_ids_by_group_and_objective: dict[tuple[str, str], tuple[str, ...]] = {}
        planned_units: set[_ResultUnitIdentity] = set()
        for group in plan.atomic_groups:
            groups_by_identity[(group.atomic_attack_name, group.technique_eval_hash)] = group
            grouped_by_name.setdefault(group.atomic_attack_name, []).append(group)
            seed_ids_by_objective: dict[str, list[str]] = {}
            for seed_id in group.seed_group_ids:
                seed = seeds_by_id[seed_id]
                seed_ids_by_objective.setdefault(seed.objective_sha256, []).append(seed_id)
            seed_ids_by_group_and_objective.update(
                {
                    (group.id, objective_sha256): tuple(seed_ids)
                    for objective_sha256, seed_ids in seed_ids_by_objective.items()
                }
            )
            planned_units.update(
                _ResultUnitIdentity(atomic_group_id=group.id, seed_group_id=seed_group_id)
                for seed_group_id in group.seed_group_ids
            )

        return cls(
            groups_by_identity=groups_by_identity,
            groups_by_name={name: tuple(groups) for name, groups in grouped_by_name.items()},
            seed_ids_by_group_and_objective=seed_ids_by_group_and_objective,
            planned_units=frozenset(planned_units),
        )

    def resolve_group(
        self,
        *,
        atomic_attack_name: str,
        technique_eval_hash: str | None,
    ) -> ScenarioRunPlanAtomicGroup | None:
        """
        Resolve one planned group from persisted attribution.

        Returns:
            ScenarioRunPlanAtomicGroup | None: The uniquely matching group.
        """
        if technique_eval_hash is not None:
            return self.groups_by_identity.get((atomic_attack_name, technique_eval_hash))
        matching_groups = self.groups_by_name.get(atomic_attack_name, ())
        return matching_groups[0] if len(matching_groups) == 1 else None


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

    def list_runs(self, *, limit: int = 100) -> ScenarioRunListResponse:
        """
        List scenario runs by querying the database (most recent first).

        Args:
            limit (int): Maximum number of runs to return. Defaults to 100.

        Returns:
            ScenarioRunListResponse with runs.
        """
        results = self._memory.get_scenario_result_headers(limit=limit)
        items = [self._build_list_response_from_header(scenario_result=result) for result in results]
        return ScenarioRunListResponse(items=items)

    def _build_list_response_from_header(self, *, scenario_result: ScenarioResult) -> ScenarioRunListItem:
        """
        Build a bounded run-history item without hydrating attack results.

        Returns:
            ScenarioRunListItem: Lightweight run metadata.
        """
        status = scenario_result.scenario_run_state
        terminal = status in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        plan = self._load_run_plan(scenario_result=scenario_result)
        total_attacks = sum(len(group.seed_group_ids) for group in plan.atomic_groups) if plan is not None else None
        techniques_used = (
            list(dict.fromkeys(group.display_group for group in plan.atomic_groups)) if plan is not None else []
        )
        updated_at = (
            scenario_result.completion_time
            if terminal and scenario_result.completion_time is not None
            else scenario_result.creation_time
        )
        return ScenarioRunListItem(
            scenario_result_id=str(scenario_result.id),
            scenario_name=scenario_result.scenario_name,
            scenario_registry_name=plan.scenario_registry_name if plan else None,
            scenario_version=scenario_result.scenario_version,
            status=status,
            created_at=scenario_result.creation_time,
            updated_at=updated_at,
            error=scenario_result.error_message,
            error_type=scenario_result.error_type,
            techniques_used=techniques_used,
            total_attacks=total_attacks,
            labels=scenario_result.labels,
            completed_at=scenario_result.completion_time if terminal else None,
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
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
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
        plan = self._load_run_plan(scenario_result=scenario_result)
        plan_lookup = _ScenarioPlanLookup.from_plan(plan=plan)

        # Build result fields from DB (always computed so in-progress runs show progress)
        total_attacks, completed_attacks, objective_achieved_rate = self._calculate_progress_counts(
            scenario_result=scenario_result,
            plan=plan,
            plan_lookup=plan_lookup,
        )
        techniques_used = (
            list(dict.fromkeys(group.display_group for group in plan.atomic_groups))
            if plan is not None
            else scenario_result.get_techniques_used()
        )

        # Surface per-attack errors and retry pressure regardless of overall run status:
        # a COMPLETED scenario can still hide errored objectives or rate-limit retries.
        failed_attacks: list[AttackErrorSummary] = []
        attack_retries: list[AttackRetrySummary] = []
        total_retries = 0
        attempts_by_unit: dict[_ResultUnitIdentity, int] = {}
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
                unit_identity = self._resolve_result_unit_identity(
                    atomic_attack_name=atomic_attack_name,
                    attack_result=attack_result,
                    plan_lookup=plan_lookup,
                )
                attempts_by_unit[unit_identity] = attempts_by_unit.get(unit_identity, 0) + 1
                retries = getattr(attack_result, "total_retries", 0)
                if isinstance(retries, int):
                    total_retries += retries

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
        total_retries += sum(max(0, attempt_count - 1) for attempt_count in attempts_by_unit.values())

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
        )

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

    @staticmethod
    def _load_run_plan(*, scenario_result: ScenarioResult) -> ScenarioRunPlan | None:
        """
        Load a validated plan from scenario metadata.

        Returns:
            ScenarioRunPlan | None: The stored plan, or None for a legacy row.
        """
        metadata = getattr(scenario_result, "metadata", None)
        raw_plan = (metadata or {}).get(SCENARIO_RUN_PLAN_METADATA_KEY)
        return ScenarioRunPlan.model_validate(raw_plan) if raw_plan is not None else None

    @staticmethod
    def _resolve_result_unit_identity(
        *,
        atomic_attack_name: str,
        attack_result: AttackResult,
        plan_lookup: _ScenarioPlanLookup,
    ) -> _ResultUnitIdentity:
        """
        Resolve one attack attempt to its stable planned-unit identity.

        Returns:
            _ResultUnitIdentity: The atomic-group and seed-group IDs.
        """
        atomic_identifier = attack_result.atomic_attack_identifier
        typed_identifier = (
            AtomicAttackIdentifier.from_component_identifier(atomic_identifier)
            if isinstance(atomic_identifier, ComponentIdentifier)
            else None
        )
        objective = str(attack_result.objective)
        attribution_data = attack_result.attribution_data
        attributed_seed_group_id = attribution_data.get("seed_group_id") if isinstance(attribution_data, dict) else None
        seed_group_id = str(attributed_seed_group_id) if attributed_seed_group_id else ""
        if not seed_group_id and typed_identifier is not None and typed_identifier.seed_identifiers:
            seed_group_id = typed_identifier.logical_seed_group_id

        atomic_group_id = atomic_attack_name
        eval_hash = attribution_data.get("parent_eval_hash") if isinstance(attribution_data, dict) else None
        planned_group = plan_lookup.resolve_group(
            atomic_attack_name=atomic_attack_name,
            technique_eval_hash=str(eval_hash) if eval_hash is not None else None,
        )
        if planned_group is not None:
            atomic_group_id = planned_group.id
            if not seed_group_id:
                objective_sha256 = to_sha256(objective)
                matching_seed_ids = plan_lookup.seed_ids_by_group_and_objective.get(
                    (planned_group.id, objective_sha256),
                    (),
                )
                if len(matching_seed_ids) == 1:
                    seed_group_id = matching_seed_ids[0]
        if not seed_group_id:
            seed_group_id = config_hash({"objective": objective})
        return _ResultUnitIdentity(atomic_group_id=atomic_group_id, seed_group_id=seed_group_id)

    def _calculate_progress_counts(
        self,
        *,
        scenario_result: ScenarioResult,
        plan: ScenarioRunPlan | None,
        plan_lookup: _ScenarioPlanLookup,
    ) -> tuple[int, int, int]:
        """
        Calculate planned-unit totals without inflating retries or error attempts.

        Returns:
            tuple[int, int, int]: Total, completed, and success-rate percentage.
        """
        latest_result_by_unit: dict[_ResultUnitIdentity, AttackResult] = {}
        for atomic_attack_name, results in scenario_result.attack_results.items():
            for attack_result in results:
                unit_identity = self._resolve_result_unit_identity(
                    atomic_attack_name=atomic_attack_name,
                    attack_result=attack_result,
                    plan_lookup=plan_lookup,
                )
                previous = latest_result_by_unit.get(unit_identity)
                if previous is None or self._result_order_key(attack_result) > self._result_order_key(previous):
                    latest_result_by_unit[unit_identity] = attack_result

        planned_units = plan_lookup.planned_units if plan is not None else frozenset(latest_result_by_unit)
        total = len(planned_units)
        completed_results = [result for unit, result in latest_result_by_unit.items() if unit in planned_units]
        completed = len(completed_results)
        succeeded = sum(result.outcome == AttackOutcome.SUCCESS for result in completed_results)
        rate = int((succeeded / completed) * 100) if completed else 0
        return total, completed, rate

    @staticmethod
    def _result_order_key(attack_result: AttackResult) -> tuple[datetime, str]:
        """Return a deterministic chronological key for one hydrated result attempt."""
        timestamp = attack_result.timestamp
        if not isinstance(timestamp, datetime):
            timestamp = datetime.min.replace(tzinfo=timezone.utc)
        return timestamp, str(attack_result.attack_result_id)

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

        cursor = self._decode_progress_cursor(since=since, scenario_result_id=scenario_result_id)
        deltas, has_more = self._memory.get_scenario_attack_result_deltas(
            scenario_result_id=scenario_result_id,
            cursor=cursor,
            limit=limit,
        )
        plan = self._load_run_plan(scenario_result=header_result)
        plan_lookup = _ScenarioPlanLookup.from_plan(plan=plan)
        plan_complete = plan is not None
        response_plan = plan if since is None else None
        if plan is None and since is None:
            response_plan = self._synthesize_legacy_plan(deltas=deltas)

        response_plan_lookup = plan_lookup if plan is not None else _ScenarioPlanLookup.from_plan(plan=response_plan)
        results = [self._map_progress_delta(delta=delta, plan_lookup=response_plan_lookup) for delta in deltas]
        next_cursor = (
            self._encode_progress_cursor(scenario_result_id=scenario_result_id, delta=deltas[-1]) if deltas else since
        )
        terminal = header_result.scenario_run_state in (
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.CANCELLED,
        )
        return ScenarioRunProgress(
            run=ScenarioProgressHeader(
                scenario_result_id=scenario_result_id,
                scenario_name=header_result.scenario_name,
                scenario_registry_name=plan.scenario_registry_name if plan else None,
                scenario_version=header_result.scenario_version,
                status=header_result.scenario_run_state,
                created_at=header_result.creation_time,
                completed_at=header_result.completion_time if terminal else None,
            ),
            plan=response_plan,
            reset=False,
            active_atomic_group_ids=list(active_group_ids),
            results=results,
            next_cursor=next_cursor,
            has_more=has_more,
            plan_complete=plan_complete,
        )

    @staticmethod
    def _map_progress_delta(
        *,
        delta: ScenarioAttackResultDelta,
        plan_lookup: _ScenarioPlanLookup,
    ) -> ScenarioProgressResult:
        """
        Map a lightweight memory row to its REST progress representation.

        Returns:
            ScenarioProgressResult: The mapped progress delta.
        """
        atomic_attack_name = str(delta.attribution_data.get("parent_collection") or "")
        eval_hash = delta.attribution_data.get("parent_eval_hash")
        atomic_group_id = config_hash(
            {"atomic_attack_name": atomic_attack_name, "technique_eval_hash": eval_hash or ""}
        )
        planned_group = plan_lookup.resolve_group(
            atomic_attack_name=atomic_attack_name,
            technique_eval_hash=str(eval_hash) if eval_hash is not None else None,
        )
        if planned_group is not None:
            atomic_group_id = planned_group.id
        attributed_seed_group_id = delta.attribution_data.get("seed_group_id")
        seed_group_id = str(attributed_seed_group_id) if attributed_seed_group_id else ""
        if (
            not seed_group_id
            and delta.atomic_attack_identifier is not None
            and delta.atomic_attack_identifier.seed_identifiers
        ):
            seed_group_id = delta.atomic_attack_identifier.logical_seed_group_id
        if not seed_group_id and delta.objective_sha256:
            matching_seed_ids = plan_lookup.seed_ids_by_group_and_objective.get(
                (atomic_group_id, delta.objective_sha256),
                (),
            )
            if len(matching_seed_ids) == 1:
                seed_group_id = matching_seed_ids[0]
        if not seed_group_id:
            seed_group_id = config_hash({"objective": delta.objective})
        return ScenarioProgressResult(
            attack_result_id=delta.attack_result_id,
            atomic_group_id=atomic_group_id,
            atomic_attack_name=atomic_attack_name,
            seed_group_id=seed_group_id,
            outcome=delta.outcome,
            execution_time_ms=delta.execution_time_ms,
            timestamp=delta.timestamp,
            total_retries=delta.total_retries,
            retries=delta.retry_events,
            error_type=delta.error_type,
            error_message=delta.error_message,
        )

    @staticmethod
    def _synthesize_legacy_plan(*, deltas: list[ScenarioAttackResultDelta]) -> ScenarioRunPlan:
        """
        Synthesize only known completed legacy units without claiming pending totals.

        Returns:
            ScenarioRunPlan: An incomplete plan containing only known units.
        """
        seeds: dict[str, ScenarioRunPlanSeedGroup] = {}
        groups: dict[str, ScenarioRunPlanAtomicGroup] = {}
        seen_seed_ids_by_group: dict[str, set[str]] = {}
        empty_plan_lookup = _ScenarioPlanLookup.from_plan(plan=None)
        for delta in deltas:
            mapped = ScenarioRunService._map_progress_delta(
                delta=delta,
                plan_lookup=empty_plan_lookup,
            )
            seeds.setdefault(
                mapped.seed_group_id,
                ScenarioRunPlanSeedGroup(
                    id=mapped.seed_group_id,
                    objective_sha256=delta.objective_sha256 or to_sha256(delta.objective),
                    objective=delta.objective,
                ),
            )
            group = groups.setdefault(
                mapped.atomic_group_id,
                ScenarioRunPlanAtomicGroup(
                    id=mapped.atomic_group_id,
                    atomic_attack_name=mapped.atomic_attack_name,
                    display_group=mapped.atomic_attack_name,
                    technique_eval_hash=str(delta.attribution_data.get("parent_eval_hash") or ""),
                    seed_group_ids=[],
                ),
            )
            seen_seed_ids = seen_seed_ids_by_group.setdefault(mapped.atomic_group_id, set())
            if mapped.seed_group_id not in seen_seed_ids:
                seen_seed_ids.add(mapped.seed_group_id)
                group.seed_group_ids.append(mapped.seed_group_id)
        return ScenarioRunPlan(atomic_groups=list(groups.values()), seed_groups=list(seeds.values()))

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
