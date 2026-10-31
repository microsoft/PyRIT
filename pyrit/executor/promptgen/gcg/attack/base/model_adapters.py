# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Model and tokenizer contracts GCG relies on, read through standard Hugging Face interfaces."""

import hashlib
import inspect
import json
from typing import Any

import torch


class GCGModelAdapter:
    """The parts of a causal language model that GCG reads, without architecture-specific branches."""

    def __init__(self, model: Any) -> None:
        """
        Wrap a loaded causal language model.

        Args:
            model (Any): The loaded causal language model.
        """
        self.model = model
        try:
            forward_parameters = inspect.signature(model.forward).parameters
        except (TypeError, ValueError):
            forward_parameters = {}
        self.supports_selective_logits = "logits_to_keep" in forward_parameters
        self.supports_prefix_cache = "past_key_values" in forward_parameters and "use_cache" in forward_parameters

    @property
    def embedding_matrix(self) -> torch.Tensor:
        """The input embedding matrix GCG computes token gradients through."""
        weight: torch.Tensor = self.model.get_input_embeddings().weight
        return weight

    @property
    def device(self) -> torch.device:
        """The device of the input embedding matrix."""
        return self.embedding_matrix.device

    @property
    def dtype(self) -> torch.dtype:
        """The dtype of the input embedding matrix."""
        return self.embedding_matrix.dtype

    @property
    def num_embeddings(self) -> int:
        """The number of token ids the model can embed."""
        return int(self.embedding_matrix.shape[0])

    def validate(self, *, model_path: str) -> None:
        """
        Check that GCG can compute token gradients through the input embeddings.

        Args:
            model_path (str): The model path, used in error messages.

        Raises:
            ValueError: If the model exposes no floating point input embedding matrix.
        """
        try:
            weight = self.embedding_matrix
        except (AttributeError, NotImplementedError):
            weight = None
        if not isinstance(weight, torch.Tensor):
            raise ValueError(
                f"Model {model_path!r} does not expose an input embedding matrix through get_input_embeddings(). "
                "GCG computes token gradients through that matrix."
            )
        if not weight.is_floating_point():
            raise ValueError(
                f"The input embeddings of {model_path!r} are {weight.dtype}, so GCG cannot compute token gradients "
                "through them. Load the model without quantizing the embedding layer."
            )


class GCGTokenizerAdapter:
    """The parts of a tokenizer that GCG reads when rendering prompts and sharing candidates across models."""

    def __init__(self, tokenizer: Any) -> None:
        """
        Wrap a tokenizer.

        Args:
            tokenizer (Any): The tokenizer paired with a GCG model.
        """
        self.tokenizer = tokenizer

    @property
    def vocab_size(self) -> int:
        """The size of the base vocabulary, without added tokens."""
        return int(self.tokenizer.vocab_size)

    def vocabulary_identity(self, *, num_embeddings: int) -> str:
        """
        Return a stable identity for the token ids a model with ``num_embeddings`` rows can sample.

        Two tokenizers with the same identity map every sampleable id to the same token, so
        their gradients can be summed. Added tokens past the embedding rows are ignored since
        they can never be sampled.

        Args:
            num_embeddings (int): The number of rows in the paired model's input embedding matrix.

        Returns:
            str: A SHA-256 hex digest of the sampleable ``(token, id)`` pairs.
        """
        vocab = sorted((i, token) for token, i in self.tokenizer.get_vocab().items() if i < num_embeddings)
        return hashlib.sha256(json.dumps(vocab).encode()).hexdigest()

    def validate(self, *, model_path: str) -> None:
        """
        Check that the tokenizer can return the character offsets GCG uses to locate prompt slices.

        Args:
            model_path (str): The model path, used in error messages.

        Raises:
            ValueError: If the tokenizer is not backed by the Hugging Face tokenizers library.
        """
        if not self.tokenizer.is_fast:
            raise ValueError(
                f"The tokenizer for {model_path!r} ({type(self.tokenizer).__name__}) is not backed by the Hugging "
                "Face tokenizers library, so it cannot return the character-to-token offsets GCG uses to locate the "
                "goal, control and target. Load it with the default tokenizers backend."
            )
