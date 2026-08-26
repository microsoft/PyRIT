# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private Hugging Face sequence-classification runtime."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class _HuggingFaceSequenceClassificationResult:
    """Raw sequence-classification logits and their model-config label order."""

    logits: tuple[tuple[float, ...], ...]
    labels: tuple[str, ...]


class _HuggingFaceSequenceClassifier:
    """Run local Hugging Face sequence classification without blocking the event loop."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        model_path: str | Path | None = None,
        revision: str | None = None,
        token: str | None = None,
        cache_dir: str | Path | None = None,
        local_files_only: bool = False,
        trust_remote_code: bool = False,
        device: str | None = None,
        torch_dtype: Any | None = None,
        model_kwargs: Mapping[str, Any] | None = None,
        tokenizer_kwargs: Mapping[str, Any] | None = None,
        tokenization_options: Mapping[str, Any] | None = None,
    ) -> None:
        """
        Initialize a lazily loaded sequence classifier.

        Args:
            model_id (str | None): Hugging Face Hub model ID.
            model_path (str | Path | None): Local model directory.
            revision (str | None): Optional Hub model revision.
            token (str | None): Optional Hugging Face token. Defaults to ``HUGGINGFACE_TOKEN``.
            cache_dir (str | Path | None): Optional Hugging Face cache directory.
            local_files_only (bool): Require model assets to exist locally.
            trust_remote_code (bool): Allow custom model code from the model repository.
            device (str | None): Torch device. Defaults to CUDA when available, otherwise CPU.
            torch_dtype (Any | None): Optional dtype forwarded to the model loader.
            model_kwargs (Mapping[str, Any] | None): Additional model loader options.
            tokenizer_kwargs (Mapping[str, Any] | None): Additional tokenizer loader options.
            tokenization_options (Mapping[str, Any] | None): Options applied to every inference batch.

        Raises:
            ValueError: If neither or both model locations are provided, or if a revision is
                attached to a local directory.
        """
        if bool(model_id) == bool(model_path):
            raise ValueError("Provide exactly one of model_id or model_path.")
        if model_path is not None and revision is not None:
            raise ValueError("revision is only supported with model_id.")

        self._model_name_or_path = model_id or str(model_path)
        self._revision = revision
        self._token = token
        self._cache_dir = cache_dir
        self._local_files_only = local_files_only
        self._trust_remote_code = trust_remote_code
        self._requested_device = device
        self._torch_dtype = torch_dtype
        self._model_kwargs = dict(model_kwargs or {})
        self._tokenizer_kwargs = dict(tokenizer_kwargs or {})
        self._tokenization_options = dict(tokenization_options or {})
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._device: str | None = None
        self._load_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()

    async def load_model_async(self) -> None:
        """Download as needed and load the tokenizer and model exactly once."""
        if self._is_loaded:
            return
        async with self._load_lock:
            if self._is_loaded:
                return
            await asyncio.to_thread(self._load_model)

    async def predict_logits_async(
        self,
        *,
        texts: Sequence[str],
    ) -> _HuggingFaceSequenceClassificationResult:
        """
        Classify a batch of texts and return unnormalized logits.

        Args:
            texts (Sequence[str]): Texts to classify in one model forward pass.

        Returns:
            _HuggingFaceSequenceClassificationResult: Raw logits and label ordering.
        """
        if not texts:
            return _HuggingFaceSequenceClassificationResult(logits=(), labels=())

        await self.load_model_async()
        async with self._inference_lock:
            return await asyncio.to_thread(self._predict_logits, list(texts))

    @property
    def _is_loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    def _get_from_pretrained_kwargs(self) -> dict[str, Any]:
        token = self._token or os.environ.get("HUGGINGFACE_TOKEN") or None
        options: dict[str, Any] = {
            "local_files_only": self._local_files_only,
            "token": token,
            "trust_remote_code": self._trust_remote_code,
        }
        if self._cache_dir is not None:
            options["cache_dir"] = str(self._cache_dir)
        if self._revision is not None:
            options["revision"] = self._revision
        return options

    def _load_model(self) -> None:
        try:
            import torch
            from transformers import (
                AutoModelForSequenceClassification,  # type: ignore[ty:possibly-missing-import]
                AutoTokenizer,  # type: ignore[ty:possibly-missing-import]
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeError(
                "Local Hugging Face inference requires the 'huggingface' extra. "
                "Install it with `pip install pyrit[huggingface]`."
            ) from exc

        common_options = self._get_from_pretrained_kwargs()
        tokenizer_options = {**common_options, **self._tokenizer_kwargs}
        model_options = {**common_options, **self._model_kwargs}
        if self._torch_dtype is not None:
            model_options["torch_dtype"] = self._torch_dtype

        tokenizer = AutoTokenizer.from_pretrained(self._model_name_or_path, **tokenizer_options)
        model = AutoModelForSequenceClassification.from_pretrained(
            self._model_name_or_path,
            **model_options,
        )
        device = self._requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = tokenizer
        self._model = model.to(device)
        self._model.eval()
        self._device = device

    def _predict_logits(self, texts: list[str]) -> _HuggingFaceSequenceClassificationResult:
        import torch

        tokenizer = self._tokenizer
        model = self._model
        if tokenizer is None or model is None or self._device is None:
            raise RuntimeError("The Hugging Face model is not loaded.")

        encoded = tokenizer(
            texts,
            return_tensors="pt",
            **self._tokenization_options,
        )
        encoded_on_device = {name: tensor.to(self._device) for name, tensor in encoded.items()}
        with torch.inference_mode():
            logits_tensor = model(**encoded_on_device).logits

        if logits_tensor.ndim != 2 or logits_tensor.shape[0] != len(texts):
            raise ValueError(f"Expected logits shape ({len(texts)}, labels), got {tuple(logits_tensor.shape)}.")

        logits = tuple(tuple(float(value) for value in row) for row in logits_tensor.float().cpu().tolist())
        labels = self._get_labels(label_count=len(logits[0]))
        return _HuggingFaceSequenceClassificationResult(logits=logits, labels=labels)

    def _get_labels(self, *, label_count: int) -> tuple[str, ...]:
        model = self._model
        if model is None:
            raise RuntimeError("The Hugging Face model is not loaded.")
        id_to_label = getattr(model.config, "id2label", None)
        if isinstance(id_to_label, Mapping) and len(id_to_label) == label_count:
            try:
                ordered = sorted(id_to_label.items(), key=lambda item: int(item[0]))
            except (TypeError, ValueError):
                ordered = []
            if ordered:
                return tuple(str(label) for _, label in ordered)
        return tuple(f"LABEL_{index}" for index in range(label_count))
