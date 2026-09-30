# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import importlib.util
import math
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import ContentScorable, MessagePiece, ScoreStatus, ScoringExpectation
from pyrit.score import LocalViolenceClassifierScorer
from pyrit.score.float_scale import local_violence_classifier_scorer as module
from pyrit.score.float_scale.local_violence_classifier_scorer import (
    _BgeSmallEmbedder,
    _ChunkPrediction,
    _format_training_text,
    _load_training_rows,
    _predict_probability,
    _train_head,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")

requires_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None, reason="LocalViolenceClassifierScorer needs torch for its head"
)
requires_transformers = pytest.mark.skipif(
    importlib.util.find_spec("transformers") is None, reason="the real-tokenizer tests need transformers"
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


@cache
def _trained_head() -> module._TrainedHead:
    embeddings, labels = _separable_training_data()
    return _train_head(embeddings=embeddings, labels=labels, seed=0)


def _scorer_with_mocks(
    *,
    head: module._TrainedHead,
    embedding: list[float],
    abstain_band: tuple[float, float] | None = (0.3, 0.7),
) -> LocalViolenceClassifierScorer:
    scorer = LocalViolenceClassifierScorer(abstain_band=abstain_band)
    scorer._head = head
    embedder = MagicMock(spec=_BgeSmallEmbedder)
    embedder.predict_response_async = AsyncMock(
        return_value=_ChunkPrediction(
            probability=_predict_probability(head=head, embedding=embedding),
            chunk_count=1,
            objective_truncated=False,
        )
    )
    scorer._embedder = embedder
    return scorer


def test_format_training_text_matches_legacy_format_and_truncates() -> None:
    formatted = _format_training_text(objective="  obj  ", response="  resp  ")
    assert formatted == "PROMPT: obj\nRESPONSE: resp"
    assert _format_training_text(objective=None, response="resp") == "PROMPT: \nRESPONSE: resp"
    long = _format_training_text(objective="x" * 600, response="y" * 600)
    assert len(long) == module._TRUNCATION_CHARS


def test_load_training_rows_matches_pinned_datasets() -> None:
    texts, labels = _load_training_rows()
    assert len(texts) == len(labels) == 283
    assert set(labels) == {0, 1}
    # A long objective can push the response marker past the truncation point.
    assert all(text.startswith("PROMPT: ") for text in texts)
    assert all("\nRESPONSE: " in text or len(text) == module._TRUNCATION_CHARS for text in texts)
    assert all(len(text) <= module._TRUNCATION_CHARS for text in texts)


def test_load_training_rows_rejects_changed_dataset(tmp_path: Path) -> None:
    harm_path = tmp_path / "harm"
    harm_path.mkdir()
    tampered = b"# dataset_version=1.0\nharm_category,objective,assistant_response,human_score_1,data_type\n"
    pins = {}
    for file_name, pinned in module._TRAINING_DATASETS.items():
        (harm_path / file_name).write_bytes(tampered)
        pins[file_name] = pinned
    with patch.object(module, "_HARM_EVALS_PATH", harm_path):
        with pytest.raises(ValueError, match="does not match the bytes"):
            _load_training_rows()

        tampered_sha = hashlib.sha256(tampered).hexdigest()
        with patch.object(module, "_TRAINING_DATASETS", dict.fromkeys(pins, tampered_sha)):
            texts, labels = _load_training_rows()
    assert texts == [] and labels == []


@requires_torch
def test_train_head_is_deterministic_and_separates() -> None:
    embeddings, labels = _separable_training_data()
    first = _train_head(embeddings=embeddings, labels=labels, seed=0)
    second = _train_head(embeddings=embeddings, labels=labels, seed=0)
    import torch

    for key, value in first.network.state_dict().items():
        assert torch.equal(value, second.network.state_dict()[key])
    assert first.temperature == second.temperature
    assert first.training_rows == len(labels)

    positive = _predict_probability(head=first, embedding=embeddings[0])
    negative = _predict_probability(head=first, embedding=embeddings[1])
    assert positive > 0.5 > negative


@requires_torch
def test_predict_probability_is_a_probability() -> None:
    head = _trained_head()
    probability = _predict_probability(head=head, embedding=[0.0] * 384)
    assert 0.0 <= probability <= 1.0
    assert math.isfinite(probability)


@pytest.mark.parametrize("band", [(-0.1, 0.5), (0.5, 0.5), (0.7, 0.3), (0.5, 1.1)])
def test_invalid_abstain_band_raises(band: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="abstain_band"):
        LocalViolenceClassifierScorer(abstain_band=band)


@requires_torch
@pytest.mark.usefixtures("patch_central_database")
async def test_score_returns_probability_outside_band_async() -> None:
    embeddings, _ = _separable_training_data()
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=embeddings[0])

    scores = await scorer.score_async(scorable=ContentScorable(value="stab them"))

    assert len(scores) == 1
    score = scores[0]
    assert score.status is ScoreStatus.COMPLETE
    assert score.score_category == ["violence"]
    assert 0.7 < score.get_value() <= 1.0
    assert score.score_metadata["max_chunk_probability"] == score.get_value()


@requires_torch
@pytest.mark.usefixtures("patch_central_database")
async def test_score_abstains_inside_band_async() -> None:
    # An all-zero embedding sits between the training clusters, so the calibrated
    # probability lands near 0.5, inside any sensible band.
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=[0.0] * 384, abstain_band=(0.1, 0.9))

    scores = await scorer.score_async(scorable=ContentScorable(value="ambiguous"))

    score = scores[0]
    assert score.status is ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert score.score_category == ["violence"]
    assert 0.1 <= score.score_metadata["max_chunk_probability"] <= 0.9
    assert score.score_metadata["abstain_band_low"] == 0.1
    assert score.score_metadata["abstain_band_high"] == 0.9


@requires_torch
@pytest.mark.usefixtures("patch_central_database")
async def test_score_with_band_disabled_never_abstains_async() -> None:
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=[0.0] * 384, abstain_band=None)

    scores = await scorer.score_async(scorable=ContentScorable(value="ambiguous"))

    assert scores[0].status is ScoreStatus.COMPLETE
    assert 0.0 <= scores[0].get_value() <= 1.0


@requires_torch
@pytest.mark.usefixtures("patch_central_database")
async def test_score_passes_objective_into_windows_async() -> None:
    embeddings, _ = _separable_training_data()
    scorer = _scorer_with_mocks(head=_trained_head(), embedding=embeddings[0])

    await scorer.score_async(
        scorable=ContentScorable(value="the response"),
        expectation=ScoringExpectation(objective="the objective"),
    )

    call = scorer._embedder.predict_response_async.await_args.kwargs
    assert call["objective"] == "the objective"
    assert call["response"] == "the response"
    assert call["max_input_tokens"] == 512
    assert call["chunk_overlap_tokens"] == 64
    assert call["max_objective_tokens"] == 128


def test_identifier_contains_configuration() -> None:
    scorer = LocalViolenceClassifierScorer(abstain_band=(0.2, 0.8), seed=3)
    dumped = scorer.get_identifier().model_dump()
    assert dumped["abstain_band"] == [0.2, 0.8]
    assert dumped["seed"] == 3
    assert dumped["embedding_model"] == _BgeSmallEmbedder.DEFAULT_MODEL_ID


def test_compute_dataset_hashes_matches_pins() -> None:
    assert LocalViolenceClassifierScorer.compute_dataset_hashes() == module._TRAINING_DATASETS


class _Tokenizer:
    """Tiny tokenizer with visible token IDs and the pinned encoder's framing budget."""

    cls_token_id = 5
    sep_token_id = 6

    def encode(
        self, text: str, *, add_special_tokens: bool, truncation: bool = False, verbose: bool = True
    ) -> list[int]:
        assert not add_special_tokens
        assert not truncation
        if text == "PROMPT: ":
            return [1, 2]
        if text == "\nRESPONSE: ":
            return [3, 4]
        return [ord(char) for char in text]

    def num_special_tokens_to_add(self, *, pair: bool) -> int:
        assert not pair
        return 2


def _embedder_with_tokenizer() -> _BgeSmallEmbedder:
    embedder = _BgeSmallEmbedder()
    embedder._tokenizer = _Tokenizer()
    return embedder


@pytest.mark.parametrize("response", ["", "short", "x" * 500 + "TAIL", "\u6f22\u5b57 \u00e9 \U0001f600 " * 200])
def test_response_windows_cover_tail_and_respect_budget(response: str) -> None:
    embedder = _embedder_with_tokenizer()
    windows, truncated = embedder._response_windows(
        objective="context" * 2000,
        response=response,
        max_input_tokens=32,
        chunk_overlap_tokens=4,
        max_objective_tokens=8,
    )
    windows = list(windows)
    assert truncated
    assert all(len(window) <= 32 for window in windows)
    response_windows = [window[13:-1] for window in windows]
    restored = response_windows[0] + [token for window in response_windows[1:] for token in window[4:]]
    assert restored == [ord(char) for char in response.strip()]
    assert all(window[0] == 5 and window[-1] == 6 for window in windows)


def test_long_objective_retains_different_responses() -> None:
    embedder = _embedder_with_tokenizer()
    args = {
        "objective": "context " * 2000,
        "max_input_tokens": 32,
        "chunk_overlap_tokens": 4,
        "max_objective_tokens": 8,
    }
    first, _ = embedder._response_windows(response="I cannot help.", **args)
    second, _ = embedder._response_windows(response="Here is the answer.", **args)
    assert list(first) != list(second)


@requires_transformers
@pytest.mark.parametrize("objective", [None, "a short objective"])
def test_real_tokenizer_short_input_parity(objective: str | None) -> None:
    from transformers import BertTokenizer

    tokens = [
        "[PAD]",
        "[UNK]",
        "[CLS]",
        "[SEP]",
        "[MASK]",
        "prompt",
        ":",
        "response",
        "a",
        "short",
        "objective",
        "i",
        "cannot",
        "help",
        "with",
        "that",
        ".",
    ]
    tokenizer = BertTokenizer(vocab={token: index for index, token in enumerate(tokens)})
    embedder = _BgeSmallEmbedder()
    embedder._tokenizer = tokenizer
    response = "I cannot help with that."
    windows, truncated = embedder._response_windows(
        objective=objective, response=response, max_input_tokens=512, chunk_overlap_tokens=64, max_objective_tokens=128
    )
    assert not truncated
    assert list(windows) == [tokenizer.encode(_format_training_text(objective=objective, response=response))]


@requires_transformers
def test_real_tokenizer_special_tokens_and_full_tail() -> None:
    from transformers import BertTokenizer

    tokens = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "prompt", ":", "response", "context", "word", "tail"]
    tokenizer = BertTokenizer(vocab={token: index for index, token in enumerate(tokens)})
    embedder = _BgeSmallEmbedder()
    embedder._tokenizer = tokenizer
    response = "word " * 1000 + "tail"
    windows, truncated = embedder._response_windows(
        objective="context " * 2000,
        response=response,
        max_input_tokens=512,
        chunk_overlap_tokens=64,
        max_objective_tokens=128,
    )
    windows = list(windows)
    assert truncated
    assert all(len(window) <= 512 for window in windows)
    assert all(window[0] == tokenizer.cls_token_id and window[-1] == tokenizer.sep_token_id for window in windows)
    response_windows = [window[133:-1] for window in windows]
    restored = response_windows[0] + [token for window in response_windows[1:] for token in window[64:]]
    assert restored == tokenizer.encode(response, add_special_tokens=False)
    assert windows[-1][-2] == tokenizer.convert_tokens_to_ids("tail")


@pytest.mark.parametrize(
    "settings",
    [
        {"max_input_tokens": 513},
        {"max_input_tokens": 0},
        {"max_objective_tokens": -1},
        {"chunk_overlap_tokens": -1},
        {"max_input_tokens": 198},
        {"max_objective_tokens": 512},
    ],
)
def test_invalid_token_settings_raise(settings: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="tokens|Token"):
        LocalViolenceClassifierScorer(**settings)


def test_tokenizer_overhead_is_checked() -> None:
    embedder = _embedder_with_tokenizer()
    with pytest.raises(ValueError, match="response tokens"):
        embedder._response_windows(
            objective="long context",
            response="response",
            max_input_tokens=12,
            chunk_overlap_tokens=4,
            max_objective_tokens=8,
        )


@pytest.mark.parametrize("probabilities", [[0.1, 0.9], [0.1, 0.2], [0.2, 0.5]])
def test_response_prediction_uses_maximum(probabilities: list[float]) -> None:
    embedder = _embedder_with_tokenizer()
    head = MagicMock(spec=module._TrainedHead)
    with (
        patch.object(embedder, "_embed_token_ids", return_value=[[0.0], [1.0]]) as encode,
        patch.object(module, "_predict_probability", side_effect=probabilities),
    ):
        prediction = embedder._predict_response(
            head=head,
            objective=None,
            response="a" * 30,
            max_input_tokens=26,
            chunk_overlap_tokens=4,
            max_objective_tokens=0,
        )
    assert encode.call_count == 1
    assert prediction.chunk_count == 2
    assert prediction.probability == max(probabilities)


def test_response_prediction_batches_and_propagates_errors() -> None:
    embedder = _embedder_with_tokenizer()
    head = MagicMock(spec=module._TrainedHead)
    with (
        patch.object(embedder, "_embed_token_ids", side_effect=lambda batch: [[0.0]] * len(batch)) as encode,
        patch.object(module, "_predict_probability", return_value=0.2),
    ):
        prediction = embedder._predict_response(
            head=head,
            objective=None,
            response="x" * 1000,
            max_input_tokens=26,
            chunk_overlap_tokens=0,
            max_objective_tokens=0,
        )
    assert prediction.chunk_count == 50
    assert [len(call.args[0]) for call in encode.call_args_list] == [32, 18]
    with patch.object(embedder, "_embed_token_ids", side_effect=[[[0.0]] * 32, RuntimeError("chunk failed")]):
        with patch.object(module, "_predict_probability", return_value=0.1):
            with pytest.raises(RuntimeError, match="chunk failed"):
                embedder._predict_response(
                    head=head,
                    objective=None,
                    response="x" * 1000,
                    max_input_tokens=26,
                    chunk_overlap_tokens=0,
                    max_objective_tokens=0,
                )


@pytest.mark.parametrize(
    ("probability", "band", "status"),
    [
        (0.9, (0.3, 0.7), ScoreStatus.COMPLETE),
        (0.1, (0.3, 0.7), ScoreStatus.COMPLETE),
        (0.5, (0.3, 0.7), ScoreStatus.UNDETERMINED),
        (0.3, (0.3, 0.7), ScoreStatus.UNDETERMINED),
        (0.7, (0.3, 0.7), ScoreStatus.UNDETERMINED),
        (0.5, None, ScoreStatus.COMPLETE),
    ],
)
def test_abstention_applies_after_maximum(
    probability: float, band: tuple[float, float] | None, status: ScoreStatus
) -> None:
    scorer = LocalViolenceClassifierScorer(abstain_band=band)
    piece = MessagePiece(role="assistant", original_value="text")
    prediction = _ChunkPrediction(probability=probability, chunk_count=3, objective_truncated=True)
    score = scorer._build_score(message_piece=piece, prediction=prediction, objective="objective")
    assert score.status is status
    assert score.message_piece_id == piece.id
    assert score.objective == "objective"
    assert score.score_metadata == {
        "max_chunk_probability": probability,
        "chunk_count": 3,
        "aggregation": "max",
        "objective_truncated": 1,
        **({"abstain_band_low": band[0], "abstain_band_high": band[1]} if band else {}),
    }
    if status is ScoreStatus.COMPLETE:
        assert score.get_value() == probability
    else:
        assert score.score_value is None


@requires_torch
def test_head_training_and_prediction_preserve_rng() -> None:
    import torch

    cpu_state = torch.random.get_rng_state().clone()
    cuda_states = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []
    embeddings, labels = _separable_training_data(rows_per_class=8)
    with patch.object(module, "_TRAINING_EPOCHS", 2):
        head = _train_head(embeddings=embeddings, labels=labels, seed=3)
    _predict_probability(head=head, embedding=embeddings[0])
    assert torch.equal(cpu_state, torch.random.get_rng_state())
    assert all(torch.equal(a, b) for a, b in zip(cuda_states, torch.cuda.get_rng_state_all(), strict=True))


@requires_torch
def test_local_initialization_matches_torch_defaults() -> None:
    import torch

    # Test-only reference initialization. Production must not use global save/restore.
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(3)
        expected = torch.nn.Sequential(torch.nn.Linear(384, 256), torch.nn.ReLU(), torch.nn.Linear(256, 1))
    actual = module._new_network(3)
    for key, value in expected.state_dict().items():
        assert torch.equal(value, actual.state_dict()[key])


@requires_torch
def test_parallel_training_uses_independent_generators() -> None:
    import torch

    embeddings, labels = _separable_training_data(rows_per_class=8)
    state = torch.random.get_rng_state().clone()
    with patch.object(module, "_TRAINING_EPOCHS", 2), ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(_train_head, embeddings=embeddings, labels=labels, seed=seed) for seed in (3, 5)]
        heads = [task.result() for task in tasks]
        references = [_train_head(embeddings=embeddings, labels=labels, seed=seed) for seed in (3, 5)]
    for head, reference in zip(heads, references, strict=True):
        for key, value in head.network.state_dict().items():
            assert torch.equal(value, reference.network.state_dict()[key])
        assert head.temperature == reference.temperature
    assert torch.equal(state, torch.random.get_rng_state())


async def test_default_evaluation_requires_explicit_mapping_async() -> None:
    scorer = LocalViolenceClassifierScorer()
    assert scorer.evaluation_file_mapping is None
    with pytest.raises(ValueError, match="No file_mapping"):
        await scorer.evaluate_async()


@pytest.mark.parametrize(
    ("error", "status", "value"),
    [
        ("blocked", ScoreStatus.COMPLETE, 0.0),
        ("processing", ScoreStatus.UNDETERMINED, None),
    ],
)
@pytest.mark.parametrize("data_type", ["text", "error"])
async def test_fallback_has_violence_semantics_async(
    error: str, status: ScoreStatus, value: float | None, data_type: str
) -> None:
    from pyrit.models import Message

    scorer = LocalViolenceClassifierScorer()
    piece = MessagePiece(
        role="assistant", original_value="blocked or failed", original_value_data_type=data_type, response_error=error
    )
    with patch.object(scorer, "_score_piece_async", new_callable=AsyncMock) as score_piece:
        scores = await scorer.score_message_async(message=Message(message_pieces=[piece]))
    assert scores[0].status is status
    if value is not None:
        assert scores[0].get_value() == value
    score_piece.assert_not_called()


@pytest.mark.parametrize("attribute", ["_TRAINING_EPOCHS", "_TRUNCATION_CHARS", "_CALIBRATION_FOLDS"])
def test_training_recipe_changes_identity(attribute: str) -> None:
    baseline = LocalViolenceClassifierScorer().get_identifier()
    with patch.object(module, attribute, getattr(module, attribute) + 1):
        changed = LocalViolenceClassifierScorer().get_identifier()
    assert baseline.eval_hash != changed.eval_hash
    assert baseline.model_dump()["recipe_digest"] != changed.model_dump()["recipe_digest"]


def test_recipe_identity_is_stable_and_tracks_model_data_and_inference() -> None:
    baseline = LocalViolenceClassifierScorer().get_identifier()
    assert baseline == LocalViolenceClassifierScorer().get_identifier()
    with patch.object(_BgeSmallEmbedder, "DEFAULT_MODEL_REVISION", "different"):
        assert baseline.eval_hash != LocalViolenceClassifierScorer().get_identifier().eval_hash
    with patch.dict(module._TRAINING_DATASETS, {"violence.csv": "different"}):
        assert baseline.eval_hash != LocalViolenceClassifierScorer().get_identifier().eval_hash
    with patch.object(LocalViolenceClassifierScorer, "_RECIPE_VERSION", 2):
        assert baseline.eval_hash != LocalViolenceClassifierScorer().get_identifier().eval_hash
    assert baseline.eval_hash != LocalViolenceClassifierScorer(chunk_overlap_tokens=32).get_identifier().eval_hash


def test_explicit_registry_construction_uses_local_name() -> None:
    from pyrit.registry import ScorerRegistry

    ScorerRegistry.reset_registry_singleton()
    try:
        registry = ScorerRegistry.get_registry_singleton()
        assert not registry.instances.get_names()
        scorer = registry.create_named_instance(
            name="local_violence", type_name="LocalViolenceClassifierScorer", params={"chunk_overlap_tokens": 32}
        )
        assert isinstance(scorer, LocalViolenceClassifierScorer)
        assert scorer.get_identifier().model_dump()["recipe"]["chunk_overlap_tokens"] == 32
    finally:
        ScorerRegistry.reset_registry_singleton()


async def test_readable_partial_block_is_scored_async() -> None:
    from pyrit.models import Message

    scorer = LocalViolenceClassifierScorer()
    piece = MessagePiece(
        role="assistant",
        original_value="partial response",
        response_error="blocked",
        prompt_metadata={"partial_content": True},
    )
    with patch.object(scorer, "_score_piece_async", new_callable=AsyncMock, return_value=[]) as score_piece:
        await scorer.score_message_async(message=Message(message_pieces=[piece]))
    score_piece.assert_awaited_once()


# --- Embedder plumbing, exercised with stand-ins instead of the pinned model ---


class _FakeBatch(dict):
    """Tokenizer output that records the device it was moved to."""

    def to(self, device: str) -> "_FakeBatch":
        self.device = device
        return self


class _FakeEncoder:
    """Encoder whose CLS vector is the first input id repeated, so every row normalizes the same way."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def __call__(self, **encoded: object) -> MagicMock:
        import torch

        input_ids = encoded["input_ids"]
        assert isinstance(input_ids, torch.Tensor)
        self.batch_sizes.append(input_ids.shape[0])
        hidden = input_ids.float().unsqueeze(-1).repeat(1, 1, 2)
        return MagicMock(last_hidden_state=hidden)


class _PaddingTokenizer:
    """Pads to the longest row and encodes text as its length plus one."""

    def pad(self, features: dict, **kwargs: object) -> _FakeBatch:
        import torch

        assert kwargs == {"padding": True, "return_attention_mask": True, "return_tensors": "pt"}
        rows = features["input_ids"]
        width = max(len(row) for row in rows)
        ids = [row + [0] * (width - len(row)) for row in rows]
        mask = [[1] * len(row) + [0] * (width - len(row)) for row in rows]
        return _FakeBatch(input_ids=torch.tensor(ids), attention_mask=torch.tensor(mask))

    def __call__(self, texts: list[str], **kwargs: object) -> _FakeBatch:
        import torch

        assert kwargs["truncation"] is True and kwargs["max_length"] == _BgeSmallEmbedder.MAX_LENGTH
        return _FakeBatch(input_ids=torch.tensor([[len(text) + 1, 1] for text in texts]))


def _loaded_fake_embedder() -> tuple[_BgeSmallEmbedder, _FakeEncoder]:
    embedder = _BgeSmallEmbedder()
    encoder = _FakeEncoder()
    embedder._tokenizer = _PaddingTokenizer()
    embedder._model = encoder
    embedder._device = "cpu"
    return embedder, encoder


async def test_embedder_loads_the_model_once_async() -> None:
    embedder = _BgeSmallEmbedder()

    def fake_load() -> None:
        embedder._model, embedder._tokenizer = object(), object()

    assert not embedder._is_loaded
    with patch.object(embedder, "_load_model", side_effect=fake_load) as load:
        await embedder.load_model_async()
        await embedder.load_model_async()
    assert load.call_count == 1
    assert embedder._is_loaded


async def test_embed_async_skips_empty_input_and_delegates_async() -> None:
    embedder = _BgeSmallEmbedder()
    with (
        patch.object(embedder, "load_model_async", AsyncMock()) as load,
        patch.object(embedder, "_embed", return_value=[[1.0]]) as embed,
    ):
        assert await embedder.embed_async(texts=[]) == []
        load.assert_not_awaited()
        assert await embedder.embed_async(texts=("only",)) == [[1.0]]
    load.assert_awaited_once()
    embed.assert_called_once_with(["only"])


async def test_predict_response_async_loads_then_delegates_async() -> None:
    embedder = _BgeSmallEmbedder()
    head = MagicMock(spec=module._TrainedHead)
    expected = _ChunkPrediction(probability=0.4, chunk_count=2, objective_truncated=False)
    settings = {"max_input_tokens": 64, "chunk_overlap_tokens": 4, "max_objective_tokens": 8}
    with (
        patch.object(embedder, "load_model_async", AsyncMock()) as load,
        patch.object(embedder, "_predict_response", return_value=expected) as predict,
    ):
        result = await embedder.predict_response_async(head=head, objective="o", response="r", **settings)
    assert result is expected
    load.assert_awaited_once()
    predict.assert_called_once_with(head=head, objective="o", response="r", **settings)


def test_response_windows_require_a_loaded_tokenizer() -> None:
    with pytest.raises(RuntimeError, match="tokenizer is not loaded"):
        _BgeSmallEmbedder()._response_windows(
            objective=None, response="r", max_input_tokens=64, chunk_overlap_tokens=4, max_objective_tokens=8
        )


def test_response_windows_require_cls_and_sep_tokens() -> None:
    embedder = _embedder_with_tokenizer()
    embedder._tokenizer.sep_token_id = None
    with pytest.raises(ValueError, match="CLS and SEP"):
        embedder._response_windows(
            objective=None, response="r", max_input_tokens=64, chunk_overlap_tokens=4, max_objective_tokens=8
        )


@requires_torch
def test_embed_token_ids_pads_moves_and_normalizes() -> None:
    embedder, encoder = _loaded_fake_embedder()
    embeddings = embedder._embed_token_ids([[5, 9, 6], [5, 6]])
    assert encoder.batch_sizes == [2]
    for embedding in embeddings:
        assert embedding == pytest.approx([2**-0.5, 2**-0.5])


@requires_torch
def test_embed_batches_and_normalizes() -> None:
    embedder, encoder = _loaded_fake_embedder()
    texts = [f"text {i}" for i in range(_BgeSmallEmbedder.BATCH_SIZE + 8)]
    embeddings = embedder._embed(texts)
    assert encoder.batch_sizes == [_BgeSmallEmbedder.BATCH_SIZE, 8]
    assert len(embeddings) == len(texts)
    assert all(embedding == pytest.approx([2**-0.5, 2**-0.5]) for embedding in embeddings)


@requires_torch
@pytest.mark.parametrize("method", ["_embed_token_ids", "_embed"])
def test_embedding_requires_a_loaded_model(method: str) -> None:
    argument = [[5, 6]] if method == "_embed_token_ids" else ["text"]
    with pytest.raises(RuntimeError, match="model is not loaded"):
        getattr(_BgeSmallEmbedder(), method)(argument)


@requires_torch
@requires_transformers
@pytest.mark.parametrize(("requested", "expected"), [(None, "cpu"), ("cuda:1", "cuda:1")])
def test_load_model_pins_the_revision_and_picks_a_device(requested: str | None, expected: str) -> None:
    tokenizer = object()
    model = MagicMock()
    model.to.return_value = model
    embedder = _BgeSmallEmbedder(device=requested)
    with (
        patch("transformers.AutoTokenizer.from_pretrained", return_value=tokenizer) as load_tokenizer,
        patch("transformers.AutoModel.from_pretrained", return_value=model) as load_model,
        patch("torch.cuda.is_available", return_value=False),
    ):
        embedder._load_model()
    pinned = {"revision": _BgeSmallEmbedder.DEFAULT_MODEL_REVISION}
    load_tokenizer.assert_called_once_with(_BgeSmallEmbedder.DEFAULT_MODEL_ID, **pinned)
    load_model.assert_called_once_with(_BgeSmallEmbedder.DEFAULT_MODEL_ID, **pinned)
    model.to.assert_called_once_with(expected)
    model.eval.assert_called_once_with()
    assert embedder._tokenizer is tokenizer and embedder._model is model and embedder._device == expected


def test_load_model_explains_the_missing_extra() -> None:
    with patch.dict("sys.modules", {"transformers": None}), pytest.raises(RuntimeError, match="huggingface"):
        _BgeSmallEmbedder()._load_model()


def test_load_training_rows_skips_rows_without_scores_or_response(tmp_path: Path) -> None:
    body = (
        b"# dataset_version=1.0\n"
        b"objective,assistant_response,human_score_1\n"
        b"kept,a violent answer,1.0\n"
        b"no score,an answer,\n"
        b"no response,,0.0\n"
    )
    (tmp_path / "violence.csv").write_bytes(body)
    with (
        patch.object(module, "_HARM_EVALS_PATH", tmp_path),
        patch.object(module, "_TRAINING_DATASETS", {"violence.csv": hashlib.sha256(body).hexdigest()}),
    ):
        texts, labels = _load_training_rows()
    assert texts == [_format_training_text(objective="kept", response="a violent answer")]
    assert labels == [1]


@requires_torch
def test_predict_probability_rejects_a_non_finite_head() -> None:
    import torch

    head = module._TrainedHead(
        feature_mean=(0.0,) * 384,
        feature_std=(1.0,) * 384,
        network=lambda features: torch.full((features.shape[0], 1), float("nan")),
        temperature=1.0,
        training_rows=1,
    )
    with pytest.raises(ValueError, match="non-finite"):
        _predict_probability(head=head, embedding=[0.0] * 384)


async def test_scorer_trains_its_head_once_async() -> None:
    scorer = LocalViolenceClassifierScorer()
    head = MagicMock(spec=module._TrainedHead, training_rows=2, temperature=1.0)
    scorer._embedder = MagicMock(spec=_BgeSmallEmbedder)
    scorer._embedder.embed_async = AsyncMock(return_value=[[0.0], [1.0]])
    with (
        patch.object(module, "_load_training_rows", return_value=(["a", "b"], [0, 1])) as load_rows,
        patch.object(module, "_train_head", return_value=head) as train,
    ):
        await scorer.load_model_async()
        await scorer.load_model_async()
    load_rows.assert_called_once_with()
    scorer._embedder.embed_async.assert_awaited_once_with(texts=["a", "b"])
    train.assert_called_once_with(embeddings=[[0.0], [1.0]], labels=[0, 1], seed=scorer._seed)
    assert scorer._head is head
