# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import pickle
import threading
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch, sentinel

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
torch = pytest.importorskip("torch", reason="torch not installed")

MultiPromptAttack = attack_manager_mod.MultiPromptAttack
AttackPrompt = attack_manager_mod.AttackPrompt
PromptManager = attack_manager_mod.PromptManager
EvaluateAttack = attack_manager_mod.EvaluateAttack
IndividualPromptAttack = attack_manager_mod.IndividualPromptAttack
ModelWorker = attack_manager_mod.ModelWorker
ModelWorkerOperation = attack_manager_mod.ModelWorkerOperation
ModelWorkerTask = attack_manager_mod.ModelWorkerTask
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack
get_embedding_layer = attack_manager_mod.get_embedding_layer
get_embedding_matrix = attack_manager_mod.get_embedding_matrix
get_embeddings = attack_manager_mod.get_embeddings

gcg_attack_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack",
    reason="GCG optional dependencies not installed",
)
GCGMultiPromptAttack = gcg_attack_mod.GCGMultiPromptAttack
GCGPromptManager = gcg_attack_mod.GCGPromptManager
token_gradients = gcg_attack_mod.token_gradients

default_implementations_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.default_implementations",
    reason="GCG optional dependencies not installed",
)
LengthPreservingFilter = default_implementations_mod.LengthPreservingFilter
StandardGCGSampling = default_implementations_mod.StandardGCGSampling

import numpy as np  # noqa: E402

generator_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.generator",
    reason="GCG optional dependencies not installed",
)
GCGGenerator = generator_mod.GCGGenerator

from unit.executor.promptgen.gcg.trajectory_stubs import (  # noqa: E402
    RecordingGCGAttack,
    TrajectoryPromptManager,
    TrajectoryWorker,
)


@dataclass
class _TinyModelOutput:
    logits: torch.Tensor


class _TinyCausalLM(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embedding = torch.nn.Embedding(8, 4)
        self.projection = torch.nn.Linear(4, 8, bias=False)

    @property
    def device(self) -> torch.device:
        return self.embedding.weight.device

    def forward(self, *, inputs_embeds: torch.Tensor) -> _TinyModelOutput:
        return _TinyModelOutput(logits=self.projection(inputs_embeds.cumsum(dim=1)))


def _backward_coordinate_gradient(
    *,
    model: _TinyCausalLM,
    input_ids: torch.Tensor,
    input_slice: slice,
    target_slice: slice,
    loss_slice: slice,
) -> torch.Tensor:
    embedding_weights = model.embedding.weight
    one_hot = torch.zeros(
        input_ids[input_slice].shape[0],
        embedding_weights.shape[0],
        device=model.device,
        dtype=embedding_weights.dtype,
    )
    one_hot.scatter_(1, input_ids[input_slice].unsqueeze(1), torch.ones(one_hot.shape[0], 1))
    one_hot.requires_grad_()
    input_embeddings = (one_hot @ embedding_weights).unsqueeze(0)
    embeddings = model.embedding(input_ids.unsqueeze(0)).detach()
    full_embeddings = torch.cat(
        [embeddings[:, : input_slice.start, :], input_embeddings, embeddings[:, input_slice.stop :, :]], dim=1
    )
    logits = model(inputs_embeds=full_embeddings).logits
    loss = torch.nn.CrossEntropyLoss()(logits[0, loss_slice, :], input_ids[target_slice])
    loss.backward()
    assert one_hot.grad is not None
    return one_hot.grad.clone()


class TestGetFilteredCands:
    """Tests for MultiPromptAttack.get_filtered_cands."""

    def _make_attack_with_worker(self, *, vocab_size: int = 100) -> tuple:
        """Create a minimal MultiPromptAttack with a mocked worker for get_filtered_cands."""
        attack = object.__new__(MultiPromptAttack)
        mock_worker = MagicMock()
        mock_worker.tokenizer.vocab_size = vocab_size
        # Mock decode to return a simple string representation
        mock_worker.tokenizer.decode.side_effect = lambda ids, **kwargs: "tok_" + "_".join(str(t) for t in ids.tolist())
        # Mock tokenizer call to return input_ids matching the length of input
        mock_worker.tokenizer.side_effect = lambda text, **kwargs: MagicMock(
            input_ids=list(range(len(text.split("_")) - 1))
        )
        # "!" token maps to id 0
        mock_worker.tokenizer.__call__ = mock_worker.tokenizer.side_effect
        first_call = MagicMock()
        first_call.input_ids = [0]
        mock_worker.tokenizer.return_value = first_call
        attack.workers = [mock_worker]
        return attack, mock_worker

    def test_returns_list_of_strings(self) -> None:
        """get_filtered_cands should return a list of decoded strings."""
        attack, worker = self._make_attack_with_worker()
        # Simple decode: each row -> "tok_X_Y"
        worker.tokenizer.decode.side_effect = lambda ids, **kwargs: f"ctrl_{ids[0]}"
        worker.tokenizer.side_effect = lambda text, **kwargs: MagicMock(input_ids=[0])

        cands = torch.tensor([[5], [6], [7]])
        result = attack.get_filtered_cands(0, cands, filter_cand=False)
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(s, str) for s in result)

    def test_filter_cand_false_returns_all(self) -> None:
        """With filter_cand=False, all candidates should be returned."""
        attack, worker = self._make_attack_with_worker()
        worker.tokenizer.decode.side_effect = lambda ids, **kwargs: f"ctrl_{ids[0]}"
        # Reset side_effect so return_value is used for tokenizer("!") call
        worker.tokenizer.side_effect = None
        worker.tokenizer.return_value = MagicMock(input_ids=[0])

        cands = torch.tensor([[5], [6], [7]])
        result = attack.get_filtered_cands(0, cands, filter_cand=False)
        assert len(result) == 3

    def test_clamps_out_of_vocab_tokens(self) -> None:
        """Tokens above vocab_size should be replaced."""
        attack, worker = self._make_attack_with_worker(vocab_size=10)
        worker.tokenizer.decode.side_effect = lambda ids, **kwargs: f"ctrl_{ids[0]}"
        worker.tokenizer.side_effect = lambda text, **kwargs: MagicMock(input_ids=[0])

        cands = torch.tensor([[5], [15], [7]])  # 15 > vocab_size=10
        attack.get_filtered_cands(0, cands, filter_cand=False)
        # After clamping, the out-of-range token should have been replaced
        assert cands[1][0].item() != 15

    def test_filter_cand_true_pads_to_batch_size(self) -> None:
        """With filter_cand=True, result should be padded to match input batch size."""
        attack, worker = self._make_attack_with_worker()
        # Make all candidates decode to the same as curr_control so they get filtered out
        worker.tokenizer.decode.side_effect = lambda ids, **kwargs: "same_control"
        worker.tokenizer.side_effect = lambda text, **kwargs: MagicMock(input_ids=[0])

        # But make the last one different
        decode_results = ["same_control", "same_control", "different"]
        call_count = [0]

        def decode_fn(ids, **kwargs):
            idx = min(call_count[0], len(decode_results) - 1)
            call_count[0] += 1
            return decode_results[idx]

        worker.tokenizer.decode.side_effect = decode_fn
        worker.tokenizer.side_effect = lambda text, **kwargs: MagicMock(input_ids=[0])

        cands = torch.tensor([[1], [2], [3]])
        result = attack.get_filtered_cands(0, cands, filter_cand=True, curr_control="same_control")
        # Should always return exactly len(cands) results
        assert len(result) == 3


class TestTargetAndControlLoss:
    """Tests for AttackPrompt.target_loss and control_loss."""

    def test_target_loss_returns_correct_shape(self) -> None:
        """target_loss should return tensor of shape (batch, target_len)."""
        prompt = object.__new__(AttackPrompt)
        prompt._target_slice = slice(5, 8)  # 3 target tokens

        batch_size = 4
        seq_len = 10
        vocab_size = 50
        logits = torch.randn(batch_size, seq_len, vocab_size)
        ids = torch.randint(0, vocab_size, (batch_size, seq_len))

        loss = prompt.target_loss(logits, ids)
        assert loss.shape == (batch_size, 3)

    def test_target_loss_is_finite(self) -> None:
        """target_loss should always return finite values."""
        prompt = object.__new__(AttackPrompt)
        prompt._target_slice = slice(3, 6)

        logits = torch.randn(2, 8, 30)
        ids = torch.randint(0, 30, (2, 8))

        loss = prompt.target_loss(logits, ids)
        assert torch.isfinite(loss).all()

    def test_control_loss_returns_correct_shape(self) -> None:
        """control_loss should return tensor of shape (batch, control_len)."""
        prompt = object.__new__(AttackPrompt)
        prompt._control_slice = slice(2, 5)  # 3 control tokens

        batch_size = 4
        seq_len = 10
        vocab_size = 50
        logits = torch.randn(batch_size, seq_len, vocab_size)
        ids = torch.randint(0, vocab_size, (batch_size, seq_len))

        loss = prompt.control_loss(logits, ids)
        assert loss.shape == (batch_size, 3)

    def test_control_loss_is_finite(self) -> None:
        """control_loss should always return finite values."""
        prompt = object.__new__(AttackPrompt)
        prompt._control_slice = slice(2, 5)

        logits = torch.randn(2, 8, 30)
        ids = torch.randint(0, 30, (2, 8))

        loss = prompt.control_loss(logits, ids)
        assert torch.isfinite(loss).all()

    def test_target_loss_higher_for_wrong_predictions(self) -> None:
        """Loss should be higher when logits don't predict the correct target tokens."""
        prompt = object.__new__(AttackPrompt)
        prompt._target_slice = slice(3, 5)

        vocab_size = 10
        ids = torch.zeros(1, 6, dtype=torch.long)
        ids[0, 3] = 2
        ids[0, 4] = 3

        # Logits that perfectly predict the target
        good_logits = torch.full((1, 6, vocab_size), -10.0)
        good_logits[0, 2, 2] = 10.0  # predicts token 2 at position 3
        good_logits[0, 3, 3] = 10.0  # predicts token 3 at position 4

        # Logits that predict wrong tokens
        bad_logits = torch.full((1, 6, vocab_size), -10.0)
        bad_logits[0, 2, 7] = 10.0  # predicts wrong token
        bad_logits[0, 3, 8] = 10.0  # predicts wrong token

        good_loss = prompt.target_loss(good_logits, ids).mean()
        bad_loss = prompt.target_loss(bad_logits, ids).mean()
        assert bad_loss > good_loss


class TestSampleControl:
    """Tests for GCGPromptManager.sample_control."""

    def _make_prompt_manager(self, *, n_control_tokens: int = 5, vocab_size: int = 50) -> GCGPromptManager:
        """Create a minimal GCGPromptManager with stubbed internals for sample_control testing."""
        pm = object.__new__(GCGPromptManager)
        pm._nonascii_toks = torch.tensor([])
        # Simulate control_toks property
        pm._prompts = [MagicMock()]
        pm._prompts[0].control_toks = torch.randint(0, vocab_size, (n_control_tokens,))
        return pm

    def test_returns_correct_shape(self) -> None:
        """sample_control should return (batch_size, n_control_tokens) tensor."""
        n_control = 5
        vocab_size = 50
        batch_size = 16
        pm = self._make_prompt_manager(n_control_tokens=n_control, vocab_size=vocab_size)

        grad = torch.randn(n_control, vocab_size)
        result = pm.sample_control(grad, batch_size, topk=10)
        assert result.shape == (batch_size, n_control)

    def test_output_tokens_within_vocab(self) -> None:
        """All sampled tokens should be within vocabulary range."""
        n_control = 5
        vocab_size = 50
        batch_size = 32
        pm = self._make_prompt_manager(n_control_tokens=n_control, vocab_size=vocab_size)

        grad = torch.randn(n_control, vocab_size)
        result = pm.sample_control(grad, batch_size, topk=10)
        assert (result >= 0).all()
        assert (result < vocab_size).all()

    def test_each_candidate_differs_in_at_most_one_position(self) -> None:
        """Each candidate replaces exactly one position with a token sampled from top-k.

        The replacement token is drawn uniformly from top-k, so it may equal the
        original token at that position (giving diffs == 0). The function only
        guarantees that *at most* one position differs from the original; asserting
        exactly one would make the test flaky against the underlying randomness.
        """
        n_control = 10
        vocab_size = 50
        batch_size = 8
        pm = self._make_prompt_manager(n_control_tokens=n_control, vocab_size=vocab_size)

        grad = torch.randn(n_control, vocab_size)
        original_toks = pm._prompts[0].control_toks.clone()
        result = pm.sample_control(grad, batch_size, topk=10)

        for i in range(batch_size):
            diffs = (result[i] != original_toks.to(result.device)).sum().item()
            assert diffs <= 1, f"Candidate {i} differs in {diffs} positions, expected at most 1"

    def test_non_ascii_filtering(self) -> None:
        """When allow_non_ascii=False, the newly sampled token should not be non-ASCII.

        Note: sample_control only changes ONE position per candidate, so unchanged positions
        may still contain non-ASCII tokens from the original control. We verify that the
        *changed* position doesn't use a non-ASCII token.
        """
        n_control = 5
        vocab_size = 20
        batch_size = 64
        pm = self._make_prompt_manager(n_control_tokens=n_control, vocab_size=vocab_size)
        # Use only ASCII tokens in original control
        pm._prompts[0].control_toks = torch.tensor([0, 1, 2, 3, 4])
        # Mark tokens 15-19 as non-ASCII
        pm._nonascii_toks = torch.tensor([15, 16, 17, 18, 19])

        # Create gradient that strongly favors non-ASCII tokens
        grad = torch.zeros(n_control, vocab_size)
        grad[:, 15:20] = -100.0  # Negative gradient = top candidates after negation

        result = pm.sample_control(grad, batch_size, topk=5, allow_non_ascii=False)
        original = pm._prompts[0].control_toks
        non_ascii_set = {15, 16, 17, 18, 19}

        for i in range(batch_size):
            # Find the position that changed
            diffs = result[i] != original.to(result.device)
            changed_positions = diffs.nonzero(as_tuple=True)[0]
            for pos in changed_positions:
                new_tok = result[i, pos].item()
                assert new_tok not in non_ascii_set, f"Candidate {i} position {pos}: sampled non-ASCII token {new_tok}"


# Architectures built as tiny random models to exercise the embedding helpers.
# The first three predate this generic path and must keep returning float16 from
# get_embeddings; the rest were previously rejected outright.
_HALF_PRECISION_ARCHITECTURES = ["gpt2", "gptj", "gpt_neox"]
_OTHER_ARCHITECTURES = ["llama", "mistral", "mixtral", "phi3", "qwen3", "starcoder2"]

_TINY_CONFIG = {
    "hidden_size": 32,
    "num_hidden_layers": 1,
    "num_attention_heads": 4,
    "num_key_value_heads": 4,
    "intermediate_size": 64,
    "vocab_size": 256,
}
_EXTRA_CONFIG = {
    "phi3": {
        "max_position_embeddings": 64,
        "original_max_position_embeddings": 64,
        "pad_token_id": 0,
    },
}


def _tiny_model(model_type: str) -> Any:
    """Build a small randomly initialized model of the given architecture."""
    transformers = pytest.importorskip("transformers", reason="transformers not installed")
    config = transformers.AutoConfig.for_model(model_type, **_TINY_CONFIG, **_EXTRA_CONFIG.get(model_type, {}))
    return transformers.AutoModelForCausalLM.from_config(config)


class TestEmbeddingHelpers:
    """Tests for get_embedding_layer, get_embedding_matrix, get_embeddings."""

    @pytest.mark.parametrize("model_type", _HALF_PRECISION_ARCHITECTURES + _OTHER_ARCHITECTURES)
    def test_helpers_resolve_embeddings_for_any_causal_model(self, model_type: str) -> None:
        """Any model AutoModelForCausalLM can load should resolve through the helpers."""
        model = _tiny_model(model_type)
        expected = model.get_input_embeddings()

        assert get_embedding_layer(model) is expected
        assert get_embedding_matrix(model) is expected.weight

        embedded = get_embeddings(model, torch.tensor([[1, 2, 3]]))
        assert embedded.shape[-1] == model.config.hidden_size

    @pytest.mark.parametrize("model_type", _HALF_PRECISION_ARCHITECTURES)
    def test_get_embeddings_keeps_half_precision_for_legacy_architectures(self, model_type: str) -> None:
        """GPT-2, GPT-J and GPT-NeoX returned float16 before this path existed."""
        model = _tiny_model(model_type)
        assert get_embeddings(model, torch.tensor([[1, 2, 3]])).dtype == torch.float16

    @pytest.mark.parametrize("model_type", _OTHER_ARCHITECTURES)
    def test_get_embeddings_keeps_embedding_dtype_for_other_architectures(self, model_type: str) -> None:
        """Everything else keeps the embedding dtype rather than being downcast."""
        model = _tiny_model(model_type)
        expected_dtype = model.get_input_embeddings().weight.dtype
        assert get_embeddings(model, torch.tensor([[1, 2, 3]])).dtype == expected_dtype


class TestPromptManagerInit:
    """Tests for PromptManager initialization validation."""

    def test_raises_when_managers_are_missing(self) -> None:
        with pytest.raises(ValueError, match="PromptManager requires a managers mapping"):
            PromptManager(
                goals=["goal"],
                targets=["target"],
                tokenizer=MagicMock(),
            )

    def test_raises_on_mismatched_goals_targets(self) -> None:
        with pytest.raises(ValueError, match="Length of goals and targets must match"):
            PromptManager(
                goals=["goal1", "goal2"],
                targets=["target1"],
                tokenizer=MagicMock(),
                managers={"AP": MagicMock()},
            )

    def test_raises_on_empty_goals(self) -> None:
        with pytest.raises(ValueError, match="Must provide at least one goal"):
            PromptManager(
                goals=[],
                targets=[],
                tokenizer=MagicMock(),
                managers={"AP": MagicMock()},
            )


class TestEvaluateAttackInit:
    """Tests for EvaluateAttack initialization validation."""

    @pytest.mark.parametrize(
        ("attack_class", "expected_message"),
        [
            (MultiPromptAttack, "MultiPromptAttack requires a managers mapping"),
            (ProgressiveMultiPromptAttack, "ProgressiveMultiPromptAttack requires a managers mapping"),
            (IndividualPromptAttack, "IndividualPromptAttack requires a managers mapping"),
            (EvaluateAttack, "EvaluateAttack requires a managers mapping"),
        ],
    )
    def test_attack_raises_when_managers_are_missing(self, *, attack_class: type[Any], expected_message: str) -> None:
        with pytest.raises(ValueError, match=expected_message):
            attack_class(goals=["goal"], targets=["target"], workers=[])

    def test_raises_with_multiple_workers(self) -> None:
        mock_worker1 = MagicMock()
        mock_worker1.model.name_or_path = "m1"
        mock_worker1.tokenizer.name_or_path = "t1"
        mock_worker1.tokenizer.chat_template = "{{ messages[0]['content'] }}"
        mock_worker2 = MagicMock()
        mock_worker2.model.name_or_path = "m2"
        mock_worker2.tokenizer.name_or_path = "t2"
        mock_worker2.tokenizer.chat_template = "{{ messages[0]['content'] }}"

        with pytest.raises(ValueError, match="exactly 1 worker"):
            EvaluateAttack(
                goals=["goal"],
                targets=["target"],
                workers=[mock_worker1, mock_worker2],
                managers={"AP": MagicMock(), "PM": MagicMock(), "MPA": MagicMock()},
            )


_CHATML_TEMPLATE = (
    "{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] }}<|end|>{% endfor %}"
    "{% if add_generation_prompt %}<|assistant|>{% endif %}"
)

# A complete-conversation template is allowed to ignore add_generation_prompt, so the user-only
# render stops before the assistant marker instead of after it.
_NO_GENERATION_PROMPT_TEMPLATE = "{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] }}<|end|>{% endfor %}"


def _fast_tokenizer(*, chat_template: str = _CHATML_TEMPLATE, byte_level: bool = False) -> Any:
    """
    Build an offline tokenizer with real offsets, decoding, and special role tokens.

    Args:
        chat_template (str): The Jinja chat template to render with.
        byte_level (bool): Use byte-level BPE instead of whitespace-delimited words.

    Returns:
        Any: A ``PreTrainedTokenizerFast`` with ``chat_template`` set.
    """
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    if byte_level:
        vocabulary = ["[UNK]", *sorted(pre_tokenizers.ByteLevel.alphabet())]
        backend = Tokenizer(models.BPE(dict(zip(vocabulary, range(len(vocabulary)), strict=True)), merges=[]))
        backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
        backend.decoder = decoders.ByteLevel()
    else:
        vocabulary = [
            "[UNK]",
            "Say",
            "it",
            "assistant",
            "done",
            "!",
            "Sure",
            ",",
            "here",
            "is",
            "the",
            "plan",
            "Respond",
            "with",
            "now",
            "user",
            "model",
            "goal",
            "control",
            "target",
            "hello",
            "world",
        ]
        backend = Tokenizer(
            models.WordLevel(dict(zip(vocabulary, range(len(vocabulary)), strict=True)), unk_token="[UNK]")
        )
        backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]")
    tokenizer.add_special_tokens(
        {"additional_special_tokens": ["<|user|>", "<|assistant|>", "<|end|>", "<start_of_turn>", "<end_of_turn>"]}
    )
    tokenizer.chat_template = chat_template
    return tokenizer


class TestUpdateIdsErrorPaths:
    """Real-tokenizer coverage of prompt boundaries and unsupported templates."""

    @pytest.mark.parametrize(
        "chat_template",
        [
            "<|user|>goal control<|end|><|assistant|>target<|end|>",
            "{% for m in messages %}{{ m['content'] | upper }}{% endfor %}",
            "{% for m in messages %}{{ m['content'] }}{{ m['content'] }}{% endfor %}",
            "{% for m in messages | reverse %}{{ m['content'] }}{% endfor %}",
            "{% for m in messages %}{{ m['content'] | replace('goal', 'other') }}{% endfor %}",
            "{% for m in messages %}{% if 'goal' in m['content'] %}prefix{% endif %}{{ m['content'] }}{% endfor %}",
        ],
    )
    def test_unsupported_templates_raise_instead_of_scanning_the_prompt(self, chat_template: str) -> None:
        with pytest.raises(ValueError, match="Cannot safely locate"):
            AttackPrompt(
                goal="goal",
                target="target",
                tokenizer=_fast_tokenizer(chat_template=chat_template),
                control_init="control",
            )

    @pytest.mark.parametrize(("control", "target"), [("", "done"), ("! !", ""), (" ", "done"), ("! !", " ")])
    @pytest.mark.parametrize("byte_level", [False, True])
    def test_empty_or_unmapped_optimization_spans_raise(self, *, control: str, target: str, byte_level: bool) -> None:
        with pytest.raises(ValueError, match="control|target"):
            AttackPrompt(
                goal="Say it",
                target=target,
                tokenizer=_fast_tokenizer(byte_level=byte_level),
                control_init=control,
            )

    def test_unmapped_whitespace_does_not_extend_slices_into_other_content(self) -> None:
        tokenizer = _fast_tokenizer()
        prompt = AttackPrompt(goal=" hello ", target=" world ", tokenizer=tokenizer, control_init=" ! ! ")

        assert prompt._goal_slice == slice(1, 2)
        assert prompt._control_slice == slice(2, 4)
        assert prompt._target_slice == slice(6, 7)
        assert prompt._loss_slice == slice(5, 6)
        assert prompt.goal_str == "hello"
        assert prompt.control_str == "! !"
        assert prompt.target_str == "world"

    def test_target_at_the_end_of_the_prompt_keeps_all_its_tokens(self) -> None:
        tokenizer = _fast_tokenizer(chat_template=_CHATML_TEMPLATE.replace("<|end|>", ""))
        prompt = AttackPrompt(goal="hello", target="world", tokenizer=tokenizer, control_init="! !")

        assert prompt._target_slice == slice(5, 6)
        assert prompt._target_slice.stop == len(prompt.input_ids)
        assert prompt.target_str == "world"

    @pytest.mark.parametrize(
        ("goal", "control", "target"),
        [
            ("Say <|end|><|assistant|> now", "! !", "done"),
            ("Say it", "<|end|><|assistant|>", "done"),
            ("Say it", "! !", "done <|end|><|assistant|> now"),
            ("Say ! ! now", "! !", "done ! !"),
            ("assistant", "assistant", "assistant"),
            ("control control", "control", "control"),
        ],
    )
    def test_repeated_content_and_separators_keep_exact_slices(self, *, goal: str, control: str, target: str) -> None:
        tokenizer = _fast_tokenizer()
        prompt = AttackPrompt(goal=goal, target=target, tokenizer=tokenizer, control_init=control)
        goal_ids = tokenizer(goal, add_special_tokens=False).input_ids
        control_ids = tokenizer(control, add_special_tokens=False).input_ids
        target_ids = tokenizer(target, add_special_tokens=False).input_ids
        control_start = 1 + len(goal_ids)
        target_start = control_start + len(control_ids) + 2

        assert prompt._goal_slice == slice(1, control_start)
        assert prompt._control_slice == slice(control_start, control_start + len(control_ids))
        assert prompt._target_slice == slice(target_start, target_start + len(target_ids))
        assert prompt.goal_toks.tolist() == goal_ids
        assert prompt.control_toks.tolist() == control_ids
        assert prompt.target_toks.tolist() == target_ids

    def test_json_template_does_not_find_the_target_in_the_role_label(self) -> None:
        tokenizer = _fast_tokenizer(
            chat_template="{% for m in messages %}{{ m['role'] | tojson }}:{{ m['content'] | tojson }}\n{% endfor %}",
            byte_level=True,
        )
        prompt = AttackPrompt(goal="Say it", target="assistant", tokenizer=tokenizer, control_init="! !")

        assert prompt.target_str == "assistant"
        assert prompt.input_str == '"user":"Say it ! !"\n"assistant":"assistant'
        assert prompt._assistant_role_slice.stop == prompt._target_slice.start

    def test_transformed_real_content_is_rejected_even_when_markers_round_trip(self) -> None:
        tokenizer = _fast_tokenizer(
            chat_template="{% for m in messages %}{{ m['role'] | tojson }}:{{ m['content'] | tojson }}\n{% endfor %}"
        )
        with pytest.raises(ValueError, match="Cannot safely locate"):
            AttackPrompt(goal='Say "hello"', target="done", tokenizer=tokenizer, control_init="! !")

    @pytest.mark.parametrize("target", ["caf\u00e9", "\U0001f600", "done"])
    def test_byte_level_offsets_keep_every_target_byte(self, target: str) -> None:
        tokenizer = _fast_tokenizer(byte_level=True)
        prompt = AttackPrompt(goal="Say it", target=target, tokenizer=tokenizer, control_init="! !")

        assert prompt.target_toks.tolist() == tokenizer(target, add_special_tokens=False).input_ids
        assert prompt.target_str == target
        assert prompt.input_str == f"<|user|>Say it ! !<|end|><|assistant|>{target}"
        assert prompt._loss_slice == slice(prompt._target_slice.start - 1, prompt._target_slice.stop - 1)

    def test_chat_template_special_tokens_are_not_added_twice(self) -> None:
        from tokenizers import processors

        tokenizer = _fast_tokenizer(chat_template="{{ bos_token }}" + _CHATML_TEMPLATE)
        tokenizer.add_special_tokens({"bos_token": "<s>"})
        tokenizer.backend_tokenizer.post_processor = processors.TemplateProcessing(
            single="<s> $A", special_tokens=[("<s>", tokenizer.bos_token_id)]
        )
        prompt = AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="! !")

        assert prompt.input_ids.tolist().count(tokenizer.bos_token_id) == 1
        assert prompt._goal_slice == slice(2, 4)
        assert prompt._target_slice == slice(8, 9)

    def test_token_shared_with_assistant_scaffolding_is_rejected(self) -> None:
        tokenizer = _fast_tokenizer(chat_template="{{ messages[0]['content'] }} week{{ messages[1]['content'] }}")
        with pytest.raises(ValueError, match="token.*boundary"):
            AttackPrompt(goal="Say it", target="end", tokenizer=tokenizer, control_init="! !")

    def test_slow_tokenizer_has_an_actionable_error(self) -> None:
        from transformers import PreTrainedTokenizer

        class SlowTokenizer(PreTrainedTokenizer):
            def get_vocab(self) -> dict[str, int]:
                return {"[UNK]": 0}

            def _tokenize(self, text: str, **kwargs: Any) -> list[str]:
                return text.split()

            def _convert_token_to_id(self, token: str) -> int:
                return 0

            def _convert_id_to_token(self, index: int) -> str:
                return "[UNK]"

        tokenizer = SlowTokenizer(unk_token="[UNK]", chat_template=_CHATML_TEMPLATE)
        assert not tokenizer.is_fast
        with pytest.raises(ValueError, match="fast tokenizer.*use_fast=True"):
            AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="! !")

    def test_probe_rendering_errors_are_not_swallowed(self) -> None:
        tokenizer = _fast_tokenizer()
        with (
            patch.object(
                tokenizer,
                "apply_chat_template",
                side_effect=["<|user|>Say it ! !<|end|><|assistant|>done<|end|>", RuntimeError("probe failed")],
            ),
            pytest.raises(RuntimeError, match="probe failed"),
        ):
            AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="! !")

    @pytest.mark.parametrize("byte_level", [False, True])
    @pytest.mark.parametrize("goal", ["", " \t", " hello ", "hello"])
    @pytest.mark.parametrize("trim", [False, True])
    def test_whitespace_and_empty_goals_preserve_content(self, *, byte_level: bool, goal: str, trim: bool) -> None:
        template = _CHATML_TEMPLATE.replace("m['content']", "m['content'] | trim") if trim else _CHATML_TEMPLATE
        tokenizer = _fast_tokenizer(chat_template=template, byte_level=byte_level)
        prompt = AttackPrompt(goal=goal, target=" world ", tokenizer=tokenizer, control_init=" ! ! ")

        assert prompt.goal_str == goal.strip()
        assert prompt.control_str == "! !"
        assert prompt.target_str == "world"
        assert prompt._goal_slice.stop <= prompt._control_slice.start < prompt._control_slice.stop
        assert prompt._control_slice.stop <= prompt._target_slice.start < prompt._target_slice.stop

    @pytest.mark.parametrize("byte_level", [False, True])
    def test_control_and_content_setters_recompute_boundaries(self, byte_level: bool) -> None:
        tokenizer = _fast_tokenizer(byte_level=byte_level)
        prompt = AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="! !")

        for control in ("assistant", "<|end|><|assistant|>", "! !"):
            prompt.control_str = control
            assert prompt.control_toks.tolist() == tokenizer(control, add_special_tokens=False).input_ids
            assert prompt.goal_str == "Say it"
            assert prompt.target_str == "done"

        control_ids = tokenizer("assistant", add_special_tokens=False).input_ids
        prompt.control_toks = torch.tensor(control_ids)
        prompt.goal_str = ""
        prompt.target_str = "assistant"
        assert prompt.goal_toks.numel() == 0
        assert prompt.control_toks.tolist() == control_ids
        assert prompt.target_toks.tolist() == control_ids
        assert prompt._control_slice.stop <= prompt._target_slice.start

    def test_template_without_a_separator_uses_content_positions(self) -> None:
        tokenizer = _fast_tokenizer(chat_template="{{ messages[0]['content'] }}{{ messages[1]['content'] }}")
        prompt = AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="!")

        assert prompt._control_slice == slice(2, 3)
        assert prompt._target_slice == slice(3, 4)
        assert prompt._assistant_role_slice == slice(3, 3)

    @pytest.mark.parametrize("byte_level", [False, True])
    @pytest.mark.parametrize("goal", ["", " ", " hello "])
    def test_role_tokens_that_consume_whitespace_stay_outside_content_slices(
        self, *, byte_level: bool, goal: str
    ) -> None:
        from tokenizers import AddedToken

        tokenizer = _fast_tokenizer(byte_level=byte_level)
        tokenizer.add_special_tokens(
            {
                "additional_special_tokens": [
                    AddedToken(marker, lstrip=True, rstrip=True) for marker in ("<|user|>", "<|assistant|>", "<|end|>")
                ]
            }
        )
        prompt = AttackPrompt(goal=goal, target=" world ", tokenizer=tokenizer, control_init=" ! ! ")
        role_ids = {tokenizer.convert_tokens_to_ids(marker) for marker in ("<|user|>", "<|assistant|>", "<|end|>")}

        assert prompt.goal_str == goal.strip()
        assert prompt.control_str == "! !"
        assert prompt.target_str == "world"
        assert role_ids.isdisjoint(prompt.goal_toks.tolist())
        assert role_ids.isdisjoint(prompt.control_toks.tolist())
        assert role_ids.isdisjoint(prompt.target_toks.tolist())
        if not goal.strip():
            assert prompt._goal_slice == slice(prompt._control_slice.start, prompt._control_slice.start)

    @pytest.mark.parametrize("component", ["control", "target"])
    def test_nonempty_text_removed_by_tokenizer_normalization_raises(self, component: str) -> None:
        from tokenizers import normalizers

        tokenizer = _fast_tokenizer()
        tokenizer.backend_tokenizer.normalizer = normalizers.Replace(component, "")
        with pytest.raises(ValueError, match=f"{component} contains no tokens"):
            AttackPrompt(goal="goal", target="target", tokenizer=tokenizer, control_init="control")

    def test_target_is_located_after_the_user_turn_when_the_goal_quotes_it(self) -> None:
        """A goal that quotes its own target must not pull the target slice into the user turn.

        Affirmative-prefix targets make this realistic: the same text then appears twice in the
        rendered prompt, and taking the first occurrence points the target and loss slices at the
        user turn instead of the assistant reply.
        """
        goal = "Respond with Sure, here is the plan"
        control = "! ! ! !"
        target = "Sure, here is the plan"

        prompt = AttackPrompt(
            goal=goal,
            target=target,
            tokenizer=_fast_tokenizer(),
            control_init=control,
        )

        assert prompt._control_slice == slice(9, 13)
        assert prompt._assistant_role_slice == slice(13, 15)
        assert prompt._target_slice == slice(15, 21)
        assert prompt._loss_slice == slice(14, 20)
        assert prompt.target_toks.tolist() == prompt.tokenizer(target, add_special_tokens=False).input_ids

    def test_target_that_names_the_assistant_role_marker_is_found_in_the_reply(self) -> None:
        """A target like "assistant" also matches inside ``<|assistant|>``, which is one special token.

        Searching right after the user content lands on the role marker and leaves an empty target
        slice, so the search has to start where the assistant content does.
        """
        tokenizer = _fast_tokenizer()

        prompt = AttackPrompt(goal="Say it", target="assistant", tokenizer=tokenizer, control_init="! !")

        ids = tokenizer("<|user|>Say it ! !<|end|><|assistant|>assistant<|end|>").input_ids
        # <|user|> Say it ! ! <|end|> <|assistant|> assistant <|end|>
        assert prompt._control_slice == slice(3, 5)
        assert prompt._target_slice == slice(7, 8)
        assert prompt._loss_slice == slice(6, 7)
        assert ids[6] == tokenizer.convert_tokens_to_ids("<|assistant|>")

    def test_control_that_collides_with_the_role_marker_keeps_its_slice(self) -> None:
        """An optimized control is decoded vocabulary tokens, so it can contain ordinary words.

        A control holding "assistant" also matches inside ``<|assistant|>``; bounding the search to
        the user content is what keeps the control slice pointing at the suffix GCG optimizes.
        """
        tokenizer = _fast_tokenizer()

        prompt = AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="assistant")

        # <|user|> Say it assistant <|end|> <|assistant|> done <|end|>
        assert prompt._control_slice == slice(3, 4)
        assert prompt._target_slice == slice(6, 7)
        assert prompt._loss_slice == slice(5, 6)

    def test_boundary_holds_when_the_template_ignores_the_generation_prompt(self) -> None:
        """``add_generation_prompt`` is documented as a no-op for templates that do not support it.

        The user-only render is still a prefix of the full prompt, so it cannot be trusted as the
        assistant boundary: it stops before ``<|assistant|>`` and a target of "assistant" would
        match the role marker again.
        """
        tokenizer = _fast_tokenizer(chat_template=_NO_GENERATION_PROMPT_TEMPLATE)

        prompt = AttackPrompt(goal="Say it", target="assistant", tokenizer=tokenizer, control_init="! !")

        # <|user|> Say it ! ! <|end|> <|assistant|> assistant <|end|>
        assert prompt._control_slice == slice(3, 5)
        assert prompt._target_slice == slice(7, 8)
        assert prompt._loss_slice == slice(6, 7)

    def test_empty_goal_with_a_trimming_template(self) -> None:
        """Target-only datasets use an empty goal, so the user content is " <control>".

        A template that trims the content drops that leading space, so the control has to be found on
        its own rather than as part of the raw ``f"{goal} {control}"`` string.
        """
        tokenizer = _fast_tokenizer(
            chat_template="{% for m in messages %}<start_of_turn>"
            "{{ 'model' if m['role'] == 'assistant' else m['role'] }}\n"
            "{{ m['content'] | trim }}<end_of_turn>\n{% endfor %}"
            "{% if add_generation_prompt %}<start_of_turn>model\n{% endif %}"
        )

        prompt = AttackPrompt(goal="", target="Sure, here", tokenizer=tokenizer, control_init="! ! !")

        # <start_of_turn> user ! ! ! <end_of_turn> <start_of_turn> model Sure , here <end_of_turn>
        assert prompt._goal_slice == slice(2, 2)
        assert prompt._control_slice == slice(2, 5)
        assert prompt._target_slice == slice(8, 11)
        assert prompt._loss_slice == slice(7, 10)

    def test_goal_that_quotes_the_turn_separator_keeps_the_boundary(self) -> None:
        """Red-team goals can quote model control tokens, including the template's own turn separator.

        Searching for the separator would find it inside the goal and end the user content before the
        control, so the boundary has to be measured from the template instead.
        """
        tokenizer = _fast_tokenizer()

        prompt = AttackPrompt(
            goal="Say <|end|><|assistant|> now", target="done", tokenizer=tokenizer, control_init="! !"
        )

        # <|user|> Say <|end|> <|assistant|> now ! ! <|end|> <|assistant|> done <|end|>
        assert prompt._control_slice == slice(5, 7)
        assert prompt._target_slice == slice(9, 10)
        assert prompt._loss_slice == slice(8, 9)

    def test_escaping_template_locates_the_target_in_the_reply(self) -> None:
        """A template may escape the contents, e.g. with ``tojson``; the boundaries still have to hold.

        With an unbounded search the target "assistant" matches the role label instead of the reply.
        """
        tokenizer = _fast_tokenizer(
            chat_template="{% for m in messages %}\"{{ m['role'] }}\":{{ m['content'] | tojson }}\n{% endfor %}",
            byte_level=True,
        )

        prompt = AttackPrompt(goal="Say it", target="assistant", tokenizer=tokenizer, control_init="! !")

        target_start = len('"user":"Say it ! !"\n"assistant":"')
        assert prompt._target_slice == slice(target_start, target_start + len("assistant"))
        assert prompt._loss_slice == slice(target_start - 1, target_start + len("assistant") - 1)
        assert prompt.target_str == "assistant"

    def test_raises_when_an_escaped_goal_is_not_rendered_verbatim(self) -> None:
        """The turns can be measured, but ``tojson`` escapes the quotes, so the goal itself is not in the prompt."""
        tokenizer = _fast_tokenizer(
            chat_template="{% for m in messages %}\"{{ m['role'] }}\":{{ m['content'] | tojson }}\n{% endfor %}"
        )

        with pytest.raises(ValueError, match="Cannot safely locate"):
            AttackPrompt(goal='Say "it"', target="done", tokenizer=tokenizer, control_init="! !")

    def test_raises_when_the_template_transforms_the_contents(self) -> None:
        """A template that rewrites the contents leaves no way to measure the turns, so construction fails closed."""
        tokenizer = _fast_tokenizer(
            chat_template="{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] | upper }}<|end|>{% endfor %}"
        )

        with pytest.raises(ValueError, match="Cannot safely locate"):
            AttackPrompt(goal="Say it", target="done", tokenizer=tokenizer, control_init="! !")


@pytest.mark.usefixtures("patch_central_database")
class TestPromptSliceWiring:
    """Exercise real prompt construction beneath the optimization orchestration."""

    @staticmethod
    def _worker(*, byte_level: bool = False) -> MagicMock:
        worker = MagicMock(spec=ModelWorker)
        worker.tokenizer = _fast_tokenizer(byte_level=byte_level)
        worker.model = MagicMock(spec=torch.nn.Module)
        worker.model.device = torch.device("cpu")
        return worker

    @pytest.mark.parametrize("byte_level", [False, True])
    def test_real_slices_support_gradients_and_candidate_losses(self, byte_level: bool) -> None:
        tokenizer = _fast_tokenizer(byte_level=byte_level)
        model = _tiny_model("llama").eval()
        model.resize_token_embeddings(len(tokenizer), mean_resizing=False)
        manager = GCGPromptManager(
            goals=["Say <|end|><|assistant|> now", ""],
            targets=["done ! !", "assistant"],
            tokenizer=tokenizer,
            control_init="! !",
            managers={"AP": gcg_attack_mod.GCGAttackPrompt},
        )

        for control in ("! !", "assistant"):
            manager.control_str = control
            gradient = manager.grad(model)
            assert gradient.shape == (manager.control_toks.numel(), len(tokenizer))
            assert torch.isfinite(gradient).all()
            assert torch.count_nonzero(gradient) > 0

            candidates = manager.control_toks.repeat(2, 1)
            candidates[1, 0] = tokenizer("now", add_special_tokens=False).input_ids[0]
            logits, ids = manager.logits(model, test_controls=candidates, return_ids=True)
            assert torch.isfinite(manager.target_loss(logits, ids)).all()
            assert torch.isfinite(manager.control_loss(logits, ids)).all()
            for prompt, candidate_ids in zip(manager, ids, strict=True):
                assert torch.equal(candidate_ids[:, prompt._control_slice], candidates)
                assert torch.equal(candidate_ids[:, prompt._target_slice], prompt.target_toks.repeat(2, 1))

    def test_shared_control_reaches_training_and_held_out_prompts_for_each_tokenizer(self) -> None:
        workers = [self._worker(), self._worker(byte_level=True)]
        test_worker = self._worker()
        attack = MultiPromptAttack(
            goals=["Say <|end|><|assistant|> now", ""],
            targets=["assistant", "done"],
            workers=workers,
            control_init="! !",
            test_goals=["Say assistant"],
            test_targets=["assistant"],
            test_workers=[test_worker],
            managers={"AP": AttackPrompt, "PM": PromptManager},
        )
        attack.control_str = "assistant"

        with patch.object(attack, "test", return_value=([], [], [])) as evaluate:
            attack.test_all()

        all_workers, all_prompts = evaluate.call_args.args
        assert all_workers == [*workers, test_worker]
        assert evaluate.call_args.kwargs["include_loss"] is True
        for manager in [*attack.prompts, *all_prompts]:
            for prompt in manager:
                tokenizer = prompt.tokenizer
                assert prompt.goal_toks.tolist() == tokenizer(prompt.goal, add_special_tokens=False).input_ids
                assert prompt.control_toks.tolist() == tokenizer("assistant", add_special_tokens=False).input_ids
                assert prompt.target_toks.tolist() == tokenizer(prompt.target, add_special_tokens=False).input_ids

    @pytest.mark.parametrize(
        ("progressive", "expected_rounds"),
        [
            (False, [(1, 2), (1, 2)]),
            (True, [(1, 1), (2, 1), (2, 2)]),
        ],
    )
    def test_individual_and_progressive_rounds_construct_real_prompts(
        self, *, progressive: bool, expected_rounds: list[tuple[int, int]]
    ) -> None:
        rounds: list[tuple[int, int]] = []

        def run_inner(attack: MultiPromptAttack, **kwargs: Any) -> tuple[str, float, int]:
            rounds.append((len(attack.goals), len(attack.workers)))
            attack.control_str = "assistant"
            for manager in attack.prompts:
                for prompt in manager:
                    assert prompt.control_str == "assistant"
                    assert (
                        prompt.target_toks.tolist()
                        == prompt.tokenizer(prompt.target, add_special_tokens=False).input_ids
                    )
            return "assistant", 0.5, 1

        attack_class = ProgressiveMultiPromptAttack if progressive else IndividualPromptAttack
        attack = attack_class(
            goals=["Say <|end|><|assistant|> now", ""],
            targets=["assistant", "done"],
            workers=[self._worker(), self._worker(byte_level=True)],
            control_init="! !",
            managers={"AP": AttackPrompt, "PM": PromptManager, "MPA": MultiPromptAttack},
        )
        with patch.object(MultiPromptAttack, "run", autospec=True, side_effect=run_inner):
            attack.run(n_steps=3, stop_on_success=False, incr_control=False, verbose=False)

        assert rounds == expected_rounds


class TestGetWorkersChatTemplateValidation:
    """Tests for the chat-template precondition in get_workers."""

    def test_raises_when_tokenizer_has_no_chat_template(self) -> None:
        """Models without a chat_template cannot be used with apply_chat_template-based
        GCG; get_workers should raise a clear ValueError pointing to the cause."""
        from unittest.mock import patch

        get_workers = attack_manager_mod.get_workers

        params = MagicMock()
        params.tokenizer_paths = ["fake/no-chat-template-model"]
        params.token = ""
        params.tokenizer_kwargs = [{}]

        bare_tokenizer = MagicMock()
        bare_tokenizer.chat_template = None
        bare_tokenizer.pad_token = "<pad>"

        with patch.object(attack_manager_mod.AutoTokenizer, "from_pretrained", return_value=bare_tokenizer):
            with pytest.raises(ValueError, match="no chat_template configured"):
                get_workers(params)

    def test_starts_workers_for_training(self) -> None:
        params = MagicMock()
        params.tokenizer_paths = ["fake/chat-model"]
        params.tokenizer_kwargs = [{}]
        params.model_paths = ["fake/chat-model"]
        params.model_kwargs = [{}]
        params.devices = ["cpu"]
        params.token = ""
        params.num_train_models = 1

        tokenizer = MagicMock()
        tokenizer.pad_token = "<pad>"
        tokenizer.chat_template = "{{ messages[0]['content'] }}"
        worker = MagicMock()

        with (
            patch.object(attack_manager_mod.AutoTokenizer, "from_pretrained", return_value=tokenizer),
            patch.object(attack_manager_mod, "ModelWorker", return_value=worker),
        ):
            train_workers, test_workers = attack_manager_mod.get_workers(params, evaluation=False)

        worker.start.assert_called_once_with()
        assert train_workers == [worker]
        assert test_workers == []


def test_model_worker_uses_model_device_dispatch() -> None:
    model = MagicMock()
    moved_model = MagicMock()
    evaluated_model = MagicMock()
    model.to.return_value = moved_model
    moved_model.eval.return_value = evaluated_model

    with (
        patch.object(attack_manager_mod.AutoModelForCausalLM, "from_pretrained", return_value=model),
        patch.object(attack_manager_mod.mp, "JoinableQueue", side_effect=[MagicMock(), MagicMock()]),
    ):
        worker = ModelWorker(
            model_path="fake/model",
            token="",
            model_kwargs={},
            tokenizer=MagicMock(),
            device="cpu",
        )

    model.to.assert_called_once_with(torch.device("cpu"))
    moved_model.eval.assert_called_once_with()
    assert worker.model is evaluated_model


def test_model_worker_task_payload_excludes_model() -> None:
    worker = object.__new__(ModelWorker)
    worker.model = sentinel.model
    worker.tasks = MagicMock()
    prompt = {"prompt": "value"}

    worker(prompt, ModelWorkerOperation.GRAD, 42, option=True)

    task = worker.tasks.put.call_args.args[0]
    assert isinstance(task, ModelWorkerTask)
    assert task.obj == prompt
    assert task.obj is not prompt
    assert task.operation is ModelWorkerOperation.GRAD
    assert task.args == (42,)
    assert task.kwargs == {"option": True}
    assert not hasattr(task, "model")
    assert all(argument is not worker.model for argument in task.args)
    assert all(value is not worker.model for value in task.kwargs.values())
    assert pickle.loads(pickle.dumps(task)) == task


@pytest.mark.parametrize(
    ("operation", "method_name"),
    [
        (ModelWorkerOperation.GRAD, "grad"),
        (ModelWorkerOperation.LOGITS, "logits"),
        (ModelWorkerOperation.CONTRAST_LOGITS, "contrast_logits"),
        (ModelWorkerOperation.TEST, "test"),
        (ModelWorkerOperation.TEST_LOSS, "test_loss"),
    ],
)
def test_model_worker_run_uses_worker_owned_model(operation: ModelWorkerOperation, method_name: str) -> None:
    model = MagicMock()
    target = MagicMock()
    getattr(target, method_name).return_value = sentinel.result
    task = ModelWorkerTask(
        obj=target,
        operation=operation,
        args=(sentinel.argument,),
        kwargs={"option": True},
    )
    tasks = MagicMock()
    tasks.get.side_effect = [task, None]
    results = MagicMock()

    ModelWorker.run(model, tasks, results)

    model.requires_grad_.assert_called_once_with(False)
    model.zero_grad.assert_called_once_with(set_to_none=True)
    getattr(target, method_name).assert_called_once_with(model, sentinel.argument, option=True)
    results.put.assert_called_once_with(sentinel.result)
    assert tasks.task_done.call_count == 2


def test_multi_prompt_test_dispatches_without_model_payload() -> None:
    attack = object.__new__(MultiPromptAttack)
    worker = MagicMock()
    worker.results.get.side_effect = [[(True, 1)], [0.25]]
    prompt = MagicMock()

    result = attack.test([worker], [prompt], include_loss=True)

    assert result == ([[True]], [[1]], [[0.25]])
    assert worker.call_args_list == [
        call(prompt, ModelWorkerOperation.TEST),
        call(prompt, ModelWorkerOperation.TEST_LOSS),
    ]


class _Queue:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def get(self) -> Any:
        return self._items.pop(0)


class _WorkerStub:
    def __init__(
        self,
        *,
        gradient: torch.Tensor,
        logits: torch.Tensor,
        token_ids: torch.Tensor,
        tokenizer: MagicMock,
    ) -> None:
        self.model = MagicMock()
        self.model.device = "cpu"
        self.tokenizer = tokenizer
        self.results = _Queue([gradient, (logits, token_ids)])
        self.calls: list[tuple] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


class _PromptManagerStub:
    def __init__(
        self,
        *,
        prompt: AttackPrompt,
        control_tokens: torch.Tensor,
        disallowed_tokens: torch.Tensor,
        control_str: str,
    ) -> None:
        self._prompts = [prompt]
        self._control_tokens = control_tokens
        self._disallowed_tokens = disallowed_tokens
        self.control_str = control_str

    def __len__(self) -> int:
        return len(self._prompts)

    def __getitem__(self, i: int) -> AttackPrompt:
        return self._prompts[i]

    @property
    def control_toks(self) -> torch.Tensor:
        return self._control_tokens

    @property
    def disallowed_toks(self) -> torch.Tensor:
        return self._disallowed_tokens


class _SpySampling:
    def __init__(self, *, sampled_tokens: torch.Tensor) -> None:
        self.sampled_tokens = sampled_tokens
        self.calls: list[dict] = []

    def sample_candidates(
        self,
        *,
        gradient: torch.Tensor,
        control_tokens: torch.Tensor,
        batch_size: int,
        top_k: int,
        temperature: float,
        allow_non_ascii: bool,
        non_ascii_tokens: torch.Tensor,
    ) -> torch.Tensor:
        self.calls.append(
            {
                "gradient": gradient.clone(),
                "control_tokens": control_tokens.clone(),
                "batch_size": batch_size,
                "top_k": top_k,
                "temperature": temperature,
                "allow_non_ascii": allow_non_ascii,
                "non_ascii_tokens": non_ascii_tokens.clone(),
            }
        )
        return self.sampled_tokens.clone()


class _SpyLoss:
    def __init__(self, *, losses: torch.Tensor) -> None:
        self.losses = losses
        self.calls: list[dict] = []

    def compute_loss(
        self,
        *,
        logits: torch.Tensor,
        token_ids: torch.Tensor,
        target_slice: slice,
        control_slice: slice,
    ) -> torch.Tensor:
        self.calls.append(
            {
                "logits": logits.clone(),
                "token_ids": token_ids.clone(),
                "target_slice": target_slice,
                "control_slice": control_slice,
            }
        )
        return self.losses.to(logits.device)


class _SpyFilter:
    def __init__(self, *, candidates: list[str]) -> None:
        self.candidates = list(candidates)
        self.calls: list[dict] = []

    def filter_candidates(
        self,
        *,
        candidate_tokens: torch.Tensor,
        tokenizer: MagicMock,
        current_control: str,
    ) -> list[str]:
        self.calls.append(
            {
                "candidate_tokens": candidate_tokens.clone(),
                "tokenizer": tokenizer,
                "current_control": current_control,
            }
        )
        return list(self.candidates)


class TestGCGMultiPromptAttackStepWiring:
    @staticmethod
    def _make_tokenizer() -> MagicMock:
        tokenizer = MagicMock()
        tokenizer.vocab_size = 100

        def decode_fn(ids, **_kwargs):
            values = ids.tolist() if hasattr(ids, "tolist") else list(ids)
            return " ".join(str(int(v)) for v in values)

        def call_fn(text, **_kwargs):
            output = MagicMock()
            if text == "!":
                output.input_ids = [0]
            else:
                output.input_ids = [int(piece) for piece in text.split()] if text else []
            return output

        tokenizer.decode.side_effect = decode_fn
        tokenizer.side_effect = call_fn
        return tokenizer

    @staticmethod
    def _make_prompt(*, target_slice: slice, control_slice: slice) -> AttackPrompt:
        prompt = object.__new__(AttackPrompt)
        prompt._target_slice = target_slice
        prompt._control_slice = control_slice
        return prompt

    @staticmethod
    def _make_attack(
        *,
        worker: _WorkerStub,
        prompt_manager: _PromptManagerStub,
        sampling: object | None = None,
        loss: object | None = None,
        candidate_filter: object | None = None,
    ) -> GCGMultiPromptAttack:
        attack = object.__new__(GCGMultiPromptAttack)
        attack.workers = [worker]
        attack.models = [worker.model]
        attack.prompts = [prompt_manager]
        attack._sampling = sampling
        attack._loss = loss
        attack._candidate_filter = candidate_filter
        return attack

    def test_step_default_path_matches_legacy_behavior(self) -> None:
        gradient = torch.tensor(
            [
                [0.3, -0.4, 0.8, -0.2, 0.1, 0.5],
                [-0.3, 0.2, -0.8, 0.4, 0.1, 0.7],
                [0.2, 0.6, -0.1, -0.5, 0.4, -0.2],
            ],
            dtype=torch.float32,
        )
        logits = torch.randn(1, 8, 10)
        token_ids = torch.randint(0, 10, (1, 8))
        control_tokens = torch.tensor([1, 2, 3], dtype=torch.long)
        disallowed_tokens = torch.tensor([], dtype=torch.long)
        target_slice = slice(4, 6)
        control_slice = slice(1, 4)
        current_control = "99 99 99"
        tokenizer = self._make_tokenizer()

        worker = _WorkerStub(gradient=gradient.clone(), logits=logits, token_ids=token_ids, tokenizer=tokenizer)
        prompt = self._make_prompt(target_slice=target_slice, control_slice=control_slice)
        prompt_manager = _PromptManagerStub(
            prompt=prompt,
            control_tokens=control_tokens,
            disallowed_tokens=disallowed_tokens,
            control_str=current_control,
        )
        attack = self._make_attack(worker=worker, prompt_manager=prompt_manager)

        target_weight = 1.3
        control_weight = 0.2
        torch.manual_seed(2026)
        actual_control, actual_loss = attack.step(
            batch_size=1,
            topk=3,
            temp=1.0,
            allow_non_ascii=True,
            target_weight=target_weight,
            control_weight=control_weight,
            verbose=True,
            filter_cand=True,
        )

        legacy_prompt_manager = object.__new__(GCGPromptManager)
        legacy_prompt_for_sampling = MagicMock()
        legacy_prompt_for_sampling.control_toks = control_tokens.clone()
        legacy_prompt_manager._prompts = [legacy_prompt_for_sampling]
        legacy_prompt_manager._nonascii_toks = disallowed_tokens

        legacy_attack = object.__new__(MultiPromptAttack)
        legacy_worker = MagicMock()
        legacy_worker.tokenizer = tokenizer
        legacy_attack.workers = [legacy_worker]

        legacy_prompt_for_loss = self._make_prompt(target_slice=target_slice, control_slice=control_slice)
        normalized_gradient = gradient / gradient.norm(dim=-1, keepdim=True)
        torch.manual_seed(2026)
        legacy_control_cand = legacy_prompt_manager.sample_control(
            normalized_gradient.clone(),
            1,
            topk=3,
            temp=1.0,
            allow_non_ascii=True,
        )
        legacy_controls = legacy_attack.get_filtered_cands(
            0,
            legacy_control_cand,
            filter_cand=True,
            curr_control=current_control,
        )
        legacy_loss = target_weight * legacy_prompt_for_loss.target_loss(logits, token_ids).mean(
            dim=-1
        ) + control_weight * legacy_prompt_for_loss.control_loss(logits, token_ids).mean(dim=-1)

        assert actual_control == legacy_controls[0]
        assert actual_loss == pytest.approx(legacy_loss[0].item())
        grad_args, grad_kwargs = worker.calls[0]
        assert grad_args == (prompt_manager, ModelWorkerOperation.GRAD)
        assert grad_kwargs == {}
        logits_args, logits_kwargs = worker.calls[1]
        assert logits_args[0] is prompt
        assert logits_args[1] is ModelWorkerOperation.LOGITS
        assert len(logits_args) == 3
        assert all(argument is not worker.model for argument in logits_args)
        assert logits_kwargs == {"return_ids": True}

    def test_step_uses_custom_protocol_implementations_when_supplied(self) -> None:
        gradient = torch.randn(3, 6)
        logits = torch.randn(2, 8, 10)
        token_ids = torch.randint(0, 10, (2, 8))
        control_tokens = torch.tensor([1, 2, 3], dtype=torch.long)
        disallowed_tokens = torch.tensor([5], dtype=torch.long)
        tokenizer = self._make_tokenizer()

        worker = _WorkerStub(gradient=gradient.clone(), logits=logits, token_ids=token_ids, tokenizer=tokenizer)
        prompt = self._make_prompt(target_slice=slice(4, 6), control_slice=slice(1, 4))
        prompt_manager = _PromptManagerStub(
            prompt=prompt,
            control_tokens=control_tokens,
            disallowed_tokens=disallowed_tokens,
            control_str="current control",
        )

        sampled_tokens = torch.tensor([[8, 8, 8], [9, 9, 9]], dtype=torch.long)
        sampling = _SpySampling(sampled_tokens=sampled_tokens)
        candidate_filter = _SpyFilter(candidates=["candidate-A", "candidate-B"])
        custom_losses = torch.tensor([3.0, 0.5], dtype=torch.float32)
        loss = _SpyLoss(losses=custom_losses)
        attack = self._make_attack(
            worker=worker,
            prompt_manager=prompt_manager,
            sampling=sampling,
            loss=loss,
            candidate_filter=candidate_filter,
        )

        selected_control, normalized_loss = attack.step(
            batch_size=2,
            topk=4,
            temp=0.8,
            allow_non_ascii=False,
            target_weight=0.0,
            control_weight=1.0,
            verbose=True,
            filter_cand=True,
        )

        assert selected_control == "candidate-B"
        assert normalized_loss == pytest.approx(0.5)
        assert len(sampling.calls) == 1
        assert len(candidate_filter.calls) == 1
        assert len(loss.calls) == 1
        assert sampling.calls[0]["batch_size"] == 2
        assert sampling.calls[0]["top_k"] == 4
        assert sampling.calls[0]["allow_non_ascii"] is False
        assert candidate_filter.calls[0]["current_control"] == "current control"

    def test_gcg_multi_prompt_attack_init_with_custom_protocols(self) -> None:
        """Test GCGMultiPromptAttack.__init__ stores custom sampling/loss/filter."""
        sampling = _SpySampling(sampled_tokens=torch.tensor([[1, 2, 3]]))
        loss = _SpyLoss(losses=torch.tensor([1.0]))
        candidate_filter = _SpyFilter(candidates=["filtered"])
        workers = [MagicMock()]

        with patch.object(MultiPromptAttack, "__init__", return_value=None) as mock_base_init:
            attack = GCGMultiPromptAttack(
                goals=["goal"],
                targets=["target"],
                workers=workers,
                control_init="seed control",
                sampling=sampling,
                loss=loss,
                candidate_filter=candidate_filter,
            )

        assert mock_base_init.call_count == 1
        assert mock_base_init.call_args.args[:4] == (["goal"], ["target"], workers, "seed control")

        assert attack._sampling is sampling
        assert attack._loss is loss
        assert attack._candidate_filter is candidate_filter

    def test_step_aggregates_workers_when_grad_shapes_mismatch(self) -> None:
        """Test step handles a worker gradient shape mismatch by sampling per group."""
        tokenizer = self._make_tokenizer()
        prompt = self._make_prompt(target_slice=slice(0, 1), control_slice=slice(0, 1))
        prompt_manager1 = _PromptManagerStub(
            prompt=prompt,
            control_tokens=torch.tensor([1], dtype=torch.long),
            disallowed_tokens=torch.tensor([], dtype=torch.long),
            control_str="seed",
        )
        prompt_manager2 = _PromptManagerStub(
            prompt=prompt,
            control_tokens=torch.tensor([1], dtype=torch.long),
            disallowed_tokens=torch.tensor([], dtype=torch.long),
            control_str="seed",
        )

        grad1 = torch.tensor([[0.1, 0.2, 0.3]], dtype=torch.float32)
        grad2 = torch.tensor([[0.4, 0.5, 0.6, 0.7]], dtype=torch.float32)
        logits = torch.randn(1, 8, 10)
        token_ids = torch.randint(0, 10, (1, 8))
        worker1 = _WorkerStub(gradient=grad1, logits=logits, token_ids=token_ids, tokenizer=tokenizer)
        worker2 = _WorkerStub(gradient=grad2, logits=logits, token_ids=token_ids, tokenizer=tokenizer)
        worker1.results = _Queue([grad1, (logits, token_ids), (logits, token_ids)])
        worker2.results = _Queue([grad2, (logits, token_ids), (logits, token_ids)])

        attack = object.__new__(GCGMultiPromptAttack)
        attack.workers = [worker1, worker2]
        attack.models = [worker1.model]
        attack.prompts = [prompt_manager1, prompt_manager2]
        attack.control_str = "seed"

        class _ConstantLoss:
            @staticmethod
            def compute_loss(
                *,
                logits: torch.Tensor,
                token_ids: torch.Tensor,
                target_slice: slice,
                control_slice: slice,
            ) -> torch.Tensor:
                return torch.tensor([0.5], dtype=torch.float32)

        with (
            patch.object(
                attack,
                "_sample_control_candidates",
                return_value=torch.tensor([[1, 2, 3]], dtype=torch.long),
            ) as mock_sample,
            patch.object(attack, "_filter_control_candidates", return_value=["candidate"]),
            patch.object(attack, "_resolve_loss", return_value=_ConstantLoss()),
            patch.object(attack, "_get_control_length", return_value=None),
        ):
            control, normalized_loss = attack.step(
                batch_size=1,
                topk=2,
                temp=1.0,
                allow_non_ascii=True,
                target_weight=1.0,
                control_weight=0.1,
                verbose=True,
                filter_cand=True,
            )

        assert control == "candidate"
        assert normalized_loss == pytest.approx(0.5)
        assert mock_sample.call_count == 2
        assert mock_sample.call_args_list[0].kwargs["worker_index"] == 0
        assert mock_sample.call_args_list[1].kwargs["worker_index"] == 1

    def test_resolve_methods_return_defaults_when_none(self) -> None:
        """Test _resolve_* methods return defaults when custom protocols are None."""
        worker = _WorkerStub(
            gradient=torch.tensor([[0.1]]),
            logits=torch.randn(1, 8, 10),
            token_ids=torch.randint(0, 10, (1, 8)),
            tokenizer=self._make_tokenizer(),
        )
        prompt_manager = _PromptManagerStub(
            prompt=self._make_prompt(target_slice=slice(0, 1), control_slice=slice(0, 1)),
            control_tokens=torch.tensor([1]),
            disallowed_tokens=torch.tensor([]),
            control_str="test",
        )

        attack = self._make_attack(worker=worker, prompt_manager=prompt_manager)

        # Test _resolve_sampling returns default
        sampler = attack._resolve_sampling()
        assert sampler is not None

        # Test _resolve_loss returns default
        loss_func = attack._resolve_loss(target_weight=1.0, control_weight=0.1)
        assert loss_func is not None

        # Test _resolve_candidate_filter returns default
        filter_func = attack._resolve_candidate_filter(filter_cand=True)
        assert filter_func is not None

    def test_get_control_length_success(self) -> None:
        """Test _get_control_length returns token count after dropping the first token."""
        tokenizer = self._make_tokenizer()
        worker = _WorkerStub(
            gradient=torch.tensor([[0.1]]),
            logits=torch.randn(1, 8, 10),
            token_ids=torch.randint(0, 10, (1, 8)),
            tokenizer=tokenizer,
        )
        attack = object.__new__(GCGMultiPromptAttack)
        attack.workers = [worker]

        length = attack._get_control_length(control="1 2 3")
        assert length == 2

    def test_get_control_length_handles_error(self) -> None:
        """Test _get_control_length returns None on tokenizer error."""
        tokenizer = MagicMock()
        tokenizer.side_effect = ValueError("Tokenizer error")

        worker = _WorkerStub(
            gradient=torch.tensor([[0.1]]),
            logits=torch.randn(1, 8, 10),
            token_ids=torch.randint(0, 10, (1, 8)),
            tokenizer=tokenizer,
        )
        attack = object.__new__(GCGMultiPromptAttack)
        attack.workers = [worker]

        length = attack._get_control_length(control="test")
        assert length is None


def test_attack_prompt_logits_rejects_non_string_controls() -> None:
    prompt = object.__new__(AttackPrompt)
    prompt._control_slice = slice(1, 3)
    prompt.input_ids = torch.tensor([0, 1, 2, 3])
    prompt.tokenizer = MagicMock()
    model = MagicMock()
    model.device = torch.device("cpu")

    with pytest.raises(ValueError, match="list of strings or a tensor"):
        prompt.logits(model, test_controls=123)


def test_attack_prompt_logits_builds_attention_mask() -> None:
    prompt = object.__new__(AttackPrompt)
    prompt._control_slice = slice(1, 3)
    prompt.input_ids = torch.tensor([0, 1, 2, 3])
    prompt.tokenizer = MagicMock()
    prompt.tokenizer.return_value.input_ids = [5, 6]
    model = MagicMock()
    model.device = torch.device("cpu")
    model.return_value.logits = torch.randn(1, 4, 8)

    logits = prompt.logits(model, test_controls=["candidate"])

    assert logits.shape == (1, 4, 8)
    assert torch.equal(model.call_args.kwargs["attention_mask"], torch.ones(1, 4, dtype=torch.long))


def test_prompt_manager_grad_streams_and_sums_prompt_gradients() -> None:
    prompt_manager = object.__new__(PromptManager)
    first_prompt = MagicMock()
    first_prompt.grad.return_value = torch.tensor([1.0, 2.0])
    second_prompt = MagicMock()
    second_prompt.grad.return_value = torch.tensor([3.0, 4.0])
    third_prompt = MagicMock()
    third_prompt.grad.return_value = torch.tensor([5.0, 6.0])
    prompt_manager._prompts = [first_prompt, second_prompt, third_prompt]
    model = MagicMock()

    with patch.object(torch, "stack", side_effect=AssertionError("prompt gradients must be streamed")):
        result = prompt_manager.grad(model)

    assert torch.equal(result, torch.tensor([9.0, 12.0]))
    for prompt in prompt_manager._prompts:
        prompt.grad.assert_called_once_with(model)


def test_prompt_manager_grad_preserves_fp16_reduction_precision() -> None:
    prompt_manager = object.__new__(PromptManager)
    prompt_gradients = [
        torch.tensor([10000.0], dtype=torch.float16),
        torch.tensor([1.0], dtype=torch.float16),
        torch.tensor([-10000.0], dtype=torch.float16),
    ]
    prompt_manager._prompts = [MagicMock() for _ in prompt_gradients]
    for prompt, gradient in zip(prompt_manager._prompts, prompt_gradients, strict=True):
        prompt.grad.return_value = gradient

    result = prompt_manager.grad(MagicMock())

    expected = torch.stack(prompt_gradients).sum(dim=0)
    assert torch.equal(result, expected)
    assert result.item() == 1.0
    assert result.dtype is torch.float16


def test_multi_prompt_run_anneals_and_accepts_lower_loss() -> None:
    attack = object.__new__(MultiPromptAttack)
    prompt_manager = MagicMock()
    prompt_manager.control_str = "initial"
    attack.prompts = [prompt_manager]
    attack.logfile = None
    attack.step = MagicMock(return_value=("better", 1.0))

    control, loss, steps = attack.run(
        n_steps=1,
        prev_loss=2.0,
        stop_on_success=False,
        anneal=True,
    )

    assert (control, loss, steps) == ("better", 1.0, 1)


def test_multi_prompt_log_requires_logfile_after_parsing_results() -> None:
    attack = object.__new__(MultiPromptAttack)
    attack.goals = []
    attack.test_goals = []
    attack.workers = []
    attack.test_workers = []
    attack.logfile = None

    with pytest.raises(ValueError, match="without a logfile path"):
        attack.log(
            step_num=1,
            n_steps=1,
            control="control",
            loss=1.0,
            runtime=0.1,
            model_tests=([[True]], [[1]], [[1.0]]),
        )


def test_evaluate_attack_run_with_no_controls_returns_empty_results() -> None:
    attack = object.__new__(EvaluateAttack)
    worker = MagicMock()
    attack.workers = [worker]
    attack.logfile = None

    results = attack.run(steps=0, controls=[], batch_size=1)

    assert results == ([], [], [], [], [], [])


def test_gcg_step_requires_worker() -> None:
    attack = object.__new__(GCGMultiPromptAttack)
    attack.workers = []

    with pytest.raises(ValueError, match="at least one worker"):
        attack.step()


def test_token_gradients_matches_backward_without_model_parameter_gradients() -> None:
    torch.manual_seed(2026)
    backward_model = _TinyCausalLM()
    input_only_model = deepcopy(backward_model)
    input_ids = torch.tensor([0, 1, 2, 3, 4])
    input_slice = slice(1, 3)
    target_slice = slice(3, 5)
    loss_slice = slice(2, 4)
    expected = _backward_coordinate_gradient(
        model=backward_model,
        input_ids=input_ids,
        input_slice=input_slice,
        target_slice=target_slice,
        loss_slice=loss_slice,
    )

    with (
        patch.object(gcg_attack_mod, "get_embedding_matrix", side_effect=lambda model: model.embedding.weight),
        patch.object(gcg_attack_mod, "get_embeddings", side_effect=lambda model, ids: model.embedding(ids)),
    ):
        actual = token_gradients(
            input_only_model,
            input_ids,
            input_slice=input_slice,
            target_slice=target_slice,
            loss_slice=loss_slice,
        )

    assert torch.equal(actual, expected)
    assert not actual.requires_grad
    assert actual.grad_fn is None
    assert all(parameter.grad is None for parameter in input_only_model.parameters())


def test_token_gradients_raises_when_coordinate_gradient_missing() -> None:
    model = MagicMock()
    model.device = torch.device("cpu")
    model.return_value.logits = torch.randn(1, 3, 4)
    loss = MagicMock()
    loss_function = MagicMock(return_value=loss)

    with (
        patch.object(gcg_attack_mod, "get_embedding_matrix", return_value=torch.ones(4, 2)),
        patch.object(gcg_attack_mod, "get_embeddings", return_value=torch.ones(1, 3, 2)),
        patch.object(gcg_attack_mod.nn, "CrossEntropyLoss", return_value=loss_function),
        patch.object(gcg_attack_mod.torch.autograd, "grad", return_value=(None,)),
        pytest.raises(RuntimeError, match="Autograd did not produce token gradients"),
    ):
        token_gradients(
            model,
            torch.tensor([0, 1, 2]),
            input_slice=slice(0, 1),
            target_slice=slice(1, 2),
            loss_slice=slice(0, 1),
        )


def test_length_preserving_filter_rejects_unknown_option() -> None:
    with pytest.raises(TypeError, match="Unexpected LengthPreservingFilter option: unexpected"):
        LengthPreservingFilter(unexpected=True)


# (attack class, n_workers, n_goals, constructor kwargs) for every execution
# topology #2490 requires coverage of.
_TRAJECTORY_TOPOLOGIES: dict[str, tuple[type, int, int, dict[str, bool]]] = {
    "individual-1w-1g": (IndividualPromptAttack, 1, 1, {}),
    "individual-1w-2g": (IndividualPromptAttack, 1, 2, {}),
    "progressive-goals-1w-2g": (
        ProgressiveMultiPromptAttack,
        1,
        2,
        {"progressive_goals": True, "progressive_models": False},
    ),
    "progressive-goals-models-2w-2g": (
        ProgressiveMultiPromptAttack,
        2,
        2,
        {"progressive_goals": True, "progressive_models": True},
    ),
    "multi-2w-2g": (
        ProgressiveMultiPromptAttack,
        2,
        2,
        {"progressive_goals": False, "progressive_models": False},
    ),
}


def _run_trajectory(
    topology: str,
    *,
    seed: int,
    logfile: Path,
    run_id: str = "",
    barrier: threading.Barrier | None = None,
    switches: list[str] | None = None,
) -> dict[str, Any]:
    """Drive a real outer attack -> real GCG step loop on stub workers and return everything observable."""
    attack_cls, n_workers, n_goals, attack_kwargs = _TRAJECTORY_TOPOLOGIES[topology]
    events: list[Any] = []
    bundles: list[Any] = []
    workers = [TrajectoryWorker(i, run_id=run_id, barrier=barrier, events=switches) for i in range(n_workers)]
    managers = {"PM": TrajectoryPromptManager, "MPA": partial(RecordingGCGAttack, events=events, bundles=bundles)}
    attack = attack_cls(
        [f"goal {i}" for i in range(n_goals)],
        ["10 11", "12 13"][:n_goals],
        workers,
        control_init="1 2 3",
        test_prefixes=[],
        logfile=str(logfile),
        managers=managers,
        **attack_kwargs,
    )
    final_control, _ = attack.run(
        n_steps=4,
        batch_size=4,
        topk=6,
        allow_non_ascii=True,
        target_weight=1.0,
        control_weight=0.0,
        anneal=True,
        test_steps=1,
        incr_control=False,
        stop_on_success=False,
        verbose=False,
        random_seed=seed,
    )
    # A bundle rebuilt per inner phase would restart every stream; all phases must see the one object.
    assert bundles and bundles[0] is not None and all(b is bundles[0] for b in bundles), "phases must share one bundle"
    with open(logfile) as f:
        log = json.load(f)
    return {
        "events": events,
        "final_control": final_control,
        "controls": log["controls"],
        "losses": [round(loss, 6) for loss in log["losses"]],
    }


class TestRandomSeedDeterminism:
    """Verify that random_seed produces reproducible results across runs."""

    def test_target_augmentation_deterministic_same_seed(self) -> None:
        """Same seed produces identical augmentation results."""
        targets = ["Sure, here is how to hack", "Sure, here is how to pick a lock"]
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(42)

        result1, _ = GCGGenerator._apply_target_augmentation(train_targets=targets, test_targets=[], np_rng=rng1)
        result2, _ = GCGGenerator._apply_target_augmentation(train_targets=targets, test_targets=[], np_rng=rng2)

        assert result1 == result2

    def test_target_augmentation_different_seed_can_differ(self) -> None:
        """Different seeds can produce different augmentation results."""
        targets = ["Sure, here is how to hack"] * 20
        rng1 = np.random.default_rng(1)
        rng2 = np.random.default_rng(999)

        result1, _ = GCGGenerator._apply_target_augmentation(train_targets=targets, test_targets=[], np_rng=rng1)
        result2, _ = GCGGenerator._apply_target_augmentation(train_targets=targets, test_targets=[], np_rng=rng2)

        assert result1 != result2

    def test_sampling_deterministic_same_seed(self) -> None:
        """StandardGCGSampling produces identical candidates with same torch Generator seed."""
        sampler = StandardGCGSampling()
        gradient = torch.randn(5, 100)
        control_tokens = torch.tensor([1, 2, 3, 4, 5], dtype=torch.long)
        non_ascii = torch.tensor([50], dtype=torch.long)

        gen1 = torch.Generator().manual_seed(42)
        gen2 = torch.Generator().manual_seed(42)

        result1 = sampler.sample_candidates(
            gradient=gradient.clone(),
            control_tokens=control_tokens.clone(),
            batch_size=8,
            top_k=10,
            temperature=1.0,
            allow_non_ascii=True,
            non_ascii_tokens=non_ascii,
            torch_generator=gen1,
        )
        result2 = sampler.sample_candidates(
            gradient=gradient.clone(),
            control_tokens=control_tokens.clone(),
            batch_size=8,
            top_k=10,
            temperature=1.0,
            allow_non_ascii=True,
            non_ascii_tokens=non_ascii,
            torch_generator=gen2,
        )

        assert torch.equal(result1, result2)

    def test_sampling_different_seed_can_differ(self) -> None:
        """Different torch Generator seeds can produce different candidates."""
        sampler = StandardGCGSampling()
        gradient = torch.randn(5, 100)
        control_tokens = torch.tensor([1, 2, 3, 4, 5], dtype=torch.long)
        non_ascii = torch.tensor([50], dtype=torch.long)

        gen1 = torch.Generator().manual_seed(1)
        gen2 = torch.Generator().manual_seed(999)

        result1 = sampler.sample_candidates(
            gradient=gradient.clone(),
            control_tokens=control_tokens.clone(),
            batch_size=8,
            top_k=10,
            temperature=1.0,
            allow_non_ascii=True,
            non_ascii_tokens=non_ascii,
            torch_generator=gen1,
        )
        result2 = sampler.sample_candidates(
            gradient=gradient.clone(),
            control_tokens=control_tokens.clone(),
            batch_size=8,
            top_k=10,
            temperature=1.0,
            allow_non_ascii=True,
            non_ascii_tokens=non_ascii,
            torch_generator=gen2,
        )

        assert not torch.equal(result1, result2)

    @staticmethod
    def _run_annealing_with_boolean_tracking(
        seed: int,
    ) -> tuple[str, list[bool]]:
        """Run annealing and capture per-step acceptance booleans.

        ``run()`` sets ``self.control_str`` only when a candidate is accepted.
        We snapshot ``control_str`` at the *start* of each ``step()`` call; a
        change between consecutive snapshots proves the previous candidate was
        accepted.  The last step's decision is derived from the final control.
        """
        steps = [("c1", 2.1), ("c2", 2.2), ("c3", 2.3)]
        attack = object.__new__(MultiPromptAttack)
        attack.prompts = [MagicMock(control_str="initial")]
        attack.logfile = None

        snapshots: list[str] = []
        real_step = MagicMock(side_effect=list(steps))

        def tracking_step(**kwargs: Any) -> tuple[str, float]:
            snapshots.append(attack.control_str)
            return real_step(**kwargs)

        attack.step = MagicMock(side_effect=tracking_step)

        control, _, _ = attack.run(
            n_steps=3,
            prev_loss=2.0,
            stop_on_success=False,
            anneal=True,
            random_seed=seed,
        )

        accepted = [snapshots[i + 1] != snapshots[i] for i in range(len(snapshots) - 1)]
        accepted.append(control != snapshots[-1])

        return control, accepted

    def test_annealing_exact_history_same_seed(self) -> None:
        """Same seed reproduces the exact step-by-step acceptance booleans."""
        for _ in range(2):
            control, accepted = self._run_annealing_with_boolean_tracking(seed=42)
            # seed=42: accept c1 (draw=0.64 < threshold=0.86), accept c2 (draw=0.02 < 0.74),
            # reject c3 (draw=0.28 > threshold≈0 at temp≈1e-7) → final="c2"
            assert accepted == [True, True, False]
            assert control == "c2"

    def test_annealing_exact_history_different_seeds(self) -> None:
        """Different seeds produce verifiably different acceptance boolean sequences."""
        control_1, accepted_1 = self._run_annealing_with_boolean_tracking(seed=1)
        control_999, accepted_999 = self._run_annealing_with_boolean_tracking(seed=999)

        # Pre-computed from random.Random(seed) draws against acceptance_probability.
        # seed=1: draw=0.13<0.86→accept, draw=0.85>0.74→reject, draw=0.76>≈0→reject
        assert accepted_1 == [True, False, False]
        assert control_1 == "c1"
        # seed=999: draw=0.78<0.86→accept, draw=0.08<0.74→accept, draw=0.87>≈0→reject
        assert accepted_999 == [True, True, False]
        assert control_999 == "c2"

    @pytest.mark.parametrize("topology", list(_TRAJECTORY_TOPOLOGIES), ids=list(_TRAJECTORY_TOPOLOGIES))
    def test_overlapping_runs_reproduce_isolated_trajectories(self, topology: str, tmp_path: Path) -> None:
        """Two runs executing at the same time follow exactly the trajectories they follow alone.

        Real ``IndividualPromptAttack`` / ``ProgressiveMultiPromptAttack`` drive the real
        ``GCGMultiPromptAttack.step()`` (Torch sampling, length filter, cross-entropy loss,
        Python annealing) against stub workers whose outputs are pure functions of their
        inputs, so the seeded bundle is the only source of randomness. Each run writes a real
        logfile. A barrier in the stub worker forces the two runs to alternate step by step,
        so the recorded run-id sequence switches more than the single time a sequential
        execution would.
        """
        baseline_42 = _run_trajectory(topology, seed=42, logfile=tmp_path / "baseline_42.json")
        baseline_7 = _run_trajectory(topology, seed=7, logfile=tmp_path / "baseline_7.json")
        assert _run_trajectory(topology, seed=42, logfile=tmp_path / "repeat_42.json") == baseline_42
        assert baseline_42["events"] != baseline_7["events"]

        barrier = threading.Barrier(2, timeout=10)
        switches: list[str] = []
        outcomes: dict[str, Any] = {}

        def run(run_id: str, seed: int) -> None:
            try:
                outcomes[run_id] = _run_trajectory(
                    topology,
                    seed=seed,
                    logfile=tmp_path / f"{run_id}.json",
                    run_id=run_id,
                    barrier=barrier,
                    switches=switches,
                )
            except BaseException as exc:  # noqa: BLE001 - release the peer thread, then surface the error
                barrier.abort()
                outcomes[run_id] = exc

        threads = [threading.Thread(target=run, args=("A", 42)), threading.Thread(target=run, args=("B", 7))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        for outcome in outcomes.values():
            if isinstance(outcome, BaseException):
                raise outcome
        assert sum(a != b for a, b in zip(switches, switches[1:], strict=False)) > 1, switches
        assert outcomes["A"] == baseline_42
        assert outcomes["B"] == baseline_7

    def test_run_creates_torch_gen_for_step(self) -> None:
        """run() sets self._torch_gen so step() can access it for sampling."""
        attack = object.__new__(MultiPromptAttack)
        prompt_manager = MagicMock()
        prompt_manager.control_str = "initial"
        attack.prompts = [prompt_manager]
        attack.logfile = None
        attack.step = MagicMock(return_value=("result", 0.5))

        attack.run(n_steps=1, stop_on_success=False, anneal=False, random_seed=123)

        assert hasattr(attack, "_torch_gens")
        assert isinstance(attack._torch_gens, dict)

    def test_custom_sampler_without_torch_generator_still_works(self) -> None:
        """Custom SamplingStrategy that doesn't accept torch_generator still functions
        even when _torch_gen is set (the seeded run() path)."""
        gradient = torch.randn(3, 6)
        logits = torch.randn(2, 8, 10)
        token_ids = torch.randint(0, 10, (2, 8))
        control_tokens = torch.tensor([1, 2, 3], dtype=torch.long)
        disallowed_tokens = torch.tensor([5], dtype=torch.long)
        tokenizer = MagicMock()
        tokenizer.decode.return_value = "decoded"

        worker = _WorkerStub(gradient=gradient.clone(), logits=logits, token_ids=token_ids, tokenizer=tokenizer)
        prompt_manager = MagicMock()
        prompt_manager.control_toks = control_tokens
        prompt_manager.disallowed_toks = disallowed_tokens

        sampled_tokens = torch.tensor([[8, 8, 8]], dtype=torch.long)
        sampling = _SpySampling(sampled_tokens=sampled_tokens)

        attack = object.__new__(GCGMultiPromptAttack)
        attack._sampling = sampling
        attack.prompts = [prompt_manager]
        attack.workers = [worker]
        attack.models = [MagicMock(device=torch.device("cpu"))]
        attack.control_str = "test"
        attack._torch_gens = {0: torch.Generator(device=torch.device("cpu")).manual_seed(42)}

        result = attack._sample_control_candidates(
            worker_index=0,
            gradient=gradient,
            batch_size=1,
            topk=3,
            temp=1.0,
            allow_non_ascii=True,
        )

        assert torch.equal(result, sampled_tokens)

    def test_multi_device_generators_must_match_sampling_device(self) -> None:
        """Regression: generators on a different device than the sampling
        tensor make torch.randint raise.  When workers span devices, all
        generators must live on workers[0].model.device (the sampling
        device).  This test goes through the real MPA.run() bundle-creation
        fallback with workers on different devices.

        ``torch.Generator`` is patched to record each requested device while
        handing back a CPU generator, because CPU-only PyTorch builds reject
        ``torch.Generator(device="cuda:0")``. The assertion is on the device
        each generator was requested on, which is what the regression is about.
        """
        attack = object.__new__(MultiPromptAttack)
        attack.prompts = [MagicMock(control_str="initial")]
        attack.logfile = None
        attack.step = MagicMock(return_value=("result", 0.5))

        worker0 = MagicMock()
        worker0.model.device = torch.device("cuda:0")
        worker1 = MagicMock()
        worker1.model.device = torch.device("cuda:1")
        attack.workers = [worker0, worker1]

        real_generator = torch.Generator  # the patch below also replaces the test's own torch.Generator
        with patch.object(
            attack_manager_mod.torch, "Generator", side_effect=lambda device: real_generator()
        ) as generator_cls:
            attack.run(n_steps=1, stop_on_success=False, anneal=False, random_seed=42)

        # Both generators must be requested on the sampling device (worker 0), not
        # their own worker's device.  If worker 1's generator were on cuda:1,
        # torch.randint with device=cuda:0 would raise RuntimeError.
        sampling_device = torch.device("cuda:0")
        assert generator_cls.call_args_list == [call(device=sampling_device), call(device=sampling_device)]
        assert len(attack._torch_gens) == 2
