# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest

import pyrit.score
from pyrit.models import (
    ComponentIdentifier,
    Condition,
    ContentEntryScorable,
    ContentScorable,
    MatchesObjective,
    Message,
    MessagePiece,
    MessageScorable,
    ProgramBehaves,
    ProgramExecutionScorable,
    Score,
    ScoringExpectation,
)
from pyrit.score import (
    LegacyScorerHookError,
    MessageTrueFalseScorer,
    ProgramExecutionScorer,
    Scorer,
    ScorerPromptValidator,
    SelfAskGeneralFloatScaleScorer,
    SelfAskScaleScorer,
    TrueFalseScorer,
)
from pyrit.score.float_scale.decision_scorer import DecisionScorer
from pyrit.score.observation import execution
from pyrit.score.observation.observation_source import ObservationSource

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _concrete_in_repo_leaf_scorers() -> list[type[Scorer]]:
    root = Path(pyrit.score.__file__).parent
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        importlib.import_module(".".join(("pyrit", "score", *parts)))
    found: set[type[Scorer]] = set()
    pending: list[type[Scorer]] = [Scorer]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass not in found:
                found.add(subclass)
                pending.append(subclass)
    return sorted(
        (
            scorer_type
            for scorer_type in found
            if scorer_type.__module__.startswith("pyrit.")
            and not inspect.isabstract(scorer_type)
            and not any("_get_child_scorers" in base.__dict__ for base in scorer_type.__mro__ if base is not Scorer)
        ),
        key=lambda scorer_type: f"{scorer_type.__module__}.{scorer_type.__qualname__}",
    )


class _TypedCondition(Condition):
    condition_type: Literal["test_condition_contracts_typed"] = "test_condition_contracts_typed"
    value: str = "typed"


def _score_for(scorer: TrueFalseScorer, piece: MessagePiece, expectation: ScoringExpectation | None) -> list[Score]:
    return [
        Score(
            score_value="true",
            score_type="true_false",
            message_piece_id=piece.id,
            scored_expectation=expectation,
            scorer_class_identifier=scorer.get_identifier(),
        )
    ]


class _TypedMessageScorer(MessageTrueFalseScorer):
    CONDITION_TYPE = _TypedCondition

    def __init__(self, *, validator: ScorerPromptValidator | None = None) -> None:
        super().__init__(validator=validator or ScorerPromptValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        return _score_for(self, message_piece, expectation)


class _UndeclaredMessageScorer(_TypedMessageScorer):
    CONDITION_TYPE = None


class _LegacyPieceScorer(MessageTrueFalseScorer):
    """A pre-migration leaf: only the objective-only piece hook."""

    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return _score_for(self, message_piece, None)


class _LegacyDirectScorer(TrueFalseScorer):
    """A pre-2.0 direct family subclass that implements only the message hook."""

    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        return _score_for(self, message.get_piece(), None)


class TestObjectiveValidatorFlag:
    async def test_class_condition_takes_precedence_over_validator_flag_async(self) -> None:
        scorer = _TypedMessageScorer(validator=ScorerPromptValidator(is_objective_required=True))
        expectation = ScoringExpectation(conditions=(_TypedCondition(),))

        [score] = await scorer.score_async(scorable=ContentScorable(value="hi"), expectation=expectation)

        assert scorer.get_condition_types() == frozenset({_TypedCondition})
        assert score.get_value() is True
        assert score.scored_expectation == expectation

    @pytest.mark.parametrize(("required", "expected"), [(True, MatchesObjective), (False, None)])
    def test_undeclared_scorer_infers_condition_from_validator(
        self, required: bool, expected: type[Condition] | None
    ) -> None:
        scorer = _UndeclaredMessageScorer(validator=ScorerPromptValidator(is_objective_required=required))

        assert scorer.condition_type is expected


class TestObjectiveScorersDeclareMatchesObjective:
    @pytest.mark.parametrize("scorer_type", [SelfAskScaleScorer, DecisionScorer])
    def test_declares_matches_objective_without_validator_flag(self, scorer_type: type) -> None:
        assert scorer_type.CONDITION_TYPE is MatchesObjective
        assert scorer_type._DEFAULT_VALIDATOR.is_objective_required is False

    def test_custom_validator_cannot_drop_objective_requirement(self) -> None:
        scorer = SelfAskScaleScorer(
            chat_target=MagicMock(),
            system_prompt="Rate the response.",
            scale=MagicMock(),
            validator=ScorerPromptValidator(supported_data_types=["text", "image_path"]),
        )

        assert scorer.condition_type is MatchesObjective
        with pytest.raises(ValueError, match="MatchesObjective requires the expectation to carry an objective"):
            scorer._validate_expectation(expectation=None)

    @pytest.mark.parametrize("configured_rubric", [False, True])
    def test_general_float_scorer_keeps_configurable_objective_requirement(self, configured_rubric: bool) -> None:
        scorer = SelfAskGeneralFloatScaleScorer(
            chat_target=MagicMock(),
            system_prompt_format_string="Rate this response.",
            scale=MagicMock(),
            validator=ScorerPromptValidator(supported_data_types=["text"]) if configured_rubric else None,
        )
        if configured_rubric:
            assert scorer.get_condition_types() == frozenset()
            scorer.prepare_expectation(expectation=None)
        else:
            assert scorer.get_condition_types() == frozenset({MatchesObjective})
            with pytest.raises(ValueError, match="MatchesObjective requires"):
                scorer.prepare_expectation(expectation=None)


class TestScorableTypeDeclarations:
    def test_every_in_repo_leaf_declares_message_evidence(self) -> None:
        leaves = _concrete_in_repo_leaf_scorers()
        undeclared = [
            f"{scorer_type.__module__}.{scorer_type.__qualname__}"
            for scorer_type in leaves
            if MessageScorable not in scorer_type.SUPPORTED_SCORABLE_TYPES
        ]

        assert leaves
        assert undeclared == []

    def test_adapted_legacy_scorer_declares_message_evidence(self) -> None:
        with pytest.warns(DeprecationWarning, match="Scorer.__init__"):
            scorer = _LegacyDirectScorer()

        assert scorer.get_supported_scorable_types() == _TypedMessageScorer().get_supported_scorable_types()

    def test_program_scorer_declares_code_and_execution_evidence(self) -> None:
        scorer = ProgramExecutionScorer(source=MagicMock(spec=ObservationSource))

        assert scorer.get_supported_scorable_types() == frozenset(
            {MessageScorable, ContentScorable, ContentEntryScorable, ProgramExecutionScorable}
        )
        assert scorer.get_condition_types() == frozenset({ProgramBehaves})


class TestNoAmbientExpectation:
    def test_ambient_expectation_context_is_removed(self) -> None:
        removed = ("_CURRENT_SCORING_EXPECTATION", "_scoring_expectation_context", "_get_current_scoring_expectation")
        for name in removed:
            assert not hasattr(execution, name)


class TestLegacyHookConstruction:
    def test_error_is_a_type_error(self) -> None:
        assert issubclass(LegacyScorerHookError, TypeError)

    def test_typed_condition_with_only_legacy_piece_hook_fails_at_construction(self) -> None:
        class TypedLegacyScorer(_LegacyPieceScorer):
            CONDITION_TYPE = _TypedCondition

        with pytest.raises(
            LegacyScorerHookError,
            match=r"TypedLegacyScorer declares _TypedCondition.*Implement _score_piece_with_expectation_async",
        ):
            TypedLegacyScorer()

    def test_typed_condition_with_legacy_message_hook_fails_at_construction(self) -> None:
        class TypedMessageHookScorer(_TypedMessageScorer):
            async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
                raise AssertionError("must not run")

        with pytest.raises(LegacyScorerHookError, match=r"Implement _score_async\(\.\.\., expectation=\.\.\.\)"):
            TypedMessageHookScorer()

    @pytest.mark.parametrize("condition_type", [None, MatchesObjective])
    async def test_objective_based_legacy_hook_still_scores_async(self, condition_type: type[Condition] | None) -> None:
        legacy_type = type("ObjectiveLegacyScorer", (_LegacyPieceScorer,), {"CONDITION_TYPE": condition_type})
        expectation = ScoringExpectation(objective="say hi") if condition_type else None

        [score] = await legacy_type().score_async(scorable=ContentScorable(value="hi"), expectation=expectation)

        assert score.get_value() is True

    def test_failure_does_not_affect_sibling_classes(self) -> None:
        class TypedLegacyScorer(_LegacyPieceScorer):
            CONDITION_TYPE = _TypedCondition

        class ObjectiveLegacyScorer(_LegacyPieceScorer):
            CONDITION_TYPE = MatchesObjective

        with pytest.raises(LegacyScorerHookError):
            TypedLegacyScorer()
        assert ObjectiveLegacyScorer().condition_type is MatchesObjective
        assert _TypedMessageScorer().condition_type is _TypedCondition

    def test_direct_scorer_with_typed_legacy_hook_fails_at_construction(self) -> None:
        class TypedDirectScorer(TrueFalseScorer):
            CONDITION_TYPE = _TypedCondition

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
                raise AssertionError("must not run")

        with pytest.raises(LegacyScorerHookError, match="Implement _score_scorable_async"):
            TypedDirectScorer()

    def test_subclass_of_adapted_legacy_scorer_cannot_add_typed_condition(self) -> None:
        typed_child = type("TypedChild", (_LegacyDirectScorer,), {"CONDITION_TYPE": _TypedCondition})
        with pytest.raises(LegacyScorerHookError, match="TypedChild declares _TypedCondition"):
            typed_child()

    async def test_custom_pipeline_delegation_keeps_runtime_guard_async(self) -> None:
        class CustomPipelineScorer(_LegacyPieceScorer):
            CONDITION_TYPE = _TypedCondition

            async def _score_prepared_message_async(
                self, *, message: Message, expectation: ScoringExpectation | None
            ) -> list[Score]:
                return await super()._score_prepared_message_async(message=message, expectation=expectation)

        scorer = CustomPipelineScorer()
        with pytest.raises(LegacyScorerHookError, match="matched typed conditions"):
            await scorer.score_async(
                scorable=ContentScorable(value="hi"),
                expectation=ScoringExpectation(conditions=(_TypedCondition(),)),
            )

    async def test_custom_pipeline_can_evaluate_typed_condition_without_piece_hook_async(self) -> None:
        class CustomPipelineScorer(_LegacyPieceScorer):
            CONDITION_TYPE = _TypedCondition

            async def _score_prepared_message_async(
                self, *, message: Message, expectation: ScoringExpectation | None
            ) -> list[Score]:
                self._get_required_condition(expectation=expectation, condition_type=_TypedCondition)
                return _score_for(self, message.get_piece(), expectation)

        [score] = await CustomPipelineScorer().score_async(
            scorable=ContentScorable(value="hi"),
            expectation=ScoringExpectation(conditions=(_TypedCondition(),)),
        )
        assert score.get_value() is True
