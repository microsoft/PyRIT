# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pyrit.common.deprecation import print_deprecation_message
from pyrit.exceptions import PyritException, ScorerLLMResponseBlockedException
from pyrit.models import (
    ChatMessageRole,
    Condition,
    ContentScorable,
    MatchesObjective,
    Message,
    MessagePiece,
    MessageScorable,
    PromptResponseError,
    Scorable,
    ScorableUnion,
    Score,
    ScoringExpectation,
)
from pyrit.models.score.scorable import SCORABLE_TYPES
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.scorer import LEGACY_SCORE_ASYNC_REMOVED_IN, Scorer

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from pyrit.memory import MemoryInterface
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

logger = logging.getLogger(__name__)

#: Release in which the message-shaped batch API is removed, two minor releases out.
MESSAGE_BATCH_REMOVED_IN = "1.3.0"


@dataclass(frozen=True, kw_only=True)
class MessageScoringOptions:
    """Message-only scoring policy that is not part of evidence identity."""

    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False


def extract_objective_from_previous_turn(*, message: Message, memory: MemoryInterface) -> str:
    """
    Read the text of the turn before an assistant message and use it as the objective.

    .. deprecated::
        This conflates scoring with building an expectation. What to look for belongs to
        the caller that builds the ``ScoringExpectation``, not to the scorer. It exists only
        to support the deprecated ``infer_objective_from_request`` parameter, and both are
        removed in the next major release. Resolve the objective at the call site and pass
        it on the expectation instead.

    Args:
        message (Message): The assistant message whose previous turn supplies the objective.
        memory (MemoryInterface): Memory holding the conversation.

    Returns:
        str: The previous turn's text, or an empty string when there is none.
    """
    if not message.message_pieces:
        return ""

    scored_piece = message.get_piece()

    if scored_piece.api_role != "assistant":
        return ""

    # The request is the turn before the response being scored, not before whatever the
    # conversation has grown to since. Scoring an earlier response must not read the latest turn.
    previous_sequence = scored_piece.sequence - 1
    if previous_sequence < 0:
        return ""

    conversation = memory.get_message_pieces(conversation_id=scored_piece.conversation_id)

    return "\n".join(
        [
            piece.original_value
            for piece in conversation
            if piece.sequence == previous_sequence and piece.original_value_data_type == "text"
        ]
    )


def _readable_pieces(*, message: Message, should_score_blocked_content: bool) -> list[MessagePiece]:
    """
    Select the pieces of a message that a scorer can read.

    This is the one place that decides what "readable" means. Every caller that asks whether a
    message is readable, or that needs the readable pieces themselves, goes through here, so the
    two questions cannot drift apart.

    Args:
        message (Message): The message to filter.
        should_score_blocked_content (bool): Whether content emitted before a block counts as readable.

    Returns:
        list[MessagePiece]: The pieces worth scoring, in their original order.
    """
    return [
        piece
        for piece in message.message_pieces
        if _piece_has_readable_content(piece=piece, should_score_blocked_content=should_score_blocked_content)
    ]


def message_has_readable_content(*, message: Message, should_score_blocked_content: bool) -> bool:
    """
    Decide whether a message carries anything a scorer can read.

    Message error state is message-family policy, so it is stated here rather than on the
    generic scorer base. An error is not automatically noise, and a single bad piece does not
    discard the pieces that came through beside it, so a message is unreadable only when every
    one of its pieces is.

    Args:
        message (Message): The message to judge.
        should_score_blocked_content (bool): Whether content emitted before a block counts as readable.

    Returns:
        bool: True when at least one piece is worth scoring.
    """
    return bool(_readable_pieces(message=message, should_score_blocked_content=should_score_blocked_content))


def _piece_has_readable_content(*, piece: MessagePiece, should_score_blocked_content: bool) -> bool:
    """
    Decide whether a single piece carries anything a scorer can read.

    Returns:
        bool: True when the piece did not error, or errored with content still behind it.
    """
    if not piece.has_error() and piece.converted_value_data_type != "error":
        return True
    # An SDK-provided structured refusal is the model's own account of why it refused.
    if piece.structured_refusal is not None:
        return True
    # Content the target emitted before the block is real output, when the caller opted in.
    return should_score_blocked_content and piece.is_blocked() and bool(piece.prompt_metadata.get("partial_content"))


class MessageScorer(Scorer):
    """
    Base class for scorers whose evidence is a single message.

    Every message-shaped concern lives here: substituting refusal and blocked content,
    validating pieces, applying the role and error filters, and falling back to a neutral
    score. ``Scorer`` stays agnostic about what a scorable is, so scorers over other kinds of
    evidence can sit beside this one. A ``MessageScorableResolver`` acquires the message;
    the scorable remains inert.

    Subclasses implement ``_score_async``, which still receives a ``Message``.
    """

    #: When False, a blocked response from the scorer's own LLM produces an undetermined
    #: score instead of raising.
    raise_if_scorer_blocks: bool = True

    #: When True, a blocked response that still carries content the target emitted before the
    #: block is scored on that content. Turn off to treat any block as unreadable.
    #:
    #: This defaults to True, where the former ``score_blocked_content`` defaulted to False. The
    #: change is deliberate. Partial content is text the target actually produced, so discarding
    #: it reported a refusal the target never made and understated attack success. A block is a
    #: transport outcome, not a verdict, and reading the content the target emitted keeps the
    #: verdict with the scorer. Set this to False to score only responses that were never blocked.
    should_score_blocked_content: bool = True

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator,
        chat_target: PromptTarget | None = None,
        message_resolver: MessageScorableResolver | None = None,
    ) -> None:
        """
        Initialize message-specific scoring dependencies.

        Args:
            validator (ScorerPromptValidator): Validator for message pieces.
            chat_target (PromptTarget | None): Optional target used by the scorer.
            message_resolver (MessageScorableResolver | None): Evidence resolver.
        """
        self._validator = validator
        self._message_resolver = message_resolver or MessageScorableResolver()
        super().__init__(chat_target=chat_target)

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Return the conditions this message scorer uses as criteria.

        An objective-required validator is the existing declaration that the scorer judges
        whether the evidence satisfies the objective. Other message scorers may read the
        objective as context without matching ``MatchesObjective``.

        Returns:
            frozenset[type[Condition]]: The matched condition types.
        """
        matched = super().matched_conditions()
        if self._validator.is_objective_required:
            return matched | {MatchesObjective}
        return matched

    def required_conditions(self) -> frozenset[type[Condition]]:
        """Return the matched conditions required by this message scorer."""
        required = super().required_conditions()
        if self._validator.is_objective_required:
            return required | {MatchesObjective}
        return required

    def _validate_expectation(
        self,
        *,
        expectation: ScoringExpectation | None,
        allow_unmatched_conditions: bool = False,
    ) -> None:
        """
        Reject conditions this scorer cannot consume, and unusable ``MatchesObjective``.

        Args:
            expectation (ScoringExpectation | None): The expectation to validate.
            allow_unmatched_conditions (bool): Permit conditions addressed to sibling leaves.

        Raises:
            ValueError: If ``MatchesObjective`` is present without an objective to match.
        """
        super()._validate_expectation(
            expectation=expectation,
            allow_unmatched_conditions=allow_unmatched_conditions,
        )
        if expectation is None or not expectation.conditions:
            return
        matches_objective = MatchesObjective in self.matched_conditions() and any(
            isinstance(condition, MatchesObjective) for condition in expectation.conditions
        )
        if matches_objective and not expectation.objective:
            raise ValueError(
                "MatchesObjective requires the expectation to carry an objective. "
                "Set ScoringExpectation.objective or drop the condition."
            )

    async def score_async(
        self,
        message: Message | None = None,
        *,
        scorable: Scorable | None = None,
        expectation: ScoringExpectation | None = None,
        message_options: MessageScoringOptions | None = None,
        objective: str | None = None,
        role_filter: ChatMessageRole | None = None,
        skip_on_error_result: bool | None = None,
        infer_objective_from_request: bool | None = None,
    ) -> list[Score]:
        """
        Score message-shaped evidence, including the deprecated message API.

        Args:
            message (Message | None): Deprecated in-hand message.
            scorable (Scorable | None): Message-shaped evidence to acquire.
            expectation (ScoringExpectation | None): What to look for.
            message_options (MessageScoringOptions | None): Message-family policy.
            objective (str | None): Deprecated objective string.
            role_filter (ChatMessageRole | None): Deprecated role policy.
            skip_on_error_result (bool | None): Deprecated error policy. ``None`` means omitted.
            infer_objective_from_request (bool | None): Deprecated inference policy.

        Returns:
            list[Score]: The persisted scores, or an empty list when policy skips the message.
        """
        resolved_expectation, options, infer_objective = self._consolidate_message_inputs(
            message=message,
            scorable=scorable,
            expectation=expectation,
            message_options=message_options,
            objective=objective,
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
            infer_objective_from_request=infer_objective_from_request,
        )
        self._validate_expectation(expectation=resolved_expectation)

        # The deprecated parameter hands over the message itself, so scoring it must not round
        # trip through a reference. Re-describing it would reload the persisted originals and
        # drop role and error state for a message that was never persisted at all.
        if message is not None:
            scores = await self._score_resolved_message_async(
                message=message,
                expectation=resolved_expectation,
                options=options,
                infer_objective_from_request=infer_objective,
            )
        else:
            scores = await self._score_message_scorable_async(
                scorable=cast("Scorable", scorable),
                expectation=resolved_expectation,
                options=options,
                infer_objective_from_request=infer_objective,
            )
        return await self._validate_and_persist_scores_async(scores=scores)

    async def score_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None = None,
        message_options: MessageScoringOptions | None = None,
    ) -> list[Score]:
        """
        Score a message that is already in hand.

        Use this when the caller holds the message itself rather than a reference to it:
        an ephemeral response that was never persisted, or a scoring view a wrapping scorer
        has already prepared. Naming persisted evidence with a ``MessageScorable`` stays the
        default, because a reference is what a stored score can be audited against.

        Args:
            message (Message): The message to score.
            expectation (ScoringExpectation | None): What to look for. Defaults to None.
            message_options (MessageScoringOptions | None): Message-family policy. Defaults to None.

        Returns:
            list[Score]: The persisted scores, or an empty list when policy skips the message.
        """
        self._validate_expectation(expectation=expectation)
        scores = await self._score_resolved_message_async(
            message=message,
            expectation=expectation,
            options=message_options or MessageScoringOptions(),
            infer_objective_from_request=False,
        )
        return await self._validate_and_persist_scores_async(scores=scores)

    async def score_prompts_batch_async(
        self,
        *,
        messages: Sequence[Message],
        objectives: Sequence[str] | None = None,
        batch_size: int = 10,
        role_filter: ChatMessageRole | None = None,
        skip_on_error_result: bool = False,
        infer_objective_from_request: bool = False,
    ) -> list[Score]:
        """
        Score multiple messages in batches using the provided objectives.

        .. deprecated:: 1.1.0
            Use ``Scorer.score_batch_async`` with ``MessageScorable`` evidence instead.
            A ``Scorable`` names the evidence a score points at, so it needs no extra
            memory lookup to resolve the anchor.

        Args:
            messages (Sequence[Message]): The messages to be scored.
            objectives (Sequence[str]): The objectives/tasks based on which the prompts should be scored.
                Must have the same length as messages.
            batch_size (int): The maximum batch size for processing prompts. Defaults to 10.
            role_filter (ChatMessageRole | None): If provided, only score pieces with this role.
                Defaults to None (no filtering).
            skip_on_error_result (bool): If True, skip scoring pieces that have errors. Defaults to False.
            infer_objective_from_request (bool): If True and objective is empty, attempt to infer
                the objective from the request. Defaults to False.

        Returns:
            list[Score]: A flattened list of Score objects from all scored prompts.

        Raises:
            ValueError: If objectives is not None and the number of objectives doesn't match
                the number of messages.
        """
        print_deprecation_message(
            old_item="MessageScorer.score_prompts_batch_async",
            new_item="Scorer.score_batch_async with MessageScorable evidence",
            removed_in=MESSAGE_BATCH_REMOVED_IN,
        )

        if objectives is None:
            resolved_objectives = [""] * len(messages)
        elif len(objectives) != len(messages):
            raise ValueError("The number of objectives must match the number of messages.")
        else:
            resolved_objectives = list(objectives)

        if len(messages) == 0:
            return []

        if infer_objective_from_request:
            resolved_objectives = [
                objective or extract_objective_from_previous_turn(message=message, memory=self._memory)
                for message, objective in zip(messages, resolved_objectives, strict=True)
            ]

        return await self.score_batch_async(
            scorables=[MessageScorable.from_message(message) for message in messages],
            expectations=[ScoringExpectation(objective=objective) for objective in resolved_objectives],
            batch_size=batch_size,
            message_options=MessageScoringOptions(
                role_filter=role_filter,
                skip_on_error_result=skip_on_error_result,
            ),
        )

    @staticmethod
    async def score_response_async(
        *,
        response: Message,
        objective_scorer: Scorer | None = None,
        auxiliary_scorers: list[Scorer] | None = None,
        role_filter: ChatMessageRole = "assistant",
        objective: str | None = None,
        skip_on_error_result: bool = True,
    ) -> dict[str, list[Score]]:
        """
        Score a response using an objective scorer and optional auxiliary scorers.

        Args:
            response (Message): Response containing pieces to score.
            objective_scorer (Scorer | None): The main scorer to determine success. Defaults to None.
            auxiliary_scorers (list[Scorer] | None): List of auxiliary scorers to apply. Defaults to None.
            role_filter (ChatMessageRole): Only score pieces with this exact stored role.
                Defaults to "assistant" (real responses only, not simulated).
            objective (str | None): Task/objective for scoring context. Defaults to None.
            skip_on_error_result (bool): If True, skip scoring pieces that have errors. Defaults to True.

        Returns:
            dict[str, list[Score]]: Dictionary with keys `auxiliary_scores` and `objective_scores`
                containing lists of scores from each type of scorer.

        Raises:
            ValueError: If response is not provided.
        """
        result: dict[str, list[Score]] = {"auxiliary_scores": [], "objective_scores": []}

        if not response:
            raise ValueError("Response must be provided for scoring.")

        # If no objective_scorer is provided, only run auxiliary_scorers if present
        if objective_scorer is None:
            if auxiliary_scorers:
                aux_scores = await MessageScorer.score_response_multiple_scorers_async(
                    response=response,
                    scorers=auxiliary_scorers,
                    role_filter=role_filter,
                    objective=objective,
                    skip_on_error_result=skip_on_error_result,
                )
                result["auxiliary_scores"] = aux_scores
            # objective_scores remains empty
            return result

        # Run auxiliary and objective scoring in parallel if auxiliary_scorers is provided
        if auxiliary_scorers:
            aux_task = MessageScorer.score_response_multiple_scorers_async(
                response=response,
                scorers=auxiliary_scorers,
                role_filter=role_filter,
                objective=objective,
                skip_on_error_result=skip_on_error_result,
            )
            obj_task = MessageScorer._score_response_with_scorer_async(
                scorer=objective_scorer,
                response=response,
                expectation=ScoringExpectation(objective=objective),
                role_filter=role_filter,
                skip_on_error_result=skip_on_error_result,
            )
            aux_scores, obj_scores = await asyncio.gather(aux_task, obj_task)
            result["auxiliary_scores"] = aux_scores
            result["objective_scores"] = obj_scores
        else:
            obj_scores = await MessageScorer._score_response_with_scorer_async(
                scorer=objective_scorer,
                response=response,
                expectation=ScoringExpectation(objective=objective),
                role_filter=role_filter,
                skip_on_error_result=skip_on_error_result,
            )
            result["objective_scores"] = obj_scores
        return result

    @staticmethod
    async def score_response_multiple_scorers_async(
        *,
        response: Message,
        scorers: list[Scorer],
        role_filter: ChatMessageRole = "assistant",
        objective: str | None = None,
        skip_on_error_result: bool = True,
    ) -> list[Score]:
        """
        Score a response using multiple scorers in parallel.

        This method applies each scorer to the first scorable response piece (filtered by role and error),
        and returns all scores. This is typically used for auxiliary scoring where all results are needed.

        Args:
            response (Message): The response containing pieces to score.
            scorers (list[Scorer]): List of scorers to apply.
            role_filter (ChatMessageRole): Only score pieces with this exact stored role.
                Defaults to "assistant" (real responses only, not simulated).
            objective (str | None): Optional objective description for scoring context.
            skip_on_error_result (bool): If True, skip scoring pieces that have errors (default: True).

        Returns:
            list[Score]: All scores from all scorers
        """
        if not scorers:
            return []

        expectation = ScoringExpectation(objective=objective)
        tasks = [
            MessageScorer._score_response_with_scorer_async(
                scorer=scorer,
                response=response,
                expectation=expectation,
                role_filter=role_filter,
                skip_on_error_result=skip_on_error_result,
            )
            for scorer in scorers
        ]

        # Execute all tasks in parallel
        score_lists = await asyncio.gather(*tasks)

        # Flatten the list of lists into a single list
        return [score for scores in score_lists for score in scores]

    @staticmethod
    async def _score_response_with_scorer_async(
        *,
        scorer: Scorer,
        response: Message,
        expectation: ScoringExpectation,
        role_filter: ChatMessageRole,
        skip_on_error_result: bool,
    ) -> list[Score]:
        """
        Apply response-scoring policy without storing policy on the scorable.

        Role and error policy decide whether this response is scored at all, so they are
        settled here in the message family rather than inside a scorer that may not be
        message-shaped.

        Returns:
            list[Score]: Scores from the scorer, or an empty list when policy skips the response.
        """
        if response.get_piece().role != role_filter:
            logger.debug("Skipping scoring due to role filter mismatch.")
            return []
        if (
            isinstance(scorer, MessageScorer)
            and skip_on_error_result
            and not message_has_readable_content(
                message=response,
                should_score_blocked_content=scorer.should_score_blocked_content,
            )
        ):
            return []
        return await scorer.score_async(
            scorable=MessageScorable.from_message(response),
            expectation=expectation,
        )

    def _consolidate_message_inputs(
        self,
        *,
        message: Message | None,
        scorable: Scorable | None,
        expectation: ScoringExpectation | None,
        message_options: MessageScoringOptions | None,
        objective: str | None,
        role_filter: ChatMessageRole | None,
        skip_on_error_result: bool | None,
        infer_objective_from_request: bool | None,
    ) -> tuple[ScoringExpectation | None, MessageScoringOptions, bool]:
        if message is not None and scorable is not None:
            raise ValueError("Pass either 'message' or 'scorable', not both.")
        if message is None and scorable is None:
            raise ValueError("Either 'message' or 'scorable' must be provided.")
        if objective is not None and expectation is not None:
            raise ValueError("Pass either 'objective' or 'expectation', not both.")
        if message_options is not None and (role_filter is not None or skip_on_error_result is not None):
            raise ValueError("Pass either 'message_options' or legacy message policy arguments, not both.")

        uses_legacy_parameters = (
            message is not None
            or objective is not None
            or role_filter is not None
            or skip_on_error_result is not None
            or infer_objective_from_request is not None
        )
        if uses_legacy_parameters:
            print_deprecation_message(
                old_item="Scorer.score_async(message=..., objective=..., role_filter=..., "
                "skip_on_error_result=..., infer_objective_from_request=...)",
                new_item="Scorer.score_async(scorable=..., expectation=..., message_options=...)",
                removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
            )

        resolved_expectation = ScoringExpectation(objective=objective) if objective is not None else expectation
        options = message_options or MessageScoringOptions(
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result or False,
        )
        return resolved_expectation, options, bool(infer_objective_from_request)

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score message-shaped evidence with default message policy.

        Returns:
            list[Score]: The scores produced from the resolved message.
        """
        return await self._score_message_scorable_async(
            scorable=scorable,
            expectation=expectation,
            options=MessageScoringOptions(),
            infer_objective_from_request=False,
        )

    async def _score_message_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        options: MessageScoringOptions,
        infer_objective_from_request: bool,
    ) -> list[Score]:
        """
        Resolve a message scorable and score the message it names.

        Args:
            scorable (Scorable): A ``MessageScorable`` or a ``ContentScorable``.
            expectation (ScoringExpectation | None): What to look for.
            options (MessageScoringOptions): Message-only scoring policy.
            infer_objective_from_request (bool): Deprecated; read the objective from the
                previous turn when the expectation carries none.

        Returns:
            list[Score]: The scores, or an empty list when a filter skipped the message.

        Raises:
            TypeError: If the scorable is not message-shaped.
        """
        message = self._message_resolver.resolve(scorable=scorable, memory=self._memory)
        return await self._score_resolved_message_async(
            message=message,
            expectation=expectation,
            options=options,
            infer_objective_from_request=infer_objective_from_request,
            anchor=scorable,
        )

    async def _score_resolved_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
        options: MessageScoringOptions,
        infer_objective_from_request: bool,
        anchor: Scorable | None = None,
    ) -> list[Score]:
        """
        Run the message-scoring pipeline over an acquired message.

        Args:
            message (Message): The acquired message.
            expectation (ScoringExpectation | None): What to look for.
            options (MessageScoringOptions): Message-only scoring policy.
            infer_objective_from_request (bool): Deprecated; read the objective from the
                previous turn when the expectation carries none.
            anchor (Scorable | None): The scorable the caller named, when the message was
                acquired from one. Scores anchor on it rather than on the acquired message.

        Returns:
            list[Score]: The scores, or an empty list when a filter skipped the message.

        Raises:
            ScorerLLMResponseBlockedException: If the scorer's own LLM response is blocked by
                content filtering and ``raise_if_scorer_blocks`` is True (the default).
            PyritException: If scoring raises a PyRIT exception (re-raised with enhanced context).
            RuntimeError: If scoring raises a non-PyRIT exception (wrapped with scorer context).
        """
        objective = expectation.objective if expectation else None

        if options.role_filter is not None and message.message_pieces[0].role != options.role_filter:
            logger.debug("Skipping scoring due to role filter mismatch.")
            return []

        # This gate runs before _build_scoring_message because that method is an override hook:
        # a wrapper may keep a piece the filter would drop. Both use _readable_pieces, so the
        # gate and the filter agree on what "readable" means.
        if options.skip_on_error_result and not message_has_readable_content(
            message=message,
            should_score_blocked_content=self.should_score_blocked_content,
        ):
            return []

        scoring_message = self._build_scoring_message(message=message)

        if infer_objective_from_request and (not objective):
            objective = extract_objective_from_previous_turn(message=message, memory=self._memory)

        effective_expectation = expectation
        if expectation is None and objective is not None:
            effective_expectation = ScoringExpectation(objective=objective)
        elif expectation is not None and objective != expectation.objective:
            effective_expectation = ScoringExpectation(
                objective=objective,
                conditions=expectation.conditions,
            )

        if scoring_message is None:
            scores = self._build_fallback_score(message=message, objective=objective)
            self._finalize_message_scores(message=message, scores=scores, anchor=anchor)
            return scores

        self._validator.validate(scoring_message, objective=objective)

        try:
            scores = await self._score_prepared_message_async(
                message=scoring_message,
                expectation=effective_expectation,
            )
        except ScorerLLMResponseBlockedException as e:
            # The scorer's own LLM response was content-filtered. By default this is a real
            # error and propagates; when raise_if_scorer_blocks is False, no verdict was
            # reached, so the score is undetermined rather than a definitive negative. The
            # decision lives here in the scorer, not the transport (see doc/code/framework.md).
            if self.raise_if_scorer_blocks:
                e.message = f"Error in scorer {self.__class__.__name__}: {e.message}"
                e.args = (f"Status Code: {e.status_code}, Message: {e.message}",)
                raise
            logger.info(
                "Scorer %s LLM response was blocked by content filtering; "
                "returning an undetermined score (raise_if_scorer_blocks=False).",
                self.__class__.__name__,
            )
            first_piece = scoring_message.message_pieces[0]
            scores = [
                self._build_undetermined_score(
                    rationale=(
                        "The scorer's own LLM response was blocked by content filtering "
                        "(raise_if_scorer_blocks is False), so no verdict was reachable."
                    ),
                    description="Scorer response blocked; no verdict was reachable.",
                    message_piece_id=first_piece.id or first_piece.original_prompt_id,
                    objective=objective,
                )
            ]
        except PyritException as e:
            # Re-raise PyRIT exceptions with enhanced context while preserving type for retry decorators
            e.message = f"Error in scorer {self.__class__.__name__}: {e.message}"
            e.args = (f"Status Code: {e.status_code}, Message: {e.message}",)
            raise
        except Exception as e:
            # Wrap non-PyRIT exceptions for better error tracing
            raise RuntimeError(f"Error in scorer {self.__class__.__name__}: {str(e)}") from e

        if not scores and scoring_message.message_pieces:
            scores = self._build_fallback_score(message=scoring_message, objective=objective)

        self._finalize_message_scores(message=scoring_message, scores=scores, anchor=anchor)

        return scores

    def _finalize_message_scores(
        self,
        *,
        message: Message,
        scores: list[Score],
        anchor: Scorable | None,
    ) -> None:
        """Apply legacy and canonical evidence anchors to completed message scores."""
        persisted_piece_ids = self._get_persisted_piece_ids(message=message) if anchor is None else None
        self._drop_ephemeral_score_links(
            message=message,
            scores=scores,
            persisted_piece_ids=persisted_piece_ids,
        )
        self._stamp_scorable(
            message=message,
            scores=scores,
            anchor=anchor,
            persisted_piece_ids=persisted_piece_ids,
        )

    async def _score_prepared_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score a message after message-family policy and substitutions are applied.

        Wrapping scorers override this hook to forward the complete expectation. Existing
        leaf scorer bodies continue to receive only the objective string.

        Returns:
            list[Score]: The scores produced from the prepared message.
        """
        return await self._score_async(
            message,
            objective=expectation.objective if expectation else None,
        )

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Score the given request response asynchronously.

        This default implementation scores all supported pieces in the message
        and returns a flattened list of scores. Subclasses can override this method
        to implement custom scoring logic (e.g., aggregating scores).

        Args:
            message (Message): The message to score.
            objective (str | None): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list of Score objects.
        """
        if not message.message_pieces:
            return []

        # Score only the supported pieces
        supported_pieces = self._get_supported_pieces(message)

        tasks = [self._score_piece_async(message_piece=piece, objective=objective) for piece in supported_pieces]

        if not tasks:
            return []

        # Run all piece-level scorings concurrently
        piece_score_lists = await asyncio.gather(*tasks)

        # Flatten list[list[Score]] -> list[Score]
        return [score for sublist in piece_score_lists for score in sublist]

    @abstractmethod
    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        raise NotImplementedError

    def _build_scoring_message(self, *, message: Message) -> Message | None:
        """
        Build the view of a message this scorer reads, or nothing when there is none.

        Substitution and the error filter are one policy seen twice: a blocked piece is worth
        keeping only when readable content sits behind it, and that content is exactly what the
        scorer is handed. A structured refusal becomes its refusal text but stays marked
        blocked, so refusal scorers keep their deterministic path; content emitted before a
        block becomes ordinary text once the scorer opts into reading it.

        This shares ``_readable_pieces`` with the caller's ``skip_on_error_result`` gate, so the
        two agree on what is readable. It stays a separate step because it is an override hook:
        a wrapper that uses the message only to locate wider evidence keeps a piece this filter
        would drop (see ``ConversationScorer``), and the gate still runs before that override.

        Args:
            message (Message): The acquired message.

        Returns:
            Message | None: The message to score, or None when no piece is readable.
        """
        pieces = _readable_pieces(
            message=message,
            should_score_blocked_content=self.should_score_blocked_content,
        )
        if not pieces:
            logger.debug("Skipping scoring: every piece of the message errored with nothing behind it.")
            return None

        scoring_message = Message(message_pieces=pieces)
        scoring_message = self._apply_structured_refusal_substitution(scoring_message)
        if self.should_score_blocked_content:
            scoring_message = self._apply_blocked_content_substitution(scoring_message)
        return scoring_message

    def _get_supported_pieces(self, message: Message) -> list[MessagePiece]:
        """
        Get a list of supported message pieces for this scorer.

        Returns:
            list[MessagePiece]: List of message pieces that are supported by this scorer's validator.
        """
        return [
            piece for piece in message.message_pieces if self._validator.is_message_piece_supported(message_piece=piece)
        ]

    def _get_persisted_piece_ids(self, *, message: Message) -> set[uuid.UUID]:
        """Return the IDs from this message that memory can resolve."""
        candidate_ids = [piece.id for piece in message.message_pieces if not piece.not_in_memory]
        if not candidate_ids:
            return set()

        stored_pieces = self._memory.get_message_pieces(
            prompt_ids=[str(piece_id) for piece_id in candidate_ids],
        )
        return {piece.id for piece in stored_pieces}

    @staticmethod
    def _scorable_from_message(
        message: Message,
        *,
        persisted_piece_ids: set[uuid.UUID],
    ) -> ScorableUnion | None:
        """
        Name the evidence a prepared message holds.

        A persisted message is named by its piece ids. A message that was never persisted
        has no ids to name, so its converted content is the anchor instead.

        Returns:
            Scorable | None: The anchor, or None when the message names nothing recoverable.
        """
        pieces = message.message_pieces
        if not pieces:
            return None
        if all(piece.id in persisted_piece_ids for piece in pieces):
            return MessageScorable.from_message(message)
        if len(pieces) == 1:
            return ContentScorable.from_message(message)
        return None

    @staticmethod
    def _stamp_scorable(
        *,
        message: Message,
        scores: list[Score],
        anchor: Scorable | None,
        persisted_piece_ids: set[uuid.UUID] | None,
    ) -> None:
        """
        Anchor scores on the evidence they were taken over, when the scorer left it unset.

        The scorable the caller named is that evidence whenever there is one, so re-scoring
        stored content keeps pointing at the row it already has instead of copying it into a
        new one. Only a message handed over directly has nothing to keep, and its content
        becomes the anchor so a dropped piece link still leaves provenance behind.

        Raises:
            TypeError: If the anchor is not one of the kinds a ``Score`` can carry.
        """
        stamped = (
            anchor
            if anchor is not None
            else MessageScorer._scorable_from_message(
                message,
                persisted_piece_ids=persisted_piece_ids or set(),
            )
        )
        if stamped is None:
            return
        # Score sets validate_assignment=False, so nothing downstream checks what we attach here.
        if not isinstance(stamped, SCORABLE_TYPES):
            known = ", ".join(sorted(kind.__name__ for kind in SCORABLE_TYPES))
            raise TypeError(
                f"{type(stamped).__name__} cannot anchor a score. Add it to ScorableUnion with its own "
                f"'scorable_type' tag. Known scorables: {known}."
            )
        anchor_scorable = cast("ScorableUnion", stamped)
        for score in scores:
            if score.scorable is None:
                score.scorable = anchor_scorable

    @staticmethod
    def _drop_ephemeral_score_links(
        *,
        message: Message,
        scores: list[Score],
        persisted_piece_ids: set[uuid.UUID] | None,
    ) -> None:
        """
        Clear the piece link on scores that point at pieces which were never persisted.

        Memory cannot link a score to a piece it never stored, but the score itself is
        still worth keeping.
        """
        ephemeral_piece_ids = {
            piece.id
            for piece in message.message_pieces
            if piece.not_in_memory or (persisted_piece_ids is not None and piece.id not in persisted_piece_ids)
        }
        if not ephemeral_piece_ids:
            return

        for score in scores:
            if score.message_piece_id in ephemeral_piece_ids:
                score.message_piece_id = None  # type: ignore[ty:invalid-assignment]

    @staticmethod
    def _create_scoring_text_piece(
        *,
        piece: MessagePiece,
        content: str,
        response_error: PromptResponseError,
    ) -> MessagePiece:
        """
        Create a text scoring view that retains the persisted piece identity.

        Returns:
            MessagePiece: The text scoring view.
        """
        return MessagePiece(
            id=piece.id,
            role=piece.api_role,
            original_value=piece.original_value,
            converted_value=content,
            original_value_data_type=piece.original_value_data_type,
            converted_value_data_type="text",
            conversation_id=piece.conversation_id,
            sequence=piece.sequence,
            prompt_metadata=dict(piece.prompt_metadata),
            converter_identifiers=list(piece.converter_identifiers),  # type: ignore[arg-type]
            response_error=response_error,
            timestamp=piece.timestamp,
            original_prompt_id=piece.original_prompt_id,
            not_in_memory=piece.not_in_memory,
        )

    @classmethod
    def _create_text_piece_from_blocked(cls, piece: MessagePiece) -> MessagePiece | None:
        """
        Create a text scoring view from a blocked piece's partial content.

        Returns:
            MessagePiece | None: The scoring view, or None when content is unavailable.
        """
        partial_content = str(piece.prompt_metadata.get("partial_content", ""))
        if not partial_content:
            return None
        return cls._create_scoring_text_piece(
            piece=piece,
            content=partial_content,
            response_error="none",
        )

    @classmethod
    def _create_text_piece_from_structured_refusal(cls, piece: MessagePiece) -> MessagePiece | None:
        """
        Create a blocked text scoring view for an SDK-provided refusal.

        Returns:
            MessagePiece | None: The scoring view, or None when there is no refusal.
        """
        refusal = piece.structured_refusal
        if not refusal:
            return None
        return cls._create_scoring_text_piece(
            piece=piece,
            content=refusal,
            response_error="blocked",
        )

    def _apply_structured_refusal_substitution(self, message: Message) -> Message:
        """
        Expose structured refusal explanations while preserving blocked semantics.

        Returns:
            Message: The substituted message, or the original message.
        """
        substituted = False
        new_pieces: list[MessagePiece] = []
        for piece in message.message_pieces:
            substitute = self._create_text_piece_from_structured_refusal(piece)
            if substitute:
                new_pieces.append(substitute)
                substituted = True
                continue
            new_pieces.append(piece)
        return Message(message_pieces=new_pieces) if substituted else message

    def _apply_blocked_content_substitution(self, message: Message) -> Message:
        """
        Replace blocked pieces that have partial content with text scoring views.

        Returns:
            Message: The substituted message, or the original message.
        """
        substituted = False
        new_pieces: list[MessagePiece] = []
        for piece in message.message_pieces:
            if piece.is_blocked() and "partial_content" in piece.prompt_metadata:
                substitute = self._create_text_piece_from_blocked(piece)
                if substitute:
                    new_pieces.append(substitute)
                    substituted = True
                    continue
            new_pieces.append(piece)
        return Message(message_pieces=new_pieces) if substituted else message

    @abstractmethod
    def _build_fallback_score(
        self,
        *,
        message: Message,
        objective: str | None,
    ) -> list[Score]:
        """
        Return the scorer family's neutral result when message evidence is unscoreable.

        Args:
            message (Message): The message-shaped evidence.
            objective (str | None): The objective associated with this call.

        Returns:
            list[Score]: One or more fallback scores.
        """
        ...

    def _build_neutral_fallback_score(
        self,
        *,
        message: Message,
        objective: str | None,
        neutral_value: str,
    ) -> list[Score]:
        """
        Build the family's neutral result for a message that carries nothing to score.

        Blocked, error, and filtered messages read the same way whatever the score family is,
        so this message-family policy is stated once here. Each family supplies only its
        neutral value.

        Args:
            message (Message): The message whose first piece tells why nothing was scored.
            objective (str | None): The objective associated with this scoring call.
            neutral_value (str): The family's neutral score value, such as "false" or "0.0".

        Returns:
            list[Score]: One neutral score of this scorer's type, or one undetermined score
                when the response failed with an error.

        Raises:
            ValueError: If the first message piece has no ``id`` or ``original_prompt_id``.
        """
        first_piece = message.message_pieces[0]
        piece_id = first_piece.id or first_piece.original_prompt_id
        if piece_id is None:
            raise ValueError("Cannot create score: message piece has no id or original_prompt_id")

        if first_piece.is_blocked():
            rationale = f"The response was blocked with no content to score; returning {neutral_value}."
            description = f"Blocked response; returning {neutral_value}."
        elif first_piece.has_error():
            # A transport or protocol failure is not the target's answer, so there is no verdict.
            return [
                self._build_undetermined_score(
                    rationale=f"Response had an error: {first_piece.response_error}; no verdict was reachable.",
                    description="Error response; no verdict was reachable.",
                    message_piece_id=piece_id,
                    objective=objective,
                )
            ]
        else:
            # this can happen with multi-modal responses if no supported pieces are present
            rationale = f"No supported pieces to score after filtering; returning {neutral_value}."
            description = f"No pieces to score after filtering; returning {neutral_value}."

        return [
            Score(
                score_value=neutral_value,
                score_value_description=description,
                score_type=self.scorer_type,
                score_category=None,
                score_metadata=None,
                score_rationale=rationale,
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=piece_id,
                objective=objective,
            )
        ]
