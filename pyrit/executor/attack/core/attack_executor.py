# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Simplified AttackExecutor that uses AttackParameters directly.

This is the new, cleaner design that leverages the params_type architecture.
"""

import asyncio
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Optional,
    TypeVar,
)

from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.executor.attack.core.attack_strategy import (
    AttackStrategy,
    AttackStrategyContextT,
    AttackStrategyResultT,
)
from pyrit.models import SeedAttackGroup

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.score import TrueFalseScorer

AttackResultT = TypeVar("AttackResultT")


@dataclass
class AttackExecutorResult(Generic[AttackResultT]):
    """
    Result container for attack execution, supporting both full and partial completion.

    This class holds results from parallel attack execution. It is iterable and
    behaves like a list in the common case where all objectives complete successfully.

    When some objectives don't complete (throw exceptions), access incomplete_objectives
    to retrieve the failures, or use raise_if_incomplete() to raise the first exception.

    Note: "completed" means the execution finished, not that the attack objective was achieved.
    """

    completed_results: list[AttackResultT]
    incomplete_objectives: list[tuple[str, BaseException]]
    input_indices: list[int] = field(default_factory=list)
    """Maps each completed result to its position in the original input sequence.

    ``input_indices[i]`` is the index in the original objectives/seed_groups/params
    list that produced ``completed_results[i]``.  When some inputs fail, this lets
    callers correlate results back to the specific input that produced them.
    """

    def __iter__(self) -> Iterator[AttackResultT]:
        """
        Iterate over completed results.

        Returns:
            Iterator over completed attack results.
        """
        return iter(self.completed_results)

    def __len__(self) -> int:
        """Return number of completed results."""
        return len(self.completed_results)

    def __getitem__(self, index: int) -> AttackResultT:
        """
        Access completed results by index.

        Returns:
            The attack result at the specified index.
        """
        return self.completed_results[index]

    @property
    def has_incomplete(self) -> bool:
        """Check if any objectives didn't complete execution."""
        return len(self.incomplete_objectives) > 0

    @property
    def all_completed(self) -> bool:
        """Check if all objectives completed execution."""
        return len(self.incomplete_objectives) == 0

    @property
    def exceptions(self) -> list[BaseException]:
        """Get all exceptions from incomplete objectives."""
        return [exception for _, exception in self.incomplete_objectives]

    def raise_if_incomplete(self) -> None:
        """Raise the first exception if any objectives are incomplete."""
        if self.incomplete_objectives:
            raise self.incomplete_objectives[0][1]

    def get_results(self) -> list[AttackResultT]:
        """
        Get completed results, raising if any incomplete.

        Returns:
            List of completed attack results.
        """
        self.raise_if_incomplete()
        return self.completed_results


class AttackExecutor:
    """
    Manages the execution of attack strategies with support for parallel execution.

    The AttackExecutor provides controlled execution of attack strategies with
    concurrency limiting. It uses the attack's params_type to create parameters
    from seed groups.
    """

    def __init__(self, *, max_concurrency: int = 1) -> None:
        """
        Initialize the attack executor with configurable concurrency control.

        Args:
            max_concurrency: Maximum number of concurrent attack executions (default: 1).
                A single ``asyncio.Semaphore`` of this size is used internally to gate
                both parameter-building (``from_seed_group_async``) and execution.
                Sharing one ``AttackExecutor`` across multiple call sites therefore
                shares a single concurrency budget across all of them.

        Raises:
            ValueError: If max_concurrency is not a positive integer.
        """
        if max_concurrency <= 0:
            raise ValueError(f"max_concurrency must be a positive integer, got {max_concurrency}")
        self._max_concurrency = max_concurrency
        # The semaphore is created lazily, NOT here. asyncio synchronization primitives
        # (Semaphore, Lock, Event, ...) bind to whichever event loop is running on their
        # first await and raise ``RuntimeError: <Semaphore> is bound to a different event
        # loop`` if you reuse them under a different loop. That breaks callers who
        # construct an AttackExecutor once (e.g. at module scope, or in a notebook helper)
        # and then run it under more than one ``asyncio.run(...)`` invocation. By
        # constructing the semaphore inside ``_get_semaphore()`` and rebuilding when the
        # running loop changes, one AttackExecutor instance is safe to reuse across loops.
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """
        Return the internal semaphore, (re)building it when the running event loop changes.

        Must be called from within a running event loop. The first call binds the
        semaphore to that loop; subsequent calls reuse it as long as the same loop is
        running. If the executor is reused under a *different* event loop later, the
        semaphore is rebuilt so we don't leak the binding from the previous loop.

        Returns:
            asyncio.Semaphore: A semaphore bound to the currently running event loop,
                with permits equal to ``self._max_concurrency``.
        """
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
            self._semaphore_loop = loop
        return self._semaphore

    async def execute_attack_from_seed_groups_async(
        self,
        *,
        attack: AttackStrategy[AttackStrategyContextT, AttackStrategyResultT],
        seed_groups: Sequence[SeedAttackGroup],
        adversarial_chat: Optional["PromptTarget"] = None,
        objective_scorer: Optional["TrueFalseScorer"] = None,
        field_overrides: Sequence[dict[str, Any]] | None = None,
        return_partial_on_failure: bool = False,
        attribution: AttackResultAttribution | None = None,
        **broadcast_fields: Any,
    ) -> AttackExecutorResult[AttackStrategyResultT]:
        """
        Execute attacks in parallel, extracting parameters from SeedAttackGroups.

        Uses the attack's params_type.from_seed_group() to extract parameters,
        automatically handling which fields the attack accepts.

        Args:
            attack: The attack strategy to execute.
            seed_groups: SeedAttackGroups containing objectives and optional prompts.
            adversarial_chat: Optional chat target for generating adversarial prompts
                or simulated conversations. Required when seed groups contain
                SeedSimulatedConversation configurations.
            objective_scorer: Optional scorer for evaluating simulated conversations.
                Required when seed groups contain SeedSimulatedConversation configurations.
            field_overrides: Optional per-seed-group field overrides. If provided,
                must match the length of seed_groups. Each dict is passed to
                from_seed_group() as overrides.
            return_partial_on_failure: If True, returns partial results when some
                objectives fail. If False (default), raises the first exception.
            attribution: Optional ``AttackResultAttribution`` stamped onto every
                per-task ``AttackContext`` so the persisted ``AttackResultEntry``
                row carries ``attribution_parent_id`` + ``attribution_data``.
                When ``None`` (default), no attribution is applied. The same
                attribution is shared across all tasks; per-task identity is
                reconstructed from the row's own ``objective_sha256``.
            **broadcast_fields: Fields applied to all seed groups (e.g., memory_labels).
                Per-seed-group field_overrides take precedence.

        Returns:
            AttackExecutorResult with completed results and any incomplete objectives.

        Raises:
            ValueError: If seed_groups is empty or field_overrides length doesn't match.
            BaseException: If return_partial_on_failure=False and any objective fails.
        """
        if not seed_groups:
            raise ValueError("At least one seed_group must be provided")

        if field_overrides is not None and len(field_overrides) != len(seed_groups):
            raise ValueError(
                f"field_overrides length ({len(field_overrides)}) must match seed_groups length ({len(seed_groups)})"
            )

        params_type = attack.params_type

        # Build params list using from_seed_group_async with concurrency control
        # This can take time if the SeedSimulatedConversation generation is included
        semaphore = self._get_semaphore()

        async def build_params_async(i: int, sg: SeedAttackGroup) -> AttackParameters:
            async with semaphore:
                combined_overrides = dict(broadcast_fields)
                if field_overrides is not None:
                    combined_overrides.update(field_overrides[i])
                return await params_type.from_seed_group_async(
                    seed_group=sg,
                    adversarial_chat=adversarial_chat,
                    objective_scorer=objective_scorer,
                    **combined_overrides,
                )

        params_list = list(await asyncio.gather(*[build_params_async(i, sg) for i, sg in enumerate(seed_groups)]))

        return await self._execute_with_params_list_async(
            attack=attack,
            params_list=params_list,
            return_partial_on_failure=return_partial_on_failure,
            attribution=attribution,
        )

    async def execute_attack_async(
        self,
        *,
        attack: AttackStrategy[AttackStrategyContextT, AttackStrategyResultT],
        objectives: Sequence[str],
        field_overrides: Sequence[dict[str, Any]] | None = None,
        return_partial_on_failure: bool = False,
        attribution: AttackResultAttribution | None = None,
        **broadcast_fields: Any,
    ) -> AttackExecutorResult[AttackStrategyResultT]:
        """
        Execute attacks in parallel for each objective.

        Creates AttackParameters directly from objectives and field values.

        Args:
            attack: The attack strategy to execute.
            objectives: List of attack objectives.
            field_overrides: Optional per-objective field overrides. If provided,
                must match the length of objectives.
            return_partial_on_failure: If True, returns partial results when some
                objectives fail. If False (default), raises the first exception.
            attribution: Optional ``AttackResultAttribution`` stamped onto every
                per-task ``AttackContext`` so the persistence path can record
                orchestrator linkage. When ``None``, no attribution is applied.
            **broadcast_fields: Fields applied to all objectives (e.g., memory_labels).
                Per-objective field_overrides take precedence.

        Returns:
            AttackExecutorResult with completed results and any incomplete objectives.

        Raises:
            ValueError: If objectives is empty or field_overrides length doesn't match.
            BaseException: If return_partial_on_failure=False and any objective fails.
        """
        if not objectives:
            raise ValueError("At least one objective must be provided")

        if field_overrides is not None and len(field_overrides) != len(objectives):
            raise ValueError(
                f"field_overrides length ({len(field_overrides)}) must match objectives length ({len(objectives)})"
            )

        params_type = attack.params_type

        # Build params list
        params_list: list[AttackParameters] = []
        for i, objective in enumerate(objectives):
            # Start with broadcast fields
            fields = dict(broadcast_fields)

            # Apply per-objective overrides
            if field_overrides is not None:
                fields.update(field_overrides[i])

            # Add objective
            fields["objective"] = objective

            params = params_type(**fields)
            params_list.append(params)

        return await self._execute_with_params_list_async(
            attack=attack,
            params_list=params_list,
            return_partial_on_failure=return_partial_on_failure,
            attribution=attribution,
        )

    async def _execute_with_params_list_async(
        self,
        *,
        attack: AttackStrategy[AttackStrategyContextT, AttackStrategyResultT],
        params_list: Sequence[AttackParameters],
        return_partial_on_failure: bool = False,
        attribution: AttackResultAttribution | None = None,
    ) -> AttackExecutorResult[AttackStrategyResultT]:
        """
        Execute attacks in parallel with a list of pre-built parameters.

        This is the core execution method. It creates contexts from params
        and runs attacks with concurrency control.

        Args:
            attack: The attack strategy to execute.
            params_list: List of AttackParameters, one per execution.
            return_partial_on_failure: If True, returns partial results on failure.
            attribution: Optional ``AttackResultAttribution`` stamped onto every
                per-task ``AttackContext`` so the persistence path can record
                orchestrator linkage.

        Returns:
            AttackExecutorResult with completed results and any incomplete objectives.
        """
        semaphore = self._get_semaphore()

        async def run_one_async(index: int, params: AttackParameters) -> AttackStrategyResultT:
            async with semaphore:
                context = attack._context_type(params=params)
                if attribution is not None:
                    context._attribution = attribution
                return await attack.execute_with_context_async(context=context)

        tasks = [run_one_async(i, p) for i, p in enumerate(params_list)]
        results_or_exceptions = await asyncio.gather(*tasks, return_exceptions=True)

        return self._process_execution_results(
            objectives=[p.objective for p in params_list],
            results_or_exceptions=list(results_or_exceptions),
            return_partial_on_failure=return_partial_on_failure,
        )

    def _process_execution_results(
        self,
        *,
        objectives: Sequence[str],
        results_or_exceptions: list[Any],
        return_partial_on_failure: bool,
    ) -> AttackExecutorResult[AttackStrategyResultT]:
        """
        Process results from parallel execution into an AttackExecutorResult.

        Args:
            objectives: The objectives that were executed.
            results_or_exceptions: Results or exceptions from asyncio.gather.
            return_partial_on_failure: Whether to return partial results on failure.

        Returns:
            AttackExecutorResult with completed and incomplete results.

        Raises:
            BaseException: If return_partial_on_failure=False and any failed.
        """
        completed: list[AttackStrategyResultT] = []
        incomplete: list[tuple[str, BaseException]] = []
        completed_indices: list[int] = []

        for i, (objective, result) in enumerate(zip(objectives, results_or_exceptions, strict=False)):
            if isinstance(result, BaseException):
                incomplete.append((objective, result))
            else:
                completed.append(result)
                completed_indices.append(i)

        executor_result: AttackExecutorResult[AttackStrategyResultT] = AttackExecutorResult(
            completed_results=completed,
            incomplete_objectives=incomplete,
            input_indices=completed_indices,
        )

        if not return_partial_on_failure:
            executor_result.raise_if_incomplete()

        return executor_result
