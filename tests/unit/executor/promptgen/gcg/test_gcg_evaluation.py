# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# ruff: noqa: E402

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest
from transformers import Qwen2Config, Qwen2ForCausalLM, Qwen3NextConfig, Qwen3NextForCausalLM  # type: ignore[ty:possibly-missing-import]

pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies not installed",
)
torch = pytest.importorskip("torch", reason="torch not installed")

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (
    AttackPrompt,
    ModelWorker,
    ModelWorkerOperation,
)
from pyrit.executor.promptgen.gcg.attack.gcg.candidate_evaluator import (
    CandidateEvaluationBatch,
    GCGCandidateEvaluator,
)
from pyrit.executor.promptgen.gcg.default_implementations import CrossEntropyLoss
from pyrit.executor.promptgen.gcg.extension_protocols import LossFunction


class _QueueStub:
    def __init__(self, items: list[object]) -> None:
        self._items = list(items)

    def get(self) -> object:
        if not self._items:
            raise IndexError("Queue is empty")
        return self._items.pop(0)


class _MockWorker:
    def __init__(self, results: list[object]) -> None:
        self.results = _QueueStub(results)
        self.calls: list[tuple] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


class _MockPromptManager:
    def __init__(self, prompts: list[AttackPrompt]) -> None:
        self._prompts = list(prompts)

    def __len__(self) -> int:
        return len(self._prompts)

    def __getitem__(self, index: int) -> AttackPrompt:
        return self._prompts[index]


class _MockAttackPrompt:
    def __init__(self, target_slice: slice, control_slice: slice) -> None:
        self._target_slice = target_slice
        self._control_slice = control_slice


class TestCandidateEvaluationBatch:
    def test_batch_properties_and_num_groups(self) -> None:
        cands = [["cand1", "cand2"], ["cand3", "cand4"]]
        losses = torch.tensor([0.5, 1.2, 0.3, 0.8])
        batch = CandidateEvaluationBatch(
            control_candidates_by_group=cands,
            losses=losses,
        )

        assert batch.control_candidates_by_group == cands
        assert torch.equal(batch.losses, losses)
        assert batch.num_groups == 2

    def test_batch_immutability(self) -> None:
        batch = CandidateEvaluationBatch(
            control_candidates_by_group=[["a"]],
            losses=torch.tensor([0.1]),
        )
        with pytest.raises(FrozenInstanceError):
            batch.losses = torch.tensor([0.2])  # type: ignore[misc]


class TestGCGCandidateEvaluatorValidation:
    def test_empty_workers_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        with pytest.raises(ValueError, match="at least one worker"):
            GCGCandidateEvaluator(
                workers=[],
                prompts=[],
                loss_function=loss_fn,
                main_device=torch.device("cpu"),
            )

    def test_worker_and_prompts_count_mismatch_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        worker = MagicMock(spec=ModelWorker)
        prompt_mgr = _MockPromptManager([_MockAttackPrompt(slice(0, 1), slice(1, 2))])  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="count mismatch"):
            GCGCandidateEvaluator(
                workers=[worker, worker],
                prompts=[prompt_mgr],  # type: ignore[list-item]
                loss_function=loss_fn,
                main_device=torch.device("cpu"),
            )

    def test_empty_prompts_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        worker = MagicMock(spec=ModelWorker)
        empty_pm = _MockPromptManager([])

        with pytest.raises(ValueError, match="contain at least one prompt"):
            GCGCandidateEvaluator(
                workers=[worker],
                prompts=[empty_pm],  # type: ignore[list-item]
                loss_function=loss_fn,
                main_device=torch.device("cpu"),
            )

    def test_mismatched_prompt_counts_across_workers_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        worker1 = MagicMock(spec=ModelWorker)
        worker2 = MagicMock(spec=ModelWorker)
        pm1 = _MockPromptManager([_MockAttackPrompt(slice(0, 1), slice(1, 2))])  # type: ignore[arg-type]
        pm2 = _MockPromptManager(
            [_MockAttackPrompt(slice(0, 1), slice(1, 2)), _MockAttackPrompt(slice(0, 1), slice(1, 2))]
        )  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="identical prompt counts"):
            GCGCandidateEvaluator(
                workers=[worker1, worker2],
                prompts=[pm1, pm2],  # type: ignore[list-item]
                loss_function=loss_fn,
                main_device=torch.device("cpu"),
            )

    def test_empty_candidate_groups_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        worker = MagicMock(spec=ModelWorker)
        pm = _MockPromptManager([_MockAttackPrompt(slice(0, 1), slice(1, 2))])  # type: ignore[arg-type]
        evaluator = GCGCandidateEvaluator(
            workers=[worker],
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        with pytest.raises(ValueError, match="at least one candidate group"):
            evaluator.evaluate_candidates(
                control_candidates_by_group=[],
                batch_size=2,
            )

    def test_non_positive_batch_size_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        worker = MagicMock(spec=ModelWorker)
        pm = _MockPromptManager([_MockAttackPrompt(slice(0, 1), slice(1, 2))])  # type: ignore[arg-type]
        evaluator = GCGCandidateEvaluator(
            workers=[worker],
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        with pytest.raises(ValueError, match="batch_size must be positive"):
            evaluator.evaluate_candidates(
                control_candidates_by_group=[["a", "b"]],
                batch_size=0,
            )

    def test_group_candidate_count_mismatch_with_batch_size_raises(self) -> None:
        loss_fn = MagicMock(spec=LossFunction)
        worker = MagicMock(spec=ModelWorker)
        pm = _MockPromptManager([_MockAttackPrompt(slice(0, 1), slice(1, 2))])  # type: ignore[arg-type]
        evaluator = GCGCandidateEvaluator(
            workers=[worker],
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        with pytest.raises(ValueError, match="exactly batch_size candidates"):
            evaluator.evaluate_candidates(
                control_candidates_by_group=[["a", "b", "c"]],
                batch_size=2,
            )


class TestGCGCandidateEvaluatorExecution:
    def test_prefix_cache_rejects_additional_batched_layer_state(self) -> None:
        class CacheLayer:
            def __init__(self) -> None:
                self.keys = torch.zeros(1, 2, 3, 4)
                self.values = torch.zeros(1, 2, 3, 4)
                self.conv_states = {0: [torch.zeros(1, 2, 3)]}

        class Cache:
            layers = [CacheLayer()]

        with pytest.raises(TypeError, match="Unsupported state"):
            AttackPrompt._expand_prefix_cache(Cache(), batch_size=2)

    def test_prefix_cache_rejects_layer_without_instance_state(self) -> None:
        class CacheLayer:
            __slots__ = ("keys", "values")

            def __init__(self) -> None:
                self.keys = torch.zeros(1, 2, 3, 4)
                self.values = torch.zeros(1, 2, 3, 4)

        cache = MagicMock()
        cache.layers = [CacheLayer()]

        with pytest.raises(TypeError, match="Unsupported prefix-cache layer"):
            AttackPrompt._expand_prefix_cache(cache, batch_size=2)

    def test_prefix_cache_rejects_non_singleton_batch(self) -> None:
        layer = MagicMock()
        layer.keys = torch.zeros(2, 2, 3, 4)
        layer.values = torch.zeros(2, 2, 3, 4)
        cache = MagicMock()
        cache.layers = [layer]

        with pytest.raises(ValueError, match="exactly one sequence"):
            AttackPrompt._expand_prefix_cache(cache, batch_size=2)

    def test_prefix_cache_expands_legacy_cache(self) -> None:
        keys = torch.randn(1, 2, 3, 4)
        values = torch.randn(1, 2, 3, 4)

        expanded = AttackPrompt._expand_prefix_cache(((keys, values),), batch_size=3)

        assert expanded[0][0].shape == (3, 2, 3, 4)
        assert expanded[0][1].shape == (3, 2, 3, 4)
        assert torch.equal(expanded[0][0][0], keys[0])
        assert torch.equal(expanded[0][1][0], values[0])

    @pytest.mark.parametrize(
        ("cache", "expected_error", "message"),
        [
            (((torch.zeros(1, 2), "not-a-tensor"),), TypeError, "Unsupported prefix-cache layer"),
            (((torch.zeros(2, 2), torch.zeros(2, 2)),), ValueError, "exactly one sequence"),
            (object(), TypeError, "Unsupported prefix-cache type"),
        ],
    )
    def test_prefix_cache_rejects_unsupported_legacy_cache(
        self,
        cache: object,
        expected_error: type[Exception],
        message: str,
    ) -> None:
        with pytest.raises(expected_error, match=message):
            AttackPrompt._expand_prefix_cache(cache, batch_size=2)

    def test_selective_logits_match_full_logits_on_transformers_model(self) -> None:
        torch.manual_seed(123)
        model = Qwen2ForCausalLM(
            Qwen2Config(
                vocab_size=32,
                hidden_size=16,
                intermediate_size=32,
                num_hidden_layers=1,
                num_attention_heads=2,
                num_key_value_heads=2,
                max_position_embeddings=32,
            )
        ).eval()
        prompt = object.__new__(AttackPrompt)
        prompt._control_slice = slice(1, 3)
        prompt._target_slice = slice(4, 7)
        prompt.input_ids = torch.tensor([1, 2, 3, 4, 5, 6, 7, 8])
        prompt.tokenizer = MagicMock()
        candidates = torch.tensor([[9, 10], [11, 12]])
        loss_fn = CrossEntropyLoss(target_weight=0.7, control_weight=0.3)

        full_logits, token_ids = prompt.logits(model, candidates, return_ids=True)
        expected = loss_fn.compute_loss(
            logits=full_logits,
            token_ids=token_ids,
            target_slice=prompt._target_slice,
            control_slice=prompt._control_slice,
        )
        actual = prompt.loss(model, candidates, loss_fn)

        assert torch.equal(actual, expected)

    def test_prefix_cached_loss_matches_full_forward_on_transformers_model(self) -> None:
        torch.manual_seed(123)
        model = Qwen2ForCausalLM(
            Qwen2Config(
                vocab_size=32,
                hidden_size=16,
                intermediate_size=32,
                num_hidden_layers=2,
                num_attention_heads=2,
                num_key_value_heads=2,
                max_position_embeddings=32,
            )
        ).eval()
        prompt = object.__new__(AttackPrompt)
        prompt._control_slice = slice(3, 5)
        prompt._target_slice = slice(6, 9)
        prompt.input_ids = torch.tensor([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        prompt.tokenizer = MagicMock()
        candidates = torch.tensor([[11, 12], [13, 14], [15, 16]])
        loss_fn = CrossEntropyLoss(target_weight=0.7, control_weight=0.3)

        full_logits, token_ids = prompt.logits(model, candidates, return_ids=True)
        expected = loss_fn.compute_loss(
            logits=full_logits,
            token_ids=token_ids,
            target_slice=prompt._target_slice,
            control_slice=prompt._control_slice,
        )
        attention_mask = torch.ones_like(token_ids)
        prompt._build_candidate_batch = MagicMock(return_value=(token_ids, attention_mask))
        forward_calls = []

        def capture_forward(_module, _args, kwargs, output):
            forward_calls.append((kwargs, output.logits.shape))

        hook = model.register_forward_hook(capture_forward, with_kwargs=True)
        try:
            actual = prompt.loss(model, candidates, loss_fn, use_prefix_cache=True)
        finally:
            hook.remove()

        assert torch.allclose(actual, expected, rtol=1e-5, atol=1e-6)
        assert actual.argmin() == expected.argmin()
        prefix_kwargs, prefix_logits_shape = forward_calls[0]
        assert prefix_kwargs["logits_to_keep"] == 1
        assert prefix_logits_shape[1] == 1
        assert torch.equal(
            prefix_kwargs["attention_mask"],
            attention_mask[:1, : prompt._control_slice.start - 1],
        )
        assert torch.equal(forward_calls[1][0]["attention_mask"], attention_mask)

    def test_hybrid_cache_falls_back_to_uncached_selective_logits(self) -> None:
        torch.manual_seed(123)
        model = Qwen3NextForCausalLM(
            Qwen3NextConfig(
                vocab_size=32,
                hidden_size=16,
                intermediate_size=32,
                num_hidden_layers=2,
                num_attention_heads=2,
                num_key_value_heads=1,
                head_dim=8,
                max_position_embeddings=32,
                linear_conv_kernel_dim=4,
                linear_key_head_dim=8,
                linear_value_head_dim=8,
                linear_num_key_heads=2,
                linear_num_value_heads=2,
                moe_intermediate_size=16,
                shared_expert_intermediate_size=16,
                num_experts_per_tok=1,
                num_experts=2,
                layer_types=["linear_attention", "full_attention"],
            )
        ).eval()
        prompt = object.__new__(AttackPrompt)
        prompt._control_slice = slice(3, 5)
        prompt._target_slice = slice(6, 9)
        prompt.input_ids = torch.tensor([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        prompt.tokenizer = MagicMock()
        candidates = torch.tensor([[11, 12], [13, 14], [15, 16]])
        loss_fn = CrossEntropyLoss(target_weight=0.7, control_weight=0.3)

        expected = prompt.loss(model, candidates, loss_fn)
        forward_calls = []

        def capture_forward(_module, _args, kwargs, _output):
            forward_calls.append(kwargs)

        hook = model.register_forward_hook(capture_forward, with_kwargs=True)
        try:
            actual = prompt.loss(model, candidates, loss_fn, use_prefix_cache=True)
        finally:
            hook.remove()

        assert torch.equal(actual, expected)
        assert len(forward_calls) == 2
        assert forward_calls[0]["input_ids"].shape[0] == 1
        assert forward_calls[0]["logits_to_keep"] == 1
        assert forward_calls[1]["input_ids"].shape[0] == len(candidates)
        assert "past_key_values" not in forward_calls[1]

    def test_attack_prompt_worker_loss_matches_direct_computation(self) -> None:
        logits = torch.randn(2, 6, 10)
        token_ids = torch.randint(0, 10, (2, 6))
        prompt = MagicMock()
        prompt.logits.return_value = (logits, token_ids)
        prompt._target_slice = slice(3, 5)
        prompt._control_slice = slice(1, 3)
        loss_fn = CrossEntropyLoss(target_weight=0.7, control_weight=0.3)
        model = MagicMock()
        candidates = ["cand-1", "cand-2"]

        actual = AttackPrompt.loss(prompt, model, candidates, loss_fn)
        expected = loss_fn.compute_loss(
            logits=logits,
            token_ids=token_ids,
            target_slice=prompt._target_slice,
            control_slice=prompt._control_slice,
        )

        assert torch.equal(actual, expected)
        prompt.logits.assert_called_once_with(model, candidates, return_ids=True)

    def test_attack_prompt_loss_handles_uninspectable_model_forward(self) -> None:
        logits = torch.randn(2, 6, 10)
        token_ids = torch.randint(0, 10, (2, 6))
        prompt = MagicMock()
        prompt.logits.return_value = (logits, token_ids)
        prompt._target_slice = slice(3, 5)
        prompt._control_slice = slice(1, 3)
        loss_fn = CrossEntropyLoss(target_weight=0.7, control_weight=0.3)
        model = MagicMock()
        model.forward = object()

        actual = AttackPrompt.loss(prompt, model, ["cand-1", "cand-2"], loss_fn)

        expected = loss_fn.compute_loss(
            logits=logits,
            token_ids=token_ids,
            target_slice=prompt._target_slice,
            control_slice=prompt._control_slice,
        )
        assert torch.equal(actual, expected)
        prompt.logits.assert_called_once_with(model, ["cand-1", "cand-2"], return_ids=True)

    def test_attack_prompt_worker_loss_selects_only_required_logits_when_supported(self) -> None:
        class SelectiveModel:
            device = torch.device("cpu")

            def forward(self, *, logits_to_keep: int | torch.Tensor = 0) -> None:
                del logits_to_keep

        full_logits = torch.randn(2, 6, 10)
        token_ids = torch.randint(0, 10, (2, 6))
        prompt = MagicMock()
        prompt._target_slice = slice(3, 5)
        prompt._control_slice = slice(1, 3)
        loss_fn = CrossEntropyLoss(target_weight=0.7, control_weight=0.3)
        expected_positions = torch.tensor([2, 3, 0, 1])
        prompt.logits.return_value = (full_logits[:, expected_positions, :], token_ids)
        model = SelectiveModel()

        actual = AttackPrompt.loss(prompt, model, ["cand-1", "cand-2"], loss_fn)
        expected = loss_fn.compute_loss(
            logits=full_logits,
            token_ids=token_ids,
            target_slice=prompt._target_slice,
            control_slice=prompt._control_slice,
        )

        assert torch.equal(actual, expected)
        prompt.logits.assert_called_once()
        args, kwargs = prompt.logits.call_args
        assert args == (model, ["cand-1", "cand-2"])
        assert kwargs["return_ids"] is True
        assert torch.equal(kwargs["logits_to_keep"], expected_positions)

    def test_builtin_loss_is_computed_in_worker(self) -> None:
        prompt = _MockAttackPrompt(target_slice=slice(3, 5), control_slice=slice(1, 3))
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]
        computed_losses = torch.tensor([0.42, 0.99])
        worker = _MockWorker([computed_losses])
        loss_fn = CrossEntropyLoss(target_weight=1.0, control_weight=0.1)
        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        candidates = [["cand-1", "cand-2"]]
        result = evaluator.evaluate_candidates(control_candidates_by_group=candidates, batch_size=2)

        assert torch.equal(result.losses, computed_losses)
        assert len(worker.calls) == 1
        args, kwargs = worker.calls[0]
        assert args == (prompt, ModelWorkerOperation.LOSS, candidates[0], loss_fn)
        assert kwargs == {"use_prefix_cache": False}

    def test_builtin_loss_accumulates_across_workers(self) -> None:
        prompts = [
            _MockPromptManager([_MockAttackPrompt(slice(3, 5), slice(1, 3))]),
            _MockPromptManager([_MockAttackPrompt(slice(3, 5), slice(1, 3))]),
        ]
        workers = [
            _MockWorker([torch.tensor([0.2, 0.3])]),
            _MockWorker([torch.tensor([0.5, 0.7])]),
        ]
        loss_fn = CrossEntropyLoss()
        evaluator = GCGCandidateEvaluator(
            workers=workers,  # type: ignore[arg-type]
            prompts=prompts,  # type: ignore[arg-type]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        result = evaluator.evaluate_candidates(
            control_candidates_by_group=[["cand-1", "cand-2"]],
            batch_size=2,
        )

        assert torch.allclose(result.losses, torch.tensor([0.7, 1.0]))
        assert all(worker.calls[0][0][1] is ModelWorkerOperation.LOSS for worker in workers)

    def test_prefix_cache_opt_in_is_forwarded_to_worker(self) -> None:
        prompt = _MockAttackPrompt(target_slice=slice(3, 5), control_slice=slice(1, 3))
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]
        computed_losses = torch.tensor([0.42, 0.99])
        worker = _MockWorker([computed_losses])
        loss_fn = CrossEntropyLoss()
        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
            use_prefix_cache=True,
        )

        evaluator.evaluate_candidates(control_candidates_by_group=[["cand-1", "cand-2"]], batch_size=2)

        assert worker.calls == [
            (
                (prompt, ModelWorkerOperation.LOSS, ["cand-1", "cand-2"], loss_fn),
                {"use_prefix_cache": True},
            )
        ]

    def test_builtin_loss_subclass_uses_custom_loss_path(self) -> None:
        class CustomCrossEntropyLoss(CrossEntropyLoss):
            pass

        prompt = _MockAttackPrompt(target_slice=slice(3, 5), control_slice=slice(1, 3))
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]
        logits = torch.randn(2, 6, 10)
        token_ids = torch.randint(0, 10, (2, 6))
        worker = _MockWorker([(logits, token_ids)])
        loss_fn = CustomCrossEntropyLoss()
        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        evaluator.evaluate_candidates(control_candidates_by_group=[["cand-1", "cand-2"]], batch_size=2)

        assert worker.calls[0][0][1] is ModelWorkerOperation.LOGITS

    def test_single_worker_single_prompt_evaluation(self) -> None:
        target_slice = slice(3, 5)
        control_slice = slice(1, 3)
        prompt = _MockAttackPrompt(target_slice=target_slice, control_slice=control_slice)
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]

        logits = torch.randn(2, 6, 10)
        token_ids = torch.randint(0, 10, (2, 6))
        worker = _MockWorker([(logits, token_ids)])

        computed_losses = torch.tensor([0.42, 0.99])
        loss_fn = MagicMock(spec=LossFunction)
        loss_fn.compute_loss.return_value = computed_losses

        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        candidates = [["cand-1", "cand-2"]]
        result = evaluator.evaluate_candidates(
            control_candidates_by_group=candidates,
            batch_size=2,
        )

        assert result.num_groups == 1
        assert torch.equal(result.losses, computed_losses)
        assert result.control_candidates_by_group == candidates

        # Check worker dispatch
        assert len(worker.calls) == 1
        args, kwargs = worker.calls[0]
        assert args[0] is prompt
        assert args[1] == ModelWorkerOperation.LOGITS
        assert args[2] == ["cand-1", "cand-2"]
        assert kwargs == {"return_ids": True}

        # Check loss computation parameters
        loss_fn.compute_loss.assert_called_once_with(
            logits=logits,
            token_ids=token_ids,
            target_slice=target_slice,
            control_slice=control_slice,
        )

    def test_multi_worker_multi_prompt_accumulation(self) -> None:
        prompt_w0_p0 = _MockAttackPrompt(slice(1, 2), slice(2, 3))
        prompt_w0_p1 = _MockAttackPrompt(slice(3, 4), slice(4, 5))
        prompt_w1_p0 = _MockAttackPrompt(slice(1, 2), slice(2, 3))
        prompt_w1_p1 = _MockAttackPrompt(slice(3, 4), slice(4, 5))

        pm0 = _MockPromptManager([prompt_w0_p0, prompt_w0_p1])  # type: ignore[arg-type]
        pm1 = _MockPromptManager([prompt_w1_p0, prompt_w1_p1])  # type: ignore[arg-type]

        logits_dummy = torch.randn(2, 4, 8)
        ids_dummy = torch.zeros((2, 4), dtype=torch.long)

        # 2 prompts * 1 group = 2 worker calls per worker
        worker0 = _MockWorker([(logits_dummy, ids_dummy), (logits_dummy, ids_dummy)])
        worker1 = _MockWorker([(logits_dummy, ids_dummy), (logits_dummy, ids_dummy)])

        # Losses per call: (w0, p0)=1.0, (w1, p0)=2.0, (w0, p1)=3.0, (w1, p1)=4.0
        # Expected accumulated loss per candidate = 1.0 + 2.0 + 3.0 + 4.0 = 10.0
        losses_sequence = [
            torch.tensor([1.0, 1.0]),
            torch.tensor([2.0, 2.0]),
            torch.tensor([3.0, 3.0]),
            torch.tensor([4.0, 4.0]),
        ]
        loss_fn = MagicMock(spec=LossFunction)
        loss_fn.compute_loss.side_effect = losses_sequence

        evaluator = GCGCandidateEvaluator(
            workers=[worker0, worker1],  # type: ignore[list-item]
            prompts=[pm0, pm1],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        result = evaluator.evaluate_candidates(
            control_candidates_by_group=[["c1", "c2"]],
            batch_size=2,
        )

        assert torch.allclose(result.losses, torch.tensor([10.0, 10.0]))
        assert len(worker0.calls) == 2
        assert len(worker1.calls) == 2
        assert loss_fn.compute_loss.call_count == 4

    def test_multi_group_sequential_evaluation_offsets(self) -> None:
        prompt = _MockAttackPrompt(slice(0, 1), slice(1, 2))
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]

        logits_dummy = torch.randn(2, 3, 5)
        ids_dummy = torch.zeros((2, 3), dtype=torch.long)

        # 3 candidate groups = 3 calls to the worker
        worker = _MockWorker([(logits_dummy, ids_dummy), (logits_dummy, ids_dummy), (logits_dummy, ids_dummy)])

        # Distinct losses per group
        losses_g0 = torch.tensor([1.0, 2.0])
        losses_g1 = torch.tensor([3.0, 4.0])
        losses_g2 = torch.tensor([5.0, 6.0])

        loss_fn = MagicMock(spec=LossFunction)
        loss_fn.compute_loss.side_effect = [losses_g0, losses_g1, losses_g2]

        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        cands = [["g0_c0", "g0_c1"], ["g1_c0", "g1_c1"], ["g2_c0", "g2_c1"]]
        result = evaluator.evaluate_candidates(
            control_candidates_by_group=cands,
            batch_size=2,
        )

        assert result.num_groups == 3
        expected_losses = torch.tensor([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        assert torch.equal(result.losses, expected_losses)
        assert len(worker.calls) == 3

    def test_cross_device_loss_tensor_transfer(self) -> None:
        prompt = _MockAttackPrompt(slice(0, 1), slice(1, 2))
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]
        logits = torch.randn(2, 3, 5)
        ids = torch.zeros((2, 3), dtype=torch.long)
        worker = _MockWorker([(logits, ids)])

        main_device = torch.device("cpu")
        returned_loss = torch.tensor([2.5, 3.5])
        mock_loss_tensor = MagicMock()
        mock_loss_tensor.to.return_value = returned_loss

        loss_fn = MagicMock(spec=LossFunction)
        loss_fn.compute_loss.return_value = mock_loss_tensor

        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=main_device,
        )

        result = evaluator.evaluate_candidates(
            control_candidates_by_group=[["c1", "c2"]],
            batch_size=2,
        )

        mock_loss_tensor.to.assert_called_once_with(main_device)
        assert torch.equal(result.losses, returned_loss)

    def test_verbose_progress_bar_reporting(self) -> None:
        prompt1 = _MockAttackPrompt(slice(0, 1), slice(1, 2))
        prompt2 = _MockAttackPrompt(slice(0, 1), slice(1, 2))
        pm = _MockPromptManager([prompt1, prompt2])  # type: ignore[arg-type]

        logits = torch.randn(2, 3, 5)
        ids = torch.zeros((2, 3), dtype=torch.long)
        worker = _MockWorker([(logits, ids), (logits, ids)])

        loss_fn = MagicMock(spec=LossFunction)
        loss_fn.compute_loss.side_effect = [torch.tensor([1.0, 2.0]), torch.tensor([0.5, 1.5])]

        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        # Should execute without error with verbose=True
        result = evaluator.evaluate_candidates(
            control_candidates_by_group=[["c1", "c2"]],
            batch_size=2,
            verbose=True,
        )

        assert torch.equal(result.losses, torch.tensor([1.5, 3.5]))

    def test_non_finite_losses_nan_inf_safety(self) -> None:
        prompt = _MockAttackPrompt(slice(0, 1), slice(1, 2))
        pm = _MockPromptManager([prompt])  # type: ignore[arg-type]
        logits = torch.randn(2, 3, 5)
        ids = torch.zeros((2, 3), dtype=torch.long)
        worker = _MockWorker([(logits, ids)])

        non_finite_loss = torch.tensor([float("inf"), float("nan")])
        loss_fn = MagicMock(spec=LossFunction)
        loss_fn.compute_loss.return_value = non_finite_loss

        evaluator = GCGCandidateEvaluator(
            workers=[worker],  # type: ignore[list-item]
            prompts=[pm],  # type: ignore[list-item]
            loss_function=loss_fn,
            main_device=torch.device("cpu"),
        )

        result = evaluator.evaluate_candidates(
            control_candidates_by_group=[["c1", "c2"]],
            batch_size=2,
            verbose=True,
        )

        assert torch.isinf(result.losses[0])
        assert torch.isnan(result.losses[1])
