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
    Condition,
    ContentScorable,
    Identifiable,
    Message,
    MessageScorable,
    Scorable,
    Score,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    ScoreType,
    ScoringExpectation,
)
from pyrit.prompt_target.batch_helper import batch_task_async
from pyrit.prompt_target.common.target_requirements import TargetRequirements

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


async def _legacy_score_scorable_async(
    self: Scorer,
    *,
    scorable: Scorable,
    expectation: ScoringExpectation | None,
) -> list[Score]:
    """
    Route a scorable to a pre-2.0 subclass that only implements ``_score_async``.

    Returns:
        list[Score]: The scores the legacy scorer body produced.
    """
    from pyrit.score.message_scorable_resolver import MessageScorableResolver

    print_deprecation_message(
        old_item=f"{type(self).__name__}._score_async on a direct Scorer subclass",
        new_item="pyrit.score.MessageScorer (or TrueFalseScorer / FloatScaleScorer) as the base class",
        removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
    )
    resolver = getattr(self, "_message_resolver", None) or MessageScorableResolver()
    message = resolver.resolve(scorable=scorable, memory=self._memory)
    legacy_score_async = self._score_async  # type: ignore[ty:unresolved-attribute]
    scores: list[Score] = await legacy_score_async(message, objective=expectation.objective if expectation else None)
    return scores


def _adapt_legacy_message_scorer(cls: type) -> None:
    """
    Give a pre-2.0 direct ``Scorer`` subclass an implementation of the scorable contract.

    Subclasses of ``MessageScorer`` already inherit one, so they are left alone. A class that
    predates the split implements ``_score_async`` instead, and would otherwise fail to
    instantiate because ``_score_scorable_async`` is abstract. ``ABCMeta`` recomputes
    ``__abstractmethods__`` after ``__init_subclass__``, so assigning it here is enough.
    """
    for base in cls.__mro__:
        if base is Scorer:
            break
        if "_score_scorable_async" in base.__dict__:
            return

    if not any("_score_async" in base.__dict__ for base in cls.__mro__):
        return

    cls._score_scorable_async = _legacy_score_scorable_async  # type: ignore[ty:invalid-assignment, ty:unresolved-attribute]


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

    #: Condition types this scorer can use as its criterion. Wrapping scorers report their
    #: children's union so the root can reject conditions that reach no configured leaf.
    MATCHED_CONDITIONS: ClassVar[frozenset[type[Condition]]] = frozenset()

    #: Matched condition types that this scorer cannot operate without. The empty-condition
    #: legacy path remains valid during the transition to typed expectations.
    REQUIRED_CONDITIONS: ClassVar[frozenset[type[Condition]]] = frozenset()

    _identifier: ComponentIdentifier | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Enforce the keyword-only constructor contract on subclasses.

        See ``.github/instructions/scorers.instructions.md`` for the contract.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="Scorer")
        _adapt_legacy_message_scorer(cls)

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the Scorer.

        Args:
            chat_target (PromptTarget | None): Chat target used by the scorer, if any. When
                provided, it is validated against ``TARGET_REQUIREMENTS``.
            validator (ScorerPromptValidator | None): Deprecated. Message validation moved to
                ``MessageScorer``; a value passed here is kept so pre-2.0 subclasses keep working.
        """
        if validator is not None:
            print_deprecation_message(
                old_item="Scorer.__init__(validator=...)",
                new_item="MessageScorer.__init__(validator=...)",
                removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
            )
            if getattr(self, "_validator", None) is None:
                self._validator = validator
        if chat_target is not None:
            type(self).TARGET_REQUIREMENTS.validate(target=chat_target)

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Return the condition types this scorer can use as its criterion.

        Returns:
            frozenset[type[Condition]]: The matched condition types.
        """
        return type(self).MATCHED_CONDITIONS

    def required_conditions(self) -> frozenset[type[Condition]]:
        """
        Return the matched condition types this scorer requires.

        Returns:
            frozenset[type[Condition]]: The required condition types.
        """
        return type(self).REQUIRED_CONDITIONS

    def get_chat_target(self) -> PromptTarget | None:
        """
        Return the chat target used by this scorer, or None if it doesn't use one.

        Subclasses that wrap other scorers (e.g. inverters, composites) should
        override to delegate to their inner scorer(s).

        Returns:
            PromptTarget | None: The chat target, or None if not applicable.
        """
        prompt_target: PromptTarget | None = getattr(self, "_prompt_target", None)
        return prompt_target

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
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None = None,
    ) -> list[Score]:
        """
        Score a scorable against an expectation, persist the results, and return them.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What to look for. Defaults to None.

        Returns:
            list[Score]: A list of Score objects representing the results.

        Raises:
            TypeError: If this scorer does not support this kind of scorable.
        """
        self._validate_expectation(expectation=expectation)
        scores = await self._score_scorable_async(scorable=scorable, expectation=expectation)
        return self._validate_and_persist_scores(scores=scores)

    def _validate_expectation(
        self,
        *,
        expectation: ScoringExpectation | None,
        allow_unmatched_conditions: bool = False,
    ) -> None:
        """
        Reject conditions no scorer in this tree consumes, and ambiguous routing.

        Args:
            expectation (ScoringExpectation | None): The expectation to validate.
            allow_unmatched_conditions (bool): Permit conditions addressed to sibling leaves.

        Raises:
            ValueError: If a condition is unsupported at the root, if a required condition is
                absent, or if more than one condition of the same matched type is present.
        """
        if expectation is None or not expectation.conditions:
            return

        matched = self.matched_conditions()
        unmatched = [condition for condition in expectation.conditions if not isinstance(condition, tuple(matched))]
        if unmatched and not allow_unmatched_conditions:
            names = ", ".join(sorted({type(condition).__name__ for condition in unmatched}))
            matched_names = ", ".join(sorted(cls.__name__ for cls in matched)) or "none"
            raise ValueError(
                f"{type(self).__name__} does not match the condition(s) {names}. Matched conditions: {matched_names}."
            )

        for condition_type in matched:
            matches = [condition for condition in expectation.conditions if isinstance(condition, condition_type)]
            if len(matches) > 1:
                raise ValueError(
                    f"{type(self).__name__} received {len(matches)} {condition_type.__name__} conditions. "
                    "A scorer matches at most one condition of a given type."
                )

        missing = [
            condition_type
            for condition_type in self.required_conditions()
            if not any(isinstance(condition, condition_type) for condition in expectation.conditions)
        ]
        if missing:
            names = ", ".join(sorted(condition_type.__name__ for condition_type in missing))
            raise ValueError(f"{type(self).__name__} requires the condition(s) {names}.")

    def _validate_and_persist_scores(self, *, scores: list[Score]) -> list[Score]:
        """
        Validate and persist non-empty scorer output.

        Returns:
            list[Score]: The original scores.
        """
        if not scores:
            return []

        self.validate_return_scores(scores=scores)
        self._memory.add_scores_to_memory(scores=scores)
        return scores

    @abstractmethod
    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
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

        Raises:
            TypeError: If the scorer does not support this kind of scorable.
        """
        raise NotImplementedError

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
            expectation=ScoringExpectation(objective=objective),
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
            expectation=ScoringExpectation(objective=objective),
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
            TypeError: If this is not a message scorer.
        """
        if objectives is None:
            resolved_objectives = [""] * len(messages)
        elif len(objectives) != len(messages):
            raise ValueError("The number of objectives must match the number of messages.")
        else:
            resolved_objectives = list(objectives)

        if len(messages) == 0:
            return []

        from pyrit.score.message_scorer import (
            MessageScorer,
            MessageScoringOptions,
            extract_objective_from_previous_turn,
        )

        if not isinstance(self, MessageScorer):
            raise TypeError("score_prompts_batch_async requires a MessageScorer.")
        if infer_objective_from_request:
            resolved_objectives = [
                objective or extract_objective_from_previous_turn(message=message, memory=self._memory)
                for message, objective in zip(messages, resolved_objectives, strict=True)
            ]

        scorables = [MessageScorable.from_message(message) for message in messages]
        expectations = [ScoringExpectation(objective=objective) for objective in resolved_objectives]
        message_options = MessageScoringOptions(
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
        )

        # Some scorers do not have an associated prompt target; batch helper validates RPM only when present
        prompt_target = getattr(self, "_prompt_target", None)
        results = await batch_task_async(
            task_func=self.score_async,
            task_arguments=["scorable", "expectation"],
            prompt_target=cast("PromptTarget", prompt_target),
            batch_size=batch_size,
            items_to_batch=[scorables, expectations],
            message_options=message_options,
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

        Returns:
            list[Score]: Scores from the message scorer.

        Raises:
            TypeError: If the scorer does not use the message-scoring contract.
        """
        from pyrit.score.message_scorer import MessageScorer, MessageScoringOptions

        if not isinstance(scorer, MessageScorer):
            raise TypeError("Response scoring helpers require MessageScorer instances.")
        return await scorer.score_async(
            scorable=MessageScorable.from_message(response),
            expectation=expectation,
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
            obj_task = Scorer._score_response_with_scorer_async(
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
            obj_scores = await Scorer._score_response_with_scorer_async(
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
            Scorer._score_response_with_scorer_async(
                scorer=scorer,
                response=response,
                expectation=expectation,
                role_filter=role_filter,
                skip_on_error_result=skip_on_error_result,
            )
            for scorer in scorers
        ]

        if not tasks:
            return []

        # Execute all tasks in parallel
        score_lists = await asyncio.gather(*tasks)

        # Flatten the list of lists into a single list
        return [score for scores in score_lists for score in scores]
