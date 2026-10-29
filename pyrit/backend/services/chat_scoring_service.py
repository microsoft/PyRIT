# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Policy for chat scoring, its publication, and the explicit handoff from automated execution."""

import json
import logging
import traceback
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar

from pydantic import PydanticUserError

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.scores import ApplyChatScorerRequest, ApplyChatScorerResponse
from pyrit.backend.services.manual_send_scheduler import complete_owned_write_async, get_manual_send_scheduler
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.executor.attack import attack_outcome_from_score
from pyrit.executor.attack.core.attack_scoring import score_attack_response_async
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import AttackStateConflictError
from pyrit.models import AttackOutcome, AttackResult, Message, MessagePiece, MessageScorable, Score, ScoringExpectation
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.results.attack_result import AttackScoringInputs, ManualContinuation, ScoringFailure
from pyrit.score import Scorer, TrueFalseScorer

logger = logging.getLogger(__name__)


class ChatScoringError(RuntimeError):
    """The selected scorer could not evaluate the saved response."""


@dataclass(frozen=True)
class ChatScoringSelection:
    """The chat scorer and complete input that an attack's next evaluation uses."""

    scorer: RegisteredComponentReference
    objective: str
    expectation: ScoringExpectation
    inherited: bool = False

    def is_equivalent_to(self, other: "ChatScoringSelection | None") -> bool:
        """
        Check whether ``other`` evaluates with the same scorer configuration and input.

        Returns:
            bool: True when a result for one selection is a result for the other.
        """
        return (
            other is not None
            and other.scorer.identifier_hash == self.scorer.identifier_hash
            and other.objective == self.objective
            and other.expectation == self.expectation
        )


@dataclass(frozen=True)
class ChatEvaluation:
    """The result of evaluating one saved response for a scoring selection."""

    selection: ChatScoringSelection
    response: Message
    score: Score | None = None
    failure: ScoringFailure | None = None
    reused: bool = False


@dataclass(frozen=True)
class ConversationScoring:
    """The scoring state of a conversation's latest live response."""

    latest_response: Message | None
    score: Score | None
    failure: ScoringFailure | None
    outcome: AttackOutcome


class ChatScoringService:
    """Own chat selection, evaluation, and publication policy; keep component lookup in the scorer catalog."""

    MAX_PUBLICATION_ATTEMPTS: ClassVar[int] = 5

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

    def selection(self, attack: AttackResult) -> ChatScoringSelection | None:
        """
        Resolve the scorer and complete input for the attack's next evaluation.

        Returns:
            The selection, or None when scoring is off or the objective is empty.
        """
        inputs = self.inputs(attack)
        if inputs is None or inputs.scorer is None or not attack.objective.strip():
            return None
        return ChatScoringSelection(
            scorer=inputs.scorer,
            objective=attack.objective,
            expectation=inputs.expectation or ScoringExpectation(objective=attack.objective),
            inherited=inputs.inherited,
        )

    def resolve_selection(self, selection: ChatScoringSelection) -> Scorer:
        """
        Resolve the registered scorer for a selection.

        Returns:
            The existing compatible scorer.
        """
        return self.resolve(selection.scorer, inherited=selection.inherited, expectation=selection.expectation)

    @staticmethod
    def latest_response(messages: Sequence[Message]) -> Message | None:
        """
        Find the latest live assistant response; injected (simulated) history is not one.

        Returns:
            The response, or None when the conversation has no live response.
        """
        return next((message for message in reversed(messages) if message.message_pieces[0].role == "assistant"), None)

    def conversation_scoring(
        self,
        *,
        attack: AttackResult,
        conversation_id: str,
        messages: Sequence[Message],
        scores: Sequence[Score],
    ) -> ConversationScoring:
        """
        Describe the scoring state of a conversation's latest live response.

        Args:
            attack (AttackResult): The attack that owns the conversation.
            conversation_id (str): The conversation to describe.
            messages (Sequence[Message]): The conversation's messages.
            scores (Sequence[Score]): The stored scores already read for ``messages``.

        Returns:
            The current selection's result and failure, and the outcome to show for the response.
        """
        response = self.latest_response(messages)
        selection = self.selection(attack)
        score = self._response_score(attack=attack, selection=selection, response=response, scores=scores)
        failure = attack.get_scoring_failure(conversation_id)
        if failure is not None and not (
            response is not None
            and selection is not None
            and failure.response_id in {str(piece.id) for piece in response.message_pieces}
            and failure.scorer.identifier_hash == selection.scorer.identifier_hash
            and failure.objective == selection.objective
        ):
            failure = None
        return ConversationScoring(
            latest_response=response,
            score=score,
            failure=failure,
            outcome=self._outcome(attack=attack, conversation_id=conversation_id, response=response, score=score),
        )

    async def evaluate_async(
        self,
        *,
        selection: ChatScoringSelection,
        response: Message,
        scorer: Scorer | None,
        error: str | None = None,
        reuse: bool = False,
    ) -> ChatEvaluation:
        """
        Evaluate a saved response, or reuse a complete stored result for the same evaluation.

        Scorer failures become recorded diagnostics rather than exceptions.

        Args:
            selection (ChatScoringSelection): The scorer and input to evaluate with.
            response (Message): The saved response to evaluate.
            scorer (Scorer | None): The resolved scorer, or None when it could not be resolved.
            error (str | None): Why the scorer could not be resolved. Defaults to None.
            reuse (bool): Return a complete stored result instead of evaluating again. Defaults to False.

        Returns:
            The score or failure, and whether a stored result was reused.
        """
        if reuse and scorer is not None and error is None:
            existing = await self._complete_result_async(scorer=scorer, selection=selection, response=response)
            if existing is not None:
                return ChatEvaluation(selection=selection, response=response, score=existing, reused=True)
        piece = response.message_pieces[0]
        try:
            if error is not None or scorer is None:
                raise ValueError(error or "The chat scorer is unavailable.")
            results = await score_attack_response_async(
                response=response, objective_scorer=scorer, auxiliary_scorers=[], expectation=selection.expectation
            )
            if not results["objective_scores"]:
                raise ValueError("The scorer did not return an objective score.")
            return ChatEvaluation(selection=selection, response=response, score=results["objective_scores"][0])
        except Exception as exc:
            logger.exception("Chat scoring failed for response '%s'.", piece.id)
            failure = ScoringFailure(
                scorer=selection.scorer,
                objective=selection.objective,
                response_id=str(piece.id),
                conversation_id=str(piece.conversation_id),
                error=error or self.failure_detail(exception=exc, response_id=str(piece.id)),
            )
            return ChatEvaluation(selection=selection, response=response, failure=failure)

    async def publish_async(
        self,
        *,
        attack_result_id: str,
        evaluation: ChatEvaluation,
        message_fields: Callable[[AttackResult], dict[str, Any]] | None = None,
        require_current: bool = False,
    ) -> None:
        """
        Record an evaluation on its attack when it still answers the attack's scoring selection.

        A score always remains stored. The attack records it only while the objective and scorer
        still match, and changes the attack's result only for its latest response, without
        replacing a human score.

        Args:
            attack_result_id (str): The attack to update.
            evaluation (ChatEvaluation): The evaluation to record.
            message_fields (Callable[[AttackResult], dict[str, Any]] | None): A send's own updates
                for the same guarded write. Defaults to None.
            require_current (bool): Reject an evaluation whose selection changed. Defaults to False.

        Raises:
            LookupError: If the attack no longer exists.
            AttackStateConflictError: If ``require_current`` is set and the selection changed, or the
                attack keeps changing during publication.
        """
        memory = CentralMemory.get_memory_instance()
        for _ in range(self.MAX_PUBLICATION_ATTEMPTS):
            attacks = await memory.get_attack_results_async(attack_result_ids=[attack_result_id])
            if not attacks:
                raise LookupError(f"Attack '{attack_result_id}' not found")
            attack = attacks[0]
            current = evaluation.selection.is_equivalent_to(self.selection(attack))
            if require_current and not current:
                raise AttackStateConflictError("The objective or scorer changed. Reload before applying the scorer.")
            fields: dict[str, Any] = {"timestamp": datetime.now(UTC)}
            if message_fields is not None:
                fields.update(message_fields(attack))
            if current:
                fields.update(self._evaluation_fields(attack=attack, evaluation=evaluation))
            try:
                await memory.update_attack_result_conditionally_async(
                    attack_result_id=attack_result_id,
                    expected_fields=self._publication_guard(attack),
                    update_fields=fields,
                )
                return
            except AttackStateConflictError:
                logger.info(
                    "Retrying chat score publication for response '%s'.", evaluation.response.message_pieces[0].id
                )
        raise AttackStateConflictError(
            "The response and score were saved, but the attack kept changing during publication. Refresh its results."
        )

    async def apply_async(self, *, attack_result_id: str, request: ApplyChatScorerRequest) -> ApplyChatScorerResponse:
        """
        Apply the selected scorer to the latest saved response with manual-send conversation ownership.

        Returns:
            The persisted result and whether a complete stored result was reused.

        Raises:
            ChatScoringError: If the scorer produced no result. Its diagnostics are recorded.
        """
        scheduler = get_manual_send_scheduler()
        conversation_id = str(request.conversation_id)
        with scheduler.reserve(conversation_id=conversation_id):
            async with scheduler.operation_async():
                selection, response = await self._apply_context_async(
                    attack_result_id=attack_result_id, request=request
                )
                evaluation = await self.evaluate_async(
                    selection=selection, response=response, scorer=self.resolve_selection(selection), reuse=True
                )
                async with scheduler.metadata_update_async(attack_result_id=attack_result_id):
                    await complete_owned_write_async(
                        partial(
                            self.publish_async,
                            attack_result_id=attack_result_id,
                            evaluation=evaluation,
                            require_current=True,
                        )
                    )
        if evaluation.score is None:
            raise ChatScoringError(
                "The scorer could not evaluate the latest response. Open the automated score for details."
            )
        return ApplyChatScorerResponse(
            conversation_id=conversation_id, score=ScoreView.from_domain(evaluation.score), reused=evaluation.reused
        )

    @staticmethod
    def is_latest_response(*, attack: AttackResult, piece: MessagePiece) -> bool:
        """
        Check whether ``piece`` is at least as recent as the attack's recorded response.

        Returns:
            bool: True when the attack records no newer response.
        """
        return attack.last_response is None or attack.last_response.timestamp <= piece.timestamp

    @staticmethod
    def failure_detail(*, exception: Exception, response_id: str) -> str:
        """
        Retain scorer diagnostics for trusted operators without adding request headers.

        Returns:
            The exception chain, provider error details, and traceback with a response reference.
        """
        from httpx import HTTPStatusError, ResponseNotRead
        from openai import APIStatusError

        cause: BaseException = exception
        seen: set[int] = set()
        provider_details: list[str] = []
        while id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, APIStatusError):
                provider_details.append(
                    f"Provider HTTP status: {cause.status_code}\n"
                    f"Provider request ID: {cause.request_id}\n"
                    f"Provider error body: {json.dumps(cause.body, default=str)}"
                )
            elif isinstance(cause, HTTPStatusError):
                response = cause.response
                try:
                    body = response.text
                except ResponseNotRead:
                    body = "(response body was not read)"
                request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("request-id")
                    or response.headers.get("apim-request-id")
                )
                provider_details.append(
                    f"Provider HTTP status: {response.status_code}\n"
                    f"Provider request ID: {request_id}\nProvider error body: {body}"
                )
            nested = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
            if nested is None:
                break
            cause = nested
        kind = type(cause).__name__
        if isinstance(cause, PydanticUserError) and cause.code:
            kind = f"{kind} ({cause.code})"
        return "\n\n".join(
            [
                "The objective scorer failed for this saved response.",
                f"Cause: {kind}\nMessage: {cause}\nResponse reference: {response_id}",
                *provider_details,
                "Traceback:\n" + "".join(traceback.format_exception(exception)),
            ]
        )

    def _response_score(
        self,
        *,
        attack: AttackResult,
        selection: ChatScoringSelection | None,
        response: Message | None,
        scores: Sequence[Score],
    ) -> Score | None:
        """
        Select the current scorer's stored result for a response, preferring a complete verdict.

        Returns:
            The result, or None when the response has no result for the current selection.
        """
        if response is None:
            return None
        if self.execution_read_only(attack):
            recorded = attack.automated_score
            piece_ids = {str(piece.id) for piece in response.message_pieces}
            return recorded if recorded is not None and str(recorded.message_piece_id) in piece_ids else None
        if selection is None:
            return None
        try:
            scorer = self.resolve_selection(selection)
        except ValueError:
            return None
        matches = scorer.find_matching_scores(
            scores=scores, scorable=MessageScorable.from_message(response), expectation=selection.expectation
        )
        return next((score for score in matches if not score.is_undetermined), matches[0] if matches else None)

    def _outcome(
        self, *, attack: AttackResult, conversation_id: str, response: Message | None, score: Score | None
    ) -> AttackOutcome:
        """
        Choose the outcome to show for a conversation's latest live response.

        A human score overrides the attack. The stored outcome is shown for the response it was
        recorded for when it is authoritative: an automated result or an execution error.
        Otherwise the current scorer's result decides.

        Returns:
            The outcome for the response.
        """
        if attack.human_score is not None:
            return attack.outcome
        recorded_here = (
            conversation_id == attack.conversation_id
            if attack.last_response is None
            else response is not None
            and str(attack.last_response.id) in {str(piece.id) for piece in response.message_pieces}
        )
        if recorded_here and (self.execution_read_only(attack) or attack.outcome == AttackOutcome.ERROR):
            return attack.outcome
        return attack_outcome_from_score(score) if score is not None else AttackOutcome.UNDETERMINED

    @staticmethod
    async def _complete_result_async(
        *, scorer: Scorer, selection: ChatScoringSelection, response: Message
    ) -> Score | None:
        """
        Find a complete stored result for the same evaluation.

        Returns:
            The newest complete matching result, or None.
        """
        scores = await CentralMemory.get_memory_instance().get_prompt_scores_async(
            prompt_ids=[piece.id for piece in response.message_pieces]
        )
        matches = scorer.find_matching_scores(
            scores=scores, scorable=MessageScorable.from_message(response), expectation=selection.expectation
        )
        return next((score for score in matches if not score.is_undetermined), None)

    def _evaluation_fields(self, *, attack: AttackResult, evaluation: ChatEvaluation) -> dict[str, Any]:
        """
        Prepare the attack fields that record an evaluation for the current selection.

        Returns:
            The conversation's scoring diagnostics and, for the latest response, the attack's result.
        """
        piece = evaluation.response.message_pieces[0]
        errors = dict(attack.metadata.get(AttackResult.SCORING_ERRORS_KEY, {}))
        if evaluation.failure is None:
            errors.pop(str(piece.conversation_id), None)
        else:
            errors[str(piece.conversation_id)] = evaluation.failure.model_dump(mode="json")
        fields: dict[str, Any] = {"attack_metadata": {AttackResult.SCORING_ERRORS_KEY: errors}}
        if not self.is_latest_response(attack=attack, piece=piece):
            return fields
        score = evaluation.score
        fields.update(last_response_id=piece.id, automated_score_id=score.id if score else None)
        if attack.human_score is None:
            fields.update(
                outcome=(attack_outcome_from_score(score) if score else AttackOutcome.UNDETERMINED).value,
                outcome_reason=score.score_rationale if score else None,
            )
        return fields

    @staticmethod
    def _publication_guard(attack: AttackResult) -> dict[str, Any]:
        """
        Capture the attack state that a publication was prepared from.

        Returns:
            The fields that must be unchanged when the publication is written.
        """
        return {
            "objective": attack.objective,
            "attack_metadata": attack.metadata,
            "human_score_id": attack.human_score.id if attack.human_score else None,
            "last_response_id": attack.last_response.id if attack.last_response else None,
        }

    async def _apply_context_async(
        self, *, attack_result_id: str, request: ApplyChatScorerRequest
    ) -> tuple[ChatScoringSelection, Message]:
        """
        Validate an Apply request against the attack's current selection and latest live response.

        Returns:
            The current selection and the latest live response.

        Raises:
            LookupError: If the attack or conversation does not exist for this attack.
            AttackStateConflictError: If the objective, scorer, or latest response changed.
            ValueError: If the conversation has no live response.
        """
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not attacks:
            raise LookupError(f"Attack '{attack_result_id}' not found")
        attack = attacks[0]
        self.require_manual(attack)
        conversation_id = str(request.conversation_id)
        if conversation_id not in attack.get_active_conversation_ids():
            raise LookupError("The conversation does not belong to this attack.")
        selection = self.selection(attack)
        if (
            selection is None
            or selection.objective != request.objective
            or selection.scorer.identifier_hash != request.scorer.identifier_hash
        ):
            raise AttackStateConflictError("The objective or scorer changed. Reload before applying the scorer.")
        response = self.latest_response(await memory.get_conversation_messages_async(conversation_id=conversation_id))
        if response is None:
            raise ValueError("This conversation has no saved assistant response to score.")
        if str(request.response_id) not in {str(piece.id) for piece in response.message_pieces}:
            raise AttackStateConflictError("The latest response changed. Reload before applying the scorer.")
        return selection, response

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
