# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.score._classifiers.hugging_face import _HuggingFaceSequenceClassifier


def _fake_runtime_modules() -> tuple[ModuleType, ModuleType, MagicMock, MagicMock, MagicMock, MagicMock]:
    tokenizer = MagicMock()
    input_tensor = MagicMock()
    input_tensor.to.return_value = input_tensor
    tokenizer.return_value = {"input_ids": input_tensor}

    logits = MagicMock()
    logits.ndim = 2
    logits.shape = (1, 3)
    logits.float.return_value.cpu.return_value.tolist.return_value = [[-1.0, 0.0, 1.0]]

    model = MagicMock()
    model.to.return_value = model
    model.config.id2label = {2: "third", 0: "first", 1: "second"}
    model.return_value = SimpleNamespace(logits=logits)

    tokenizer_factory = MagicMock(return_value=tokenizer)
    model_factory = MagicMock(return_value=model)
    transformers = ModuleType("transformers")
    transformers.AutoTokenizer = SimpleNamespace(from_pretrained=tokenizer_factory)
    transformers.AutoModelForSequenceClassification = SimpleNamespace(from_pretrained=model_factory)

    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    torch.inference_mode = nullcontext
    return torch, transformers, tokenizer_factory, model_factory, tokenizer, model


def test_classifier_requires_exactly_one_location() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _HuggingFaceSequenceClassifier()
    with pytest.raises(ValueError, match="exactly one"):
        _HuggingFaceSequenceClassifier(model_id="org/model", model_path="model")


def test_classifier_rejects_revision_for_local_path() -> None:
    with pytest.raises(ValueError, match="only supported with model_id"):
        _HuggingFaceSequenceClassifier(model_path="model", revision="abc123")


async def test_classifier_loads_lazily_and_owns_inference_options() -> None:
    torch, transformers, tokenizer_factory, model_factory, tokenizer, model = _fake_runtime_modules()
    classifier = _HuggingFaceSequenceClassifier(
        model_id="org/model",
        revision="abc123",
        cache_dir="cache",
        tokenizer_kwargs={"truncation_side": "left"},
        tokenization_options={"max_length": 512, "truncation": True},
    )

    assert not classifier._is_loaded
    with (
        patch.dict("os.environ", {"HUGGINGFACE_TOKEN": "environment-token"}),
        patch.dict(sys.modules, {"torch": torch, "transformers": transformers}),
    ):
        first = await classifier.predict_logits_async(texts=["hello"])
        second = await classifier.predict_logits_async(texts=["again"])

    assert classifier._is_loaded
    assert first.logits == ((-1.0, 0.0, 1.0),)
    assert first.labels == ("first", "second", "third")
    assert second.labels == first.labels
    tokenizer_factory.assert_called_once_with(
        "org/model",
        cache_dir="cache",
        local_files_only=False,
        revision="abc123",
        token="environment-token",
        trust_remote_code=False,
        truncation_side="left",
    )
    model_factory.assert_called_once()
    tokenizer.assert_called_with(
        ["again"],
        return_tensors="pt",
        max_length=512,
        truncation=True,
    )
    model.eval.assert_called_once()


async def test_classifier_empty_batch_does_not_load() -> None:
    classifier = _HuggingFaceSequenceClassifier(model_id="org/model")

    result = await classifier.predict_logits_async(texts=[])

    assert result.logits == ()
    assert result.labels == ()
    assert not classifier._is_loaded


async def test_load_model_async_is_single_flight() -> None:
    classifier = _HuggingFaceSequenceClassifier(model_id="org/model")

    def _load_model() -> None:
        classifier._model = MagicMock()
        classifier._tokenizer = MagicMock()

    with patch.object(classifier, "_load_model", side_effect=_load_model) as load_model:
        await asyncio.gather(classifier.load_model_async(), classifier.load_model_async())

    load_model.assert_called_once()
