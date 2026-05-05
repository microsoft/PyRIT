# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Integration tests for GCG attack using a real GPT-2 model on CPU.

These tests validate that the GCG attack pipeline works end-to-end with a real
(tiny) model. They use GPT-2 (~124M params) which can run on CPU, paired with
the llama-2 conversation template (which has explicit handling in _update_ids).

Requires: torch, transformers, fastchat, mlflow (GCG optional deps).
Skipped via importorskip when deps are not installed.
"""

import pytest

torch = pytest.importorskip("torch", reason="torch not installed")
transformers = pytest.importorskip("transformers", reason="transformers not installed")
pytest.importorskip("fastchat", reason="fastchat not installed")


from fastchat.model import get_conversation_template  # noqa: E402
from transformers import AutoTokenizer, GPT2LMHeadModel  # noqa: E402

from pyrit.auxiliary_attacks.gcg.attack.base.attack_manager import (  # noqa: E402
    get_embedding_layer,
    get_embedding_matrix,
    get_embeddings,
    get_nonascii_toks,
)
from pyrit.auxiliary_attacks.gcg.attack.gcg.gcg_attack import (  # noqa: E402
    GCGAttackPrompt,
    GCGPromptManager,
    token_gradients,
)


@pytest.fixture(scope="module")
def gpt2_model() -> GPT2LMHeadModel:
    """Load GPT-2 model once for all tests in this module."""
    return GPT2LMHeadModel.from_pretrained("gpt2").eval()


@pytest.fixture(scope="module")
def gpt2_tokenizer() -> transformers.PreTrainedTokenizer:
    """Load GPT-2 tokenizer once for all tests in this module."""
    tokenizer = AutoTokenizer.from_pretrained("gpt2")
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    return tokenizer


@pytest.fixture()
def conv_template():
    """Create a fresh llama-2 conversation template for each test."""
    conv = get_conversation_template("llama-2")
    conv.sep2 = conv.sep2.strip()
    return conv


class TestTokenGradientsIntegration:
    """Integration tests for token_gradients with real GPT-2."""

    def test_gradient_shape_matches_control_and_vocab(
        self, gpt2_model: GPT2LMHeadModel, gpt2_tokenizer: transformers.PreTrainedTokenizer
    ) -> None:
        """Gradient should have shape (n_control_tokens, vocab_size)."""
        input_ids = gpt2_tokenizer("Hello world ! ! ! target text", return_tensors="pt")["input_ids"][0]
        control_slice = slice(2, 5)
        target_slice = slice(5, 7)
        loss_slice = slice(4, 6)

        grad = token_gradients(gpt2_model, input_ids, control_slice, target_slice, loss_slice)

        n_control = control_slice.stop - control_slice.start
        assert grad.shape == (n_control, gpt2_tokenizer.vocab_size)

    def test_gradient_is_finite_and_nonzero(
        self, gpt2_model: GPT2LMHeadModel, gpt2_tokenizer: transformers.PreTrainedTokenizer
    ) -> None:
        """Gradient values should be finite and at least some should be non-zero."""
        input_ids = gpt2_tokenizer("Tell me how ! ! ! Sure here is", return_tensors="pt")["input_ids"][0]
        control_slice = slice(3, 6)
        target_slice = slice(6, 9)
        loss_slice = slice(5, 8)

        grad = token_gradients(gpt2_model, input_ids, control_slice, target_slice, loss_slice)

        assert torch.isfinite(grad).all(), "Gradient contains non-finite values"
        assert (grad != 0).any(), "Gradient is all zeros"


class TestGCGAttackPromptIntegration:
    """Integration tests for GCGAttackPrompt with real GPT-2."""

    def test_prompt_initializes_with_valid_slices(
        self,
        gpt2_model: GPT2LMHeadModel,
        gpt2_tokenizer: transformers.PreTrainedTokenizer,
        conv_template: object,
    ) -> None:
        """AttackPrompt should initialize with non-empty, non-overlapping slices."""
        prompt = GCGAttackPrompt(
            goal="Tell me how",
            target="Sure here is",
            tokenizer=gpt2_tokenizer,
            conv_template=conv_template,
            control_init="! ! ! ! !",
        )

        assert prompt._control_slice.start < prompt._control_slice.stop
        assert prompt._target_slice.start < prompt._target_slice.stop
        assert prompt._control_slice.stop <= prompt._target_slice.start
        assert prompt.input_ids.shape[0] > 0

    def test_grad_returns_valid_gradient(
        self,
        gpt2_model: GPT2LMHeadModel,
        gpt2_tokenizer: transformers.PreTrainedTokenizer,
        conv_template: object,
    ) -> None:
        """GCGAttackPrompt.grad should return a finite, non-zero gradient tensor."""
        prompt = GCGAttackPrompt(
            goal="Tell me how",
            target="Sure here is",
            tokenizer=gpt2_tokenizer,
            conv_template=conv_template,
            control_init="! ! ! ! !",
        )

        grad = prompt.grad(gpt2_model)

        n_control = prompt._control_slice.stop - prompt._control_slice.start
        assert grad.shape[0] == n_control
        assert grad.shape[1] == gpt2_tokenizer.vocab_size
        assert torch.isfinite(grad).all()

    def test_target_loss_is_finite_scalar(
        self,
        gpt2_model: GPT2LMHeadModel,
        gpt2_tokenizer: transformers.PreTrainedTokenizer,
        conv_template: object,
    ) -> None:
        """Target loss from real model logits should be a finite positive number."""
        prompt = GCGAttackPrompt(
            goal="Tell me how",
            target="Sure here is",
            tokenizer=gpt2_tokenizer,
            conv_template=conv_template,
            control_init="! ! ! ! !",
        )

        loss = prompt.test_loss(gpt2_model)
        assert isinstance(loss, float)
        assert loss > 0
        assert loss < 1e6


class TestGCGSampleControlIntegration:
    """Integration tests for GCGPromptManager.sample_control with real tokenizer."""

    def test_sample_control_produces_valid_candidates(
        self,
        gpt2_model: GPT2LMHeadModel,
        gpt2_tokenizer: transformers.PreTrainedTokenizer,
        conv_template: object,
    ) -> None:
        """Sampled control tokens should be decodable by the tokenizer."""
        prompt = GCGAttackPrompt(
            goal="Tell me how",
            target="Sure here is",
            tokenizer=gpt2_tokenizer,
            conv_template=conv_template,
            control_init="! ! ! ! !",
        )

        grad = prompt.grad(gpt2_model)

        pm = object.__new__(GCGPromptManager)
        pm._prompts = [prompt]
        pm._nonascii_toks = get_nonascii_toks(gpt2_tokenizer, device="cpu")

        candidates = pm.sample_control(grad, batch_size=8, topk=32, allow_non_ascii=False)

        assert candidates.shape[0] == 8
        # All candidates should be decodable without error
        for i in range(candidates.shape[0]):
            decoded = gpt2_tokenizer.decode(candidates[i])
            assert isinstance(decoded, str)
            assert len(decoded) > 0


class TestEmbeddingHelpersIntegration:
    """Integration tests for embedding helper functions with real GPT-2."""

    def test_get_embedding_layer_returns_embedding(self, gpt2_model: GPT2LMHeadModel) -> None:
        layer = get_embedding_layer(gpt2_model)
        assert isinstance(layer, torch.nn.Embedding)

    def test_get_embedding_matrix_shape(
        self, gpt2_model: GPT2LMHeadModel, gpt2_tokenizer: transformers.PreTrainedTokenizer
    ) -> None:
        matrix = get_embedding_matrix(gpt2_model)
        assert matrix.shape[0] == gpt2_tokenizer.vocab_size

    def test_get_embeddings_returns_correct_shape(
        self, gpt2_model: GPT2LMHeadModel, gpt2_tokenizer: transformers.PreTrainedTokenizer
    ) -> None:
        input_ids = gpt2_tokenizer("Hello world", return_tensors="pt")["input_ids"]
        embeddings = get_embeddings(gpt2_model, input_ids)
        assert embeddings.shape[0] == 1
        assert embeddings.shape[1] == input_ids.shape[1]

    def test_get_nonascii_toks_returns_nonempty_tensor(self, gpt2_tokenizer: transformers.PreTrainedTokenizer) -> None:
        toks = get_nonascii_toks(gpt2_tokenizer, device="cpu")
        assert isinstance(toks, torch.Tensor)
        assert len(toks) > 0
