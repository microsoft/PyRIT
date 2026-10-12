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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from pyrit.executor.attack.core.attack_executor import AttackExecutor
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_result_attribution import enrich_attack_result_identifier_async
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    AttackResultSelection,
    AttackSeedGroup,
    ScoringExpectation,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
    from pyrit.models import ComponentIdentifier
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
    """Stop on the first ``AttackOutcome.SUCCESS``; continue past other outcomes.
    Outcome: SUCCESS if any succeeded, ERROR if all errored, UNDETERMINED if any are undecided, else FAILURE.
    Resilient adaptive default — keep trying other strategies past transient errors."""

    FIRST_DECISIVE = "first_decisive"
    """Stop on the first ``AttackOutcome.SUCCESS`` or ``AttackOutcome.ERROR``;
    continue past FAILURE and UNDETERMINED. Use the same outcome rule as FIRST_SUCCESS.
    Use when ERRORs should short-circuit the sequence."""

    STRICT_ALL = "strict_all"
    """Stop on the first non-SUCCESS. Outcome: SUCCESS only if every child attack succeeded,
    ERROR if any errored, FAILURE if any failed, otherwise UNDETERMINED.
    Pipeline semantics — each child attack is required."""

    EXHAUSTIVE = "exhaustive"
    """Run every child attack. Use the same outcome rule as FIRST_SUCCESS.
    Use for evaluation sweeps where you want to try everything."""

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
    front (e.g. merging per-technique ``AttackTechniqueSeedGroup`` objects
    into a shared base) without any implicit fallback at the compound
    layer.

    Attributes:
        strategy (AttackStrategy): The inner attack to run for this entry.
        seed_group (AttackSeedGroup): The seed group dispatched to the
            inner attack. Must carry the objective.
        adversarial_chat (PromptTarget | None): Forwarded to the executor
            for inner attacks that need an adversarial chat target (e.g.
            multi-turn attacks, or seed groups with simulated-conversation
            configs).
        objective_scorer (TrueFalseScorer | None): Forwarded to the
            executor for inner attacks that need an objective scorer.
        memory_labels (Mapping[str, str]): Per-entry labels merged on top
            of the compound's ``context.memory_labels`` for this call.
        technique_identifier (ComponentIdentifier | None): Optional packaged technique
            identity used for the compound configuration and persisted child results.
        identity_seed_group (AttackSeedGroup | None): Source seeds before technique merging.
            Defaults to ``seed_group`` when no separate source group is supplied.
    """

    strategy: AttackStrategy[Any, AttackResult]
    seed_group: AttackSeedGroup
    adversarial_chat: PromptTarget | None = None
    objective_scorer: TrueFalseScorer | None = None
    memory_labels: Mapping[str, str] = field(default_factory=dict)
    technique_identifier: ComponentIdentifier | None = None
    identity_seed_group: AttackSeedGroup | None = None


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

    DELEGATES_SCORING: ClassVar[bool] = True

    RESULT_ROLE: ClassVar[AttackResultRole] = AttackResultRole.ORCHESTRATION

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
        SequentialAttack.validate_constructor_parameters({"child_attacks": child_attacks})

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
        self._memory = CentralMemory.get_memory_instance()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Include explicitly packaged child configurations and the completion policy.

        Returns:
            ComponentIdentifier: The sequence configuration.
        """
        # Keep existing run identities stable unless the caller supplies packaged techniques.
        if not any(child.technique_identifier is not None for child in self._child_attacks):
            return super()._build_identifier()
        return self._create_identifier(
            params={"completion_policy": self._completion_policy.value},
            children={
                "child_attacks": [
                    child.technique_identifier
                    if child.technique_identifier is not None
                    else child.strategy.get_identifier()
                    for child in self._child_attacks
                ]
            },
        )

    @classmethod
    def validate_constructor_parameters(cls, params: dict[str, Any]) -> None:
        """
        Validate the child selection without constructing or executing child attacks.

        Raises:
            ValueError: If the child selection is empty.
        """
        super().validate_constructor_parameters(params)
        if "child_attacks" in params and not params["child_attacks"]:
            raise ValueError("child_attacks must contain at least one SequentialChildAttack")

    def _validate_context(self, *, context: AttackContext[AttackParameters]) -> None:
        if not context.objective or context.objective.isspace():
            raise ValueError("Attack objective must be provided and non-empty")

    async def _setup_async(self, *, context: AttackContext[AttackParameters]) -> None:
        """No-op: per-child-attack setup is owned by each inner strategy's executor."""

    async def _teardown_async(self, *, context: AttackContext[AttackParameters]) -> None:
        """No-op: per-child-attack teardown is owned by each inner strategy's executor."""

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> SequentialAttackResult:
        results: list[AttackResult] = []
        child_ids: list[str] = []
        context._error_result_metadata.update(
            {
                self.CHILD_ATTACK_RESULT_IDS_KEY: child_ids,
                self.COMPLETION_POLICY_KEY: self._completion_policy.value,
            }
        )

        for attempt_index, child_attack in enumerate(self._child_attacks, start=1):
            labels = {**context.memory_labels, **dict(child_attack.memory_labels)}
            # Each child shares the parent's attribution plus its own position.
            attribution = replace(context._attribution, attempt_index=attempt_index) if context._attribution else None
            result = await self._run_child_attack_async(
                child_attack=child_attack,
                memory_labels=labels,
                attribution=attribution,
                expectation=context.params.expectation,
                child_result_ids=child_ids,
            )
            results.append(result)
            child_ids.append(result.attack_result_id)
            await self._enrich_child_result_async(child_attack=child_attack, result=result)
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
            timestamp=datetime.now(UTC),
            last_response=None,
            automated_score=None,
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
        child_result_ids: list[str],
        attribution: AttackResultAttribution | None = None,
        expectation: ScoringExpectation | None = None,
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
            expectation (ScoringExpectation | None): Explicit scoring input forwarded unchanged.
                Omission leaves the child's seed preparation and objective fallback in control.
            child_result_ids (list[str]): This execution's ordered child links. A confirmed
                persisted result from a failed dispatch is appended before its exception is re-raised.

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
        expectation_override = {"expectation": expectation} if expectation is not None else {}
        executor_result = await self._executor.execute_attack_from_seed_groups_async(
            attack=child_attack.strategy,
            seed_groups=[child_attack.seed_group],
            adversarial_chat=child_attack.adversarial_chat,
            objective_scorer=child_attack.objective_scorer,
            memory_labels=memory_labels,
            return_partial_on_failure=True,
            attribution=attribution,
            **expectation_override,
        )
        if executor_result.completed_results:
            return executor_result.completed_results[0]
        if executor_result.incomplete_objectives:
            if executor_result.incomplete_result_ids and executor_result.incomplete_result_ids[0] is not None:
                result_id = executor_result.incomplete_result_ids[0]
                child_result_ids.append(result_id)
                if child_attack.technique_identifier is not None:
                    stored = await self._memory.get_attack_results_async(
                        attack_result_ids=[result_id],
                        result_selection=AttackResultSelection.ALL_RESULTS,
                    )
                    if not stored:
                        raise RuntimeError(f"Persisted child attack result '{result_id}' could not be loaded")
                    await self._enrich_child_result_async(child_attack=child_attack, result=stored[0])
            raise executor_result.incomplete_objectives[0][1]
        raise RuntimeError(  # pragma: no cover - defensive
            "AttackExecutor returned neither completed nor incomplete results."
        )

    async def _enrich_child_result_async(self, *, child_attack: SequentialChildAttack, result: AttackResult) -> None:
        if child_attack.technique_identifier is not None:
            await enrich_attack_result_identifier_async(
                result=result,
                technique_identifier=child_attack.technique_identifier,
                seed_group=(
                    child_attack.identity_seed_group
                    if child_attack.identity_seed_group is not None
                    else child_attack.seed_group
                ),
                memory=self._memory,
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
            if any(r.outcome is AttackOutcome.FAILURE for r in results):
                return AttackOutcome.FAILURE
            return AttackOutcome.UNDETERMINED
        # FIRST_SUCCESS, FIRST_DECISIVE, EXHAUSTIVE all share any-success semantics.
        if any(r.outcome is AttackOutcome.SUCCESS for r in results):
            return AttackOutcome.SUCCESS
        if all(r.outcome is AttackOutcome.ERROR for r in results):
            return AttackOutcome.ERROR
        if any(r.outcome is AttackOutcome.UNDETERMINED for r in results):
            return AttackOutcome.UNDETERMINED
        return AttackOutcome.FAILURE
