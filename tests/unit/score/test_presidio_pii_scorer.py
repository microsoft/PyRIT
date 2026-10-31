# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score.true_false.presidio_pii_scorer import PresidioPiiScorer


@dataclass
class FakeRecognizerResult:
    """Minimal stand-in for presidio_analyzer.RecognizerResult."""

    entity_type: str
    start: int
    end: int
    score: float


def _make_analyzer(results: list[FakeRecognizerResult]) -> MagicMock:
    analyzer = MagicMock()
    analyzer.analyze.return_value = results
    return analyzer


async def test_true_when_entity_found(patch_central_database) -> None:
    analyzer = _make_analyzer([FakeRecognizerResult("US_SSN", 0, 11, 0.85)])
    scorer = PresidioPiiScorer(entities=["US_SSN"], analyzer=analyzer)

    scores = await scorer.score_text_async("123-45-6789")

    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert scores[0].score_type == "true_false"
    metadata = scores[0].score_metadata
    assert "US_SSN" in metadata["entity_types"]
    assert metadata["count_US_SSN"] == 1
    assert metadata["total_detections"] == 1


async def test_false_when_no_entity_found(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(entities=["US_SSN"], analyzer=analyzer)

    scores = await scorer.score_text_async("Hello world, nothing here.")

    assert len(scores) == 1
    assert scores[0].get_value() is False
    metadata = scores[0].score_metadata
    assert metadata["entity_types"] == ""
    assert metadata["total_detections"] == 0


async def test_entity_filter_respected(patch_central_database) -> None:
    analyzer = _make_analyzer(
        [
            FakeRecognizerResult("EMAIL_ADDRESS", 0, 15, 0.9),
        ]
    )
    scorer = PresidioPiiScorer(entities=["US_SSN"], analyzer=analyzer)

    await scorer.score_text_async("user@example.com")

    analyzer.analyze.assert_called_once()
    call_kwargs = analyzer.analyze.call_args
    assert call_kwargs.kwargs.get("entities") == ["US_SSN"] or call_kwargs[1].get("entities") == ["US_SSN"]


async def test_threshold_respected(patch_central_database) -> None:
    analyzer = _make_analyzer([FakeRecognizerResult("PHONE_NUMBER", 0, 12, 0.3)])
    scorer = PresidioPiiScorer(entities=["PHONE_NUMBER"], score_threshold=0.5, analyzer=analyzer)

    await scorer.score_text_async("555-123-4567")

    call_kwargs = analyzer.analyze.call_args
    assert call_kwargs.kwargs.get("score_threshold") == 0.5 or call_kwargs[1].get("score_threshold") == 0.5


async def test_metadata_contains_types_and_counts_never_values(patch_central_database) -> None:
    analyzer = _make_analyzer(
        [
            FakeRecognizerResult("CREDIT_CARD", 0, 19, 0.95),
            FakeRecognizerResult("CREDIT_CARD", 25, 44, 0.95),
            FakeRecognizerResult("US_SSN", 50, 61, 0.85),
        ]
    )
    scorer = PresidioPiiScorer(analyzer=analyzer)

    scores = await scorer.score_text_async("4111-1111-1111-1111 and 4222-2222-2222-2222 and 123-45-6789")

    metadata = scores[0].score_metadata
    assert metadata["count_CREDIT_CARD"] == 2
    assert metadata["count_US_SSN"] == 1
    assert metadata["total_detections"] == 3
    assert metadata["entity_types"] == "CREDIT_CARD, US_SSN"
    raw = str(scores[0].score_metadata)
    assert "4111" not in raw
    assert "6789" not in raw


async def test_lookalike_does_not_trigger(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(entities=["US_SSN"], analyzer=analyzer)

    scores = await scorer.score_text_async("Order number 123-45-6789A is ready")

    assert scores[0].get_value() is False


async def test_missing_dependency_error() -> None:
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = PresidioPiiScorer(entities=["US_SSN"])

        with patch.dict("sys.modules", {"presidio_analyzer": None}):
            with pytest.raises(RuntimeError, match="presidio-analyzer is required"):
                await scorer.score_text_async("123-45-6789")


async def test_empty_input(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(analyzer=analyzer)

    scores = await scorer.score_text_async("")

    assert len(scores) == 1
    assert scores[0].get_value() is False
    metadata = scores[0].score_metadata
    assert metadata["total_detections"] == 0


async def test_multiple_entity_types_detected(patch_central_database) -> None:
    analyzer = _make_analyzer(
        [
            FakeRecognizerResult("EMAIL_ADDRESS", 0, 15, 0.9),
            FakeRecognizerResult("PHONE_NUMBER", 20, 32, 0.7),
        ]
    )
    scorer = PresidioPiiScorer(entities=["EMAIL_ADDRESS", "PHONE_NUMBER"], analyzer=analyzer)

    scores = await scorer.score_text_async("user@example.com 555-123-4567")

    assert scores[0].get_value() is True
    metadata = scores[0].score_metadata
    assert metadata["entity_types"] == "EMAIL_ADDRESS, PHONE_NUMBER"


async def test_none_entities_passes_none_to_analyzer(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(entities=None, analyzer=analyzer)

    await scorer.score_text_async("some text")

    call_kwargs = analyzer.analyze.call_args
    assert call_kwargs.kwargs.get("entities") is None or call_kwargs[1].get("entities") is None


async def test_identifier_stable(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    first = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.5, analyzer=analyzer)
    second = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.5, analyzer=analyzer)

    assert first.get_identifier().hash == second.get_identifier().hash


async def test_identifier_changes_with_params(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    first = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.5, analyzer=analyzer)
    second = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.7, analyzer=analyzer)

    assert first.get_identifier().hash != second.get_identifier().hash
