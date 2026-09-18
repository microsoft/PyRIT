# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# ruff: noqa: E402

from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, etc.) not installed",
)
torch = pytest.importorskip("torch", reason="torch not installed")

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import ModelWorker, ModelWorkerOperation, PromptManager
from pyrit.executor.promptgen.gcg.attack.gcg.candidate_proposer import (
    CandidateProposalBatch,
    GCGCandidateProposer,
)
from pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack import GCGMultiPromptAttack
from pyrit.executor.promptgen.gcg.extension_protocols import CandidateFilter, SamplingStrategy


def _create_mock_worker(gradient_shape: tuple[int, ...]) -> MagicMock:
    """Create a mock ModelWorker returning a gradient of the specified shape."""
    worker = MagicMock()
    worker.tokenizer = MagicMock()
    worker.tokenizer.decode.side_effect = lambda ids, **kwargs: f"tok_{ids[0]}"
    worker.results = MagicMock()
    # Return a tensor of ones for the specified gradient shape
    worker.results.get.side_effect = lambda: torch.ones(gradient_shape, dtype=torch.float32)
    return worker


def _create_mock_prompt_manager() -> MagicMock:
    """Create a mock PromptManager holding control tokens."""
    prompt_manager = MagicMock()
    prompt_manager.control_toks = torch.tensor([1, 2, 3], dtype=torch.long)
    prompt_manager.disallowed_toks = None
    return prompt_manager


class TestCandidateProposalBatch:
    """Unit tests for the CandidateProposalBatch dataclass."""

    def test_num_groups_property(self) -> None:
        batch = CandidateProposalBatch(
            control_candidates_by_group=[["cand1", "cand2"], ["cand3"]],
            group_worker_indices=[0, 1],
        )
        assert batch.num_groups == 2
        assert batch.control_candidates_by_group == [["cand1", "cand2"], ["cand3"]]
        assert batch.group_worker_indices == [0, 1]


class TestGCGCandidateProposer:
    """Unit tests for the GCGCandidateProposer component."""

    def test_empty_workers_raises_value_error(self) -> None:
        sampling = MagicMock(spec=SamplingStrategy)
        candidate_filter = MagicMock(spec=CandidateFilter)
        with pytest.raises(ValueError, match="at least one worker"):
            GCGCandidateProposer(
                workers=[],
                prompts=[],
                sampling=sampling,
                candidate_filter=candidate_filter,
                main_device=torch.device("cpu"),
            )

    def test_worker_and_prompt_count_mismatch_raises_value_error(self) -> None:
        worker = _create_mock_worker((1, 10, 20))
        sampling = MagicMock(spec=SamplingStrategy)
        candidate_filter = MagicMock(spec=CandidateFilter)
        with pytest.raises(ValueError, match="count mismatch"):
            GCGCandidateProposer(
                workers=[worker],
                prompts=[],
                sampling=sampling,
                candidate_filter=candidate_filter,
                main_device=torch.device("cpu"),
            )

    def test_single_worker_gradient_dispatch_and_normalization(self) -> None:
        worker = _create_mock_worker((1, 3, 4))
        prompt_manager = _create_mock_prompt_manager()
        sampling = MagicMock(spec=SamplingStrategy)
        sampling.sample_candidates.return_value = torch.tensor([[10], [20]])
        candidate_filter = MagicMock(spec=CandidateFilter)
        candidate_filter.filter_candidates.return_value = ["filtered_1", "filtered_2"]

        proposer = GCGCandidateProposer(
            workers=[worker],
            prompts=[prompt_manager],
            sampling=sampling,
            candidate_filter=candidate_filter,
            main_device=torch.device("cpu"),
        )

        batch = proposer.propose_candidates(
            batch_size=2,
            topk=5,
            temp=1.0,
            allow_non_ascii=True,
            filter_cand=True,
            current_control_str="! ! !",
        )

        # Worker was dispatched with GRAD operation
        worker.assert_called_once_with(prompt_manager, ModelWorkerOperation.GRAD)
        # Results queue was queried
        worker.results.get.assert_called_once()
        # Sampling received normalized gradient (norm along dim=-1 should be ~1.0)
        call_kwargs = sampling.sample_candidates.call_args.kwargs
        grad_arg = call_kwargs["gradient"]
        norms = grad_arg.norm(dim=-1)
        assert torch.allclose(norms, torch.ones_like(norms))
        # Candidate filter received sampled tensor and tokenizer
        candidate_filter.filter_candidates.assert_called_once_with(
            candidate_tokens=sampling.sample_candidates.return_value,
            tokenizer=worker.tokenizer,
            current_control="! ! !",
        )
        assert batch.num_groups == 1
        assert batch.control_candidates_by_group == [["filtered_1", "filtered_2"]]
        assert batch.group_worker_indices == [0]

    def test_same_shape_multiple_workers_accumulate_into_single_group(self) -> None:
        worker1 = _create_mock_worker((1, 3, 4))
        worker2 = _create_mock_worker((1, 3, 4))
        pm1 = _create_mock_prompt_manager()
        pm2 = _create_mock_prompt_manager()
        sampling = MagicMock(spec=SamplingStrategy)
        sampling.sample_candidates.return_value = torch.tensor([[10]])
        candidate_filter = MagicMock(spec=CandidateFilter)
        candidate_filter.filter_candidates.return_value = ["res"]

        proposer = GCGCandidateProposer(
            workers=[worker1, worker2],
            prompts=[pm1, pm2],
            sampling=sampling,
            candidate_filter=candidate_filter,
            main_device=torch.device("cpu"),
        )

        batch = proposer.propose_candidates(
            batch_size=1,
            topk=2,
            temp=1.0,
            allow_non_ascii=True,
            filter_cand=True,
            current_control_str="test",
        )

        assert batch.num_groups == 1
        # Accumulated gradient should have norm 2.0 along dim=-1 (sum of 2 unit-norm gradients)
        call_kwargs = sampling.sample_candidates.call_args.kwargs
        grad_arg = call_kwargs["gradient"]
        norms = grad_arg.norm(dim=-1)
        assert torch.allclose(norms, torch.full_like(norms, 2.0))
        # Sampling should only have been called once for the unified group
        assert sampling.sample_candidates.call_count == 1
        assert batch.group_worker_indices == [1]

    def test_mismatched_shapes_creates_multiple_candidate_groups(self) -> None:
        worker1 = _create_mock_worker((1, 3, 4))
        worker2 = _create_mock_worker((1, 3, 8))  # Different vocab/embedding dimension
        pm1 = _create_mock_prompt_manager()
        pm2 = _create_mock_prompt_manager()
        sampling = MagicMock(spec=SamplingStrategy)
        sampling.sample_candidates.side_effect = [
            torch.tensor([[10], [11]]),
            torch.tensor([[20], [21]]),
        ]
        candidate_filter = MagicMock(spec=CandidateFilter)
        candidate_filter.filter_candidates.side_effect = [
            ["g1_c1", "g1_c2"],
            ["g2_c1", "g2_c2"],
        ]

        proposer = GCGCandidateProposer(
            workers=[worker1, worker2],
            prompts=[pm1, pm2],
            sampling=sampling,
            candidate_filter=candidate_filter,
            main_device=torch.device("cpu"),
        )

        batch = proposer.propose_candidates(
            batch_size=2,
            topk=5,
            temp=1.0,
            allow_non_ascii=True,
            filter_cand=True,
            current_control_str="test",
        )

        assert batch.num_groups == 2
        assert batch.control_candidates_by_group == [["g1_c1", "g1_c2"], ["g2_c1", "g2_c2"]]
        assert batch.group_worker_indices == [0, 1]
        assert sampling.sample_candidates.call_count == 2
        assert candidate_filter.filter_candidates.call_count == 2


@pytest.mark.parametrize(
    ("vocab_sizes", "representatives"),
    [
        ([6, 6, 8], [1, 2]),
        ([6, 8, 8, 10], [0, 2, 3]),
        ([6, 6, 8, 8], [1, 3]),
        ([6, 6, 8, 6, 6], [1, 2, 4]),
    ],
)
@pytest.mark.parametrize("use_callbacks", [False, True])
def test_proposal_uses_last_worker_in_each_group(
    *, vocab_sizes: list[int], representatives: list[int], use_callbacks: bool
) -> None:
    workers = []
    prompts = []
    gradients = []
    for index, vocab_size in enumerate(vocab_sizes):
        gradient = torch.arange(1, 2 * vocab_size + 1, dtype=torch.float32).reshape(2, vocab_size)
        gradients.append(gradient / gradient.norm(dim=-1, keepdim=True))
        worker = MagicMock(spec=ModelWorker)
        worker.results = MagicMock()
        worker.results.get.return_value = gradient
        worker.tokenizer = MagicMock()
        worker.tokenizer.decode.return_value = f"worker-{index}"
        workers.append(worker)
        prompt = MagicMock(spec=PromptManager)
        prompt.control_toks = torch.tensor([index, index + 1])
        prompt.disallowed_toks = torch.tensor([index])
        prompt.control_str = "seed"
        prompts.append(prompt)

    sampling = MagicMock(spec=SamplingStrategy)
    sampling.sample_candidates.side_effect = lambda **kwargs: kwargs["control_tokens"].repeat(kwargs["batch_size"], 1)
    candidate_filter = MagicMock(spec=CandidateFilter)
    candidate_filter.filter_candidates.side_effect = lambda **kwargs: [
        kwargs["tokenizer"].decode(row) for row in kwargs["candidate_tokens"]
    ]
    attack = object.__new__(GCGMultiPromptAttack)
    attack.workers = workers
    attack.prompts = prompts
    attack._sampling = sampling
    attack._candidate_filter = candidate_filter
    sample_fn = MagicMock(wraps=attack._sample_control_candidates) if use_callbacks else None
    filter_fn = MagicMock(wraps=attack._filter_control_candidates) if use_callbacks else None
    proposer = GCGCandidateProposer(
        workers=workers,
        prompts=prompts,
        sampling=sampling,
        candidate_filter=candidate_filter,
        sample_fn=sample_fn,
        filter_fn=filter_fn,
        main_device=torch.device("cpu"),
    )

    batch = proposer.propose_candidates(
        batch_size=2,
        topk=3,
        temp=0.7,
        allow_non_ascii=False,
        filter_cand=False,
        current_control_str="seed",
    )

    assert batch.group_worker_indices == representatives
    assert batch.control_candidates_by_group == [[f"worker-{index}"] * 2 for index in representatives]
    assert sampling.sample_candidates.call_count == len(representatives)
    assert candidate_filter.filter_candidates.call_count == len(representatives)
    group_start = 0
    for group_index, worker_index in enumerate(representatives):
        sample_kwargs = sampling.sample_candidates.call_args_list[group_index].kwargs
        expected_gradient = torch.stack(gradients[group_start : worker_index + 1]).sum(dim=0)
        torch.testing.assert_close(sample_kwargs["gradient"], expected_gradient)
        assert sample_kwargs["control_tokens"] is prompts[worker_index].control_toks
        assert sample_kwargs["non_ascii_tokens"] is prompts[worker_index].disallowed_toks
        assert sample_kwargs["batch_size"] == 2
        assert sample_kwargs["top_k"] == 3
        assert sample_kwargs["temperature"] == 0.7
        assert sample_kwargs["allow_non_ascii"] is False
        filter_kwargs = candidate_filter.filter_candidates.call_args_list[group_index].kwargs
        assert filter_kwargs["tokenizer"] is workers[worker_index].tokenizer
        assert filter_kwargs["current_control"] == "seed"
        assert torch.equal(filter_kwargs["candidate_tokens"], prompts[worker_index].control_toks.repeat(2, 1))
        group_start = worker_index + 1
    if sample_fn is not None and filter_fn is not None:
        assert [call.kwargs["worker_index"] for call in sample_fn.call_args_list] == representatives
        assert [call.kwargs["worker_index"] for call in filter_fn.call_args_list] == representatives
        assert all(call.kwargs["filter_cand"] is False for call in filter_fn.call_args_list)
