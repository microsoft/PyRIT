# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
torch = pytest.importorskip("torch", reason="torch not installed")

MultiPromptAttack = attack_manager_mod.MultiPromptAttack
AttackPrompt = attack_manager_mod.AttackPrompt
PromptManager = attack_manager_mod.PromptManager
EvaluateAttack = attack_manager_mod.EvaluateAttack
IndividualPromptAttack = attack_manager_mod.IndividualPromptAttack
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack
get_embedding_layer = attack_manager_mod.get_embedding_layer
get_embedding_matrix = attack_manager_mod.get_embedding_matrix
get_embeddings = attack_manager_mod.get_embeddings

gcg_attack_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.gcg.gcg_attack",
    reason="GCG optional dependencies not installed",
)
GCGPromptManager = gcg_attack_mod.GCGPromptManager
token_gradients = gcg_attack_mod.token_gradients


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
