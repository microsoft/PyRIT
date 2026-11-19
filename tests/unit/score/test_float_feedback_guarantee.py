# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A scorer that promises float feedback is held to it wherever its results leave the scorer."""

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import (
    ComponentIdentifier,
    ContentScorable,
    FloatFeedback,
    InvalidFloatFeedbackError,
    MessagePiece,
    Scorable,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score import (
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    MessageTrueFalseScorer,
    ScorerPromptValidator,
    TrueFalseFallbackScorer,
    TrueFalseInverterScorer,
    TrueFalseScorer,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")

EVIDENCE = ContentScorable(value="evidence")


class _FloatScorer(FloatScaleScorer):
    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []


class _Judge(TrueFalseScorer):
    """A true/false scorer whose promise and output each test sets."""

    def __init__(self, *, promises: bool) -> None:
        self._promises = promises
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"promises": self._promises})

    def _preserves_float_scale_value(self) -> bool:
        return self._promises

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []


def _verdict(*, value: str | None = "true", metadata: dict | None = None) -> Score:
    return Score(
        score_type="true_false",
        score_value=value,
        status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
        score_category=None,
        score_value_description="",
        score_rationale="reason",
        score_metadata=metadata,
        scorer_class_identifier=ComponentIdentifier(class_name="_Judge", class_module=__name__),
        scorable=EVIDENCE,
    )


def _threshold() -> FloatScaleThresholdScorer:
    return FloatScaleThresholdScorer(scorer=_FloatScorer(), threshold=0.5)


class TestWhoPromises:
    def test_the_default_promises_nothing(self):
        assert _Judge(promises=False).capabilities.preserves_float_scale_value is False

    def test_threshold_scorers_promise_the_value_they_compared(self):
        assert _threshold().capabilities.preserves_float_scale_value is True

    def test_inverting_a_verdict_clears_the_promise(self):
        assert TrueFalseInverterScorer(scorer=_threshold()).capabilities.preserves_float_scale_value is False

    @pytest.mark.parametrize(
        ("primary", "fallback", "expected"), [(True, True, True), (True, False, False), (False, True, False)]
    )
    def test_a_fallback_promises_only_when_both_children_do(self, primary, fallback, expected):
        wrapper = TrueFalseFallbackScorer(scorer=_Judge(promises=primary), fallback_scorer=_Judge(promises=fallback))
        assert wrapper.capabilities.preserves_float_scale_value is expected


class TestPublicBoundary:
    async def test_a_complete_verdict_without_the_value_is_rejected(self, sqlite_instance: MemoryInterface):
        judge = _Judge(promises=True)
        with (
            patch.object(judge, "_score_scorable_async", new_callable=AsyncMock, return_value=[_verdict()]),
            pytest.raises(InvalidFloatFeedbackError, match="_Judge promised a float value"),
        ):
            await judge.score_async(scorable=EVIDENCE)
        assert await sqlite_instance.get_scores_async() == []

    @pytest.mark.parametrize(
        "score",
        [_verdict(metadata={FloatFeedback.METADATA_KEY: 0.6}), _verdict(value=None)],
        ids=["complete_with_value", "undetermined"],
    )
    async def test_results_that_keep_the_promise_pass(self, score: Score, sqlite_instance: MemoryInterface):
        judge = _Judge(promises=True)
        with patch.object(judge, "_score_scorable_async", new_callable=AsyncMock, return_value=[score]):
            assert len(await judge.score_async(scorable=EVIDENCE)) == 1

    async def test_a_scorer_that_promises_nothing_is_not_checked(self, sqlite_instance: MemoryInterface):
        judge = _Judge(promises=False)
        with patch.object(judge, "_score_scorable_async", new_callable=AsyncMock, return_value=[_verdict()]):
            assert len(await judge.score_async(scorable=EVIDENCE)) == 1

    async def test_threshold_results_carry_the_compared_value(self, sqlite_instance: MemoryInterface):
        scorer = _threshold()
        child = Score(
            score_type="float_scale",
            score_value="0.4",
            score_category=None,
            score_value_description="",
            score_rationale="",
            score_metadata={},
            scorer_class_identifier=ComponentIdentifier(class_name="_FloatScorer", class_module=__name__),
            scorable=EVIDENCE,
        )
        with patch.object(scorer._scorer, "_score_scorable_async", new_callable=AsyncMock, return_value=[child]):
            (result,) = await scorer.score_async(scorable=EVIDENCE)
        assert result.get_value() is False
        assert result.score_metadata[FloatFeedback.METADATA_KEY] == 0.4


class TestNestedBoundary:
    async def test_a_child_that_breaks_its_promise_fails_inside_the_tree(self, sqlite_instance: MemoryInterface):
        primary, fallback = _Judge(promises=True), _Judge(promises=True)
        wrapper = TrueFalseFallbackScorer(scorer=primary, fallback_scorer=fallback)
        with (
            patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[_verdict()]),
            pytest.raises(RuntimeError) as excinfo,
        ):
            await wrapper.score_async(scorable=EVIDENCE)
        assert isinstance(excinfo.value.__cause__, InvalidFloatFeedbackError)

    async def test_the_wrapper_keeps_the_deciding_childs_value(self, sqlite_instance: MemoryInterface):
        primary, fallback = _Judge(promises=True), _Judge(promises=True)
        wrapper = TrueFalseFallbackScorer(scorer=primary, fallback_scorer=fallback)
        kept = _verdict(metadata={FloatFeedback.METADATA_KEY: 0.7})
        with (
            patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[_verdict(value=None)]),
            patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock, return_value=[kept]),
        ):
            (result,) = await wrapper.score_async(scorable=EVIDENCE)
        assert result.score_metadata[FloatFeedback.METADATA_KEY] == 0.7


class _MessageJudge(MessageTrueFalseScorer):
    """A message scorer that promises float feedback and returns whatever the test sets."""

    def __init__(self, *, result: Score) -> None:
        self._result = result
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    def _preserves_float_scale_value(self) -> bool:
        return True

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [self._result]


class TestInHandMessageBoundary:
    """A wrapper that scores a derived message, such as a transcript, gets checked child results."""

    @staticmethod
    def _transcript() -> MessagePiece:
        piece = MessagePiece(role="assistant", original_value="transcript")
        piece.not_in_memory = True
        return piece

    async def test_a_child_that_breaks_its_promise_is_rejected(self, sqlite_instance: MemoryInterface):
        judge = _MessageJudge(result=_verdict())
        with pytest.raises(InvalidFloatFeedbackError, match="_MessageJudge promised a float value"):
            await judge._score_nested_message_async(message=self._transcript().to_message(), expectation=None)

    async def test_a_child_that_keeps_its_promise_passes(self, sqlite_instance: MemoryInterface):
        judge = _MessageJudge(result=_verdict(metadata={FloatFeedback.METADATA_KEY: 0.6}))
        scores = await judge._score_nested_message_async(message=self._transcript().to_message(), expectation=None)
        assert [score.score_metadata[FloatFeedback.METADATA_KEY] for score in scores] == [0.6]


class TestReusedResults:
    async def test_a_stored_verdict_without_the_value_is_not_reused(self):
        judge = _Judge(promises=True)
        kept = _verdict(metadata={FloatFeedback.METADATA_KEY: 0.2})
        missing = _verdict()
        undetermined = _verdict(value=None)
        with (
            patch.object(judge, "find_matching_scores", return_value=[kept, missing, undetermined]),
            patch.object(
                judge,
                "_filter_matching_scores_by_snapshot_async",
                new_callable=AsyncMock,
                side_effect=lambda **kwargs: kwargs["scores"],
            ),
        ):
            reused = await judge.find_matching_scores_async(scores=[], scorable=EVIDENCE)
        assert reused == [kept, undetermined]

    async def test_a_scorer_that_promises_nothing_reuses_as_before(self):
        judge = _Judge(promises=False)
        missing = _verdict()
        with (
            patch.object(judge, "find_matching_scores", return_value=[missing]),
            patch.object(
                judge,
                "_filter_matching_scores_by_snapshot_async",
                new_callable=AsyncMock,
                side_effect=lambda **kwargs: kwargs["scores"],
            ),
        ):
            assert await judge.find_matching_scores_async(scores=[], scorable=EVIDENCE) == [missing]
