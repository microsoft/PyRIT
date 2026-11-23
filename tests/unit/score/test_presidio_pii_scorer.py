# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import math
import re
import sys
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import PresidioPiiScorer, TrueFalseScoreAggregator
from unit.async_utils import TEST_WATCHDOG_SECONDS

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


def _fake_analyzer(*, recognizers: list[Any], models: list[dict[str, str]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        registry=SimpleNamespace(recognizers=recognizers),
        nlp_engine=SimpleNamespace(models=models or [{"lang_code": "en", "model_name": "en_core_web_sm"}]),
        analyze=lambda **_: [],
    )


def test_identifier_differs_for_same_name_recognizers_with_different_patterns(patch_central_database) -> None:
    presidio = pytest.importorskip("presidio_analyzer")

    def recognizer(regex: str) -> Any:
        pattern = presidio.Pattern(name="p", regex=regex, score=0.9)
        return presidio.PatternRecognizer(supported_entity="ORDER_ID", name="Rx", patterns=[pattern])

    digits = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(r"\d{3}")]))
    letters = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(r"[a-z]+")]))

    assert digits.get_identifier().hash != letters.get_identifier().hash


def test_identifier_differs_for_different_deny_lists(patch_central_database) -> None:
    presidio = pytest.importorskip("presidio_analyzer")

    def recognizer(deny_list: list[str]) -> Any:
        return presidio.PatternRecognizer(supported_entity="CODE_NAME", name="Rx", deny_list=deny_list)

    first = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(["alpha"])]))
    second = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(["beta"])]))

    assert first.get_identifier().hash != second.get_identifier().hash


def test_identifier_differs_for_nlp_engine_models(patch_central_database) -> None:
    small = PresidioPiiScorer(
        analyzer=_fake_analyzer(recognizers=[], models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
    )
    large = PresidioPiiScorer(
        analyzer=_fake_analyzer(recognizers=[], models=[{"lang_code": "en", "model_name": "en_core_web_lg"}])
    )

    assert small.get_identifier().hash != large.get_identifier().hash


def test_identifier_uses_analyzer_identifier(patch_central_database) -> None:
    analyzer = _fake_analyzer(recognizers=[])
    first = PresidioPiiScorer(analyzer=analyzer, analyzer_identifier="custom-recognizers-v1")
    second = PresidioPiiScorer(analyzer=analyzer, analyzer_identifier="custom-recognizers-v2")

    assert first.get_identifier().hash != second.get_identifier().hash


def test_identifier_stable_for_equivalent_analyzers(patch_central_database) -> None:
    predefined = pytest.importorskip("presidio_analyzer.predefined_recognizers")
    first = PresidioPiiScorer(
        analyzer=_fake_analyzer(recognizers=[predefined.CreditCardRecognizer(), predefined.UsSsnRecognizer()])
    )
    second = PresidioPiiScorer(
        analyzer=_fake_analyzer(recognizers=[predefined.UsSsnRecognizer(), predefined.CreditCardRecognizer()])
    )

    assert first.get_identifier().hash == second.get_identifier().hash


def _results_by_text(results: dict[str, list[FakeRecognizerResult]]) -> MagicMock:
    analyzer = MagicMock(spec=_AnalyzerSpec)
    analyzer.analyze.side_effect = lambda **kwargs: results[kwargs["text"]]
    return analyzer


async def _score_two_piece_message(
    scorer: PresidioPiiScorer, sqlite_instance: MemoryInterface, *, first: str, second: str
) -> Any:
    conversation_id = "presidio-multi-piece"
    message = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value=first, conversation_id=conversation_id),
            MessagePiece(role="assistant", original_value=second, conversation_id=conversation_id),
        ]
    )
    await sqlite_instance.add_message_to_memory_async(request=message)
    return (await scorer.score_message_async(message=message))[0]


async def test_multi_piece_metadata_sums_counts(patch_central_database, sqlite_instance: MemoryInterface) -> None:
    card = FakeRecognizerResult("CREDIT_CARD", 0, 19, 1.0)
    scorer = PresidioPiiScorer(analyzer=_results_by_text({"piece one": [card], "piece two": [card]}))

    score = await _score_two_piece_message(scorer, sqlite_instance, first="piece one", second="piece two")

    assert score.get_value() is True
    assert score.score_metadata["total_detections"] == 2
    assert score.score_metadata["count_CREDIT_CARD"] == 2
    assert score.score_metadata["entity_types"] == "CREDIT_CARD"


async def test_multi_piece_metadata_unions_entity_types(
    patch_central_database, sqlite_instance: MemoryInterface
) -> None:
    card = FakeRecognizerResult("CREDIT_CARD", 0, 19, 1.0)
    ssn = FakeRecognizerResult("US_SSN", 0, 11, 0.85)
    scorer = PresidioPiiScorer(analyzer=_results_by_text({"card": [card], "two ssns": [ssn, ssn]}))

    score = await _score_two_piece_message(scorer, sqlite_instance, first="card", second="two ssns")

    assert score.score_metadata == {
        "entity_types": "CREDIT_CARD, US_SSN",
        "total_detections": 3,
        "count_CREDIT_CARD": 1,
        "count_US_SSN": 2,
    }


async def test_multi_piece_respects_and_aggregator(patch_central_database, sqlite_instance: MemoryInterface) -> None:
    card = FakeRecognizerResult("CREDIT_CARD", 0, 19, 1.0)
    scorer = PresidioPiiScorer(
        analyzer=_results_by_text({"hit": [card], "miss": []}), score_aggregator=TrueFalseScoreAggregator.AND
    )

    score = await _score_two_piece_message(scorer, sqlite_instance, first="hit", second="miss")

    assert score.get_value() is False
    assert score.score_metadata["total_detections"] == 1
    assert scorer.get_identifier().params["score_aggregator"] == TrueFalseScoreAggregator.AND.__name__


def _presidio_executor() -> Any:
    return sys.modules[PresidioPiiScorer.__module__]._get_executor()


@contextlib.contextmanager
def _signal_on_submit() -> Any:
    """Yield an event that is set once the scorer has queued work on the Presidio worker."""
    executor = _presidio_executor()
    submitted = threading.Event()
    original_submit = executor.submit

    def submit(*args: Any, **kwargs: Any) -> Any:
        future = original_submit(*args, **kwargs)
        submitted.set()
        return future

    with patch.object(executor, "submit", side_effect=submit):
        yield submitted


async def test_score_runs_analysis_on_dedicated_presidio_worker(patch_central_database) -> None:
    thread_names: list[str] = []

    def analyze(**_: Any) -> list[FakeRecognizerResult]:
        thread_names.append(threading.current_thread().name)
        return []

    analyzer = MagicMock(spec=_AnalyzerSpec)
    analyzer.analyze.side_effect = analyze
    scorer = PresidioPiiScorer(analyzer=analyzer)

    await asyncio.gather(*(scorer.score_text_async(f"text {i}") for i in range(3)))

    assert len(thread_names) == 3
    assert all(name.startswith("pyrit-presidio") for name in thread_names)


async def test_waiting_calls_do_not_hold_default_executor_threads(patch_central_database) -> None:
    release = threading.Event()

    def analyze(**_: Any) -> list[FakeRecognizerResult]:
        release.wait(TEST_WATCHDOG_SECONDS)
        return []

    analyzer = MagicMock(spec=_AnalyzerSpec)
    analyzer.analyze.side_effect = analyze
    scorer = PresidioPiiScorer(analyzer=analyzer)

    # More waiting calls than the default executor has workers.
    tasks = [asyncio.create_task(scorer.score_text_async(f"text {i}")) for i in range(40)]
    try:
        unrelated = await asyncio.wait_for(asyncio.to_thread(lambda: "done"), TEST_WATCHDOG_SECONDS)
    finally:
        release.set()
    await asyncio.gather(*tasks)

    assert unrelated == "done"
    assert analyzer.analyze.call_count == 40


async def test_cancel_during_analyze_does_not_overlap_next_call(patch_central_database) -> None:
    started, release = threading.Event(), threading.Event()
    guard = threading.Lock()
    active = {"now": 0, "max": 0}

    def analyze(**_: Any) -> list[FakeRecognizerResult]:
        with guard:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        started.set()
        release.wait(TEST_WATCHDOG_SECONDS)
        with guard:
            active["now"] -= 1
        return []

    analyzer = MagicMock(spec=_AnalyzerSpec)
    analyzer.analyze.side_effect = analyze
    scorer = PresidioPiiScorer(analyzer=analyzer)

    try:
        first = asyncio.create_task(scorer.score_text_async("first"))
        assert await asyncio.to_thread(started.wait, TEST_WATCHDOG_SECONDS)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with _signal_on_submit() as submitted:
            second = asyncio.create_task(scorer.score_text_async("second"))
            assert await asyncio.to_thread(submitted.wait, TEST_WATCHDOG_SECONDS)
    finally:
        release.set()
    await second

    assert active["max"] == 1
    assert analyzer.analyze.call_count == 2


async def test_cancel_during_default_load_constructs_engine_once(patch_central_database) -> None:
    loading, release = threading.Event(), threading.Event()
    analyzer = _make_analyzer([])

    def create() -> MagicMock:
        loading.set()
        release.wait(TEST_WATCHDOG_SECONDS)
        return analyzer

    scorer = PresidioPiiScorer()
    with patch.object(PresidioPiiScorer, "_create_default_analyzer", side_effect=create) as create_mock:
        try:
            first = asyncio.create_task(scorer.score_text_async("first"))
            assert await asyncio.to_thread(loading.wait, TEST_WATCHDOG_SECONDS)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with _signal_on_submit() as submitted:
                retry = asyncio.create_task(scorer.score_text_async("retry"))
                assert await asyncio.to_thread(submitted.wait, TEST_WATCHDOG_SECONDS)
        finally:
            release.set()
        scores = await retry

    assert create_mock.call_count == 1
    assert scores[0].get_value() is False


def test_identifier_differs_for_ner_model_configuration(patch_central_database) -> None:
    nlp_engine = pytest.importorskip("presidio_analyzer.nlp_engine")

    def analyzer_with(default_score: float) -> SimpleNamespace:
        return SimpleNamespace(
            registry=SimpleNamespace(recognizers=[]),
            nlp_engine=SimpleNamespace(
                models=[{"lang_code": "en", "model_name": "en_core_web_sm"}],
                ner_model_configuration=nlp_engine.NerModelConfiguration(default_score=default_score),
            ),
            analyze=lambda **_: [],
        )

    first = PresidioPiiScorer(analyzer=analyzer_with(0.85))
    second = PresidioPiiScorer(analyzer=analyzer_with(0.2))

    assert first.get_identifier().hash != second.get_identifier().hash


def test_identifier_differs_for_regex_flags(patch_central_database) -> None:
    presidio = pytest.importorskip("presidio_analyzer")

    def recognizer(flags: int) -> Any:
        return presidio.PatternRecognizer(
            supported_entity="CODE_NAME", name="Rx", deny_list=["alpha"], global_regex_flags=flags
        )

    case_sensitive = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(0)]))
    case_insensitive = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(re.IGNORECASE)]))

    assert case_sensitive.get_identifier().hash != case_insensitive.get_identifier().hash


def test_identifier_ignores_order_of_set_derived_lists(patch_central_database) -> None:
    def recognizer(entities: list[str]) -> SimpleNamespace:
        return SimpleNamespace(name="Ner", to_dict=lambda: {"name": "Ner", "supported_entities": entities})

    first = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(["PERSON", "LOCATION"])]))
    second = PresidioPiiScorer(analyzer=_fake_analyzer(recognizers=[recognizer(["LOCATION", "PERSON"])]))

    assert first.get_identifier().hash == second.get_identifier().hash


def test_identifier_is_fixed_at_construction(patch_central_database) -> None:
    recognizers: list[Any] = [SimpleNamespace(name="First")]
    analyzer = _fake_analyzer(recognizers=recognizers)
    scorer = PresidioPiiScorer(analyzer=analyzer)

    # Changes after construction, even before the identity is first read, are not reflected.
    recognizers.append(SimpleNamespace(name="Second"))

    assert scorer.get_identifier().hash != PresidioPiiScorer(analyzer=analyzer).get_identifier().hash


def test_init_requires_analyzer_identifier_for_unstable_config(patch_central_database) -> None:
    def hook() -> None:
        return None

    analyzer = _fake_analyzer(recognizers=[SimpleNamespace(name="Custom", to_dict=lambda: {"hook": hook})])

    with pytest.raises(ValueError, match="analyzer_identifier"):
        PresidioPiiScorer(analyzer=analyzer)

    first = PresidioPiiScorer(analyzer=analyzer, analyzer_identifier="custom-v1")
    second = PresidioPiiScorer(analyzer=analyzer, analyzer_identifier="custom-v1")
    assert first.get_identifier().hash == second.get_identifier().hash


async def test_multi_piece_keeps_custom_aggregator_metadata(
    patch_central_database, sqlite_instance: MemoryInterface
) -> None:
    def tagging_or(scores: Any) -> Any:
        result = TrueFalseScoreAggregator.OR(scores)
        return dataclasses.replace(result, metadata={**result.metadata, "run": "nightly"})

    tagging_or.__name__ = "TAGGING_OR_"
    card = FakeRecognizerResult("CREDIT_CARD", 0, 19, 1.0)
    scorer = PresidioPiiScorer(analyzer=_results_by_text({"a": [card], "b": [card]}), score_aggregator=tagging_or)

    score = await _score_two_piece_message(scorer, sqlite_instance, first="a", second="b")

    assert score.score_metadata["run"] == "nightly"
    assert score.score_metadata["total_detections"] == 2
    assert scorer.get_identifier().params["score_aggregator"] == "TAGGING_OR_"
