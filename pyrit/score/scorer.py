# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import asyncio
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    Identifiable,
    Message,
    MessagePiece,
    PromptResponseError,
    Score,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    ScoreType,
    ScoringExpectation,
)
from pyrit.prompt_target.batch_helper import batch_task_async
from pyrit.prompt_target.common.target_requirements import TargetRequirements
from pyrit.score.scorable import ContentScorable, MessageScorable, Scorable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer_evaluation.metrics_type import RegistryUpdateBehavior
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

logger = logging.getLogger(__name__)

#: Release in which the message-shaped ``score_async`` parameters are removed.
LEGACY_SCORE_ASYNC_REMOVED_IN = "2.0.0"


class Scorer(Identifiable, abc.ABC):
    """
    Abstract base class for scorers.

    Subclasses must use the keyword-only constructor shape
    (``def __init__(self, *, ...)``); the contract is enforced at class
    definition time via ``enforce_keyword_only_init``. See
    ``.github/instructions/scorers.instructions.md`` for the full contract.
    """

    # Evaluation configuration - maps input dataset files to a result file.
    # Specifies glob patterns for datasets and a result file name.
    evaluation_file_mapping: ScorerEvalDatasetFiles | None = None

    #: Capability requirements placed on the scorer's chat target (if any).
    #: Subclasses that use a chat target should override this and pass the
    #: target to ``super().__init__(chat_target=...)`` so the base class can
    #: validate it.
    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements()

    _identifier: ComponentIdentifier | None = None

    #: When True, blocked responses that contain partial content
    #: (in prompt_metadata["partial_content"]) will be scored using that content
    #: instead of being filtered out or short-circuited.
    #: Set this on scorer instances before use. Defaults to False.
    #:
    #: Note: Partial content extraction is supported for ``OpenAIChatTarget``
    #: (Chat Completions API) and ``OpenAIResponseTarget`` (Responses API).
    score_blocked_content: bool = False

    #: Controls what happens when the scorer's *own* LLM response is blocked by content
    #: filtering (common in red-teaming, since the scorer's rationale quotes harmful content).
    #: When True (default), scoring raises ``ScorerLLMResponseBlockedException`` — a blocked
    #: scorer endpoint is treated as a real error. When False, scoring returns the scorer's
    #: type default instead (False for true/false scorers, 0.0 for float-scale). This is
    #: distinct from ``score_blocked_content``, which concerns the target-under-test response.
    #: Set this on scorer instances before use. Defaults to True.
    raise_if_scorer_blocks: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Enforce the keyword-only constructor contract on subclasses.

        See ``.github/instructions/scorers.instructions.md`` for the contract.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="Scorer")

    def __init__(self, *, validator: ScorerPromptValidator, chat_target: PromptTarget | None = None) -> None:
        """
        Initialize the Scorer.

        Args:
            validator (ScorerPromptValidator): Validator for message pieces and scorer configuration.
            chat_target (PromptTarget | None): Chat target used by the scorer, if any. When
                provided, it is validated against ``TARGET_REQUIREMENTS``.
        """
        self._validator = validator
        if chat_target is not None:
            type(self).TARGET_REQUIREMENTS.validate(target=chat_target)

    def get_chat_target(self) -> PromptTarget | None:
        """
        Return the chat target used by this scorer, or None if it doesn't use one.

        Subclasses that wrap other scorers (e.g. inverters, composites) should
        override to delegate to their inner scorer(s).

        Returns:
            PromptTarget | None: The chat target, or None if not applicable.
        """
        return getattr(self, "_prompt_target", None)

    def get_identifier(self) -> ComponentIdentifier:
        """
        Get the scorer's identifier with eval_hash always attached.

        Overrides the base ``Identifiable.get_identifier()`` so that
        ``to_dict()`` always emits the ``eval_hash`` key.

        Returns:
            ComponentIdentifier: The identity with ``eval_hash`` set.
        """
        identifier = super().get_identifier()
        identifier = identifier.with_eval_hash(ScorerEvaluationIdentifier(identifier).eval_hash)
        self._identifier = identifier
        return identifier

    @property
    def scorer_type(self) -> ScoreType:
        """
        The scorer type based on class hierarchy.

        Returns:
            ScoreType: "true_false" for TrueFalseScorer subclasses,
                      "float_scale" for FloatScaleScorer subclasses,
                      "unknown" for other scorers.
        """
        # Import here to avoid circular imports
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
        from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

        if isinstance(self, TrueFalseScorer):
            return "true_false"
        if isinstance(self, FloatScaleScorer):
            return "float_scale"
        return "unknown"

    @property
    def _memory(self) -> MemoryInterface:
        return CentralMemory.get_memory_instance()

    def _create_identifier(
        self,
        *,
        params: dict[str, Any] | None = None,
        score_aggregator: str | None = None,
        prompt_target: ComponentIdentifier | None = None,
        sub_scorers: list[ComponentIdentifier] | None = None,
    ) -> ComponentIdentifier:
        """
        Construct the scorer identifier.

        Builds a ``ScorerIdentifier`` with the base scorer ``scorer_type`` and
        the scorer's promoted params/child slots. The promoted fields are exposed
        as explicit named parameters (mirroring ``ScorerIdentifier``'s fields) so
        they cannot drift into untyped ``params`` / ``children`` dicts.

        Subclasses should call this method in their _build_identifier() implementation
        to set the identifier with their specific parameters.

        Args:
            params (dict[str, Any] | None): Additional behavioral parameters from
                the subclass (e.g., system_prompt_template, threshold). Merged into
                the base params.
            score_aggregator (str | None): Name of the aggregator function that
                combines sub-scores, promoted to ``ScorerIdentifier.score_aggregator``.
            prompt_target (ComponentIdentifier | None): The target an LLM-backed
                scorer calls, promoted to ``ScorerIdentifier.prompt_target``.
            sub_scorers (list[ComponentIdentifier] | None): Nested scorers a
                composite wraps, promoted to ``ScorerIdentifier.sub_scorers``.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return ScorerIdentifier.of(
            self,
            params=params,
            scorer_type=self.scorer_type,
            score_aggregator=score_aggregator,
            prompt_target=prompt_target,
            sub_scorers=sub_scorers,
        )

    async def score_async(
        self,
        message: Message | None = None,
        *,
        scorable: Scorable | None = None,
        expectation: ScoringExpectation | None = None,
        objective: str | None = None,
        role_filter: ChatMessageRole | None = None,
        skip_on_error_result: bool = False,
        infer_objective_from_request: bool = False,
    ) -> list[Score]:
        """
        Score a scorable against an expectation, persist the results, and return them.

        A scorer takes two inputs: the ``scorable`` says what to look at, and the
        ``expectation`` says what to look for. Keeping them separate is what lets an attack
        forward a question it does not understand to a scorer that does.


        Args:
            message (Message | None): Deprecated. Pass
                ``scorable=MessageScorable.from_message(...)`` instead.
            scorable (Scorable | None): What to look at.
            expectation (ScoringExpectation | None): What to look for. Defaults to None.
            objective (str | None): Deprecated. Pass
                ``expectation=ScoringExpectation(objective=...)`` instead.
            role_filter (ChatMessageRole | None): Deprecated. Set ``role_filter`` on the
                message scorable instead.
            skip_on_error_result (bool): Deprecated. Set ``skip_on_error_result`` on the
                message scorable instead.
            infer_objective_from_request (bool): Deprecated. Resolve the objective at the
                call site and pass it on the expectation instead.

        Returns:
            list[Score]: A list of Score objects representing the results.

        Raises:
            ValueError: If the scorable inputs are missing, duplicated, or combined with
                parameters that do not apply to them.
            TypeError: If this scorer does not support this kind of scorable.
        """
        resolved_scorable, resolved_expectation = self._consolidate_legacy_inputs(
            message=message,
            scorable=scorable,
            expectation=expectation,
            objective=objective,
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
            infer_objective_from_request=infer_objective_from_request,
        )

        scores = await self._score_scorable_async(
            scorable=resolved_scorable,
            expectation=resolved_expectation,
            infer_objective_from_request=infer_objective_from_request,
        )

        if not scores:
            return []

        self.validate_return_scores(scores=scores)
        self._memory.add_scores_to_memory(scores=scores)

        return scores

    def _consolidate_legacy_inputs(
        self,
        *,
        message: Message | None,
        scorable: Scorable | None,
        expectation: ScoringExpectation | None,
        objective: str | None,
        role_filter: ChatMessageRole | None,
        skip_on_error_result: bool,
        infer_objective_from_request: bool,
    ) -> tuple[Scorable, ScoringExpectation | None]:
        """
        Map the deprecated message-shaped parameters onto a scorable and an expectation.

        Returns:
            tuple[Scorable, ScoringExpectation | None]: The resolved scoring inputs.

        Raises:
            ValueError: If the inputs are missing, duplicated, or combined with parameters
                that do not apply to them.
        """
        if message is not None and scorable is not None:
            raise ValueError("Pass either 'message' or 'scorable', not both.")
        if message is None and scorable is None:
            raise ValueError("Either 'message' or 'scorable' must be provided.")
        if objective is not None and expectation is not None:
            raise ValueError("Pass either 'objective' or 'expectation', not both.")
        if scorable is not None and (role_filter is not None or skip_on_error_result):
            raise ValueError(
                "'role_filter' and 'skip_on_error_result' are fields on the message scorable. "
                "Set them on the scorable instead of passing them to score_async."
            )

        uses_legacy_parameters = (
            message is not None
            or objective is not None
            or role_filter is not None
            or skip_on_error_result
            or infer_objective_from_request
        )
        if uses_legacy_parameters:
            print_deprecation_message(
                old_item="Scorer.score_async(message=..., objective=..., role_filter=..., "
                "skip_on_error_result=..., infer_objective_from_request=...)",
                new_item="Scorer.score_async(scorable=..., expectation=...)",
                removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
            )

        if scorable is None:
            scorable = self._scorable_for_message(
                cast("Message", message),
                role_filter=role_filter,
                skip_on_error_result=skip_on_error_result,
            )

        if objective is not None:
            expectation = ScoringExpectation(objective=objective)

        return scorable, expectation

    @staticmethod
    def _scorable_for_message(
        message: Message,
        *,
        role_filter: ChatMessageRole | None = None,
        skip_on_error_result: bool = False,
    ) -> Scorable:
        """
        Describe an in-hand message as the scorable that names it.

        The caller asked about this message, not about everything stored alongside it, so this
        maps to the exact message rather than widening to its conversation. A message built by
        hand has nothing in memory to name, so a single unpersisted piece becomes loose content.

        This bridges the two places that still hold a ``Message`` rather than the scorable it
        came from: the deprecated ``message`` parameter, and the true/false wrappers that
        forward to an inner scorer. Both go away in phase 2, and this goes with them.

        Args:
            message (Message): The message to describe.
            role_filter (ChatMessageRole | None): Role the piece must have to be scored.
            skip_on_error_result (bool): Whether to skip the message when it holds an error.

        Returns:
            Scorable: A ``ContentScorable`` for a single unpersisted piece, else a
            ``MessageScorable`` naming the pieces.
        """
        pieces = message.message_pieces
        if len(pieces) == 1 and pieces[0].not_in_memory:
            return ContentScorable.from_message(message)

        return MessageScorable.from_message(
            message,
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
        )

    @abstractmethod
    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        infer_objective_from_request: bool = False,
    ) -> list[Score]:
        """
        Score a scorable this scorer supports.

        Subclasses implement this for the scorable kinds they handle and raise
        ``TypeError`` for the rest. ``MessageScorer`` handles the message-shaped kinds.

        An implementation returns an empty list when a filter skipped the scorable without
        scoring it. An empty list bypasses ``validate_return_scores`` and persistence.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What to look for.
            infer_objective_from_request (bool): Deprecated; resolve the objective from the
                stored conversation when the expectation carries none.

        Raises:
            TypeError: If the scorer does not support this kind of scorable.
        """
        raise NotImplementedError

    @staticmethod
    def _create_scoring_text_piece(
        *,
        piece: MessagePiece,
        content: str,
        response_error: PromptResponseError,
    ) -> MessagePiece:
        """
        Create a text-typed scoring view that retains the persisted piece identity.

        Returns:
            A text piece for scorer consumption.
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
        Create a text-typed copy of a blocked MessagePiece using its partial content.

        The substitute preserves the original piece's id (so scores link back correctly),
        sets converted_value to the partial content with converted_value_data_type="text",
        and sets response_error="none" so scorer short-circuits (e.g., refusal scorer's
        blocked check) do not fire.

        Args:
            piece: A blocked MessagePiece with prompt_metadata["partial_content"].

        Returns:
            MessagePiece with text content, or None if partial content is empty.
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
        Create a blocked text scoring view for an SDK-provided structured refusal.

        Returns:
            A text scoring view, or ``None`` when the piece is not a structured refusal.
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
        Expose structured refusal explanations as text while preserving blocked semantics.

        Returns:
            A scoring message with structured refusals substituted, or the original message.
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
        Create a copy of the message where blocked pieces with partial content are substituted.

        Each blocked piece that has prompt_metadata["partial_content"] is replaced with a
        text-typed copy (response_error="none", converted_value=partial_content). Non-blocked
        pieces and blocked pieces without partial content are kept as-is.

        Args:
            message: The original message potentially containing blocked pieces.

        Returns:
            A new Message with substituted pieces, or the original if no substitution was needed.
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

        if not substituted:
            return message

        return Message(message_pieces=new_pieces)

    @abstractmethod
    def _build_fallback_score(
        self, *, message: Message, objective: str | None, scorer_response_blocked: bool = False
    ) -> list[Score]:
        """
        Return neutral fallback ``Score`` objects when ``_score_async`` produced no scores.

        Called from ``score_async`` after ``_score_async`` returns an empty list and the
        message still has pieces (e.g. the response was blocked, had an error, or no piece
        matched the validator). Every ``Scorer`` subclass MUST implement this so that a
        consistent "attack did not succeed" value is always returned and downstream
        consumers do not need to special-case error handling.

        Most scorers return a single-element list (e.g. ``FloatScaleScorer`` returns
        ``[Score(0.0)]`` and ``TrueFalseScorer`` returns ``[Score(False)]``). Scorers
        whose normal output shape is multiple scores per message (e.g. one per category)
        should return one fallback score per logical output slot so downstream consumers
        iterating by shape continue to work on blocked / error input.

        Args:
            message (Message): The (possibly substituted) message that was scored.
            objective (str | None): The objective associated with this scoring call.
            scorer_response_blocked (bool): When True, the fallback was triggered because the
                scorer's *own* LLM response was blocked by content filtering (not the
                target-under-test). Subclasses should reflect this in the rationale.

        Returns:
            list[Score]: One or more fallback scores. Must not be empty.
        """
        ...

    @abstractmethod
    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate the scores returned by the scorer. Because some scorers may require
        specific Score types or values.

        Args:
            scores (list[Score]): The scores to be validated.
        """
        raise NotImplementedError

    async def evaluate_async(
        self,
        file_mapping: ScorerEvalDatasetFiles | None = None,
        *,
        num_scorer_trials: int = 3,
        update_registry_behavior: RegistryUpdateBehavior | None = None,
        max_concurrency: int = 10,
    ) -> ScorerMetrics | None:
        """
        Evaluate this scorer against human-labeled datasets.

        Uses file mapping to determine which datasets to evaluate and how to aggregate results.

        Args:
            file_mapping: Optional ScorerEvalDatasetFiles configuration.
                If not provided, uses the scorer's configured evaluation_file_mapping.
                Maps input file patterns to an output result file.
            num_scorer_trials: Number of times to score each response (for measuring variance). Defaults to 3.
            update_registry_behavior: Controls how existing registry entries are handled.
                - SKIP_IF_EXISTS (default): Check registry for existing results. If found, return cached metrics.
                - ALWAYS_UPDATE: Always run evaluation and overwrite any existing registry entry.
                - NEVER_UPDATE: Always run evaluation but never write to registry (for debugging).
                Defaults to RegistryUpdateBehavior.SKIP_IF_EXISTS.
            max_concurrency: Maximum number of concurrent scoring requests. Defaults to 10.

        Returns:
            ScorerMetrics: The evaluation metrics, or None if no datasets found.

        Raises:
            ValueError: If no file_mapping is provided and no evaluation_file_mapping is configured.
        """
        from pyrit.score import ScorerEvaluator
        from pyrit.score.scorer_evaluation.metrics_type import RegistryUpdateBehavior

        # Handle default for update_registry_behavior (can't use enum in signature due to forward ref)
        if update_registry_behavior is None:
            update_registry_behavior = RegistryUpdateBehavior.SKIP_IF_EXISTS

        # Use provided mapping or fall back to scorer's configured mapping
        mapping = file_mapping if file_mapping is not None else self.evaluation_file_mapping

        if mapping is None:
            raise ValueError(
                f"No file_mapping provided and no evaluation_file_mapping configured for {self.__class__.__name__}. "
                "Either provide file_mapping parameter or configure evaluation_file_mapping on the scorer class."
            )

        scorer_evaluator = ScorerEvaluator.from_scorer(self)
        return await scorer_evaluator.run_evaluation_async(
            dataset_files=mapping,
            num_scorer_trials=num_scorer_trials,
            update_registry_behavior=update_registry_behavior,
            max_concurrency=max_concurrency,
        )

    @abstractmethod
    def get_scorer_metrics(self) -> ScorerMetrics | None:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Looks up metrics by this scorer's identity hash in the JSONL result file.
        The result file may contain entries for multiple scorer configurations.

        Subclasses must implement this to return the appropriate metrics type:
        - TrueFalseScorer subclasses should return ObjectiveScorerMetrics
        - FloatScaleScorer subclasses should return HarmScorerMetrics

        Returns:
            ScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        raise NotImplementedError("Subclasses must implement get_scorer_metrics")

    async def score_text_async(self, text: str, *, objective: str | None = None) -> list[Score]:
        """
        Scores the given text based on the task using the chat target.

        Args:
            text (str): The text to be scored.
            objective (str | None): The task based on which the text should be scored

        Returns:
            list[Score]: A list of Score objects representing the results.
        """
        return await self.score_async(
            scorable=ContentScorable(value=text),
            expectation=ScoringExpectation(objective=objective) if objective is not None else None,
        )

    async def score_image_async(self, image_path: str, *, objective: str | None = None) -> list[Score]:
        """
        Score the given image using the chat target.

        Args:
            image_path (str): The path to the image file to be scored.
            objective (str | None): The objective based on which the image should be scored. Defaults to None.

        Returns:
            list[Score]: A list of Score objects representing the results.
        """
        return await self.score_async(
            scorable=ContentScorable(value=image_path, data_type="image_path"),
            expectation=ScoringExpectation(objective=objective) if objective is not None else None,
        )

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
        Score multiple prompts in batches using the provided objectives.

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
        if objectives is None:
            objectives = [""] * len(messages)
        elif len(objectives) != len(messages):
            raise ValueError("The number of objectives must match the number of messages.")

        if len(messages) == 0:
            return []

        scorables = [
            MessageScorable.from_message(message, role_filter=role_filter, skip_on_error_result=skip_on_error_result)
            for message in messages
        ]
        expectations = [ScoringExpectation(objective=objective) for objective in objectives]

        # Some scorers do not have an associated prompt target; batch helper validates RPM only when present
        prompt_target = getattr(self, "_prompt_target", None)
        results = await batch_task_async(
            task_func=self.score_async,
            task_arguments=["scorable", "expectation"],
            prompt_target=cast("PromptTarget", prompt_target),
            batch_size=batch_size,
            items_to_batch=[scorables, expectations],
            infer_objective_from_request=infer_objective_from_request,
        )

        # results is a list[list[Score]] and needs to be flattened
        return [score for sublist in results for score in sublist]

    async def score_image_batch_async(
        self, *, image_paths: Sequence[str], objectives: Sequence[str] | None = None, batch_size: int = 10
    ) -> list[Score]:
        """
        Score a batch of images asynchronously.

        Args:
            image_paths (Sequence[str]): Sequence of paths to image files to be scored.
            objectives (Sequence[str] | None): Optional sequence of objectives corresponding to each image.
                If provided, must match the length of image_paths. Defaults to None.
            batch_size (int): Maximum number of images to score concurrently. Defaults to 10.

        Returns:
            list[Score]: A list of Score objects representing the scoring results for all images.

        Raises:
            ValueError: If the number of objectives does not match the number of image_paths.
        """
        if objectives is not None and len(objectives) != len(image_paths):
            raise ValueError("The number of objectives must match the number of image_paths.")

        if len(image_paths) == 0:
            return []

        prompt_target = getattr(self, "_prompt_target", None)
        results = await batch_task_async(
            task_func=self.score_image_async,
            task_arguments=["image_path", "objective"] if objectives is not None else ["image_path"],
            prompt_target=prompt_target,
            batch_size=batch_size,
            items_to_batch=[image_paths, objectives] if objectives is not None else [image_paths],
        )

        return [score for sublist in results for score in sublist]

    def scale_value_float(self, value: float, min_value: float, max_value: float) -> float:
        """
        Scales a value from 0 to 1 based on the given min and max values. E.g. 3 stars out of 5 stars would be .5.

        Args:
            value (float): The value to be scaled.
            min_value (float): The minimum value of the range.
            max_value (float): The maximum value of the range.

        Returns:
            float: The scaled value.
        """
        if max_value == min_value:
            return 0.0

        return (value - min_value) / (max_value - min_value)

    def _extract_objective_from_response(self, response: Message) -> str:
        """
        Read the objective from the turn before an assistant response.

        Deprecated: use ``pyrit.score.message_scorer.extract_objective_from_previous_turn``.

        Args:
            response (Message): The response to extract the objective from.

        Returns:
            str: The objective extracted from the response, or empty string if not found.
        """
        from pyrit.score.message_scorer import extract_objective_from_previous_turn

        print_deprecation_message(
            old_item="Scorer._extract_objective_from_response",
            new_item="pyrit.score.message_scorer.extract_objective_from_previous_turn",
            removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
        )
        return extract_objective_from_previous_turn(message=response, memory=self._memory)

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
                aux_scores = await Scorer.score_response_multiple_scorers_async(
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
            aux_task = Scorer.score_response_multiple_scorers_async(
                response=response,
                scorers=auxiliary_scorers,
                role_filter=role_filter,
                objective=objective,
                skip_on_error_result=skip_on_error_result,
            )
            obj_task = objective_scorer.score_async(
                scorable=MessageScorable.from_message(
                    response, role_filter=role_filter, skip_on_error_result=skip_on_error_result
                ),
                expectation=ScoringExpectation(objective=objective) if objective is not None else None,
            )
            aux_scores, obj_scores = await asyncio.gather(aux_task, obj_task)
            result["auxiliary_scores"] = aux_scores
            result["objective_scores"] = obj_scores
        else:
            obj_scores = await objective_scorer.score_async(
                scorable=MessageScorable.from_message(
                    response, role_filter=role_filter, skip_on_error_result=skip_on_error_result
                ),
                expectation=ScoringExpectation(objective=objective) if objective is not None else None,
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

        # Create all scoring tasks, note TEMPORARY fix to prevent multi-piece responses from breaking scoring logic
        scorable = MessageScorable.from_message(
            response, role_filter=role_filter, skip_on_error_result=skip_on_error_result
        )
        expectation = ScoringExpectation(objective=objective) if objective is not None else None
        tasks = [scorer.score_async(scorable=scorable, expectation=expectation) for scorer in scorers]

        if not tasks:
            return []

        # Execute all tasks in parallel
        score_lists = await asyncio.gather(*tasks)

        # Flatten the list of lists into a single list
        return [score for scores in score_lists for score in scores]
