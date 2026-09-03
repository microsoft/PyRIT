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
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Literal

from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultKeysetCursor, CentralMemory
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    ScenarioAtomicGroupProgress,
    ScenarioAttackResultDelta,
    ScenarioAttackTechniqueDetails,
    ScenarioComponentIdentity,
    ScenarioDisplayGroupProgress,
    ScenarioObjectiveScorer,
    ScenarioObjectiveScorerMetrics,
    ScenarioProgressCounts,
    ScenarioProgressHeader,
    ScenarioProgressResult,
    ScenarioProgressSummary,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunProgress,
    ScenarioRunState,
    ScenarioScorerIdentity,
    ScenarioSeedGroupProgress,
    ScenarioTechniqueProgress,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    config_hash,
    project_behavioral_identity,
)
from pyrit.models.catalog.scenario import (
    AttackErrorSummary,
    AttackRetrySummary,
    RunScenarioRequest,
    ScenarioRunListItem,
    ScenarioRunSummary,
    ScenarioTechniqueSummary,
)
from pyrit.registry import InitializerRegistry, ScenarioRegistry
from pyrit.scenario import Scenario
from pyrit.score.scorer_evaluation.scorer_metrics_io import find_objective_metrics_by_eval_hash

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_RUNS = 3
_PROGRESS_CACHE_MAX_RUNS = 32

# The only display decision this layer still owns: technique seeds are rendered as
# content, so the REST payload carries just what the UI draws. Every other narrowing
# (dropping operational target params, unwrapping multi-targets, dropping the
# separately returned objective scorer) is declared by the identifier types
# themselves and applied by project_behavioral_identity.
_TECHNIQUE_SEEDS_CHILD = "technique_seeds"
_TECHNIQUE_SEED_DISPLAY_PARAMS = ("value", "data_type")


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


@dataclass
class _ProgressCacheEntry:
    """Mapped progress state for one scenario run."""

    plan_signature: str | None = None
    deltas: list[ScenarioAttackResultDelta] = field(default_factory=list)
    results: list[ScenarioProgressResult] = field(default_factory=list)
    cursor: AttackResultKeysetCursor | None = None
    summary: ScenarioProgressSummary | None = None
    summary_state: tuple[tuple[str, ...], bool, bool] | None = None


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
        self._progress_cache: OrderedDict[str, _ProgressCacheEntry] = OrderedDict()
        self._progress_cache_lock = Lock()
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
        persisted_retries: list[int] = []
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
        total_retries = self._total_retry_pressure(
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

        plan = self._load_run_plan(scenario_result=header_result)
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
        all_deltas, all_results, summary, summary_plan = self._get_progress_snapshot(
            scenario_result_id=scenario_result_id,
            plan=plan,
            plan_complete=plan_complete,
            active_group_ids=active_group_ids,
            terminal=terminal,
            objective_scorer_identifier=objective_scorer_identifier,
        )
        available = [
            (delta, result)
            for delta, result in zip(all_deltas, all_results, strict=True)
            if cursor is None
            or (delta.timestamp, uuid.UUID(delta.attack_result_id))
            > (cursor.timestamp, uuid.UUID(cursor.attack_result_id))
        ]
        page = available[:limit]
        deltas = [delta for delta, _ in page]
        results = [result for _, result in page]
        has_more = len(available) > limit
        response_plan = summary_plan if since is None else None
        next_cursor = (
            self._encode_progress_cursor(scenario_result_id=scenario_result_id, delta=deltas[-1]) if deltas else since
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
            results=results,
            summary=summary,
            next_cursor=next_cursor,
            has_more=has_more,
            plan_complete=plan_complete,
        )

    def _get_progress_snapshot(
        self,
        *,
        scenario_result_id: str,
        plan: ScenarioRunPlan | None,
        plan_complete: bool,
        active_group_ids: Sequence[str],
        terminal: bool,
        objective_scorer_identifier: ComponentIdentifier | None,
    ) -> tuple[
        list[ScenarioAttackResultDelta],
        list[ScenarioProgressResult],
        ScenarioProgressSummary,
        ScenarioRunPlan,
    ]:
        """
        Refresh and return cached mapped progress state.

        Returns:
            tuple: Deltas, mapped results, canonical summary, and effective plan.
        """
        plan_signature = plan.model_dump_json() if plan is not None else None
        with self._progress_cache_lock:
            entry = self._progress_cache.get(scenario_result_id)
            if entry is not None and entry.plan_signature == plan_signature:
                has_unenriched_identifier = any(
                    delta.atomic_attack_identifier is not None and not delta.atomic_attack_identifier.seed_identifiers
                    for delta in entry.deltas
                )
                was_terminal = entry.summary_state is not None and entry.summary_state[1]
                if has_unenriched_identifier and (not terminal or not was_terminal):
                    entry = None
            if entry is None or entry.plan_signature != plan_signature:
                entry = _ProgressCacheEntry(plan_signature=plan_signature)
                self._progress_cache[scenario_result_id] = entry
            self._progress_cache.move_to_end(scenario_result_id)
            while len(self._progress_cache) > _PROGRESS_CACHE_MAX_RUNS:
                self._progress_cache.popitem(last=False)

            first_new_index = len(entry.deltas)
            while True:
                page, has_more = self._memory.get_scenario_attack_result_deltas(
                    scenario_result_id=scenario_result_id,
                    cursor=entry.cursor,
                    limit=500,
                )
                entry.deltas.extend(page)
                if page:
                    last = page[-1]
                    entry.cursor = AttackResultKeysetCursor(
                        timestamp=last.timestamp,
                        attack_result_id=last.attack_result_id,
                    )
                if not has_more:
                    break
                if not page:
                    raise RuntimeError("Scenario progress storage returned an empty page with has_more=True.")

            summary_plan = plan or self._synthesize_legacy_plan(deltas=entry.deltas)
            if len(entry.results) < len(entry.deltas):
                plan_lookup = _ScenarioPlanLookup.from_plan(plan=summary_plan)
                entry.results.extend(
                    self._map_progress_delta(delta=delta, plan_lookup=plan_lookup)
                    for delta in entry.deltas[len(entry.results) :]
                )

            summary_state = (tuple(active_group_ids), terminal, plan_complete)
            if entry.summary is None or first_new_index < len(entry.deltas) or entry.summary_state != summary_state:
                technique_details_by_group = self._build_technique_details_by_group(
                    deltas=entry.deltas,
                    results=entry.results,
                )
                entry.summary = self._build_progress_summary(
                    plan=summary_plan,
                    plan_complete=plan_complete,
                    results=entry.results,
                    active_group_ids=active_group_ids,
                    terminal=terminal,
                    objective_scorer_identifier=objective_scorer_identifier,
                    technique_details_by_group=technique_details_by_group,
                )
                entry.summary_state = summary_state

            return (
                list(entry.deltas),
                list(entry.results),
                entry.summary,
                summary_plan,
            )

    @staticmethod
    def _build_technique_details_by_group(
        *,
        deltas: Sequence[ScenarioAttackResultDelta],
        results: Sequence[ScenarioProgressResult],
    ) -> dict[str, ScenarioAttackTechniqueDetails]:
        """
        Build one technique-details projection for each enriched atomic group.

        Returns:
            Details keyed by atomic group ID.
        """
        details_by_group: dict[str, ScenarioAttackTechniqueDetails] = {}
        for delta, result in zip(deltas, results, strict=True):
            atomic_identifier = delta.atomic_attack_identifier
            if (
                result.atomic_group_id in details_by_group
                or atomic_identifier is None
                or not atomic_identifier.seed_identifiers
                or atomic_identifier.attack_technique is None
            ):
                continue
            details_by_group[result.atomic_group_id] = ScenarioRunService._build_attack_technique_details(
                technique_identifier=atomic_identifier.attack_technique
            )
        return details_by_group

    @staticmethod
    def _build_progress_summary(
        *,
        plan: ScenarioRunPlan,
        plan_complete: bool,
        results: Sequence[ScenarioProgressResult],
        active_group_ids: Sequence[str],
        terminal: bool,
        objective_scorer_identifier: ComponentIdentifier | None,
        technique_details_by_group: dict[str, ScenarioAttackTechniqueDetails],
    ) -> ScenarioProgressSummary:
        """
        Build canonical progress rollups from a plan and persisted attempts.

        Returns:
            ScenarioProgressSummary: Progress grouped for client display.
        """
        attempts_by_unit: dict[_ResultUnitIdentity, list[ScenarioProgressResult]] = {}
        for result in results:
            identity = _ResultUnitIdentity(
                atomic_group_id=result.atomic_group_id,
                seed_group_id=result.seed_group_id,
            )
            attempts_by_unit.setdefault(identity, []).append(result)

        def aggregate(
            *,
            units: Sequence[_ResultUnitIdentity],
            planned: int | None,
        ) -> ScenarioProgressCounts:
            completed = 0
            succeeded = 0
            errors = 0
            retries = 0
            for unit in units:
                attempts = attempts_by_unit.get(unit, [])
                if attempts:
                    completed += 1
                    succeeded += int(attempts[-1].outcome == AttackOutcome.SUCCESS)
                    errors += sum(int(attempt.outcome == AttackOutcome.ERROR) for attempt in attempts)
                    retries += ScenarioRunService._total_retry_pressure(
                        attempts_per_unit=[len(attempts)],
                        persisted_retries=[attempt.total_retries for attempt in attempts],
                    )
            return ScenarioProgressCounts(
                completed=completed,
                planned=planned,
                succeeded=succeeded,
                success_percentage=int((succeeded / completed) * 100) if completed else None,
                errors=errors,
                retries=retries,
            )

        group_units: dict[str, list[_ResultUnitIdentity]] = {
            group.id: [
                _ResultUnitIdentity(atomic_group_id=group.id, seed_group_id=seed_group_id)
                for seed_group_id in group.seed_group_ids
            ]
            for group in plan.atomic_groups
        }
        overall_units = (
            [unit for units in group_units.values() for unit in units] if plan_complete else list(attempts_by_unit)
        )
        overall = aggregate(
            units=overall_units,
            planned=len(overall_units) if plan_complete else None,
        )
        # When the plan is complete the rollups iterate planned units only, so any attempt
        # that failed attribution would silently vanish from every count. Surface it instead.
        planned_units = set(overall_units)
        unattributed_attempts = sum(
            len(attempts) for unit, attempts in attempts_by_unit.items() if unit not in planned_units
        )
        if unattributed_attempts:
            logger.warning(
                "%d persisted attempt(s) matched no planned execution unit and are excluded from "
                "scenario progress rollups.",
                unattributed_attempts,
            )
        latest_results = [attempts_by_unit[unit][-1] for unit in overall_units if attempts_by_unit.get(unit)]
        objective_scorer = ScenarioRunService._build_objective_scorer(
            scorer_identifier=objective_scorer_identifier,
            results=latest_results,
        )

        active_ids = set(active_group_ids)
        atomic_groups: list[ScenarioAtomicGroupProgress] = []
        for group in plan.atomic_groups:
            units = group_units[group.id]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            if not terminal and group.id in active_ids:
                group_status: Literal["RUNNING", "PENDING", "INCOMPLETE", "COMPLETED"] = "RUNNING"
            elif counts.planned is not None and counts.planned > 0 and counts.completed >= counts.planned:
                group_status = "COMPLETED"
            elif terminal:
                group_status = "INCOMPLETE"
            else:
                group_status = "PENDING"
            atomic_groups.append(
                ScenarioAtomicGroupProgress(
                    id=group.id,
                    atomic_attack_name=group.atomic_attack_name,
                    display_group=group.display_group,
                    status=group_status,
                    technique_details=technique_details_by_group.get(group.id),
                    **counts.model_dump(),
                )
            )
        status_order = {"RUNNING": 0, "PENDING": 1, "INCOMPLETE": 2, "COMPLETED": 3}
        atomic_groups.sort(
            key=lambda group: (
                status_order[group.status],
                group.display_group,
                group.atomic_attack_name,
            )
        )

        groups_by_technique: dict[str, list[ScenarioRunPlanAtomicGroup]] = {}
        groups_by_display: dict[str, list[ScenarioRunPlanAtomicGroup]] = {}
        for group in plan.atomic_groups:
            technique_name = group.technique_name or group.display_group
            groups_by_technique.setdefault(technique_name, []).append(group)
            groups_by_display.setdefault(group.display_group, []).append(group)
        display_groups: list[ScenarioDisplayGroupProgress] = []
        for display_group, groups in groups_by_display.items():
            units = [unit for group in groups for unit in group_units[group.id]]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            display_groups.append(
                ScenarioDisplayGroupProgress(
                    id=display_group,
                    display_group=display_group,
                    atomic_attack_names=list(dict.fromkeys(group.atomic_attack_name for group in groups)),
                    atomic_group_ids=[group.id for group in groups],
                    **counts.model_dump(),
                )
            )
        display_groups.sort(key=lambda group: group.display_group)

        techniques: list[ScenarioTechniqueProgress] = []
        for technique_name, groups in groups_by_technique.items():
            units = [unit for group in groups for unit in group_units[group.id]]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            descriptions = list(dict.fromkeys(group.description for group in groups if group.description))
            tags = sorted({tag for group in groups for tag in group.tags})
            techniques.append(
                ScenarioTechniqueProgress(
                    id=technique_name,
                    display_group=technique_name,
                    atomic_attack_names=list(dict.fromkeys(group.atomic_attack_name for group in groups)),
                    atomic_group_ids=[group.id for group in groups],
                    description=descriptions[0] if descriptions else None,
                    tags=tags,
                    **counts.model_dump(),
                )
            )
        techniques.sort(key=lambda technique: technique.display_group)

        seed_by_id = {seed.id: seed for seed in plan.seed_groups}
        seed_groups: list[ScenarioSeedGroupProgress] = []
        for seed_id, seed in seed_by_id.items():
            units = [
                _ResultUnitIdentity(atomic_group_id=group.id, seed_group_id=seed_id)
                for group in plan.atomic_groups
                if seed_id in group.seed_group_ids
            ]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            seed_groups.append(
                ScenarioSeedGroupProgress(
                    id=seed_id,
                    objective=seed.objective,
                    **counts.model_dump(),
                )
            )
        seed_groups.sort(key=lambda seed: seed.objective or seed.id)

        return ScenarioProgressSummary(
            overall=overall,
            objective_scorer=objective_scorer,
            display_groups=display_groups,
            techniques=techniques,
            seed_groups=seed_groups,
            atomic_groups=atomic_groups,
            unattributed_attempts=unattributed_attempts,
        )

    @staticmethod
    def _build_objective_scorer(
        *,
        scorer_identifier: ComponentIdentifier | None,
        results: Sequence[ScenarioProgressResult],
    ) -> ScenarioObjectiveScorer | None:
        """
        Build the objective scorer identity and its official evaluation metrics.

        Returns:
            ScenarioObjectiveScorer | None: Scorer information, or None when no scorer is known.
        """
        if scorer_identifier is None:
            scorer_names = {result.score.scorer_name for result in results if result.score is not None}
            if len(scorer_names) != 1:
                return None
            return ScenarioObjectiveScorer(
                component_name=next(iter(scorer_names)),
            )

        official_metrics = find_objective_metrics_by_eval_hash(
            eval_hash=ScorerEvaluationIdentifier(scorer_identifier).eval_hash
        )
        metrics = (
            ScenarioObjectiveScorerMetrics(
                accuracy=official_metrics.accuracy,
                accuracy_standard_error=official_metrics.accuracy_standard_error,
                f1_score=official_metrics.f1_score,
                precision=official_metrics.precision,
                recall=official_metrics.recall,
                average_score_time_seconds=official_metrics.average_score_time_seconds,
            )
            if official_metrics
            else None
        )
        identity = ScenarioRunService._build_scorer_identity(scorer_identifier=scorer_identifier)
        return ScenarioObjectiveScorer(**identity.model_dump(), metrics=metrics)

    @staticmethod
    def _build_scorer_identity(*, scorer_identifier: ComponentIdentifier) -> ScenarioScorerIdentity:
        """
        Project the complete scorer identity used to distinguish configurations.

        Returns:
            ScenarioScorerIdentity: Scorer parameters and nested component identities.
        """
        projected = project_behavioral_identity(
            scorer_identifier,
            identifier_type=ScorerIdentifier,
        )
        identity = ScenarioRunService._build_component_identity(component_identifier=projected)
        return ScenarioScorerIdentity(
            component_name=identity.component_name,
            parameters=identity.parameters,
            children=identity.children,
        )

    @staticmethod
    def _total_retry_pressure(*, attempts_per_unit: Iterable[int], persisted_retries: Iterable[int]) -> int:
        """
        Combine per-attempt retries with re-attempts of the same execution unit.

        Both the CLI-facing run summary and the GUI-facing progress rollups report
        this same quantity, so the definition lives here once.

        Returns:
            int: Total retry pressure.
        """
        within_attempts = sum(max(0, retries) for retries in persisted_retries)
        repeated_units = sum(max(0, count - 1) for count in attempts_per_unit)
        return within_attempts + repeated_units

    @staticmethod
    def _build_component_identity(*, component_identifier: ComponentIdentifier) -> ScenarioComponentIdentity:
        """
        Project a component identifier without duplicating component-specific schemas.

        Returns:
            ScenarioComponentIdentity: Behavioral parameters and recursive child identities.
        """
        children: dict[str, list[ScenarioComponentIdentity]] = {}
        for child_name, child_value in component_identifier.children.items():
            child_identifiers = child_value if isinstance(child_value, list) else [child_value]
            children[child_name] = [
                ScenarioRunService._build_component_identity(component_identifier=child) for child in child_identifiers
            ]
        return ScenarioComponentIdentity(
            component_name=component_identifier.class_name,
            parameters=dict(component_identifier.params),
            children=children,
        )

    @staticmethod
    def _build_attack_technique_details(
        *,
        technique_identifier: ComponentIdentifier,
    ) -> ScenarioAttackTechniqueDetails:
        """
        Build REST details for an attack technique.

        Returns:
            ScenarioAttackTechniqueDetails: The projected technique details.
        """
        projected = project_behavioral_identity(
            technique_identifier,
            identifier_type=AttackTechniqueIdentifier,
        )
        details = ScenarioRunService._build_attack_technique_component_details(component_identifier=projected)
        return ScenarioAttackTechniqueDetails(
            component_name=details.component_name,
            parameters=details.parameters,
            children=details.children,
        )

    @staticmethod
    def _build_attack_technique_component_details(
        *,
        component_identifier: ComponentIdentifier,
    ) -> ScenarioComponentIdentity:
        """
        Map an already-projected technique component to its REST shape.

        Returns:
            ScenarioComponentIdentity: The mapped component details.
        """
        children: dict[str, list[ScenarioComponentIdentity]] = {}
        for child_name, child_value in component_identifier.children.items():
            child_identifiers = child_value if isinstance(child_value, list) else [child_value]
            if child_name == _TECHNIQUE_SEEDS_CHILD:
                children[child_name] = [
                    ScenarioRunService._build_technique_seed_details(seed_identifier=child)
                    for child in child_identifiers
                ]
            else:
                children[child_name] = [
                    ScenarioRunService._build_attack_technique_component_details(component_identifier=child)
                    for child in child_identifiers
                ]

        return ScenarioComponentIdentity(
            component_name=component_identifier.class_name,
            parameters=dict(component_identifier.params),
            children=children,
        )

    @staticmethod
    def _build_technique_seed_details(*, seed_identifier: ComponentIdentifier) -> ScenarioComponentIdentity:
        """
        Keep only seed content needed by the REST attack details.

        Returns:
            ScenarioComponentIdentity: The simplified seed details.
        """
        parameters = {
            name: seed_identifier.params[name]
            for name in _TECHNIQUE_SEED_DISPLAY_PARAMS
            if seed_identifier.params.get(name) is not None
        }
        return ScenarioComponentIdentity(
            component_name=seed_identifier.class_name,
            parameters=parameters,
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
            conversation_id=delta.conversation_id,
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
            score=delta.score,
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
