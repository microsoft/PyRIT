# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.providers import HuggingFaceModelSource, HuggingFaceSequenceClassifier


def test_model_source_requires_exactly_one_location():
    with pytest.raises(ValueError, match="exactly one"):
        HuggingFaceModelSource()
    with pytest.raises(ValueError, match="exactly one"):
        HuggingFaceModelSource(model_id="org/model", model_path="model")


def test_model_source_rejects_revision_for_local_path():
    with pytest.raises(ValueError, match="only supported with model_id"):
        HuggingFaceModelSource(model_path="model", revision="abc123")


def test_model_source_builds_remote_options_from_environment():
    source = HuggingFaceModelSource(
        model_id="org/model",
        revision="abc123",
        cache_dir=Path("cache"),
        local_files_only=True,
    )

    with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": "environment-token"}):
        options = source.get_from_pretrained_kwargs()

    assert source.model_name_or_path == "org/model"
    assert options == {
        "cache_dir": "cache",
        "local_files_only": True,
        "revision": "abc123",
        "token": "environment-token",
        "trust_remote_code": False,
    }


def _fake_runtime_modules() -> tuple[ModuleType, ModuleType, MagicMock, MagicMock, MagicMock]:
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
    model.config.id2label = {
        2: "third",
        0: "first",
        1: "second",
    }
    model.return_value = SimpleNamespace(logits=logits)

    tokenizer_factory = MagicMock(return_value=tokenizer)
    model_factory = MagicMock(return_value=model)
    transformers = ModuleType("transformers")
    transformers.AutoTokenizer = SimpleNamespace(from_pretrained=tokenizer_factory)
    transformers.AutoModelForSequenceClassification = SimpleNamespace(from_pretrained=model_factory)

    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    torch.inference_mode = nullcontext
    return torch, transformers, tokenizer_factory, model_factory, model


async def test_sequence_classifier_loads_lazily_and_returns_ordered_logits():
    torch, transformers, tokenizer_factory, model_factory, model = _fake_runtime_modules()
    runtime = HuggingFaceSequenceClassifier(
        source=HuggingFaceModelSource(model_id="org/model", revision="abc123"),
        tokenizer_kwargs={"truncation_side": "left"},
    )

    assert not runtime.is_loaded
    with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
        first = await runtime.predict_logits_async(
            texts=["hello"],
            tokenization_options={"max_length": 512, "truncation": True},
        )
        second = await runtime.predict_logits_async(texts=["again"])

    assert runtime.is_loaded
    assert runtime.device == "cpu"
    assert first.logits == ((-1.0, 0.0, 1.0),)
    assert first.labels == ("first", "second", "third")
    assert second.labels == first.labels
    tokenizer_factory.assert_called_once_with(
        "org/model",
        local_files_only=False,
        revision="abc123",
        token=None,
        trust_remote_code=False,
        truncation_side="left",
    )
    model_factory.assert_called_once()
    model.eval.assert_called_once()


async def test_sequence_classifier_empty_batch_does_not_load():
    runtime = HuggingFaceSequenceClassifier(source=HuggingFaceModelSource(model_id="org/model"))

    result = await runtime.predict_logits_async(texts=[])

    assert result.logits == ()
    assert result.labels == ()
    assert not runtime.is_loaded


async def test_load_model_async_is_single_flight():
    runtime = HuggingFaceSequenceClassifier(source=HuggingFaceModelSource(model_id="org/model"))

    def _load_model() -> None:
        runtime._model = MagicMock()
        runtime._tokenizer = MagicMock()

    with patch.object(runtime, "_load_model", side_effect=_load_model) as load_model:
        await asyncio.gather(runtime.load_model_async(), runtime.load_model_async())

    load_model.assert_called_once()
