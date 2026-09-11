# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.models import ContentScorable, ScoreStatus, ScoringExpectation
from pyrit.score import ViolenceClassifierScorer
from pyrit.score.float_scale import violence_classifier_scorer as module
from pyrit.score.float_scale.violence_classifier_scorer import (
    _BgeSmallEmbedder,
    _format_text,
    _load_training_rows,
    _predict_probability,
    _train_head,
)


def _separable_training_data(rows_per_class: int = 40) -> tuple[list[list[float]], list[int]]:
    """Two linearly separable clusters in a 384-dimensional space."""
    embeddings: list[list[float]] = []
    labels: list[int] = []
    for i in range(rows_per_class):
        jitter = (i % 7) * 0.01
        positive = [0.0] * 384
        positive[0], positive[1] = 0.8 + jitter, 0.2 - jitter
        negative = [0.0] * 384
        negative[0], negative[1] = -0.8 - jitter, -0.2 + jitter
        embeddings += [positive, negative]
        labels += [1, 0]
    return embeddings, labels


def _trained_head() -> module._TrainedHead:
    embeddings, labels = _separable_training_data()
    return _train_head(embeddings=embeddings, labels=labels, seed=0)


def _scorer_with_mocks(
    *,
    head: module._TrainedHead,
    embedding: list[float],
    abstain_band: tuple[float, float] | None = (0.3, 0.7),
) -> ViolenceClassifierScorer:
    scorer = ViolenceClassifierScorer(abstain_band=abstain_band)
    scorer._head = head
    embedder = MagicMock(spec=_BgeSmallEmbedder)
    embedder.embed_async = AsyncMock(return_value=[embedding])
    scorer._embedder = embedder
    return scorer


def test_format_text_matches_training_format_and_truncates():
    formatted = _format_text(objective="  obj  ", response="  resp  ")
    assert formatted == "PROMPT: obj\nRESPONSE: resp"
    assert _format_text(objective=None, response="resp") == "PROMPT: \nRESPONSE: resp"
    long = _format_text(objective="x" * 600, response="y" * 600)
    assert len(long) == module._TRUNCATION_CHARS


def test_load_training_rows_matches_pinned_datasets():
    texts, labels = _load_training_rows()
    assert len(texts) == len(labels) == 283
    assert set(labels) == {0, 1}
    # A long objective can push the response marker past the truncation point.
    assert all(text.startswith("PROMPT: ") for text in texts)
    assert all("\nRESPONSE: " in text or len(text) == module._TRUNCATION_CHARS for text in texts)
    assert all(len(text) <= module._TRUNCATION_CHARS for text in texts)


def test_load_training_rows_rejects_changed_dataset(monkeypatch, tmp_path):
    harm_path = tmp_path / "harm"
    harm_path.mkdir()
    tampered = b"# dataset_version=1.0\nharm_category,objective,assistant_response,human_score_1,data_type\n"
    pins = {}
    for file_name, pinned in module._TRAINING_DATASETS.items():
        (harm_path / file_name).write_bytes(tampered)
        pins[file_name] = pinned
    monkeypatch.setattr(module, "_HARM_EVALS_PATH", harm_path)

    with pytest.raises(ValueError, match="does not match the bytes"):
        _load_training_rows()

    # The guard must actually discriminate: re-pinning to the tampered bytes passes.
    tampered_sha = hashlib.sha256(tampered).hexdigest()
    monkeypatch.setattr(module, "_TRAINING_DATASETS", dict.fromkeys(pins, tampered_sha))
    texts, labels = _load_training_rows()
    assert texts == [] and labels == []


def test_train_head_is_deterministic_and_separates():
    embeddings, labels = _separable_training_data()
    first = _train_head(embeddings=embeddings, labels=labels, seed=0)
    second = _train_head(embeddings=embeddings, labels=labels, seed=0)
    assert first.state == second.state
    assert first.temperature == second.temperature
    assert first.training_rows == len(labels)

    positive = _predict_probability(head=first, embedding=embeddings[0])
    negative = _predict_probability(head=first, embedding=embeddings[1])
    assert positive > 0.5 > negative


def test_predict_probability_is_a_probability():
    head = _trained_head()
    probability = _predict_probability(head=head, embedding=[0.0] * 384)
    assert 0.0 <= probability <= 1.0
    assert math.isfinite(probability)


@pytest.mark.parametrize("band", [(-0.1, 0.5), (0.5, 0.5), (0.7, 0.3), (0.5, 1.1)])
def test_invalid_abstain_band_raises(band):
    with pytest.raises(ValueError, match="abstain_band"):
        ViolenceClassifierScorer(abstain_band=band)


@pytest.mark.usefixtures("patch_central_database")
async def test_score_async_returns_probability_outside_band():
    embeddings, _ = _separable_training_data()
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=embeddings[0])

    scores = await scorer.score_async(scorable=ContentScorable(value="stab them"))

    assert len(scores) == 1
    score = scores[0]
    assert score.status is ScoreStatus.COMPLETE
    assert score.score_category == ["violence"]
    assert 0.7 < score.get_value() <= 1.0
    assert score.score_metadata["calibrated_probability"] == pytest.approx(score.get_value(), abs=1e-5)


@pytest.mark.usefixtures("patch_central_database")
async def test_score_async_abstains_inside_band():
    # An all-zero embedding sits between the training clusters, so the calibrated
    # probability lands near 0.5, inside any sensible band.
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=[0.0] * 384, abstain_band=(0.1, 0.9))

    scores = await scorer.score_async(scorable=ContentScorable(value="ambiguous"))

    score = scores[0]
    assert score.status is ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert score.score_category == ["violence"]
    assert 0.1 <= score.score_metadata["calibrated_probability"] <= 0.9
    assert score.score_metadata["abstain_band_low"] == 0.1
    assert score.score_metadata["abstain_band_high"] == 0.9


@pytest.mark.usefixtures("patch_central_database")
async def test_score_async_with_band_disabled_never_abstains():
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=[0.0] * 384, abstain_band=None)

    scores = await scorer.score_async(scorable=ContentScorable(value="ambiguous"))

    assert scores[0].status is ScoreStatus.COMPLETE
    assert 0.0 <= scores[0].get_value() <= 1.0


@pytest.mark.usefixtures("patch_central_database")
async def test_score_async_formats_objective_into_text():
    embeddings, _ = _separable_training_data()
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=embeddings[0])

    await scorer.score_async(
        scorable=ContentScorable(value="the response"),
        expectation=ScoringExpectation(objective="the objective"),
    )

    call = scorer._embedder.embed_async.await_args.kwargs
    assert call["texts"] == ["PROMPT: the objective\nRESPONSE: the response"]


def test_identifier_contains_configuration():
    scorer = ViolenceClassifierScorer(abstain_band=(0.2, 0.8), seed=3)
    dumped = scorer.get_identifier().model_dump()
    assert dumped["abstain_band"] == [0.2, 0.8]
    assert dumped["seed"] == 3
    assert dumped["embedding_model"] == _BgeSmallEmbedder.DEFAULT_MODEL_ID


def test_compute_dataset_hashes_matches_pins():
    assert ViolenceClassifierScorer.compute_dataset_hashes() == module._TRAINING_DATASETS
