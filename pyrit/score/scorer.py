# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import asyncio
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast, final, overload

from pyrit.common.deprecation import print_deprecation_message
from pyrit.exceptions import PyritException, execution_context, get_execution_context
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    ComponentIdentifier,
    Condition,
    ContentScorable,
    Identifiable,
    MatchesObjective,
    Message,
    MessageScorable,
    Observation,
    PromptDataType,
    Scorable,
    ScorableUnion,
    Score,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    ScoreStatus,
    ScoreType,
    ScoringExpectation,
)
from pyrit.prompt_target.batch_helper import batch_task_async
from pyrit.prompt_target.common.target_requirements import TargetRequirements
from pyrit.score.observation.execution import (
    NonReplayableObservationError,
    _observation_collection,
    _ObservationEvidence,
    _ObservationEvidenceResolver,
    _scoring_expectation_context,
    _scoring_message_context,
    _scoring_scorable_context,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Awaitable, Callable, Sequence

    from pyrit.exceptions import ComponentRole
    from pyrit.models import ChatMessageRole
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer_evaluation.metrics_type import RegistryUpdateBehavior
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

logger = logging.getLogger(__name__)

#: Release in which the message-shaped ``score_async`` parameters are removed.
LEGACY_SCORE_ASYNC_REMOVED_IN = "2.0.0"
ConditionT = TypeVar("ConditionT", bound=Condition)


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
        old_item=f"{type(self).__name__}._score_async on a scorer without a MessageScorer base",
        new_item="pyrit.score.MessageScorer (or MessageTrueFalseScorer / MessageFloatScaleScorer) as the base class",
        removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
    )
    self._validate_legacy_hook_expectation(
        expectation=expectation, replacement="a MessageScorer expectation-aware hook"
    )
    resolver = getattr(self, "_message_resolver", None) or MessageScorableResolver()
    message = resolver.resolve(scorable=scorable, memory=self._memory)
    legacy_score_async = self._score_async  # type: ignore[ty:unresolved-attribute]
    with _scoring_message_context(message):
        scores: list[Score] = await legacy_score_async(
            message,
            objective=expectation.objective if expectation else None,
        )
    return scores


def _adapt_legacy_message_scorer(cls: type) -> None:
    """
    Give a pre-2.0 direct ``Scorer`` subclass an implementation of the scorable contract.

    Subclasses of ``MessageScorer`` already inherit one, so they are left alone. A class that
    predates the split implements ``_score_async`` or only ``_score_piece_async`` instead, and
    would otherwise fail to instantiate because ``_score_scorable_async`` is abstract.
    ``ABCMeta`` recomputes ``__abstractmethods__`` after ``__init_subclass__``, so assigning
    here is enough.
    """
    for base in cls.__mro__:
        if base is Scorer:
            break
        if "_score_scorable_async" in base.__dict__:
            return

    def defines(name: str) -> bool:
        return any(name in base.__dict__ for base in cls.__mro__)

    if not defines("_score_async"):
        if not defines("_score_piece_async"):
            return
        # A leaf that only fans in at the piece level used to inherit the message pipeline
        # from its family base. That pipeline now lives on the message-capable family,
        # so lend the matching family implementation here.
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer, MessageFloatScaleScorer
        from pyrit.score.message_scorer import MessageScorer
        from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer, TrueFalseScorer

        if issubclass(cls, TrueFalseScorer):
            score_async = MessageTrueFalseScorer._score_async
        elif issubclass(cls, FloatScaleScorer):
            score_async = MessageFloatScaleScorer._score_async
        else:
            score_async = MessageScorer._score_async

        cls._score_async = score_async  # type: ignore[ty:invalid-assignment, ty:unresolved-attribute]
        if not defines("_score_piece_with_expectation_async"):
            cls._score_piece_with_expectation_async = MessageScorer._score_piece_with_expectation_async  # type: ignore[ty:invalid-assignment, ty:unresolved-attribute]
        if not defines("_get_supported_pieces"):
            cls._get_supported_pieces = MessageScorer._get_supported_pieces  # type: ignore[ty:invalid-assignment, ty:unresolved-attribute]

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

    #: The single required criterion for a leaf, or None for constructor-configured scoring.
    #: Wrappers expose their children instead of declaring their own criterion.
    CONDITION_TYPE: ClassVar[type[Condition] | None] = None

    _identifier: ComponentIdentifier | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Enforce keyword-only constructors and singular leaf condition declarations.

        See ``.github/instructions/scorers.instructions.md`` for the contract.

        Raises:
            TypeError: If a subclass declares invalid or independently derived condition capabilities.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="Scorer")
        if any(
            name in cls.__dict__
            for name in ("MATCHED_CONDITIONS", "REQUIRED_CONDITIONS", "matched_conditions", "required_conditions")
        ):
            raise TypeError(f"{cls.__name__} must declare one CONDITION_TYPE, not condition sets.")
        if any(name in cls.__dict__ for name in ("condition_type", "get_condition_types")):
            raise TypeError(f"{cls.__name__} cannot override derived condition capabilities.")
        cls._check_condition_type(cls.CONDITION_TYPE)
        if cls.CONDITION_TYPE is not None and any(
            "_get_child_scorers" in base.__dict__ for base in cls.__mro__ if base is not Scorer
        ):
            raise TypeError(f"{cls.__name__} wraps scorers and cannot declare its own CONDITION_TYPE.")
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

    @property
    def supported_data_types(self) -> frozenset[PromptDataType] | None:
        """The declared readable types, or None when compatibility cannot be determined."""
        return None

    @property
    def skips_unsupported_data_types(self) -> bool:
        """Whether wholly unsupported evidence safely yields no score."""
        return False

    @property
    def allows_unsupported_pieces(self) -> bool:
        """Whether unsupported pieces can accompany readable evidence."""
        return False

    @property
    @final
    def condition_type(self) -> type[Condition] | None:
        """
        The leaf's required condition type, or None for a wrapper or configured criterion.

        Raises:
            TypeError: If a wrapper declares its own condition type.
        """
        condition_type = self._get_condition_type()
        self._check_condition_type(condition_type)
        if condition_type is not None and self._get_child_scorers():
            raise TypeError(f"{type(self).__name__} wraps scorers and cannot declare its own condition type.")
        return condition_type

    def _get_condition_type(self) -> type[Condition] | None:
        """Return the leaf declaration, or its instance-specific equivalent."""
        return self.CONDITION_TYPE

    @staticmethod
    def _check_condition_type(value: object) -> None:
        """
        Enforce a singular, concrete declaration.

        Raises:
            TypeError: If the declaration is not one specific Condition type or None.
        """
        if value is not None and (
            not isinstance(value, type) or not issubclass(value, Condition) or value is Condition
        ):
            raise TypeError("CONDITION_TYPE must be one specific Condition subclass or None.")

    @final
    def get_condition_types(self) -> frozenset[type[Condition]]:
        """
        Derive condition coverage from the scorer tree, not a separate declaration.

        Returns:
            frozenset[type[Condition]]: The union of the leaves' condition types.
        """
        condition_type = self.condition_type
        children = self._get_child_scorers()
        if children:
            return frozenset(condition_type for child in children for condition_type in child.get_condition_types())
        return frozenset({condition_type}) if condition_type is not None else frozenset[type[Condition]]()

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        """Return the wrapped scorers, or an empty tuple for a leaf."""
        return ()

    def _get_required_condition(
        self, *, expectation: ScoringExpectation | None, condition_type: type[ConditionT]
    ) -> ConditionT:
        """
        Retrieve exactly one condition of the requested type.

        Returns:
            ConditionT: The criterion used by this leaf.

        Raises:
            TypeError: If the expectation is invalid or the requested type is not this leaf's criterion.
            ValueError: If the condition is missing or duplicated.
        """
        ScoringExpectation.validate_type(expectation)
        if condition_type is not self.condition_type:
            raise TypeError(f"{type(self).__name__} can only retrieve its declared condition type.")
        matches = [
            condition
            for condition in (expectation.conditions if expectation else ())
            if isinstance(condition, condition_type)
        ]
        if not matches:
            raise ValueError(
                f"{type(self).__name__} requires one {condition_type.__name__} condition. "
                f"Supply it in ScoringExpectation.conditions; objective text alone does not supply this criterion."
            )
        if len(matches) > 1:
            raise ValueError(
                f"{type(self).__name__} received {len(matches)} {condition_type.__name__} conditions. "
                "A leaf scorer requires exactly one condition of its declared type."
            )
        return matches[0]

    def _validate_legacy_hook_expectation(self, *, expectation: ScoringExpectation | None, replacement: str) -> None:
        """
        Reject criteria a legacy hook claims to match but cannot receive.

        Raises:
            TypeError: If the hook cannot receive a matched non-objective condition.
        """
        if expectation is not None and any(
            not isinstance(condition, MatchesObjective) for condition in expectation.conditions
        ):
            raise TypeError(
                f"{type(self).__name__} must accept and forward expectation for its matched typed conditions. "
                f"Implement {replacement}."
            )

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
            ScoreType: "true_false" for TrueFalseScorerBase subclasses,
                      "float_scale" for FloatScaleScorerBase subclasses,
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
        children: dict[str, ComponentIdentifier] | None = None,
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
            children (dict[str, ComponentIdentifier] | None): Additional component
                dependencies not covered by the promoted child slots.

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
            children=children,
        )

    async def score_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None = None,
    ) -> list[Score]:
        """
        Score a scorable against an expectation, persist the results, and return them.

        Every supplied condition must be supported by this scorer tree.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What to look for. Defaults to None.

        Returns:
            list[Score]: Zero or more persisted scores. An empty list means that this scorer
                does not apply to the evidence. A non-empty list contains completed or
                undetermined verdicts.

        Raises:
            TypeError: If this scorer does not support this kind of scorable.
            ValueError: If conditions are unsupported, missing, or duplicated.
            PyritException: If scoring raises a PyRIT exception (re-raised with enhanced context).
            RuntimeError: If scoring raises a non-PyRIT exception (wrapped with scorer context).
        """
        expectation = self.prepare_expectation(expectation=expectation)
        with _observation_collection() as collector:
            try:
                with _scoring_scorable_context(scorable), _scoring_expectation_context(expectation):
                    scores = await self._score_scorable_async(scorable=scorable, expectation=expectation)
            except PyritException as e:
                e.message = f"Error in scorer {self.__class__.__name__}: {e.message}"
                e.args = (f"Status Code: {e.status_code}, Message: {e.message}",)
                raise
            except Exception as e:
                raise RuntimeError(f"Error in scorer {self.__class__.__name__}: {str(e)}") from e

            self._stamp_scored_expectation(scores=scores, expectation=expectation)
            observations = collector.referenced_by(scores=scores)
            return await self._validate_and_persist_scores_async(
                scores=scores,
                observations=observations,
            )

    @staticmethod
    def _stamp_scored_expectation(*, scores: list[Score], expectation: ScoringExpectation | None) -> None:
        """
        Record on each score the expectation it was judged against.

        The scorer, not the score, knows the expectation it used, so it stamps the finished
        scores before they persist. ``objective`` is the derived view, so it is refreshed to
        match. A ``None`` expectation leaves the scores unchanged.

        Args:
            scores (list[Score]): The scores to stamp.
            expectation (ScoringExpectation | None): The expectation the scorer used.
        """
        if expectation is None:
            return
        for score in scores:
            object.__setattr__(score, "scored_expectation", expectation)
            object.__setattr__(score, "objective", expectation.objective)

    @staticmethod
    async def score_with_scorers_async(
        *,
        scorable: Scorable,
        scorers: Sequence[Scorer],
        expectation: ScoringExpectation | None = None,
        scorer_roles: Sequence[ComponentRole] | None = None,
    ) -> list[list[Score]]:
        """
        Score evidence concurrently with independently persisted scoring roots.

        Each root receives the original scorable and complete expectation through its public
        ``score_async`` method. Each root is validated independently before any scorer runs.
        This does not apply message-specific evidence policies.

        Args:
            scorable (Scorable): The evidence each scorer acquires.
            scorers (Sequence[Scorer]): The ordered scoring roots.
            expectation (ScoringExpectation | None): The complete scoring question. Defaults to None.
            scorer_roles (Sequence[ComponentRole] | None): One execution role per scorer, in
                input order. Omission preserves the caller's execution context.

        Returns:
            list[list[Score]]: One score list per root, in input order, including empty lists.

        Raises:
            TypeError: If the expectation is not a ``ScoringExpectation``.
            ValueError: If conditions are unmatched, ambiguous, or missing required criteria,
                or the role count differs from the scorer count.
        """
        roots = tuple(scorers)
        roles = tuple(scorer_roles) if scorer_roles is not None else (None,) * len(roots)
        if len(roles) != len(roots):
            raise ValueError("scorer_roles must have one entry per scorer.")
        Scorer.validate_expectation_for_scorers(scorers=roots, expectation=expectation)
        return await asyncio.gather(
            *(
                Scorer._score_with_context_async(
                    scorer=scorer, scorable=scorable, expectation=expectation, component_role=role
                )
                for scorer, role in zip(roots, roles, strict=True)
            )
        )

    @staticmethod
    async def _score_with_context_async(
        *,
        scorer: Scorer,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        component_role: ComponentRole | None,
    ) -> list[Score]:
        """
        Call a scoring root with an optional role and its own component identifier.

        Returns:
            list[Score]: The root's persisted scores.
        """
        if component_role is None:
            return await scorer.score_async(scorable=scorable, expectation=expectation)
        parent = get_execution_context()
        with execution_context(
            component_role=component_role,
            component_identifier=scorer.get_identifier(),
            attack_strategy_name=parent.attack_strategy_name if parent else None,
            attack_identifier=parent.attack_identifier if parent else None,
            objective_target_conversation_id=parent.objective_target_conversation_id if parent else None,
            objective=parent.objective if parent else None,
        ):
            return await scorer.score_async(scorable=scorable, expectation=expectation)

    @staticmethod
    def validate_expectation_for_scorers(
        *,
        scorers: Sequence[Scorer],
        expectation: ScoringExpectation | None,
    ) -> None:
        """
        Validate the complete expectation independently against each scoring root.

        Args:
            scorers (Sequence[Scorer]): The independent scoring roots.
            expectation (ScoringExpectation | None): The complete scoring question.

        Raises:
            TypeError: If the expectation is not a ``ScoringExpectation``.
            ValueError: If conditions are unmatched, ambiguous, or missing required criteria.
        """
        ScoringExpectation.validate_type(expectation)
        if not scorers and expectation is not None and expectation.conditions:
            raise ValueError("No scorer is configured to evaluate the supplied conditions.")
        for scorer in scorers:
            scorer.prepare_expectation(expectation=expectation)

    def prepare_expectation(self, *, expectation: ScoringExpectation | None) -> ScoringExpectation | None:
        """
        Resolve objective-only input and validate the complete scorer tree.

        Returns:
            ScoringExpectation | None: The effective input used for judgment and attribution.
        """
        expectation = self._normalize_expectation(expectation=expectation)
        self._validate_expectation(expectation=expectation)
        return expectation

    def _normalize_expectation(self, *, expectation: ScoringExpectation | None) -> ScoringExpectation | None:
        """
        Supply the objective criterion only for an original condition-free input.

        Returns:
            ScoringExpectation | None: The input with its objective default resolved.
        """
        ScoringExpectation.validate_type(expectation)
        if expectation is not None and not expectation.conditions and MatchesObjective in self.get_condition_types():
            return expectation.model_copy(update={"conditions": (MatchesObjective(),)})
        return expectation

    @overload
    def _select_expectation(self, *, expectation: ScoringExpectation) -> ScoringExpectation: ...

    @overload
    def _select_expectation(self, *, expectation: None) -> None: ...

    def _select_expectation(self, *, expectation: ScoringExpectation | None) -> ScoringExpectation | None:
        """
        Select this child's conditions without changing shared context or applying defaults.

        Returns:
            ScoringExpectation | None: The supported subset, preserving the input's context.
        """
        if expectation is None:
            return None
        supported = tuple(self.get_condition_types())
        conditions = tuple(condition for condition in expectation.conditions if isinstance(condition, supported))
        return (
            expectation
            if conditions == expectation.conditions
            else expectation.model_copy(update={"conditions": conditions})
        )

    def _get_child_expectations(
        self, *, expectation: ScoringExpectation | None
    ) -> tuple[tuple[Scorer, ScoringExpectation | None], ...]:
        """
        Prepare child inputs for recursive validation, including wrapper-specific context.

        Returns:
            tuple: Each child and its effective expectation.
        """
        return tuple((child, child._select_expectation(expectation=expectation)) for child in self._get_child_scorers())

    def select_expectation(self, *, expectation: ScoringExpectation | None) -> ScoringExpectation | None:
        """
        Select the part of an input that this scorer tree reads, without validation.

        Callers that decide which scorers apply to an input use this subset. The result
        can omit criteria that this tree requires.

        Returns:
            ScoringExpectation | None: The supported conditions and the shared context.
        """
        return self._select_expectation(expectation=self._normalize_expectation(expectation=expectation))

    def _validate_expectation(
        self,
        *,
        expectation: ScoringExpectation | None,
    ) -> None:
        """
        Validate coverage and every required child against its supported subset.

        Args:
            expectation (ScoringExpectation | None): The expectation to validate.

        Raises:
            TypeError: If the expectation is not a ``ScoringExpectation``.
            ValueError: If a condition is unsupported, a required condition is absent, or
                more than one condition of the same supported type is present.
        """
        ScoringExpectation.validate_type(expectation)
        condition_type = self.condition_type
        if condition_type is not None:
            self._get_required_condition(expectation=expectation, condition_type=condition_type)
        supported = tuple(self.get_condition_types())
        unmatched = [
            condition
            for condition in (expectation.conditions if expectation is not None else ())
            if not isinstance(condition, supported)
        ]
        if unmatched:
            names = ", ".join(sorted({type(condition).__name__ for condition in unmatched}))
            raise ValueError(f"{type(self).__name__} does not support condition(s): {names}.")
        for child, selected in self._get_child_expectations(expectation=expectation):
            child._validate_expectation(expectation=selected)

    async def _validate_and_persist_scores_async(
        self,
        *,
        scores: list[Score],
        observations: Sequence[Observation] = (),
    ) -> list[Score]:
        """
        Validate and persist non-empty scorer output.

        Returns:
            list[Score]: The original scores.
        """
        if not scores:
            return []

        self.validate_return_scores(scores=scores)
        requires_file_copy = any(
            isinstance(score.scorable, ContentScorable) and score.scorable.data_type in MEDIA_PATH_DATA_TYPES
            for score in scores
        )
        if requires_file_copy:
            if observations:
                await self._memory.add_scores_to_memory_async(
                    scores=scores,
                    observations=observations,
                )
            else:
                await self._memory.add_scores_to_memory_async(scores=scores)
        else:
            if observations:
                self._memory.add_scores_to_memory(
                    scores=scores,
                    observations=observations,
                )
            else:
                self._memory.add_scores_to_memory(scores=scores)
        return scores

    async def _score_nested_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score a scorable as a child in a scorer tree.

        The parent supplies only this child's supported conditions. The root scorer
        owns persistence, so this path validates input and output without persisting.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What to look for.

        Returns:
            list[Score]: The validated child scores.
        """
        self._validate_expectation(expectation=expectation)
        with _scoring_scorable_context(scorable), _scoring_expectation_context(expectation):
            scores = await self._score_scorable_async(scorable=scorable, expectation=expectation)
        self._stamp_scored_expectation(scores=scores, expectation=expectation)
        if scores:
            self.validate_return_scores(scores=scores)
        return scores

    async def score_observation_async(
        self,
        *,
        observation: Observation,
        expectation: ScoringExpectation | None = None,
    ) -> list[Score]:
        """
        Judge managed evidence again without calling its original source.

        Target-backed judgment observations require the original expectation.
        To evaluate stored attack evidence against a new expectation, use
        ``score_async`` with that evidence's scorable. This can call the scoring
        target again, but does not rerun the attack.

        Args:
            observation (Observation): The stored evidence to judge.
            expectation (ScoringExpectation | None): What to look for. Defaults to None.

        Returns:
            list[Score]: Newly persisted scores linked to the existing observation.

        Raises:
            NonReplayableObservationError: If this scorer or payload cannot replay.
        """
        expectation = self.prepare_expectation(expectation=expectation)
        stored_observations = self._memory.get_observations(observation_ids=[observation.id])
        if not stored_observations:
            raise NonReplayableObservationError(f"Observation {observation.id} is not stored in memory.")
        stored_observation = stored_observations[0]
        if stored_observation != observation:
            raise NonReplayableObservationError(
                f"Observation {observation.id} does not match its canonical stored evidence."
            )
        evidence = _ObservationEvidenceResolver(memory=self._memory).resolve(observation=observation)
        scores = self._score_observation(
            observation=observation,
            evidence=evidence,
            expectation=expectation,
        )
        for score in scores:
            score.scorable = observation.scorable
            if observation.id not in score.observation_ids:
                score.observation_ids.append(observation.id)
        self._stamp_scored_expectation(scores=scores, expectation=expectation)
        return await self._validate_and_persist_scores_async(scores=scores)

    def _score_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Judge resolved evidence without I/O.

        Raises:
            NonReplayableObservationError: Always, unless a scorer implements replay.
        """
        raise NonReplayableObservationError(f"{type(self).__name__} does not implement observation replay.")

    def _build_undetermined_score(
        self,
        *,
        rationale: str,
        description: str | None = None,
        message_piece_id: uuid.UUID | str | None = None,
        scorable: ScorableUnion | None = None,
        objective: str | None = None,
        score_category: list[str] | None = None,
        score_metadata: dict[str, str | int | float] | None = None,
    ) -> Score:
        """
        Build a score that reports no verdict was reachable.

        Args:
            rationale (str): Why no verdict was reachable.
            description (str | None): Short description of the outcome.
            message_piece_id (uuid.UUID | str | None): The message piece anchor, if any.
            scorable (Scorable | None): What the score is about, if known here.
            objective (str | None): The objective associated with this scoring call.
            score_category (list[str] | None): Categories whose verdict is undetermined.
            score_metadata (dict[str, str | int | float] | None): Context retained from the scorer.

        Returns:
            Score: An undetermined score of this scorer's type.
        """
        return Score(
            score_value=None,
            status=ScoreStatus.UNDETERMINED,
            score_value_description=description,
            score_type=self.scorer_type,
            score_category=score_category,
            score_metadata=score_metadata,
            score_rationale=rationale,
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=message_piece_id,
            scorable=scorable,
            objective=objective,
        )

    @staticmethod
    def _piece_id_from_scorable(scorable: Scorable | None) -> uuid.UUID | str | None:
        """
        Return the message piece a scorable names, so message-anchored joins keep working.

        Returns:
            uuid.UUID | str | None: The first named piece id, or None when none is named.
        """
        if isinstance(scorable, MessageScorable) and scorable.message_piece_ids:
            return scorable.message_piece_ids[0]
        return None

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

        An implementation returns ``[]`` when this scorer does not apply to the evidence.
        Otherwise, it returns one or more completed or undetermined ``Score`` results.
        An empty list bypasses ``validate_return_scores`` and persistence.

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

    @staticmethod
    async def score_response_async(
        *,
        response: Message,
        objective_scorer: Scorer | None = None,
        auxiliary_scorers: list[Scorer] | None = None,
        role_filter: ChatMessageRole | None = None,
        expectation: ScoringExpectation | None = None,
        objective: str | None = None,
        skip_on_error_result: bool | None = None,
    ) -> dict[str, list[Score]]:
        """
        Score a response through the message family. Deprecated.

        Response scoring is message-only policy, so it moved to ``MessageScorer``.
        ``role_filter`` and ``skip_on_error_result`` are deprecated compatibility filters.
        New code declares the roles it reads on the scorer.
        ``expectation`` is forwarded unchanged; ``objective`` remains a deprecated alternative
        until 2.0 and cannot be supplied alongside a non-null expectation.

        Returns:
            dict[str, list[Score]]: Auxiliary and objective scores, keyed by
                ``auxiliary_scores`` and ``objective_scores``.
        """
        from pyrit.score.message_scorer import MessageScorer

        print_deprecation_message(
            old_item="Scorer.score_response_async",
            new_item="MessageScorer.score_response_async",
            removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
        )
        return await MessageScorer.score_response_async(
            response=response,
            objective_scorer=objective_scorer,
            auxiliary_scorers=auxiliary_scorers,
            role_filter=role_filter,
            expectation=expectation,
            objective=objective,
            skip_on_error_result=skip_on_error_result,
        )

    @staticmethod
    async def score_response_multiple_scorers_async(
        *,
        response: Message,
        scorers: list[Scorer],
        role_filter: ChatMessageRole | None = None,
        expectation: ScoringExpectation | None = None,
        objective: str | None = None,
        skip_on_error_result: bool | None = None,
    ) -> list[Score]:
        """
        Score a response with several scorers through the message family. Deprecated.

        ``role_filter`` and ``skip_on_error_result`` are deprecated compatibility filters.
        ``expectation`` is forwarded unchanged; ``objective`` remains a deprecated alternative
        until 2.0 and cannot be supplied alongside a non-null expectation.

        Returns:
            list[Score]: Every score the scorers produced.
        """
        from pyrit.score.message_scorer import MessageScorer

        print_deprecation_message(
            old_item="Scorer.score_response_multiple_scorers_async",
            new_item="MessageScorer.score_response_multiple_scorers_async",
            removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
        )
        return await MessageScorer.score_response_multiple_scorers_async(
            response=response,
            scorers=scorers,
            role_filter=role_filter,
            expectation=expectation,
            objective=objective,
            skip_on_error_result=skip_on_error_result,
        )

    async def score_batch_async(
        self,
        *,
        scorables: Sequence[Scorable],
        expectations: Sequence[ScoringExpectation | None] | None = None,
        batch_size: int = 10,
        **score_async_kwargs: Any,
    ) -> list[Score]:
        """
        Score many scorables concurrently.

        Batching is concurrency and rate limiting only, so it says nothing about what kind of
        evidence the scorables hold. ``MessageScorer`` builds its message batch API on this.

        Args:
            scorables (Sequence[Scorable]): The evidence to score.
            expectations (Sequence[ScoringExpectation | None] | None): What to look for in each
                scorable. Must match the length of ``scorables``. Defaults to None, which passes
                no expectation.
            batch_size (int): The maximum number of scorables to score at once. Defaults to 10.
            **score_async_kwargs (Any): Extra keyword arguments forwarded to ``score_async``.

        Returns:
            list[Score]: A flattened list of the scores from every scorable.

        Raises:
            ValueError: If the number of expectations does not match the number of scorables.
        """
        return await self._score_batch_with_task_async(
            task_func=self.score_async,
            scorables=scorables,
            expectations=expectations,
            batch_size=batch_size,
            **score_async_kwargs,
        )

    async def _score_batch_nested_async(
        self,
        *,
        scorables: Sequence[Scorable],
        expectations: Sequence[ScoringExpectation | None] | None = None,
        batch_size: int = 10,
    ) -> list[Score]:
        """
        Score child evidence in a batch without persisting intermediate scores.

        Returns:
            list[Score]: A flattened list of validated child scores.
        """
        return await self._score_batch_with_task_async(
            task_func=self._score_nested_async,
            scorables=scorables,
            expectations=expectations,
            batch_size=batch_size,
        )

    async def _score_batch_with_task_async(
        self,
        *,
        task_func: Callable[..., Awaitable[list[Score]]],
        scorables: Sequence[Scorable],
        expectations: Sequence[ScoringExpectation | None] | None,
        batch_size: int,
        **task_kwargs: Any,
    ) -> list[Score]:
        """
        Run one public or nested scoring task over a batch.

        Returns:
            list[Score]: A flattened list of scores from every scorable.

        Raises:
            ValueError: If the number of expectations does not match the number of scorables.
        """
        if expectations is None:
            resolved_expectations: list[ScoringExpectation | None] = [None] * len(scorables)
        elif len(expectations) != len(scorables):
            raise ValueError("The number of expectations must match the number of scorables.")
        else:
            resolved_expectations = list(expectations)

        if len(scorables) == 0:
            return []

        # Some scorers do not have an associated prompt target; batch helper validates RPM only when present
        prompt_target = getattr(self, "_prompt_target", None)
        results = await batch_task_async(
            task_func=task_func,
            task_arguments=["scorable", "expectation"],
            prompt_target=cast("PromptTarget", prompt_target),
            batch_size=batch_size,
            items_to_batch=[list(scorables), resolved_expectations],
            **task_kwargs,
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
