# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Policy for manual scoring and the explicit handoff from automated execution."""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.scores import ApplyChatScorerRequest, ApplyChatScorerResponse
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.executor.attack import attack_outcome_from_score
from pyrit.executor.attack.core.attack_scoring import score_attack_response_async
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import AttackStateConflictError
from pyrit.models import AttackOutcome, AttackResult, Message, MessagePiece, MessageScorable, Score, ScoringExpectation
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.results.attack_result import AttackScoringInputs, ManualContinuation, ScoringFailure
from pyrit.models.score import ScoreStatus
from pyrit.score import Scorer, TrueFalseScorer

logger = logging.getLogger(__name__)


class ChatScoringError(RuntimeError):
    """The selected scorer failed to evaluate the saved response."""


class ChatScoringService:
    """Own chat selection policy; keep executable-component lookup in the scorer catalog."""

    @staticmethod
    def execution_read_only(attack: AttackResult) -> bool:
        """
        Determine whether execution changes require a separate manual result.

        Returns:
            True for automated or unidentified historical executions.
        """
        identifier = attack.get_attack_strategy_identifier()
        return identifier is None or identifier.class_module != "pyrit.backend"

    @classmethod
    def require_manual(cls, attack: AttackResult) -> None:
        """Reject execution changes to the automated source."""
        if cls.execution_read_only(attack):
            raise PermissionError(
                "Automated results are read-only. Choose Continue manually to create a separate attack."
            )

    @staticmethod
    def disabled_reason(scorer: Scorer, *, expectation: ScoringExpectation | None = None) -> str | None:
        """
        Check the scorer's declared evidence and expectation contracts.

        Returns:
            An incompatibility reason or None.
        """
        if not isinstance(scorer, TrueFalseScorer) or MessageScorable not in scorer.get_supported_scorable_types():
            return "Chat objective scoring requires a true/false scorer that accepts message evidence."
        try:
            scorer.prepare_expectation(expectation=expectation or ScoringExpectation(objective="Chat objective"))
        except (ValueError, TypeError) as exc:
            return f"This scorer cannot evaluate the supplied chat criteria: {exc}"
        return None

    def resolve(
        self,
        reference: RegisteredComponentReference,
        *,
        inherited: bool = False,
        expectation: ScoringExpectation | None = None,
    ) -> Scorer:
        """
        Resolve a registered selection without constructing a replacement.

        Returns:
            The existing compatible scorer.
        """
        catalog = get_scorer_service()
        scorer = (
            catalog.find_registered_scorer(reference.identifier_hash)
            if inherited
            else catalog.resolve_registered_scorer(reference)
        )
        if scorer is None:
            raise ValueError("The original objective scorer is no longer registered.")
        reason = self.disabled_reason(scorer, expectation=expectation)
        if reason:
            raise ValueError(reason)
        return scorer

    def inputs(self, attack: AttackResult) -> AttackScoringInputs | None:
        """
        Resolve the attack-owned inputs or the registered original scorer.

        Returns:
            The effective inputs; None means no selection was made.
        """
        stored = attack.scoring_inputs
        if stored is not None:
            return stored
        if not self.execution_read_only(attack):
            return None
        identifier = attack.get_attack_strategy_identifier()
        original = identifier.get_child("objective_scorer") if identifier else None
        if original is None and attack.automated_score is not None:
            original = attack.automated_score.scorer_class_identifier
        if original is None:
            return None
        scorer = get_scorer_service().find_registered_scorer(original.hash)
        if not isinstance(scorer, TrueFalseScorer):
            return None
        return AttackScoringInputs(
            scorer=RegisteredComponentReference(registry_name=original.unique_name, identifier_hash=original.hash),
            revision=f"inherited:{original.hash}",
            inherited=True,
            expectation=attack.automated_score.scored_expectation if attack.automated_score else None,
        )

    def objective_settings(
        self,
        *,
        attack: AttackResult,
        objective: str,
        scorer: RegisteredComponentReference | None,
    ) -> dict[str, Any] | None:
        """
        Reset edited criteria, or apply a default to the first manual objective.

        Returns:
            New settings when the objective change affects scoring, otherwise None.
        """
        current = attack.scoring_inputs
        if current is not None and current.expectation is not None and attack.objective != objective:
            return current.model_copy(update={"expectation": None, "revision": str(uuid.uuid4())}).to_metadata()
        if (
            scorer is None
            or attack.objective.strip()
            or not objective.strip()
            or current is not None
            or self.execution_read_only(attack)
        ):
            return None
        self.resolve(scorer)
        return AttackScoringInputs(scorer=scorer, revision=str(uuid.uuid4())).to_metadata()

    @staticmethod
    def latest_response(messages: Sequence[Message]) -> Message | None:
        """
        Find the latest saved, non-simulated assistant response.

        Returns:
            The response, or None when no real assistant response is saved.
        """
        return next((message for message in reversed(messages) if message.message_pieces[0].role == "assistant"), None)

    async def response_score_async(
        self, *, attack: AttackResult, response: Message | None, complete_only: bool = False
    ) -> Score | None:
        """
        Find a persisted score for the same response, scorer configuration, and full expectation.

        Returns:
            A complete matching score if available, otherwise the latest incomplete result, or None.
        """
        if response is None:
            return None
        piece_ids = {str(piece.id) for piece in response.message_pieces}
        if self.execution_read_only(attack):
            score = attack.automated_score
            return score if score and str(score.message_piece_id) in piece_ids else None
        inputs = self.inputs(attack)
        if not inputs or not inputs.scorer or not attack.objective.strip():
            return None
        expectation = inputs.expectation or ScoringExpectation(objective=attack.objective)
        evidence = MessageScorable.from_message(response)
        scores = await CentralMemory.get_memory_instance().get_prompt_scores_async(prompt_ids=list(piece_ids))
        matches = [
            score
            for score in scores
            if score.scorer_class_identifier is not None
            and score.scorer_class_identifier.hash == inputs.scorer.identifier_hash
            and score.scored_expectation == expectation
            and (score.scorable == evidence or (score.scorable is None and len(piece_ids) == 1))
            and (not complete_only or score.status == ScoreStatus.COMPLETE)
        ]
        if not matches:
            return None
        return max(matches, key=lambda score: (score.status == ScoreStatus.COMPLETE, score.timestamp, str(score.id)))

    @staticmethod
    async def score_response_async(
        *, response: Message, scorer: Scorer | None, expectation: ScoringExpectation | None
    ) -> Score:
        """
        Evaluate through the shared attack-scoring policy.

        Returns:
            The framework-selected objective score.
        """
        scores = await score_attack_response_async(
            response=response, objective_scorer=scorer, auxiliary_scorers=[], expectation=expectation
        )
        if not scores["objective_scores"]:
            raise ValueError("The scorer did not return an objective score.")
        return scores["objective_scores"][0]

    async def apply_async(self, *, attack_result_id: str, request: ApplyChatScorerRequest) -> ApplyChatScorerResponse:
        """
        Apply once under the conversation ownership and execution budget used by manual sends.

        Returns:
            The persisted result and whether a complete matching score was reused.
        """
        scheduler = get_manual_send_scheduler()
        conversation_id = str(request.conversation_id)
        with scheduler.reserve(conversation_id=conversation_id):
            async with scheduler.operation_async():
                attack, response = await self._apply_context_async(attack_result_id=attack_result_id, request=request)
                inputs = self.inputs(attack)
                assert inputs is not None and inputs.scorer is not None
                expectation = inputs.expectation or ScoringExpectation(objective=attack.objective)
                scorer = self.resolve(inputs.scorer, inherited=inputs.inherited, expectation=expectation)
                score = await self.response_score_async(attack=attack, response=response, complete_only=True)
                reused = score is not None
                failure = None
                if score is None:
                    try:
                        score = await self.score_response_async(
                            response=response, scorer=scorer, expectation=expectation
                        )
                    except Exception as exc:
                        logger.exception("Chat scoring failed for response '%s'.", request.response_id)
                        failure = ScoringFailure(
                            scorer=inputs.scorer,
                            objective=attack.objective,
                            response_id=str(request.response_id),
                            conversation_id=conversation_id,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                async with scheduler.metadata_update_async(attack_result_id=attack_result_id):
                    publication = asyncio.create_task(
                        self._publish_async(
                            attack_result_id=attack_result_id,
                            request=request,
                            expectation=expectation,
                            score=score,
                            failure=failure,
                        )
                    )
                    try:
                        await asyncio.shield(publication)
                    except asyncio.CancelledError:
                        await publication
                        raise
                if failure is not None:
                    raise ChatScoringError(failure.error)
                assert score is not None
                return ApplyChatScorerResponse(
                    conversation_id=conversation_id, score=ScoreView.from_domain(score), reused=reused
                )

    async def _apply_context_async(
        self, *, attack_result_id: str, request: ApplyChatScorerRequest
    ) -> tuple[AttackResult, Message]:
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not attacks:
            raise LookupError(f"Attack '{attack_result_id}' not found")
        attack = attacks[0]
        self.require_manual(attack)
        conversation_id = str(request.conversation_id)
        if conversation_id not in attack.get_active_conversation_ids():
            raise LookupError("The conversation does not belong to this attack.")
        inputs = self.inputs(attack)
        if (
            not attack.objective.strip()
            or attack.objective != request.objective
            or not inputs
            or not inputs.scorer
            or inputs.scorer.identifier_hash != request.scorer.identifier_hash
        ):
            raise AttackStateConflictError("The objective or scorer changed. Reload before applying the scorer.")
        messages = await memory.get_conversation_messages_async(conversation_id=conversation_id)
        response = self.latest_response(messages)
        if response is None:
            raise ValueError("This conversation has no saved assistant response to score.")
        if str(response.message_pieces[0].id) != str(request.response_id):
            raise AttackStateConflictError("The latest response changed. Reload before applying the scorer.")
        return attack, response

    async def _publish_async(
        self,
        *,
        attack_result_id: str,
        request: ApplyChatScorerRequest,
        expectation: ScoringExpectation,
        score: Score | None,
        failure: ScoringFailure | None,
    ) -> None:
        memory = CentralMemory.get_memory_instance()
        while True:
            attack, response = await self._apply_context_async(attack_result_id=attack_result_id, request=request)
            inputs = self.inputs(attack)
            if inputs is None or (inputs.expectation or ScoringExpectation(objective=attack.objective)) != expectation:
                raise AttackStateConflictError("The scoring criteria changed. Reload before applying the scorer.")
            errors = dict(attack.metadata.get(AttackResult.SCORING_ERRORS_KEY, {}))
            conversation_id = str(request.conversation_id)
            if failure is None:
                errors.pop(conversation_id, None)
            else:
                errors[conversation_id] = failure.model_dump(mode="json")
            fields: dict[str, Any] = {
                "attack_metadata": {AttackResult.SCORING_ERRORS_KEY: errors},
                "timestamp": datetime.now(UTC),
            }
            if attack.last_response is None or attack.last_response.timestamp <= response.message_pieces[0].timestamp:
                fields["automated_score_id"] = score.id if score else None
                fields["last_response_id"] = response.message_pieces[0].id
                if attack.human_score is None:
                    fields.update(
                        outcome=attack_outcome_from_score(score).value if score else AttackOutcome.UNDETERMINED.value,
                        outcome_reason=score.score_rationale if score else None,
                    )
            try:
                await memory.update_attack_result_conditionally_async(
                    attack_result_id=attack_result_id,
                    expected_fields={
                        "objective": attack.objective,
                        "attack_metadata": attack.metadata,
                        "last_response_id": attack.last_response.id if attack.last_response else None,
                        "human_score_id": attack.human_score.id if attack.human_score else None,
                    },
                    update_fields=fields,
                )
                return
            except AttackStateConflictError:
                logger.info("Retrying chat score publication for response '%s'.", request.response_id)

    def continue_manually(
        self,
        *,
        source: AttackResult,
        destination: AttackResult,
        source_conversation_id: str,
        retained_pieces: Sequence[MessagePiece],
        explicit_selection: bool,
    ) -> None:
        """Record provenance and copy the original scoring inputs, not the original score."""
        if not self.execution_read_only(source):
            return
        response = max(
            (piece for piece in retained_pieces if piece.role == "assistant"),
            key=lambda piece: (piece.sequence, piece.timestamp),
            default=None,
        )
        destination.metadata[ManualContinuation.METADATA_KEY] = ManualContinuation(
            source_attack_result_id=source.attack_result_id,
            source_conversation_id=source_conversation_id,
            source_response_id=str(response.id) if response else None,
        ).model_dump(mode="json")
        if not explicit_selection:
            inputs = self.inputs(source) or AttackScoringInputs()
            destination.metadata.update(
                inputs.model_copy(
                    update={
                        "revision": str(uuid.uuid4()),
                        "expectation": inputs.expectation if destination.objective == source.objective else None,
                    }
                ).to_metadata()
            )


def get_chat_scoring_service() -> ChatScoringService:
    """Return stateless chat policy using the current registry lifetime."""
    return ChatScoringService()
