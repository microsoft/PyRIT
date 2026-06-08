# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``SequentialAttack`` — runs a sequence of inner ``AttackStrategy``
child attacks against a single objective, controlled by a
``SequenceCompletionPolicy``.

The compound preserves the one-objective → one-``AttackResult`` invariant:
each invocation returns one ``SequentialAttackResult`` whose outcome
reflects the sequence according to the chosen
``SequenceCompletionPolicy``.

Each inner child attack is dispatched through ``AttackExecutor``, so it
persists as its own first-class ``AttackResult`` row. The envelope owns
no conversation of its own; callers reach the inner results either via
``SequentialAttackResult.child_attack_results`` (in-memory, populated at
execute time) or by re-fetching from memory using the IDs in
``metadata["child_attack_result_ids"]``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import Field

from pyrit.executor.attack.core.attack_executor import AttackExecutor
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.models import AttackOutcome, AttackResult, SeedAttackGroup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
    from pyrit.prompt_target import PromptTarget
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class SequenceCompletionPolicy(str, Enum):
    """
    How a ``SequentialAttack`` iterates and aggregates its child attacks.

    Each policy bundles a stop condition (when to halt iteration) and an
    outcome rule (how to derive the envelope's outcome from the inner
    results), chosen so each policy matches a common use case.
    """

    FIRST_SUCCESS = "first_success"
    """Stop on the first ``AttackOutcome.SUCCESS``; continue past ERROR and FAILURE.
    Outcome: SUCCESS if any child attack succeeded, ERROR if every child attack errored, else FAILURE.
    Resilient adaptive default — keep trying other strategies past transient errors."""

    FIRST_DECISIVE = "first_decisive"
    """Stop on the first ``AttackOutcome.SUCCESS`` or ``AttackOutcome.ERROR``;
    continue past FAILURE. Outcome: SUCCESS if any child attack succeeded, ERROR if every
    child attack errored, else FAILURE. Use when ERRORs should short-circuit the sequence."""

    STRICT_ALL = "strict_all"
    """Stop on the first non-SUCCESS. Outcome: SUCCESS only if every child attack succeeded,
    ERROR if any child attack errored, else FAILURE. Pipeline semantics — each child attack is
    required."""

    EXHAUSTIVE = "exhaustive"
    """Run every child attack regardless of intermediate outcomes. Outcome: SUCCESS if any
    child attack succeeded, ERROR if every child attack errored, else FAILURE. Use for evaluation
    sweeps where you want to try everything."""

    LAST_RESULT = "last_result"
    """Run every child attack; inherit the last child attack's outcome verbatim. Use for chained
    refinement where the final attempt is canonical."""


@dataclass(frozen=True)
class SequentialChildAttack:
    """
    One child attack in a ``SequentialAttack``.

    Each entry bundles an ``AttackStrategy`` with the inputs that the
    compound forwards to ``AttackExecutor`` when dispatching it.
    ``seed_group`` is required per entry so callers compose seed groups up
    front (e.g. merging per-technique ``SeedAttackTechniqueGroup`` objects
    into a shared base) without any implicit fallback at the compound
    layer.

    Attributes:
        strategy (AttackStrategy): The inner attack to run for this entry.
        seed_group (SeedAttackGroup): The seed group dispatched to the
            inner attack. Must carry the objective.
        adversarial_chat (PromptTarget | None): Forwarded to the executor
            for inner attacks that need an adversarial chat target (e.g.
            multi-turn attacks, or seed groups with simulated-conversation
            configs).
        objective_scorer (TrueFalseScorer | None): Forwarded to the
            executor for inner attacks that need an objective scorer.
        memory_labels (Mapping[str, str]): Per-entry labels merged on top
            of the compound's ``context.memory_labels`` for this call.
    """

    strategy: AttackStrategy[Any, AttackResult]
    seed_group: SeedAttackGroup
    adversarial_chat: PromptTarget | None = None
    objective_scorer: TrueFalseScorer | None = None
    memory_labels: Mapping[str, str] = field(default_factory=dict)


class SequentialAttackResult(AttackResult):
    """
    Result of a ``SequentialAttack`` execution.

    Inherits every field from ``AttackResult``. The envelope owns no
    conversation, last response, or last score of its own — those live
    on the inner per-child-attack ``AttackResult`` rows. Callers reach
    the inner results via:

    * ``child_attack_results`` — the in-memory ``AttackResult`` list,
      populated at execute time. Empty on freshly loaded-from-DB
      envelopes; use ``child_attack_result_ids`` and re-fetch from memory in
      that case.
    * ``child_attack_result_ids`` — derived from ``child_attack_results`` when
      populated, else read from ``metadata["child_attack_result_ids"]`` which
      survives DB round-trips.

    Attributes:
        child_attack_results (list[AttackResult]): The inner per-child-attack
            results, in dispatch order. Populated at execute time; empty
            after a DB round-trip.
        completion_policy (SequenceCompletionPolicy): The policy that
            governed the sequence's iteration and aggregation. Also
            mirrored into ``metadata["completion_policy"]`` for DB
            round-trip.
    """

    child_attack_results: list[AttackResult] = Field(default_factory=list)
    completion_policy: SequenceCompletionPolicy = SequenceCompletionPolicy.FIRST_SUCCESS

    @property
    def child_attack_result_ids(self) -> list[str]:
        """
        The ``attack_result_id`` of each inner child attack, in dispatch order.

        Reads from ``child_attack_results`` when it is populated (in-memory
        case), otherwise falls back to ``metadata["child_attack_result_ids"]``
        so callers can navigate envelopes loaded back from the database
        without having the live result instances.
        """
        if self.child_attack_results:
            return [r.attack_result_id for r in self.child_attack_results]
        return list(self.metadata.get("child_attack_result_ids", []))


class SequentialAttack(AttackStrategy[AttackContext[AttackParameters], SequentialAttackResult]):
    """
    Run a sequence of ``AttackStrategy`` child attacks against one objective.

    Use this when an objective should be attacked by several techniques
    in sequence — for example "try Crescendo first, fall back to
    PromptSending" — without breaking the one-objective →
    one-``AttackResult`` invariant or pushing branching logic up to the
    Scenario layer. Each child attack runs as a real attack through
    ``AttackExecutor`` and persists its own row; the compound returns
    one ``SequentialAttackResult`` whose iteration and aggregation are
    controlled by ``SequenceCompletionPolicy``.

    The default ``SequenceCompletionPolicy.FIRST_SUCCESS`` matches the
    adaptive "try strategies until one works" pattern, resilient to
    transient inner errors. See ``SequenceCompletionPolicy`` for the
    other policies (``FIRST_DECISIVE``, ``STRICT_ALL``, ``EXHAUSTIVE``,
    ``LAST_RESULT``).

    Example:

    .. code-block:: python

        sequential = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=crescendo, seed_group=sg),
                SequentialChildAttack(strategy=prompt_sending, seed_group=sg),
            ],
        )
        result = await sequential.execute_async(objective="...")
    """

    CHILD_ATTACK_RESULT_IDS_KEY: str = "child_attack_result_ids"
    """Metadata key under which the per-child-attack result IDs are stored."""

    COMPLETION_POLICY_KEY: str = "completion_policy"
    """Metadata key under which the active ``SequenceCompletionPolicy`` value is stored."""

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        child_attacks: Sequence[SequentialChildAttack],
        completion_policy: SequenceCompletionPolicy = SequenceCompletionPolicy.FIRST_SUCCESS,
    ) -> None:
        """
        Args:
            objective_target (PromptTarget): Target the compound is
                nominally bound to (forwarded to ``AttackStrategy``
                for identifier construction). Each child attack runs against
                whatever target its own strategy is configured with.
            child_attacks (Sequence[SequentialChildAttack]): Child attacks
                to run, in order. Must be non-empty.
            completion_policy (SequenceCompletionPolicy): Iteration +
                aggregation policy. Defaults to
                ``SequenceCompletionPolicy.FIRST_SUCCESS`` (resilient
                adaptive).

        Raises:
            ValueError: If ``child_attacks`` is empty.
        """
        if not child_attacks:
            raise ValueError("child_attacks must contain at least one SequentialChildAttack")

        super().__init__(
            objective_target=objective_target,
            context_type=AttackContext,
            # Inner child attacks expand their own next_message / prepended_conversation
            # via their own params_type; the compound takes no per-call message
            # overrides.
            params_type=AttackParameters.excluding("next_message", "prepended_conversation"),
            logger=logger,
        )
        self._child_attacks: list[SequentialChildAttack] = list(child_attacks)
        self._completion_policy = completion_policy
        self._executor = AttackExecutor(max_concurrency=1)

    def _validate_context(self, *, context: AttackContext[AttackParameters]) -> None:
        if not context.objective or context.objective.isspace():
            raise ValueError("Attack objective must be provided and non-empty")

    async def _setup_async(self, *, context: AttackContext[AttackParameters]) -> None:
        """No-op: per-child-attack setup is owned by each inner strategy's executor."""

    async def _teardown_async(self, *, context: AttackContext[AttackParameters]) -> None:
        """No-op: per-child-attack teardown is owned by each inner strategy's executor."""

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> SequentialAttackResult:
        results: list[AttackResult] = []

        for child_attack in self._child_attacks:
            labels = {**context.memory_labels, **dict(child_attack.memory_labels)}
            result = await self._run_child_attack_async(
                child_attack=child_attack,
                memory_labels=labels,
                attribution=context._attribution,
            )
            results.append(result)
            if self._should_stop_after(result=result):
                break

        outcome = self._compute_outcome(results=results)

        # SequentialAttack is a wrapper and therefore has no conversation,
        # last_response, or last_score — those live on the inner
        # per-child-attack rows surfaced via ``child_attack_results``.
        # ``executed_turns`` is the sum of the turns spent across every
        # child attack that ran.
        return SequentialAttackResult(
            conversation_id="",
            objective=context.objective,
            attack_result_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            last_response=None,
            last_score=None,
            executed_turns=sum(r.executed_turns for r in results),
            outcome=outcome,
            child_attack_results=results,
            completion_policy=self._completion_policy,
            metadata={
                self.CHILD_ATTACK_RESULT_IDS_KEY: [r.attack_result_id for r in results],
                self.COMPLETION_POLICY_KEY: self._completion_policy.value,
            },
        )

    async def _run_child_attack_async(
        self,
        *,
        child_attack: SequentialChildAttack,
        memory_labels: dict[str, str],
        attribution: AttackResultAttribution | None = None,
    ) -> AttackResult:
        """
        Execute one child attack via ``AttackExecutor`` and return its result.

        Isolated as a method so tests can patch the per-child-attack call
        surface without monkey-patching ``AttackExecutor``.

        Args:
            child_attack (SequentialChildAttack): The child entry to
                dispatch.
            memory_labels (dict[str, str]): Memory labels for this call
                (already merged from context + child).
            attribution (AttackResultAttribution | None): Attribution
                forwarded from the compound's context (e.g. when the
                compound is itself nested under a ``Scenario``). When
                provided, the executor stamps it onto every inner
                ``AttackResult`` so the persisted child rows carry the
                parent linkage.

        Returns:
            AttackResult: The ``AttackResult`` produced by the inner
            attack for ``child_attack.seed_group``.

        Raises:
            BaseException: Re-raised from
                ``AttackExecutorResult.incomplete_objectives`` if the
                inner attack failed.
            RuntimeError: If the executor returned neither a completed
                result nor an incomplete objective (defensive guard).
        """
        executor_result = await self._executor.execute_attack_from_seed_groups_async(
            attack=child_attack.strategy,
            seed_groups=[child_attack.seed_group],
            adversarial_chat=child_attack.adversarial_chat,
            objective_scorer=child_attack.objective_scorer,
            memory_labels=memory_labels,
            attribution=attribution,
        )
        if executor_result.completed_results:
            return executor_result.completed_results[0]
        if executor_result.incomplete_objectives:
            raise executor_result.incomplete_objectives[0][1]
        raise RuntimeError(  # pragma: no cover - defensive
            "AttackExecutor returned neither completed nor incomplete results."
        )

    def _should_stop_after(self, *, result: AttackResult) -> bool:
        if self._completion_policy is SequenceCompletionPolicy.FIRST_SUCCESS:
            return result.outcome is AttackOutcome.SUCCESS
        if self._completion_policy is SequenceCompletionPolicy.FIRST_DECISIVE:
            return result.outcome in (AttackOutcome.SUCCESS, AttackOutcome.ERROR)
        if self._completion_policy is SequenceCompletionPolicy.STRICT_ALL:
            return result.outcome is not AttackOutcome.SUCCESS
        # EXHAUSTIVE and LAST_RESULT run every child attack to completion.
        return False

    def _compute_outcome(self, *, results: list[AttackResult]) -> AttackOutcome:
        if self._completion_policy is SequenceCompletionPolicy.LAST_RESULT:
            return results[-1].outcome
        if self._completion_policy is SequenceCompletionPolicy.STRICT_ALL:
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
