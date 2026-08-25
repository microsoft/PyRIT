# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pickle
from copy import deepcopy
from dataclasses import dataclass
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


class TestEmbeddingHelpers:
    """Tests for get_embedding_layer, get_embedding_matrix, get_embeddings."""

    def test_get_embedding_layer_raises_for_unknown_model(self) -> None:
        """Should raise ValueError for unsupported model types."""
        mock_model = MagicMock()
        # Ensure it doesn't match any isinstance checks
        mock_model.__class__ = type("UnknownModel", (), {})
        with pytest.raises(ValueError, match="Unknown model type"):
            get_embedding_layer(mock_model)

    def test_get_embedding_matrix_raises_for_unknown_model(self) -> None:
        mock_model = MagicMock()
        mock_model.__class__ = type("UnknownModel", (), {})
        with pytest.raises(ValueError, match="Unknown model type"):
            get_embedding_matrix(mock_model)

    def test_get_embeddings_raises_for_unknown_model(self) -> None:
        mock_model = MagicMock()
        mock_model.__class__ = type("UnknownModel", (), {})
        with pytest.raises(ValueError, match="Unknown model type"):
            get_embeddings(mock_model, torch.tensor([1, 2, 3]))


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


class TestUpdateIdsErrorPaths:
    """Tests covering the error / fallback paths in AttackPrompt._update_ids."""

    def test_raises_when_substring_not_in_rendered_prompt(self) -> None:
        """If the chat template strips/transforms goal/control/target so they don't appear
        verbatim in the rendered prompt, _update_ids must raise a clear ValueError."""
        tokenizer = MagicMock()
        # Chat template that drops the user content entirely — goal/control won't appear in prompt
        tokenizer.apply_chat_template.return_value = "[INST] [/INST] hello"
        # tokenizer(...) returns an encoding-like object
        encoding = MagicMock()
        encoding.input_ids = [1, 2, 3, 4]
        encoding.char_to_token.return_value = 1
        tokenizer.return_value = encoding

        with pytest.raises(ValueError, match="Could not locate goal/control/target"):
            AttackPrompt(
                goal="this-goal-is-missing",
                target="this-target-is-missing",
                tokenizer=tokenizer,
                control_init="this-control-is-missing",
            )

    def test_start_tok_walks_forward_when_initial_position_has_no_token(self) -> None:
        """char_to_token returns None for the start position (e.g., whitespace squashed
        into the previous token); start_tok must walk forward to the next mappable
        character. Slices should still be valid."""
        # Use a fully mocked tokenizer so we can deterministically force char_to_token
        # to return None at specific positions, otherwise real tokenizers usually map
        # every byte and never trigger the fallback.
        prompt_text = "USER hello !! ASSISTANT world"
        toks = list(range(15))

        def char_to_token(pos: int) -> int | None:
            # Positions of "h" and "w" both return None; the next char does map. This
            # exercises the cur += 1 walk-forward branch in start_tok.
            char = prompt_text[pos] if 0 <= pos < len(prompt_text) else ""
            if char in ("h", "w"):
                return None
            # Map remaining positions in a way that preserves slice ordering
            return min(pos // 2, len(toks) - 1)

        encoding = MagicMock()
        encoding.input_ids = toks
        encoding.char_to_token.side_effect = char_to_token

        tokenizer = MagicMock()
        tokenizer.apply_chat_template.return_value = prompt_text
        tokenizer.return_value = encoding

        # Construction must succeed even though char_to_token returns None at goal/target
        # start positions ("h" / "w").
        prompt = AttackPrompt(
            goal="hello",
            target="world",
            tokenizer=tokenizer,
            control_init="!!",
        )
        assert isinstance(prompt._goal_slice.start, int)
        assert isinstance(prompt._target_slice.start, int)

    def test_start_tok_returns_len_toks_when_no_position_maps(self) -> None:
        """If char_to_token returns None for every position from char_pos to end-of-prompt,
        start_tok must return len(toks) as a safe fallback (line 211)."""
        prompt_text = "USER hello !! ASSISTANT world tail"
        toks = list(range(20))

        def char_to_token(pos: int) -> int | None:
            char = prompt_text[pos] if 0 <= pos < len(prompt_text) else ""
            # "tail" sits at end and never maps to a token (forces start_tok to exhaust
            # the loop and hit `return len(toks)`); other content maps normally.
            tail_start = prompt_text.find("tail")
            if pos >= tail_start:
                return None
            return min(pos // 2, len(toks) - 1)

        encoding = MagicMock()
        encoding.input_ids = toks
        encoding.char_to_token.side_effect = char_to_token

        tokenizer = MagicMock()
        tokenizer.apply_chat_template.return_value = prompt_text
        tokenizer.return_value = encoding

        # "tail" as the target — its start position and every position after it returns
        # None, so start_tok exits the while loop and returns len(toks).
        prompt = AttackPrompt(
            goal="hello",
            target="tail",
            tokenizer=tokenizer,
            control_init="!!",
        )
        assert prompt._target_slice.start == len(toks)

    def test_end_tok_returns_len_toks_when_target_is_at_prompt_end(self) -> None:
        """If the target sits at the very end of the rendered prompt,
        char_to_token(end_pos) returns None — end_tok must clamp to len(toks)
        (line 201 in attack_manager.py)."""
        # Fully-mocked tokenizer so we can deterministically force char_to_token to
        # return None at the position just past the target. Mirrors the pattern used
        # by the two adjacent tests above.
        prompt_text = "[INST] hello !! [/INST] world"
        toks = list(range(10))
        target_end_pos = len(prompt_text)  # one past the final char of "world"

        def char_to_token(pos: int) -> int | None:
            # Position at/after end-of-prompt has no token → triggers the
            # `return len(toks)` fallback in end_tok.
            if pos >= target_end_pos:
                return None
            # Everything else maps to a valid token index that preserves ordering.
            return min(pos // 3, len(toks) - 1)

        encoding = MagicMock()
        encoding.input_ids = toks
        encoding.char_to_token.side_effect = char_to_token

        tokenizer = MagicMock()
        tokenizer.apply_chat_template.return_value = prompt_text
        tokenizer.return_value = encoding

        prompt = AttackPrompt(
            goal="hello",
            target="world",  # sits at end of prompt_text; target end has no token
            tokenizer=tokenizer,
            control_init="!!",
        )
        # end_tok(target_end_pos) saw None from char_to_token → clamped to len(toks).
        assert prompt._target_slice.stop == len(toks)
        assert prompt._target_slice.stop > prompt._target_slice.start


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
