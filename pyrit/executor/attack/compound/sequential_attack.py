# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``SequentialAttack`` — runs a sequence of inner ``AttackStrategy``
steps against a single objective, controlled by a ``SequencePolicy``.

The compound preserves the one-objective → one-``AttackResult`` invariant:
each invocation returns one ``SequentialAttackResult`` whose outcome
reflects the sequence according to the chosen ``SequencePolicy``.

Each inner step is dispatched through ``AttackExecutor``, so it
persists as its own first-class ``AttackResult`` row. The envelope result
records the inner ``attack_result_id`` of every attempt under
``metadata["attempt_result_ids"]`` so callers can fetch the per-attempt
details from memory.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from pyrit.executor.attack.core.attack_executor import AttackExecutor
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.models import AttackOutcome, AttackResult, SeedAttackGroup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.prompt_target import PromptTarget
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class SequencePolicy(str, Enum):
    """
    How a ``SequentialAttack`` iterates and aggregates its steps.

    Each policy bundles a stop condition (when to halt iteration) and an
    outcome rule (how to derive the envelope's outcome from the inner
    results), chosen so each policy matches a common use case.
    """

    FIRST_SUCCESS = "first_success"
    """Stop on the first ``AttackOutcome.SUCCESS``; continue past ERROR and FAILURE.
    Outcome: SUCCESS if any step succeeded, ERROR if every step errored, else FAILURE.
    Resilient adaptive default — keep trying other strategies past transient errors."""

    FIRST_DECISIVE = "first_decisive"
    """Stop on the first ``AttackOutcome.SUCCESS`` or ``AttackOutcome.ERROR``;
    continue past FAILURE. Outcome: SUCCESS if any step succeeded, ERROR if every
    step errored, else FAILURE. Use when ERRORs should short-circuit the sequence."""

    STRICT_ALL = "strict_all"
    """Stop on the first non-SUCCESS. Outcome: SUCCESS only if every step succeeded,
    ERROR if any step errored, else FAILURE. Pipeline semantics — each step is
    required."""

    EXHAUSTIVE = "exhaustive"
    """Run every step regardless of intermediate outcomes. Outcome: SUCCESS if any
    step succeeded, ERROR if every step errored, else FAILURE. Use for evaluation
    sweeps where you want to try everything."""

    LAST_RESULT = "last_result"
    """Run every step; inherit the last step's outcome verbatim. Use for chained
    refinement where the final attempt is canonical."""


@dataclass(frozen=True)
class SequentialAttackStep:
    """
    One step in a ``SequentialAttack``.

    Each step bundles an ``AttackStrategy`` with the inputs that the
    compound forwards to ``AttackExecutor`` when dispatching it.
    ``seed_group`` is required per step so callers compose seed groups up
    front (e.g. merging per-technique ``SeedAttackTechniqueGroup`` objects
    into a shared base) without any implicit fallback at the compound
    layer.

    Attributes:
        strategy (AttackStrategy): The inner attack to run for this step.
        seed_group (SeedAttackGroup): The seed group dispatched to the
            inner attack. Must carry the objective.
        adversarial_chat (PromptTarget | None): Forwarded to the executor
            for inner attacks that need an adversarial chat target (e.g.
            multi-turn attacks, or seed groups with simulated-conversation
            configs).
        objective_scorer (TrueFalseScorer | None): Forwarded to the
            executor for inner attacks that need an objective scorer.
        memory_labels (Mapping[str, str]): Per-step labels merged on top
            of the compound's ``context.memory_labels`` for this call.
    """

    strategy: AttackStrategy[Any, AttackResult]
    seed_group: SeedAttackGroup
    adversarial_chat: Optional[PromptTarget] = None
    objective_scorer: Optional[TrueFalseScorer] = None
    memory_labels: Mapping[str, str] = field(default_factory=dict)


@dataclass
class SequentialAttackResult(AttackResult):
    """
    Result of a ``SequentialAttack`` execution.

    Inherits every field from ``AttackResult``. The IDs of each inner
    attempt are stored in ``metadata["attempt_result_ids"]`` so callers
    can fetch the per-attempt rows from memory.
    """

    @property
    def attempt_result_ids(self) -> list[str]:
        """The ``attack_result_id`` of each inner attempt, in dispatch order."""
        return list(self.metadata.get("attempt_result_ids", []))


class SequentialAttack(AttackStrategy[AttackContext[AttackParameters], SequentialAttackResult]):
    """
    Run a sequence of ``AttackStrategy`` steps against one objective.

    Use this when an objective should be attacked by several techniques in
    sequence — for example "try Crescendo first, fall back to
    PromptSending" — without breaking the one-objective →
    one-``AttackResult`` invariant or pushing branching logic up to the
    Scenario layer. Each inner step runs as a real attack through
    ``AttackExecutor`` and persists its own row; the compound returns
    one ``SequentialAttackResult`` whose iteration and aggregation are
    controlled by ``SequencePolicy``.

    The default ``SequencePolicy.FIRST_SUCCESS`` matches the adaptive
    "try strategies until one works" pattern, resilient to transient
    inner errors. See ``SequencePolicy`` for the other policies
    (``FIRST_DECISIVE``, ``STRICT_ALL``, ``EXHAUSTIVE``, ``LAST_RESULT``).

    Example:

    .. code-block:: python

        sequential = SequentialAttack(
            objective_target=target,
            steps=[
                SequentialAttackStep(strategy=crescendo, seed_group=sg),
                SequentialAttackStep(strategy=prompt_sending, seed_group=sg),
            ],
        )
        result = await sequential.execute_async(objective="...")
    """

    ATTEMPT_RESULT_IDS_KEY: str = "attempt_result_ids"
    """Metadata key under which the per-attempt result IDs are stored."""

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        steps: Sequence[SequentialAttackStep],
        policy: SequencePolicy = SequencePolicy.FIRST_SUCCESS,
    ) -> None:
        """
        Args:
            objective_target (PromptTarget): Target the compound is
                nominally bound to (forwarded to ``AttackStrategy``
                for identifier construction). Each inner step runs against
                whatever target its own strategy is configured with.
            steps (Sequence[SequentialAttackStep]): Steps to run, in
                order. Must be non-empty.
            policy (SequencePolicy): Iteration + aggregation policy. Defaults to
                ``SequencePolicy.FIRST_SUCCESS`` (resilient adaptive).

        Raises:
            ValueError: If ``steps`` is empty.
        """
        if not steps:
            raise ValueError("steps must contain at least one SequentialAttackStep")

        super().__init__(
            objective_target=objective_target,
            context_type=AttackContext,
            # Inner steps expand their own next_message / prepended_conversation
            # via their own params_type; the compound takes no per-call message
            # overrides.
            params_type=AttackParameters.excluding("next_message", "prepended_conversation"),
            logger=logger,
        )
        self._steps: list[SequentialAttackStep] = list(steps)
        self._policy = policy
        self._executor = AttackExecutor(max_concurrency=1)

    def _validate_context(self, *, context: AttackContext[AttackParameters]) -> None:
        if not context.objective or context.objective.isspace():
            raise ValueError("Attack objective must be provided and non-empty")

    async def _setup_async(self, *, context: AttackContext[AttackParameters]) -> None:
        """No-op: per-step setup is owned by each inner strategy's executor."""

    async def _teardown_async(self, *, context: AttackContext[AttackParameters]) -> None:
        """No-op: per-step teardown is owned by each inner strategy's executor."""

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> SequentialAttackResult:
        results: list[AttackResult] = []

        for step in self._steps:
            labels = {**context.memory_labels, **dict(step.memory_labels)}
            result = await self._run_step_async(step=step, memory_labels=labels)
            results.append(result)
            if self._should_stop_after(result=result):
                break

        last_result = results[-1]
        outcome = self._compute_outcome(results=results)

        return SequentialAttackResult(
            conversation_id=last_result.conversation_id,
            objective=last_result.objective,
            attack_result_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            last_response=last_result.last_response,
            last_score=last_result.last_score,
            executed_turns=last_result.executed_turns,
            outcome=outcome,
            metadata={
                self.ATTEMPT_RESULT_IDS_KEY: [r.attack_result_id for r in results],
            },
        )

    async def _run_step_async(
        self,
        *,
        step: SequentialAttackStep,
        memory_labels: dict[str, str],
    ) -> AttackResult:
        """
        Execute one step via ``AttackExecutor`` and return its result.

        Isolated as a method so tests can patch the per-step call surface
        without monkey-patching ``AttackExecutor``.

        Returns:
            AttackResult: The ``AttackResult`` produced by the inner
            attack for ``step.seed_group``.

        Raises:
            BaseException: Re-raised from
                ``AttackExecutorResult.incomplete_objectives`` if the
                inner attack failed.
            RuntimeError: If the executor returned neither a completed
                result nor an incomplete objective (defensive guard).
        """
        executor_result = await self._executor.execute_attack_from_seed_groups_async(
            attack=step.strategy,
            seed_groups=[step.seed_group],
            adversarial_chat=step.adversarial_chat,
            objective_scorer=step.objective_scorer,
            memory_labels=memory_labels,
        )
        if executor_result.completed_results:
            return executor_result.completed_results[0]
        if executor_result.incomplete_objectives:
            raise executor_result.incomplete_objectives[0][1]
        raise RuntimeError(  # pragma: no cover - defensive
            "AttackExecutor returned neither completed nor incomplete results."
        )

    def _should_stop_after(self, *, result: AttackResult) -> bool:
        if self._policy is SequencePolicy.FIRST_SUCCESS:
            return result.outcome is AttackOutcome.SUCCESS
        if self._policy is SequencePolicy.FIRST_DECISIVE:
            return result.outcome in (AttackOutcome.SUCCESS, AttackOutcome.ERROR)
        if self._policy is SequencePolicy.STRICT_ALL:
            return result.outcome is not AttackOutcome.SUCCESS
        # EXHAUSTIVE and LAST_RESULT run every step to completion.
        return False

    def _compute_outcome(self, *, results: list[AttackResult]) -> AttackOutcome:
        if self._policy is SequencePolicy.LAST_RESULT:
            return results[-1].outcome
        if self._policy is SequencePolicy.STRICT_ALL:
            if all(r.outcome is AttackOutcome.SUCCESS for r in results):
                return AttackOutcome.SUCCESS
            if any(r.outcome is AttackOutcome.ERROR for r in results):
                return AttackOutcome.ERROR
            return AttackOutcome.FAILURE
        # FIRST_SUCCESS, FIRST_DECISIVE, EXHAUSTIVE all share any-success semantics.
        if any(r.outcome is AttackOutcome.SUCCESS for r in results):
            return AttackOutcome.SUCCESS
        if all(r.outcome is AttackOutcome.ERROR for r in results):
            return AttackOutcome.ERROR
        return AttackOutcome.FAILURE
