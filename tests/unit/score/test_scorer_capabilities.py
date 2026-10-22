# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
from typing import Literal, cast
from unittest.mock import MagicMock

import pytest

import pyrit.score
from pyrit.models import (
    ComponentIdentifier,
    Condition,
    ContentScorable,
    MatchesObjective,
    Message,
    MessagePiece,
    Scorable,
    ScorableUnion,
    Score,
    ScoringExpectation,
)
from pyrit.score import (
    LegacyScorerHookError,
    ManualScorer,
    MessageScorer,
    MessageTrueFalseScorer,
    Scorer,
    ScorerCapabilities,
    ScorerPromptValidator,
    SelfAskGeneralFloatScaleScorer,
    SelfAskScaleScorer,
    SubStringScorer,
    TrueFalseInverterScorer,
    TrueFalseScorer,
)
from pyrit.score.audio_transcript_scorer import AudioTranscriptHelper
from pyrit.score.float_scale.decision_scorer import DecisionScorer
from pyrit.score.observation import execution
from pyrit.score.video_scorer import VideoHelper

pytestmark = pytest.mark.usefixtures("patch_central_database")


class _TypedCondition(Condition):
    condition_type: Literal["test_capabilities_typed"] = "test_capabilities_typed"
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


class _GenericScorer(TrueFalseScorer):
    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_type="true_false",
                scorable=cast("ScorableUnion", scorable),
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class TestCapabilities:
    def test_message_scorer_reports_validator_evidence_and_condition(self) -> None:
        validator = ScorerPromptValidator(
            supported_data_types=["text", "image_path"],
            supported_roles=["assistant"],
            required_metadata=["source"],
        )
        capabilities = _TypedMessageScorer(validator=validator).capabilities

        assert capabilities == ScorerCapabilities(
            condition_type=_TypedCondition,
            supported_data_types=frozenset({"text", "image_path"}),
            supported_roles=frozenset({"assistant"}),
            required_metadata=frozenset({"source"}),
        )

    def test_capabilities_are_frozen(self) -> None:
        capabilities = _TypedMessageScorer().capabilities
        with pytest.raises(dataclasses.FrozenInstanceError):
            capabilities.condition_type = None  # type: ignore[misc]

    def test_validator_defaults_exclude_simulated_roles(self) -> None:
        capabilities = _TypedMessageScorer().capabilities

        assert capabilities.supported_roles is not None
        assert "assistant" in capabilities.supported_roles
        assert "simulated_assistant" not in capabilities.supported_roles
        assert capabilities.required_metadata == frozenset()

    def test_class_condition_takes_precedence_over_validator_flag(self) -> None:
        scorer = _TypedMessageScorer(validator=ScorerPromptValidator(is_objective_required=True))

        assert scorer.capabilities.condition_type is _TypedCondition

    @pytest.mark.parametrize(("required", "expected"), [(True, MatchesObjective), (False, None)])
    def test_undeclared_scorer_infers_condition_from_validator(
        self, required: bool, expected: type[Condition] | None
    ) -> None:
        scorer = _UndeclaredMessageScorer(validator=ScorerPromptValidator(is_objective_required=required))

        assert scorer.capabilities.condition_type is expected

    def test_scorer_without_message_evidence_reports_none(self) -> None:
        capabilities = ManualScorer(value=True, rationale="checked", user_identifier="reviewer").capabilities

        assert capabilities == ScorerCapabilities()
        assert capabilities.supported_data_types is None
        assert capabilities.supported_roles is None

    def test_wrapper_reports_no_condition_of_its_own(self) -> None:
        wrapper = TrueFalseInverterScorer(scorer=_TypedMessageScorer())

        assert wrapper.capabilities.condition_type is None
        assert wrapper.get_condition_types() == frozenset({_TypedCondition})

    def test_capabilities_cannot_be_overridden(self) -> None:
        with pytest.raises(TypeError, match="cannot override derived condition capabilities"):
            type("InvalidScorer", (_TypedMessageScorer,), {"capabilities": ScorerCapabilities()})

    def test_evidence_hook_can_be_overridden_but_condition_stays_derived(self) -> None:
        class CustomEvidenceScorer(_GenericScorer):
            CONDITION_TYPE = _TypedCondition

            def _get_evidence_capabilities(self) -> ScorerCapabilities:
                return ScorerCapabilities(condition_type=MatchesObjective, required_metadata=frozenset({"trace"}))

        capabilities = CustomEvidenceScorer().capabilities

        assert capabilities.condition_type is _TypedCondition
        assert capabilities.required_metadata == frozenset({"trace"})


class TestObjectiveScorersDeclareMatchesObjective:
    @pytest.mark.parametrize("scorer_type", [SelfAskScaleScorer, SelfAskGeneralFloatScaleScorer, DecisionScorer])
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

        assert scorer.capabilities.condition_type is MatchesObjective
        with pytest.raises(ValueError, match="MatchesObjective requires the expectation to carry an objective"):
            scorer._validate_expectation(expectation=None)


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
        assert ObjectiveLegacyScorer().capabilities.condition_type is MatchesObjective
        assert _TypedMessageScorer().capabilities.condition_type is _TypedCondition

    def test_direct_scorer_with_typed_legacy_hook_fails_at_class_definition(self) -> None:
        with pytest.raises(LegacyScorerHookError, match="Implement _score_scorable_async"):

            class TypedDirectScorer(Scorer):
                CONDITION_TYPE = _TypedCondition

                async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
                    return []

    def test_subclass_of_adapted_legacy_scorer_cannot_add_typed_condition(self) -> None:
        class LegacyDirectScorer(Scorer):
            async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
                return []

        with pytest.raises(LegacyScorerHookError, match="TypedChild declares _TypedCondition"):
            type("TypedChild", (LegacyDirectScorer,), {"CONDITION_TYPE": _TypedCondition})


class TestModalityPreflight:
    def test_video_audio_scorer_without_message_evidence_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"supports 'audio_path' data type\. Supported types: none reported"):
            VideoHelper._validate_audio_scorer(_GenericScorer())

    def test_video_audio_scorer_without_audio_is_rejected(self) -> None:
        scorer = _TypedMessageScorer(validator=ScorerPromptValidator(supported_data_types=["text"]))
        with pytest.raises(ValueError, match=r"Supported types: \['text'\]"):
            VideoHelper._validate_audio_scorer(scorer)

    def test_video_audio_scorer_with_audio_is_accepted(self) -> None:
        scorer = _TypedMessageScorer(validator=ScorerPromptValidator(supported_data_types=["audio_path"]))
        VideoHelper._validate_audio_scorer(scorer)

    def test_audio_text_scorer_without_text_is_rejected(self) -> None:
        scorer = _TypedMessageScorer(validator=ScorerPromptValidator(supported_data_types=["image_path"]))
        with pytest.raises(ValueError, match=r"supports 'text' data type\. Supported types: \['image_path'\]"):
            AudioTranscriptHelper._validate_text_scorer(scorer)


def _in_repo_scorer_classes() -> list[type[Scorer]]:
    root = Path(pyrit.score.__file__).parent
    for path in sorted(root.rglob("*.py")):
        module = ".".join(("pyrit", "score", *path.relative_to(root).with_suffix("").parts))
        try:
            importlib.import_module(module.removesuffix(".__init__"))
        except ImportError:
            continue  # optional extras, such as torch-backed scorers
    pending: list[type[Scorer]] = [Scorer]
    found: list[type[Scorer]] = []
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.__module__.startswith("pyrit.") and cls not in found:
            found.append(cls)
    return found


class TestInRepoScorersUseTypedHook:
    def test_no_in_repo_scorer_defines_the_legacy_piece_hook(self) -> None:
        legacy = [
            cls.__qualname__
            for cls in _in_repo_scorer_classes()
            if cls is not MessageScorer and "_score_piece_async" in cls.__dict__
        ]

        assert legacy == []

    def test_external_subclass_of_migrated_scorer_must_move_its_override(self) -> None:
        class CustomSubstringScorer(SubStringScorer):
            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                raise AssertionError("must not be silently skipped")

        with pytest.raises(
            LegacyScorerHookError, match="Move the custom policy to _score_piece_with_expectation_async"
        ):
            CustomSubstringScorer(substring="x")

    async def test_legacy_call_on_migrated_scorer_names_the_typed_hook_async(self) -> None:
        piece = MessagePiece(role="assistant", original_value="a word")
        with pytest.raises(NotImplementedError, match=r"Call _score_piece_with_expectation_async\(message_piece"):
            await SubStringScorer(substring="word")._score_piece_async(piece)

    async def test_migrated_scorer_still_records_objective_async(self) -> None:
        expectation = ScoringExpectation(objective="find the word")
        [score] = await SubStringScorer(substring="word").score_async(
            scorable=ContentScorable(value="a word"), expectation=expectation
        )

        assert score.get_value() is True
        assert score.objective == "find the word"
