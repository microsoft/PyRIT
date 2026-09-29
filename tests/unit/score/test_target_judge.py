# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget, get_mock_target_identifier, store_message

from pyrit.memory import MemoryInterface
from pyrit.models import (
    ComponentIdentifier,
    Contains,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    OutputMatches,
    Scorable,
    Score,
    ScoringExpectation,
)
from pyrit.prompt_target import PromptTarget, TargetRequirements
from pyrit.score import (
    ContentClassifier,
    ContentClassifierCategory,
    FloatScaleScorer,
    InsecureCodeScorer,
    JsonSchemaResponseHandler,
    LikertScale,
    LikertScaleEntry,
    LlamaGuardScorer,
    MessageFloatScaleScorer,
    MessageScorer,
    MessageTrueFalseScorer,
    NonReplayableObservationError,
    NumericRange,
    NumericRubric,
    Scorer,
    ScorerPromptValidator,
    SelfAskCategoryScorer,
    SelfAskGeneralFloatScaleScorer,
    SelfAskGeneralTrueFalseScorer,
    SelfAskLikertScorer,
    SelfAskQuestionAnswerScorer,
    SelfAskRefusalScorer,
    SelfAskScaleScorer,
    SelfAskTrueFalseScorer,
    ShieldGemmaGuideline,
    ShieldGemmaScorer,
    TrueFalseScorer,
    WildGuardScorer,
)
from pyrit.score.observation.execution import (
    _observation_collection,
    _scoring_expectation_context,
    _scoring_message_context,
    _scoring_scorable_context,
)
from pyrit.score.observation.target_judge import JudgmentRequest, TargetJudge

pytestmark = pytest.mark.usefixtures("patch_central_database")


def test_judgment_request_does_not_capture_ambient_evidence() -> None:
    piece = MessagePiece(role="assistant", original_value="unrelated evidence")
    with (
        _scoring_scorable_context(MessageScorable(message_piece_ids=(piece.id,))),
        _scoring_message_context(piece.to_message()),
    ):
        request = JudgmentRequest(
            expectation=None,
            system_prompt=None,
            value="prepared prompt",
            data_type="text",
            scored_prompt_id=piece.id,
            scorer_identifier=get_mock_target_identifier("Caller"),
        )
    assert request.scorable is None
    assert request.scored_message_piece is None
    with (
        _scoring_scorable_context(MessageScorable(message_piece_ids=(piece.id,))),
        _scoring_message_context(piece.to_message()),
    ):
        captured = MessageScorer._capture_judgment_evidence(request)
    assert captured.scorable == MessageScorable(message_piece_ids=(piece.id,))
    assert captured.scored_message_piece is piece
    assert request.scorable is None
    assert request.scored_message_piece is None


@pytest.mark.parametrize("include_piece", [False, True])
async def test_judge_uses_explicit_evidence_after_context_change_async(
    sqlite_instance: MemoryInterface, include_piece: bool
) -> None:
    messages = [
        store_message(MessagePiece(role="assistant", original_value=value).to_message()) for value in ("A", "B")
    ]
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("ExplicitEvidenceJudge")
    target.send_prompt_async = AsyncMock(
        side_effect=lambda **kwargs: [
            MessagePiece(
                role="assistant",
                original_value='{"score_value":"true","description":"match","rationale":"ok","metadata":""}',
            ).to_message()
        ]
    )
    judge = TargetJudge(target=target, requirements=MagicMock(spec=TargetRequirements))
    requests = [
        JudgmentRequest(
            expectation=ScoringExpectation(objective=f"Judge {message.get_value()}"),
            system_prompt=None,
            value=f"Rendered prompt for {message.get_value()}",
            data_type="text",
            scored_prompt_id=message.message_pieces[0].id,
            scorer_identifier=get_mock_target_identifier("Caller"),
            scorable=MessageScorable.from_message(message),
            scored_message_piece=message.message_pieces[0] if include_piece else None,
        )
        for message in messages
    ]
    unrelated = MessagePiece(role="assistant", original_value="unrelated evidence")
    with (
        _observation_collection() as collector,
        _scoring_scorable_context(MessageScorable(message_piece_ids=(unrelated.id,))),
        _scoring_message_context(unrelated.to_message()),
        _scoring_expectation_context(ScoringExpectation(objective="unrelated criterion")),
    ):
        results = await asyncio.gather(
            *(judge.judge_async(request=request, response_handler=JsonSchemaResponseHandler()) for request in requests)
        )
        scores = [result.to_score(score_value=result.raw_score_value, score_type="true_false") for result in results]
        observations = collector.referenced_by(scores=scores)
    sqlite_instance.add_scores_to_memory(scores=scores, observations=observations)
    assert len(observations) == 2
    for request, score in zip(requests, scores, strict=True):
        assert score.scorable == request.scorable
        assert score.scored_expectation == request.expectation
        observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
        assert observation.scorable == request.scorable
        assert observation.scored_message_piece_id == request.scored_prompt_id


async def test_judge_uses_explicit_criteria_not_ambient_context_async() -> None:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("ExplicitJudge")
    target.send_prompt_async = AsyncMock(
        side_effect=lambda **kwargs: [
            MessagePiece(
                role="assistant",
                original_value='{"score_value":"true","description":"match","rationale":"ok","metadata":""}',
            ).to_message()
        ]
    )
    requirements = MagicMock(spec=TargetRequirements)
    judge = TargetJudge(target=target, requirements=requirements)
    requirements.validate.assert_called_once_with(target=target)
    expectations = [
        ScoringExpectation(objective=value, conditions=(OutputMatches(matcher=Contains(value=value)),))
        for value in ("A", "B")
    ]
    requests = [
        JudgmentRequest(
            expectation=expectation,
            system_prompt="Judge this value.",
            value="candidate",
            data_type="text",
            scored_prompt_id=uuid.uuid4(),
            scorer_identifier=get_mock_target_identifier("Caller"),
        )
        for expectation in expectations
    ]
    with _scoring_expectation_context(ScoringExpectation(objective="wrong ambient criterion")):
        scores = await asyncio.gather(
            *(judge.judge_async(request=request, response_handler=JsonSchemaResponseHandler()) for request in requests)
        )
    assert [score.scored_expectation for score in scores] == expectations
    conversations = [call.kwargs["conversation_id"] for call in target.set_system_prompt.call_args_list]
    assert len(set(conversations)) == 2


async def test_judge_rejects_missing_explicit_evidence_before_send_async() -> None:
    piece_id = uuid.uuid4()
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    judge = TargetJudge(target=target, requirements=MagicMock(spec=TargetRequirements))
    request = JudgmentRequest(
        expectation=None,
        system_prompt=None,
        value="prepared prompt",
        data_type="text",
        scored_prompt_id=piece_id,
        scorer_identifier=get_mock_target_identifier("Caller"),
        scorable=MessageScorable(message_piece_ids=(piece_id,)),
    )
    with pytest.raises(NonReplayableObservationError, match="missing"):
        await judge.judge_async(request=request, response_handler=JsonSchemaResponseHandler())
    target.send_prompt_async.assert_not_awaited()


def test_concrete_scorer_owns_target_validation() -> None:
    target = MagicMock(spec=PromptTarget)
    with patch.object(TargetRequirements, "validate", side_effect=ValueError("unsupported target")):
        with pytest.raises(ValueError, match="unsupported target"):
            SelfAskTrueFalseScorer(chat_target=target)


@pytest.fixture(
    params=[Scorer, TrueFalseScorer, FloatScaleScorer, MessageScorer, MessageTrueFalseScorer, MessageFloatScaleScorer],
    ids=lambda base: base.__name__,
)
def legacy_scorer_type(request: pytest.FixtureRequest) -> type[Scorer]:
    def build_identifier(self: Scorer) -> ComponentIdentifier:
        return self._create_identifier()

    async def score_scorable_async(
        self: Scorer, *, scorable: Scorable, expectation: ScoringExpectation | None
    ) -> list[Score]:
        return []

    def build_fallback_score(self: Scorer, *, message: Message, objective: str | None) -> list[Score]:
        return []

    def get_scorer_metrics(self: Scorer) -> None:
        return None

    def validate_return_scores(self: Scorer, scores: list[Score]) -> None:
        return None

    scorer_type = type(
        "LegacyTargetScorer",
        (request.param,),
        {
            "_build_identifier": build_identifier,
            "_score_scorable_async": score_scorable_async,
            "_build_fallback_score": build_fallback_score,
            "get_scorer_metrics": get_scorer_metrics,
            "validate_return_scores": validate_return_scores,
        },
    )
    assert issubclass(scorer_type, Scorer)
    return scorer_type


@pytest.mark.parametrize("has_target", [False, True])
def test_legacy_constructor_only_validates_target(legacy_scorer_type: type[Scorer], has_target: bool) -> None:
    target = MagicMock(spec=PromptTarget) if has_target else None
    validator = ScorerPromptValidator(supported_data_types=["text"])
    with (
        patch.object(TargetRequirements, "validate") as validate,
        patch("pyrit.score.scorer.print_deprecation_message") as warn,
    ):
        scorer = legacy_scorer_type(chat_target=target, validator=validator)
    target_warnings = [call for call in warn.call_args_list if "chat_target" in call.kwargs["old_item"]]
    if target is None:
        validate.assert_not_called()
        assert not target_warnings
    else:
        validate.assert_called_once_with(target=target)
        assert len(target_warnings) == 1
        assert target_warnings[0].kwargs["removed_in"] == "1.4.0"
    assert scorer._validator is validator
    assert scorer.get_chat_target() is None
    assert not hasattr(scorer, "_judge")
    owned_target = MagicMock(spec=PromptTarget)
    scorer._prompt_target = owned_target
    assert scorer.get_chat_target() is owned_target


def test_legacy_constructor_rejects_invalid_target(legacy_scorer_type: type[Scorer]) -> None:
    target = MagicMock(spec=PromptTarget)
    with (
        patch.object(TargetRequirements, "validate", side_effect=ValueError("unsupported target")) as validate,
        pytest.warns(DeprecationWarning, match="chat_target"),
        pytest.raises(ValueError, match="unsupported target"),
    ):
        legacy_scorer_type(chat_target=target, validator=ScorerPromptValidator(supported_data_types=["text"]))
    validate.assert_called_once_with(target=target)


@pytest.fixture(
    params=[
        (SelfAskTrueFalseScorer, {}),
        (SelfAskQuestionAnswerScorer, {}),
        (SelfAskRefusalScorer, {}),
        (SelfAskGeneralTrueFalseScorer, {"system_prompt_format_string": "Judge the response."}),
        (
            SelfAskCategoryScorer,
            {
                "system_prompt": "Judge the response.",
                "content_classifier": ContentClassifier(
                    categories=[ContentClassifierCategory(name="none", description="No harm.")],
                    no_category_found="none",
                ),
            },
        ),
        (LlamaGuardScorer, {}),
        (ShieldGemmaScorer, {"guideline": ShieldGemmaGuideline(name="Harm", description="Harmful content.")}),
        (WildGuardScorer, {}),
        (InsecureCodeScorer, {"system_prompt": "Judge the code.", "harm_categories": ["test"]}),
        (
            SelfAskGeneralFloatScaleScorer,
            {
                "system_prompt_format_string": "Judge the response.",
                "scale": NumericRange(minimum_value=0, maximum_value=1),
            },
        ),
        (
            SelfAskLikertScorer,
            {
                "system_prompt": "Judge the response.",
                "likert_scale": LikertScale(
                    category="harm",
                    scale_descriptions=[
                        LikertScaleEntry(score_value=0, description="No harm."),
                        LikertScaleEntry(score_value=1, description="Harm."),
                    ],
                ),
            },
        ),
        (
            SelfAskScaleScorer,
            {
                "system_prompt": "Judge the response.",
                "scale": NumericRubric(minimum_value=0, maximum_value=1, category="harm"),
            },
        ),
    ],
    ids=lambda case: case[0].__name__,
)
def migrated_scorer(request: pytest.FixtureRequest) -> tuple[type[MessageScorer], dict[str, Any]]:
    return request.param


def test_migrated_scorer_has_one_target_owner(migrated_scorer: tuple[type[MessageScorer], dict[str, Any]]) -> None:
    scorer_type, kwargs = migrated_scorer
    target = MagicMock(spec=PromptTarget)
    with (
        patch.object(TargetRequirements, "validate", autospec=True) as validate,
        patch("pyrit.score.scorer.print_deprecation_message") as warn,
    ):
        scorer = scorer_type(chat_target=target, **kwargs)
    validate.assert_called_once_with(scorer_type.TARGET_REQUIREMENTS, target=target)
    warn.assert_not_called()
    assert scorer.get_chat_target() is target
    assert scorer._judge._target is target


@pytest.mark.parametrize("inherited", [False, True])
def test_migrated_scorers_reject_hidden_legacy_overrides(
    migrated_scorer: tuple[type[MessageScorer], dict[str, Any]], inherited: bool
) -> None:
    scorer_type, kwargs = migrated_scorer

    async def negate_async(
        self: MessageScorer, message_piece: MessagePiece, *, objective: str | None = None
    ) -> list[Score]:
        raise AssertionError("The legacy override must not be silently skipped.")

    custom_type = type(
        "LegacyNegatingScorer",
        (scorer_type,),  # type: ignore[ty:unsupported-dynamic-base] - exercise each real scorer's MRO
        {"_score_piece_async": negate_async},
    )
    if inherited:
        custom_type = type("InheritedLegacyNegatingScorer", (custom_type,), {})
    target = MockPromptTarget()
    with (
        patch.object(target, "send_prompt_async", new_callable=AsyncMock) as send,
        pytest.raises(TypeError, match="Move the custom policy to _score_piece_with_expectation_async"),
    ):
        custom_type(chat_target=target, **kwargs)
    send.assert_not_awaited()


async def test_migrated_typed_override_preserves_custom_verdict_async() -> None:
    class NegatingScorer(SelfAskTrueFalseScorer):
        async def _score_piece_with_expectation_async(
            self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
        ) -> list[Score]:
            scores = await super()._score_piece_with_expectation_async(message_piece, expectation=expectation)
            scores[0].score_value = str(not scores[0].get_value()).lower()
            return scores

    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("NegatingJudge")
    target.send_prompt_async = AsyncMock(
        return_value=[
            Message.from_prompt(
                prompt='{"score_value":"true","description":"match","rationale":"ok","metadata":""}',
                role="assistant",
            )
        ]
    )
    expectation = ScoringExpectation(objective="Find the answer")
    [score] = await NegatingScorer(chat_target=target).score_async(
        scorable=ContentScorable(value="The answer"), expectation=expectation
    )
    assert score.get_value() is False
    assert score.scored_expectation.objective == expectation.objective
    target.send_prompt_async.assert_awaited_once()
