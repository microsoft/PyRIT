# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import math
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.score import PresidioPiiScorer

# Standard Luhn-valid test card number; not a real account.
SYNTHETIC_CARD = "4111 1111 1111 1111"


@dataclass
class FakeRecognizerResult:
    """Minimal stand-in for presidio_analyzer.RecognizerResult."""

    entity_type: str
    start: int
    end: int
    score: float


class _AnalyzerSpec:
    """Attribute surface PresidioPiiScorer uses from presidio_analyzer.AnalyzerEngine."""

    def analyze(self, text: str, entities: list[str] | None, language: str, score_threshold: float) -> list[Any]:
        raise NotImplementedError


class _PatternRecognizerAnalyzer:
    """Runs real Presidio pattern recognizers without an NLP engine or spaCy model."""

    def __init__(self, recognizers: list[Any]) -> None:
        self._recognizers = recognizers

    def analyze(self, text: str, entities: list[str] | None, language: str, score_threshold: float) -> list[Any]:
        results = []
        for recognizer in self._recognizers:
            wanted = [entity for entity in recognizer.supported_entities if entities is None or entity in entities]
            if wanted:
                results.extend(r for r in recognizer.analyze(text, wanted, None) if r.score >= score_threshold)
        return results


def _make_analyzer(results: list[FakeRecognizerResult]) -> MagicMock:
    analyzer = MagicMock(spec=_AnalyzerSpec)
    analyzer.analyze.return_value = results
    return analyzer


async def test_score_true_when_entity_found(patch_central_database) -> None:
    analyzer = _make_analyzer([FakeRecognizerResult("CREDIT_CARD", 5, 24, 1.0)])
    scorer = PresidioPiiScorer(entities=["CREDIT_CARD"], analyzer=analyzer)

    scores = await scorer.score_text_async(f"Card {SYNTHETIC_CARD}")

    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert scores[0].score_type == "true_false"
    assert scores[0].score_metadata == {"entity_types": "CREDIT_CARD", "total_detections": 1, "count_CREDIT_CARD": 1}


async def test_score_false_when_no_entity_found(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(entities=["US_SSN"], analyzer=analyzer)

    scores = await scorer.score_text_async("Hello world, nothing here.")

    assert scores[0].get_value() is False
    assert scores[0].score_rationale == ""
    assert scores[0].score_metadata == {"entity_types": "", "total_detections": 0}


async def test_score_passes_configuration_to_analyzer(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(
        entities=["US_SSN", "CREDIT_CARD"], score_threshold=0.7, language="es", analyzer=analyzer
    )

    await scorer.score_text_async("texto de prueba")

    kwargs = analyzer.analyze.call_args.kwargs
    assert kwargs["text"] == "texto de prueba"
    assert kwargs["entities"] == ["CREDIT_CARD", "US_SSN"]
    assert kwargs["score_threshold"] == 0.7
    assert kwargs["language"] == "es"


async def test_score_none_entities_requests_all_entities(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(entities=None, analyzer=analyzer)

    await scorer.score_text_async("some text")

    assert analyzer.analyze.call_args.kwargs["entities"] is None


async def test_score_metadata_contains_types_and_counts_never_values(patch_central_database) -> None:
    analyzer = _make_analyzer(
        [
            FakeRecognizerResult("CREDIT_CARD", 0, 19, 1.0),
            FakeRecognizerResult("CREDIT_CARD", 24, 43, 1.0),
            FakeRecognizerResult("EMAIL_ADDRESS", 48, 64, 1.0),
        ]
    )
    scorer = PresidioPiiScorer(analyzer=analyzer)

    scores = await scorer.score_text_async(f"{SYNTHETIC_CARD} and {SYNTHETIC_CARD} and jane@example.com")

    metadata = scores[0].score_metadata
    assert metadata == {
        "entity_types": "CREDIT_CARD, EMAIL_ADDRESS",
        "total_detections": 3,
        "count_CREDIT_CARD": 2,
        "count_EMAIL_ADDRESS": 1,
    }
    assert scores[0].score_rationale == "Detected: CREDIT_CARD, EMAIL_ADDRESS"
    assert "4111" not in str(metadata)
    assert "example.com" not in str(metadata)


async def test_score_empty_input(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer(analyzer=analyzer)

    scores = await scorer.score_text_async("")

    assert scores[0].get_value() is False
    assert scores[0].score_metadata["total_detections"] == 0


async def test_score_persists_verdict_category_rationale_and_metadata(
    patch_central_database, sqlite_instance: MemoryInterface
) -> None:
    analyzer = _make_analyzer([FakeRecognizerResult("CREDIT_CARD", 5, 24, 1.0)])
    scorer = PresidioPiiScorer(entities=["CREDIT_CARD"], analyzer=analyzer)

    scores = await scorer.score_text_async(f"Card {SYNTHETIC_CARD} on file")
    stored = await sqlite_instance.get_scores_async(score_ids=[str(scores[0].id)])

    assert len(stored) == 1
    assert stored[0].get_value() is True
    assert stored[0].score_category == ["pii"]
    assert stored[0].score_rationale == "Detected: CREDIT_CARD"
    assert stored[0].score_metadata == {"entity_types": "CREDIT_CARD", "total_detections": 1, "count_CREDIT_CARD": 1}
    assert "4111" not in stored[0].score_rationale
    assert "4111" not in str(stored[0].score_metadata)


async def test_score_with_real_pattern_recognizers(patch_central_database) -> None:
    predefined = pytest.importorskip("presidio_analyzer.predefined_recognizers")
    analyzer = _PatternRecognizerAnalyzer([predefined.CreditCardRecognizer(), predefined.UsSsnRecognizer()])
    scorer = PresidioPiiScorer(entities=["CREDIT_CARD", "US_SSN"], analyzer=analyzer)

    hit = await scorer.score_text_async(f"Card {SYNTHETIC_CARD} was charged")
    # Presidio rejects the well-known placeholder 123-45-6789 as an SSN.
    miss = await scorer.score_text_async("Order 123-45-6789 shipped")

    assert hit[0].get_value() is True
    assert hit[0].score_metadata["count_CREDIT_CARD"] == 1
    assert miss[0].get_value() is False


async def test_score_runs_analysis_off_the_event_loop_thread(patch_central_database) -> None:
    loop_thread = threading.get_ident()
    analysis_threads: list[int] = []

    def analyze(**_: Any) -> list[FakeRecognizerResult]:
        analysis_threads.append(threading.get_ident())
        return []

    analyzer = MagicMock(spec=_AnalyzerSpec)
    analyzer.analyze.side_effect = analyze
    scorer = PresidioPiiScorer(analyzer=analyzer)

    await scorer.score_text_async("text")

    assert analysis_threads
    assert loop_thread not in analysis_threads


async def test_default_analyzer_created_once_under_concurrency(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    scorer = PresidioPiiScorer()

    with patch.object(PresidioPiiScorer, "_create_default_analyzer", return_value=analyzer) as create:
        await asyncio.gather(*(scorer.score_text_async(f"text {i}") for i in range(5)))

    assert create.call_count == 1
    assert analyzer.analyze.call_count == 5


async def test_missing_dependency_error() -> None:
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = PresidioPiiScorer(entities=["US_SSN"])

        with patch.dict("sys.modules", {"presidio_analyzer": None}):
            with pytest.raises(RuntimeError, match="presidio-analyzer is required"):
                await scorer.score_text_async("text")


def test_init_rejects_empty_entities(patch_central_database) -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        PresidioPiiScorer(entities=[])


@pytest.mark.parametrize("score_threshold", [-0.1, 1.5, math.nan, math.inf])
def test_init_rejects_invalid_score_threshold(patch_central_database, score_threshold: float) -> None:
    with pytest.raises(ValueError, match="score_threshold"):
        PresidioPiiScorer(score_threshold=score_threshold)


def test_identifier_stable(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    first = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.5, analyzer=analyzer)
    second = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.5, analyzer=analyzer)

    assert first.get_identifier().hash == second.get_identifier().hash


def test_identifier_ignores_entity_order_and_duplicates(patch_central_database) -> None:
    first = PresidioPiiScorer(entities=["US_SSN", "CREDIT_CARD"])
    second = PresidioPiiScorer(entities=["CREDIT_CARD", "US_SSN", "CREDIT_CARD"])

    assert first.get_identifier().hash == second.get_identifier().hash


def test_identifier_changes_with_params(patch_central_database) -> None:
    analyzer = _make_analyzer([])
    first = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.5, analyzer=analyzer)
    second = PresidioPiiScorer(entities=["US_SSN"], score_threshold=0.7, analyzer=analyzer)

    assert first.get_identifier().hash != second.get_identifier().hash


def test_identifier_distinguishes_injected_analyzer(patch_central_database) -> None:
    default = PresidioPiiScorer()
    injected = PresidioPiiScorer(analyzer=_make_analyzer([]))

    assert default.get_identifier().hash != injected.get_identifier().hash


def test_identifier_distinguishes_recognizer_sets(patch_central_database) -> None:
    def analyzer_with(*names: str) -> SimpleNamespace:
        return SimpleNamespace(
            registry=SimpleNamespace(recognizers=[SimpleNamespace(name=name) for name in names]),
            analyze=lambda **_: [],
        )

    first = PresidioPiiScorer(analyzer=analyzer_with("CreditCardRecognizer"))
    second = PresidioPiiScorer(analyzer=analyzer_with("CreditCardRecognizer", "UsSsnRecognizer"))
    same_as_second = PresidioPiiScorer(analyzer=analyzer_with("UsSsnRecognizer", "CreditCardRecognizer"))

    assert first.get_identifier().hash != second.get_identifier().hash
    assert second.get_identifier().hash == same_as_second.get_identifier().hash
