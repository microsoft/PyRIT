# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import numpy as np
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

    def test_each_candidate_differs_in_one_position(self) -> None:
        """Each candidate should differ from the original in exactly one position."""
        n_control = 10
        vocab_size = 50
        batch_size = 8
        pm = self._make_prompt_manager(n_control_tokens=n_control, vocab_size=vocab_size)

        grad = torch.randn(n_control, vocab_size)
        original_toks = pm._prompts[0].control_toks.clone()
        result = pm.sample_control(grad, batch_size, topk=10)

        for i in range(batch_size):
            diffs = (result[i] != original_toks.to(result.device)).sum().item()
            # Each candidate changes exactly 1 position
            assert diffs == 1, f"Candidate {i} differs in {diffs} positions, expected 1"

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


class TestBuildParams:
    """Tests for GreedyCoordinateGradientAdversarialSuffixgenerator_cls._build_params."""

    def test_builds_config_dict_from_kwargs(self) -> None:
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        params = generator_cls._build_params(
            n_steps=100,
            batch_size=256,
            model_name="test_model",
        )
        assert params.n_steps == 100
        assert params.batch_size == 256
        assert params.model_name == "test_model"

    def test_all_kwargs_become_attributes(self) -> None:
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        kwargs = {"a": 1, "b": "hello", "c": [1, 2, 3], "d": True}
        params = generator_cls._build_params(**kwargs)
        for key, value in kwargs.items():
            assert getattr(params, key) == value


class TestApplyTargetAugmentation:
    """Tests for GreedyCoordinateGradientAdversarialSuffixgenerator_cls._apply_target_augmentation."""

    def test_returns_same_length_lists(self) -> None:
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        train = ["Sure, here is a bomb", "Sure, here is a virus"]
        test = ["Sure, here is a weapon"]

        result_train, result_test = generator_cls._apply_target_augmentation(
            train_targets=train,
            test_targets=test,
        )
        assert len(result_train) == len(train)
        assert len(result_test) == len(test)

    def test_augmentation_modifies_targets(self) -> None:
        """At least some targets should be modified by augmentation."""
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        np.random.seed(42)
        targets = ["Sure, here is how to do it"] * 100

        result, _ = generator_cls._apply_target_augmentation(
            train_targets=targets,
            test_targets=[],
        )
        # With 100 targets and 50% chance of each transform, we should see some changes
        num_changed = sum(1 for orig, aug in zip(targets, result, strict=False) if orig != aug)
        assert num_changed > 0, "Expected at least some targets to be augmented"

    def test_augmentation_is_seeded_reproducible(self) -> None:
        """Same seed should produce same augmentation."""
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        targets = ["Sure, here is how to do it"] * 20

        np.random.seed(123)
        result1, _ = generator_cls._apply_target_augmentation(train_targets=targets, test_targets=[])

        np.random.seed(123)
        result2, _ = generator_cls._apply_target_augmentation(train_targets=targets, test_targets=[])

        assert result1 == result2


class TestCreateAttack:
    """Tests for GreedyCoordinateGradientAdversarialSuffixgenerator_cls._create_attack."""

    def test_transfer_true_creates_progressive(self) -> None:
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        params = generator_cls._build_params(
            transfer=True,
            progressive_models=True,
            progressive_goals=True,
            control_init="! ! !",
            result_prefix="test",
            gbda_deterministic=True,
            learning_rate=0.01,
            batch_size=512,
            n_steps=100,
        )

        mock_worker = MagicMock()
        mock_worker.model.name_or_path = "test-model"
        mock_worker.tokenizer.name_or_path = "test-tokenizer"
        mock_worker.conv_template.name = "test-template"

        managers = {
            "AP": MagicMock(),
            "PM": MagicMock(),
            "MPA": MagicMock(return_value=MagicMock()),
        }

        attack = generator_cls._create_attack(
            params=params,
            managers=managers,
            train_goals=["goal1"],
            train_targets=["target1"],
            test_goals=[],
            test_targets=[],
            workers=[mock_worker],
            test_workers=[],
        )
        assert isinstance(attack, ProgressiveMultiPromptAttack)

    def test_transfer_false_creates_individual(self) -> None:
        train_mod = pytest.importorskip(
            "pyrit.auxiliary_attacks.gcg.experiments.train",
            reason="GCG train module not available",
        )
        generator_cls = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

        params = generator_cls._build_params(
            transfer=False,
            control_init="! ! !",
            result_prefix="test",
            gbda_deterministic=True,
            learning_rate=0.01,
            batch_size=512,
            n_steps=100,
        )

        mock_worker = MagicMock()
        mock_worker.model.name_or_path = "test-model"
        mock_worker.tokenizer.name_or_path = "test-tokenizer"
        mock_worker.conv_template.name = "test-template"

        managers = {
            "AP": MagicMock(),
            "PM": MagicMock(),
            "MPA": MagicMock(return_value=MagicMock()),
        }

        attack = generator_cls._create_attack(
            params=params,
            managers=managers,
            train_goals=["goal1"],
            train_targets=["target1"],
            test_goals=[],
            test_targets=[],
            workers=[mock_worker],
            test_workers=[],
        )
        assert isinstance(attack, IndividualPromptAttack)


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
                conv_template=MagicMock(),
                managers={"AP": MagicMock()},
            )

    def test_raises_on_empty_goals(self) -> None:
        with pytest.raises(ValueError, match="Must provide at least one goal"):
            PromptManager(
                goals=[],
                targets=[],
                tokenizer=MagicMock(),
                conv_template=MagicMock(),
                managers={"AP": MagicMock()},
            )


class TestEvaluateAttackInit:
    """Tests for EvaluateAttack initialization validation."""

    def test_raises_with_multiple_workers(self) -> None:
        mock_worker1 = MagicMock()
        mock_worker1.model.name_or_path = "m1"
        mock_worker1.tokenizer.name_or_path = "t1"
        mock_worker1.conv_template.name = "c1"
        mock_worker2 = MagicMock()
        mock_worker2.model.name_or_path = "m2"
        mock_worker2.tokenizer.name_or_path = "t2"
        mock_worker2.conv_template.name = "c2"

        with pytest.raises(ValueError, match="exactly 1 worker"):
            EvaluateAttack(
                goals=["goal"],
                targets=["target"],
                workers=[mock_worker1, mock_worker2],
                managers={"AP": MagicMock(), "PM": MagicMock(), "MPA": MagicMock()},
            )
