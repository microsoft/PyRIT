# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Candidate proposal logic for Greedy Coordinate Gradient (GCG) attacks."""

from collections.abc import Callable
from dataclasses import dataclass

import torch

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (
    ModelWorker,
    ModelWorkerOperation,
    PromptManager,
)
from pyrit.executor.promptgen.gcg.extension_protocols import CandidateFilter, SamplingStrategy


@dataclass(frozen=True, slots=True)
class CandidateProposalBatch:
    """
    Grouped candidate control tokens and filtered text strings across compatible workers.

    Attributes:
        control_candidates_by_group: List of filtered candidate string lists, one per
            compatible gradient shape group.
        group_worker_indices: Index of the last worker in each contiguous compatible
            gradient shape group, used for sampling and filtering that group.
    """

    control_candidates_by_group: list[list[str]]
    group_worker_indices: list[int]

    @property
    def num_groups(self) -> int:
        """The number of candidate groups."""
        return len(self.control_candidates_by_group)


class GCGCandidateProposer:
    """Encapsulates gradient aggregation, token candidate sampling, and candidate filtering for GCG."""

    def __init__(
        self,
        *,
        workers: list[ModelWorker],
        prompts: list[PromptManager],
        sampling: SamplingStrategy | None = None,
        candidate_filter: CandidateFilter | None = None,
        sample_fn: Callable[..., torch.Tensor] | None = None,
        filter_fn: Callable[..., list[str]] | None = None,
        main_device: torch.device,
    ) -> None:
        """
        Initialize the candidate proposer with workers, prompt managers, and extension protocols.

        Args:
            workers: List of model workers participating in the attack.
            prompts: List of prompt managers associated with each worker.
            sampling: Sampling strategy protocol used to sample candidate token indices from gradients.
            candidate_filter: Candidate filter protocol used to filter and decode candidate tokens.
            sample_fn: Optional callable to sample candidates. If omitted, uses sampling protocol.
            filter_fn: Optional callable to filter candidates. If omitted, uses candidate_filter protocol.
            main_device: Target PyTorch device on which gradients are aggregated.

        Raises:
            ValueError: If workers list is empty or if worker and prompt manager counts mismatch.
        """
        if not workers:
            raise ValueError("GCG optimization requires at least one worker")
        if len(workers) != len(prompts):
            raise ValueError("Worker and PromptManager count mismatch")

        self._workers = workers
        self._prompts = prompts
        self._sampling = sampling
        self._candidate_filter = candidate_filter
        self._sample_fn = sample_fn
        self._filter_fn = filter_fn
        self._main_device = main_device

    def propose_candidates(
        self,
        *,
        batch_size: int = 1024,
        topk: int = 256,
        temp: float = 1.0,
        allow_non_ascii: bool = True,
        filter_cand: bool = True,
        current_control_str: str,
    ) -> CandidateProposalBatch:
        """
        Dispatch gradient operations, aggregate compatible shapes, and sample/filter candidate controls.

        Args:
            batch_size: Number of candidate controls per batch. Defaults to 1024.
            topk: Number of top gradient positions to sample from. Defaults to 256.
            temp: Temperature for sampling. Kept for protocol compatibility. Defaults to 1.0.
            allow_non_ascii: Whether to allow non-ASCII tokens. Defaults to True.
            filter_cand: Whether to filter invalid candidates. Defaults to True.
            current_control_str: The current decoded control string used as a fallback by length filters.

        Returns:
            CandidateProposalBatch containing filtered candidate strings per gradient shape group
            and corresponding group worker indices.

        Raises:
            RuntimeError: If workers do not produce an aggregate gradient.
        """
        # Dispatch gradient calculation to all workers
        for j, worker in enumerate(self._workers):
            worker(self._prompts[j], ModelWorkerOperation.GRAD)

        control_cands: list[list[str]] = []
        group_worker_indices: list[int] = []
        grad: torch.Tensor | None = None

        # Collect and aggregate gradients across workers
        for j, worker in enumerate(self._workers):
            new_grad: torch.Tensor = worker.results.get().to(self._main_device)
            new_grad = new_grad / new_grad.norm(dim=-1, keepdim=True)

            if grad is None:
                grad = torch.zeros_like(new_grad)

            if grad.shape != new_grad.shape:
                # Shape mismatch: finalize the preceding group
                with torch.no_grad():
                    sampled = self._sample_group(
                        worker_idx=j - 1,
                        gradient=grad,
                        batch_size=batch_size,
                        topk=topk,
                        temp=temp,
                        allow_non_ascii=allow_non_ascii,
                    )
                    filtered = self._filter_group(
                        worker_idx=j - 1,
                        control_cand=sampled,
                        filter_cand=filter_cand,
                        current_control_str=current_control_str,
                    )
                    control_cands.append(filtered)
                    group_worker_indices.append(j - 1)
                grad = new_grad
            else:
                grad += new_grad

        if grad is None:
            raise RuntimeError("GCG workers did not produce an aggregate gradient")

        # Finalize the last group
        last_worker_idx = len(self._workers) - 1
        with torch.no_grad():
            sampled = self._sample_group(
                worker_idx=last_worker_idx,
                gradient=grad,
                batch_size=batch_size,
                topk=topk,
                temp=temp,
                allow_non_ascii=allow_non_ascii,
            )
            filtered = self._filter_group(
                worker_idx=last_worker_idx,
                control_cand=sampled,
                filter_cand=filter_cand,
                current_control_str=current_control_str,
            )
            control_cands.append(filtered)
            group_worker_indices.append(last_worker_idx)

        return CandidateProposalBatch(
            control_candidates_by_group=control_cands,
            group_worker_indices=group_worker_indices,
        )

    def _sample_group(
        self,
        *,
        worker_idx: int,
        gradient: torch.Tensor,
        batch_size: int,
        topk: int,
        temp: float,
        allow_non_ascii: bool,
    ) -> torch.Tensor:
        """
        Sample candidate token indices for a specific worker's control slice.

        Args:
            worker_idx: Index of the representative worker.
            gradient: Aggregated gradient tensor for this shape group.
            batch_size: Number of candidates to sample.
            topk: Top gradient coordinates to sample from.
            temp: Sampling temperature.
            allow_non_ascii: Whether non-ASCII tokens are permitted.

        Returns:
            Tensor of sampled candidate token IDs.

        Raises:
            ValueError: If neither sample_fn nor sampling strategy was provided.
        """
        if self._sample_fn is not None:
            return self._sample_fn(
                worker_index=worker_idx,
                gradient=gradient,
                batch_size=batch_size,
                topk=topk,
                temp=temp,
                allow_non_ascii=allow_non_ascii,
            )
        if self._sampling is None:
            raise ValueError("SamplingStrategy or sample_fn must be provided")
        prompt_manager = self._prompts[worker_idx]
        return self._sampling.sample_candidates(
            gradient=gradient,
            control_tokens=prompt_manager.control_toks,
            batch_size=batch_size,
            top_k=topk,
            temperature=temp,
            allow_non_ascii=allow_non_ascii,
            non_ascii_tokens=prompt_manager.disallowed_toks,
        )

    def _filter_group(
        self,
        *,
        worker_idx: int,
        control_cand: torch.Tensor,
        filter_cand: bool,
        current_control_str: str,
    ) -> list[str]:
        """
        Filter and decode candidate token tensors into valid string controls.

        Args:
            worker_idx: Index of the representative worker.
            control_cand: Sampled candidate token IDs tensor.
            filter_cand: Whether candidate filtering is enabled.
            current_control_str: Current decoded control string fallback.

        Returns:
            List of decoded and filtered candidate control strings.

        Raises:
            ValueError: If neither filter_fn nor candidate_filter was provided.
        """
        if self._filter_fn is not None:
            return self._filter_fn(
                worker_index=worker_idx,
                control_cand=control_cand,
                filter_cand=filter_cand,
            )
        if self._candidate_filter is None:
            raise ValueError("CandidateFilter or filter_fn must be provided")
        return self._candidate_filter.filter_candidates(
            candidate_tokens=control_cand,
            tokenizer=self._workers[worker_idx].tokenizer,
            current_control=current_control_str,
        )
