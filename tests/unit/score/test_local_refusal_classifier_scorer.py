# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import builtins
import hashlib
import importlib.util
import json
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import ContentScorable, MessagePiece, ScoreStatus, ScoringExpectation
from pyrit.score import LocalRefusalClassifierScorer
from pyrit.score.true_false import local_refusal_classifier_scorer as module
from pyrit.score.true_false.local_refusal_classifier_scorer import (
    _ChunkPrediction,
    _format_response,
    _LayaEncoder,
    _load_training_rows,
    _predict_probability,
    _train_head,
    _TrainedHead,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")
requires_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="requires torch")
requires_transformers = pytest.mark.skipif(
    importlib.util.find_spec("transformers") is None, reason="requires transformers"
)


def _head() -> _TrainedHead:
    return _TrainedHead(feature_mean=(0.0,), feature_std=(1.0,), weights=(1.0,), intercept=0.0, training_rows=169)


def _scorer(
    *, probabilities: tuple[float, ...], abstain_band: tuple[float, float] | None = (0.2, 0.8)
) -> LocalRefusalClassifierScorer:
    scorer = LocalRefusalClassifierScorer(abstain_band=abstain_band)
    scorer._head = _head()
    scorer._encoder = MagicMock(spec=_LayaEncoder)
    scorer._encoder.predict_response_async = AsyncMock(
        return_value=_ChunkPrediction(probabilities=probabilities, objective_truncated=True)
    )
    return scorer


def test_legacy_training_format_and_pins() -> None:
    assert _format_response("abc") == "abc"
    assert _format_response("x" * 900) == "x" * 400
    state = module._build_state(objective="x" * 2000, response="no")
    assert list(state) == ["response", "objective"]
    assert state["objective"] == "x" * 1000
    pairs, labels = _load_training_rows()
    assert len(pairs) == len(labels) == 169
    assert set(labels) == {0, 1}
    assert all(len(response) <= 400 for _, response in pairs)
    assert LocalRefusalClassifierScorer.compute_dataset_hashes() == module._TRAINING_DATASETS


def test_training_rejects_changed_data(tmp_path: Path) -> None:
    data = b"objective,assistant_response,human_score,data_type\ncontext,no,1,text\n"
    for name in module._TRAINING_DATASETS:
        (tmp_path / name).write_bytes(data)
    with patch.object(module, "_REFUSAL_EVALS_PATH", tmp_path):
        with pytest.raises(RuntimeError, match="does not match the SHA-256"):
            _load_training_rows()
        pins = dict.fromkeys(module._TRAINING_DATASETS, hashlib.sha256(data).hexdigest())
        with patch.object(module, "_TRAINING_DATASETS", pins):
            assert _load_training_rows() == ([("context", "no")] * 2, [1, 1])


@requires_torch
def test_training_is_deterministic_and_preserves_rng() -> None:
    import torch

    features = [[4.0], [-4.0]] * 150
    labels = [1, 0] * 150
    before = torch.get_rng_state().clone()
    first = _train_head(features=features, labels=labels)
    second = _train_head(features=features, labels=labels)
    assert first == second
    assert torch.equal(torch.get_rng_state(), before)
    assert _predict_probability(head=first, features=[4.0]) > 0.8
    assert _predict_probability(head=first, features=[-4.0]) < 0.2


@requires_torch
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_head_rejects_nonfinite_logits(invalid: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        _predict_probability(head=replace(_head(), intercept=invalid), features=[1.0])


@pytest.mark.parametrize(
    ("probabilities", "band", "expected"),
    [
        ((0.9,), (0.2, 0.8), True),
        ((0.1,), (0.2, 0.8), False),
        ((0.9, 0.95), (0.2, 0.8), True),
        ((0.1, 0.05), (0.2, 0.8), False),
        ((0.1, 0.95), (0.2, 0.8), None),
        ((0.95, 0.1), (0.2, 0.8), None),
        ((0.9, 0.7), (0.2, 0.8), None),
        ((0.1, 0.3), (0.2, 0.8), None),
        ((0.2,), (0.2, 0.8), None),
        ((0.8,), (0.2, 0.8), None),
        ((0.5,), None, True),
        ((0.49, 0.5), None, None),
        ((0.1, 0.95), None, None),
        ((0.9, 0.7), None, True),
        ((0.3, 0.4), (0.1, 0.2), False),
    ],
)
async def test_chunk_verdicts_require_agreement_async(
    probabilities: tuple[float, ...], band: tuple[float, float] | None, expected: bool | None
) -> None:
    scorer = _scorer(probabilities=probabilities, abstain_band=band)
    expectation = ScoringExpectation(objective="the objective")
    (score,) = await scorer.score_async(scorable=ContentScorable(value="response"), expectation=expectation)
    assert score.score_category == ["refusal"]
    assert score.scored_expectation == expectation
    assert score.scorable is not None
    assert score.is_undetermined == (expected is None)
    if expected is not None:
        assert score.get_value() is expected
    else:
        assert score.score_value is None
    assert score.score_metadata["aggregation"] == "unanimous"
    assert score.score_metadata["chunk_count"] == len(probabilities)
    assert score.score_metadata["min_chunk_probability"] == min(probabilities)
    assert score.score_metadata["max_chunk_probability"] == max(probabilities)
    assert score.score_metadata["objective_truncated"] == 1


@pytest.mark.parametrize("probabilities", [(), (float("nan"),), (float("inf"),), (-0.1,), (1.1,)])
def test_invalid_probabilities_do_not_become_nonrefusal(probabilities: tuple[float, ...]) -> None:
    scorer = LocalRefusalClassifierScorer()
    with pytest.raises(ValueError, match="finite chunk probabilities"):
        scorer._build_score(
            message_piece=MessagePiece(role="assistant", original_value="response"),
            prediction=_ChunkPrediction(probabilities=probabilities, objective_truncated=False),
            objective=None,
        )


@pytest.mark.parametrize("objective", [None, "context " * 2000])
async def test_scoring_passes_full_response_async(objective: str | None) -> None:
    scorer = _scorer(probabilities=(0.1,))
    response = "x" * 1500 + "TAIL"
    await scorer.score_async(
        scorable=ContentScorable(value=response), expectation=ScoringExpectation(objective=objective)
    )
    args = scorer._encoder.predict_response_async.await_args.kwargs
    assert args["response"] == response
    assert args["objective"] == (objective or "")
    assert args["max_input_tokens"] == 512
    assert args["chunk_overlap_tokens"] == 64
    assert args["max_objective_tokens"] == 128


@pytest.mark.parametrize("data_type", ["text", "error"])
@pytest.mark.parametrize("structured", [False, True])
async def test_blocked_and_structured_refusals_skip_model_async(data_type: str, structured: bool) -> None:
    scorer = LocalRefusalClassifierScorer()
    piece = MessagePiece(
        role="assistant", original_value="blocked", original_value_data_type=data_type, response_error="blocked"
    )
    if structured:
        piece.mark_as_structured_refusal(refusal="I cannot assist.")
    with patch.object(scorer, "load_model_async", new_callable=AsyncMock) as load:
        (score,) = await scorer.score_message_async(message=piece.to_message())
    assert score.get_value() is True
    assert score.score_category == ["refusal"]
    load.assert_not_awaited()


async def test_partial_block_scores_emitted_content_async() -> None:
    scorer = _scorer(probabilities=(0.1,))
    piece = MessagePiece(
        role="assistant",
        original_value="Here is the answer.",
        response_error="blocked",
        prompt_metadata={"partial_content": "Here is the answer."},
    )
    (score,) = await scorer.score_message_async(message=piece.to_message())
    assert score.get_value() is False
    assert scorer._encoder.predict_response_async.await_args.kwargs["response"] == "Here is the answer."


async def test_transport_error_stays_undetermined_async() -> None:
    scorer = LocalRefusalClassifierScorer()
    piece = MessagePiece(
        role="assistant", original_value="failed", original_value_data_type="error", response_error="unknown"
    )
    with patch.object(scorer, "load_model_async", new_callable=AsyncMock) as load:
        (score,) = await scorer.score_message_async(message=piece.to_message())
    assert score.status is ScoreStatus.UNDETERMINED
    load.assert_not_awaited()


async def test_training_once_async() -> None:
    scorer = LocalRefusalClassifierScorer()
    scorer._encoder = MagicMock(spec=_LayaEncoder)
    scorer._encoder.features_async = AsyncMock(return_value=[[1.0]])
    with (
        patch.object(module, "_load_training_rows", return_value=([("o", "r")], [1])),
        patch.object(module, "_train_head", return_value=_head()) as train,
    ):
        await scorer.load_model_async()
        await scorer.load_model_async()
    train.assert_called_once()
    scorer._encoder.features_async.assert_awaited_once()


async def test_default_evaluation_requires_mapping_async() -> None:
    scorer = LocalRefusalClassifierScorer()
    assert scorer.evaluation_file_mapping is None
    with pytest.raises(ValueError, match="No file_mapping"):
        await scorer.evaluate_async()


@pytest.mark.parametrize("band", [(0.8, 0.2), (-0.1, 0.5), (0.5, 1.5), (0.4, 0.4)])
def test_invalid_band(band: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="abstain_band"):
        LocalRefusalClassifierScorer(abstain_band=band)


@pytest.mark.parametrize(
    "settings",
    [
        {"max_input_tokens": 513},
        {"max_input_tokens": 0},
        {"max_objective_tokens": -1},
        {"chunk_overlap_tokens": -1},
        {"max_objective_tokens": 512},
        {"chunk_overlap_tokens": 512},
    ],
)
def test_invalid_token_settings(settings: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        LocalRefusalClassifierScorer(**settings)


def test_recipe_is_retained_and_hashed() -> None:
    baseline = LocalRefusalClassifierScorer().get_identifier()
    recipe = baseline.params["recipe"]
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert baseline.params["recipe_digest"] == digest
    assert recipe["datasets"] == module._TRAINING_DATASETS
    assert baseline == LocalRefusalClassifierScorer().get_identifier()
    with patch.object(module, "_WEIGHT_DECAY", 20.0):
        assert baseline.eval_hash != LocalRefusalClassifierScorer().get_identifier().eval_hash
    with patch.dict(module._TRAINING_DATASETS, {"refusal.csv": "changed"}):
        assert baseline.eval_hash != LocalRefusalClassifierScorer().get_identifier().eval_hash
    for scorer in [
        LocalRefusalClassifierScorer(chunk_overlap_tokens=32),
        LocalRefusalClassifierScorer(max_objective_tokens=64),
        LocalRefusalClassifierScorer(max_input_tokens=400),
        LocalRefusalClassifierScorer(revision="different"),
        LocalRefusalClassifierScorer(abstain_band=None),
    ]:
        assert scorer.get_identifier().eval_hash != baseline.eval_hash


def test_explicit_registry_construction() -> None:
    from pyrit.registry import ScorerRegistry

    ScorerRegistry.reset_registry_singleton()
    try:
        registry = ScorerRegistry.get_registry_singleton()
        assert not registry.instances.get_names()
        scorer = registry.create_named_instance(
            name="local_refusal", type_name="LocalRefusalClassifierScorer", params={"chunk_overlap_tokens": 32}
        )
        assert isinstance(scorer, LocalRefusalClassifierScorer)
    finally:
        ScorerRegistry.reset_registry_singleton()


class _Tokenizer:
    mask_token = "[MASK]"

    def encode(self, text: str, *, add_special_tokens: bool, truncation: bool) -> list[int]:
        assert not add_special_tokens and not truncation
        return [ord(char) for char in text]


@pytest.fixture
def encoder() -> _LayaEncoder:
    encoder = _LayaEncoder()
    encoder._agent = SimpleNamespace(tok=_Tokenizer(), cfg={"max_len": 512})
    return encoder


@pytest.fixture
def common() -> Iterator[MagicMock]:
    fake = MagicMock()
    # Header includes the final separator, just as build_sequence(state="") does.
    fake.build_sequence.side_effect = lambda *args, **kwargs: ([1, 2, 3, 4, 5], [1, 2])
    with patch.dict(sys.modules, {"laya": ModuleType("laya"), "laya.common": fake}):
        yield fake


@pytest.mark.parametrize(
    "response", ["", "short", "x" * 2000 + "TAIL", 'line\n"quoted"\\text ' * 100, "\u6f22\u5b57\U0001f600" * 300]
)
def test_windows_cover_all_serialized_response_tokens(encoder: _LayaEncoder, common: MagicMock, response: str) -> None:
    windows, truncated = encoder._response_windows(
        objective="context " * 2000,
        response=response,
        max_input_tokens=96,
        chunk_overlap_tokens=8,
        max_objective_tokens=12,
    )
    windows = list(windows)
    assert truncated
    prefix_length = 4 + len('{"response": "')
    suffix_length = len('", "objective": "') + 12 + len('"}') + 1
    chunks = []
    for sequences in windows:
        assert len(sequences) == 2
        ids, markers = sequences[0]
        assert len(ids) <= 96 and markers == [1, 2]
        assert ids[-1] == 5
        chunks.append(ids[prefix_length:-suffix_length])
    restored = chunks[0] + [token for chunk in chunks[1:] for token in chunk[8:]]
    assert restored == [ord(char) for char in json.dumps(response, ensure_ascii=False)[1:-1]]
    assert common.build_sequence.call_args_list[0].kwargs["option_order"] == [0, 1]
    assert common.build_sequence.call_args_list[1].kwargs["option_order"] == [1, 0]


@requires_transformers
@pytest.mark.parametrize("objective", ["", "context " * 2000])
@pytest.mark.parametrize("long_response", [False, True])
def test_real_tokenizer_window_coverage(
    encoder: _LayaEncoder, common: MagicMock, objective: str, long_response: bool
) -> None:
    from transformers import BertTokenizer

    tokens = [
        "[PAD]",
        "[UNK]",
        "[CLS]",
        "[SEP]",
        "[MASK]",
        "response",
        "objective",
        "context",
        "word",
        "tail",
        "{",
        "}",
        '"',
        ":",
        ",",
        "\\",
        "n",
    ]
    tokenizer = BertTokenizer(vocab={token: index for index, token in enumerate(tokens)})
    encoder._agent.tok = tokenizer
    header = [tokenizer.cls_token_id, tokenizer.mask_token_id, tokenizer.mask_token_id, tokenizer.sep_token_id]
    common.build_sequence.side_effect = lambda *args, **kwargs: (header[:], [1, 2])
    response = ("word " * 1000 if long_response else "word ") + '"[MASK]"\n\\tail \u6f22\u5b57\U0001f600'
    windows, truncated = encoder._response_windows(
        objective=objective, response=response, max_input_tokens=512, chunk_overlap_tokens=64, max_objective_tokens=128
    )
    windows = list(windows)
    assert truncated == bool(objective)
    prefix = tokenizer.encode('{"response": "', add_special_tokens=False)
    objective_ids = tokenizer.encode(json.dumps(objective)[1:-1], add_special_tokens=False)[:128]
    suffix = (
        tokenizer.encode('", "objective": "', add_special_tokens=False)
        + objective_ids
        + tokenizer.encode('"}', add_special_tokens=False)
    )
    chunks = []
    for sequences in windows:
        assert len(sequences) == 2
        for ids, markers in sequences:
            assert len(ids) <= 512
            assert ids[0] == tokenizer.cls_token_id and ids[-1] == tokenizer.sep_token_id
            assert [ids[index] for index in markers] == [tokenizer.mask_token_id] * 2
        chunks.append(sequences[0][0][len(header) - 1 + len(prefix) : -(len(suffix) + 1)])
    restored = chunks[0] + [token for chunk in chunks[1:] for token in chunk[64:]]
    sanitized = json.dumps(response.replace("[MASK]", " "), ensure_ascii=False)[1:-1]
    assert restored == tokenizer.encode(sanitized, add_special_tokens=False)
    assert tokenizer.mask_token_id not in restored
    assert tokenizer.convert_tokens_to_ids("tail") in chunks[-1]


def test_missing_markers_raise(encoder: _LayaEncoder, common: MagicMock) -> None:
    common.build_sequence.side_effect = lambda *args, **kwargs: ([1, 2, 3], [1])
    with pytest.raises(ValueError, match="markers"):
        encoder._response_windows(
            objective="", response="r", max_input_tokens=96, chunk_overlap_tokens=8, max_objective_tokens=0
        )


def test_different_responses_survive_long_objective(encoder: _LayaEncoder, common: MagicMock) -> None:
    args = {
        "objective": "context " * 2000,
        "max_input_tokens": 96,
        "chunk_overlap_tokens": 8,
        "max_objective_tokens": 12,
    }
    first, _ = encoder._response_windows(response="I cannot help.", **args)
    second, _ = encoder._response_windows(response="Here is the answer.", **args)
    assert list(first) != list(second)


def test_runtime_budget_checks(encoder: _LayaEncoder, common: MagicMock) -> None:
    with pytest.raises(ValueError, match="framing"):
        encoder._response_windows(
            objective="", response="r", max_input_tokens=24, chunk_overlap_tokens=8, max_objective_tokens=0
        )
    encoder._agent.cfg["max_len"] = 64
    with pytest.raises(ValueError, match="checkpoint"):
        encoder._response_windows(
            objective="", response="r", max_input_tokens=96, chunk_overlap_tokens=8, max_objective_tokens=0
        )


def test_prediction_scores_every_window_and_propagates_errors(encoder: _LayaEncoder, common: MagicMock) -> None:
    args = {
        "head": _head(),
        "objective": "",
        "response": "x" * 900,
        "max_input_tokens": 96,
        "chunk_overlap_tokens": 8,
        "max_objective_tokens": 12,
    }
    with (
        patch.object(encoder, "_features_from_sequences", return_value=[1.0]) as features,
        patch.object(module, "_predict_probability", return_value=0.9),
    ):
        prediction = encoder._predict_response(**args)
    assert len(prediction.probabilities) == features.call_count > 1
    assert not prediction.objective_truncated
    with patch.object(encoder, "_features_from_sequences", side_effect=RuntimeError("inference failed")):
        with pytest.raises(RuntimeError, match="inference failed"):
            encoder._predict_response(**args)


async def test_predict_response_async_delegates_async(encoder: _LayaEncoder) -> None:
    prediction = _ChunkPrediction(probabilities=(0.9,), objective_truncated=False)
    with patch.object(encoder, "_predict_response", return_value=prediction) as predict:
        result = await encoder.predict_response_async(
            head=_head(),
            objective="",
            response="full response",
            max_input_tokens=512,
            chunk_overlap_tokens=64,
            max_objective_tokens=128,
        )
    assert result == prediction
    assert predict.call_args.kwargs["response"] == "full response"


async def test_encoder_loads_once_and_skips_empty_features_async() -> None:
    encoder = _LayaEncoder()
    assert await encoder.features_async(texts=[]) == []
    with patch.object(encoder, "_load_model", return_value=object()) as load:
        await encoder.load_model_async()
        await encoder.load_model_async()
    load.assert_called_once()


def test_load_pins_revision_and_supports_local_directory(tmp_path: Path) -> None:
    fake = MagicMock()
    with (
        patch.dict(sys.modules, {"laya": fake}),
        patch("huggingface_hub.snapshot_download", return_value="cache") as download,
    ):
        _LayaEncoder(device="cpu")._load_model()
        assert download.call_args.kwargs["revision"] == _LayaEncoder.DEFAULT_MODEL_REVISION
        assert download.call_args.kwargs["allow_patterns"] == list(module._CHECKPOINT_FILES)
        fake.load.assert_called_once_with("cache", device="cpu")
        download.reset_mock()
        _LayaEncoder(model_id=str(tmp_path))._load_model()
        download.assert_not_called()


def test_missing_package_error() -> None:
    original = builtins.__import__

    def import_without_laya(name: str, *args: object, **kwargs: object) -> object:
        if name == "laya":
            raise ModuleNotFoundError("laya")
        return original(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=import_without_laya):
        with pytest.raises(RuntimeError, match="pip install laya"):
            _LayaEncoder()._load_model()


@requires_torch
async def test_features_restore_option_order_and_average_async(common: MagicMock) -> None:
    import torch

    common.QTYPES = {"choice": 0}
    common.collate_items.return_value = {
        "input_ids": torch.tensor([[1, 2, 3, 4]]),
        "attention_mask": torch.tensor([[1, 1, 1, 1]]),
        "marker_pos": torch.tensor([[1, 2]]),
        "qtype": torch.tensor([0]),
    }
    model = MagicMock()
    model.encoder.return_value = SimpleNamespace(
        last_hidden_state=torch.tensor([[[0.0, 0.0], [1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]])
    )
    model.type_emb.return_value = torch.zeros((1, 2))
    model.head.layers = []
    encoder = _LayaEncoder()
    encoder._agent = SimpleNamespace(model=model, tok=SimpleNamespace(pad_token_id=0), device="cpu", cfg={})
    assert await encoder.features_async(texts=[("objective", "response")]) == [[2.0, 3.0, 2.0, 3.0]]
