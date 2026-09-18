# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Candidate evaluation logic for Greedy Coordinate Gradient (GCG) attacks."""

from dataclasses import dataclass

import torch
from tqdm.auto import tqdm

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (
    ModelWorker,
    ModelWorkerOperation,
    PromptManager,
)
from pyrit.executor.promptgen.gcg.extension_protocols import LossFunction


@dataclass(frozen=True, slots=True)
class CandidateEvaluationBatch:
    """
    Evaluated candidate control sequences and their corresponding aggregate losses.

    Attributes:
        control_candidates_by_group: The list of evaluated candidate string lists per group.
        losses: 1D PyTorch tensor of aggregate unnormalized losses across all groups and candidates
            on main_device.
    """

    control_candidates_by_group: list[list[str]]
    losses: torch.Tensor

    @property
    def num_groups(self) -> int:
        """The number of candidate groups evaluated."""
        return len(self.control_candidates_by_group)


class GCGCandidateEvaluator:
    """Encapsulates VRAM-bounded candidate evaluation across model workers and prompts."""

    def __init__(
        self,
        *,
        workers: list[ModelWorker],
        prompts: list[PromptManager],
        loss_function: LossFunction,
        main_device: torch.device,
    ) -> None:
        """
        Initialize the candidate evaluator.

        Args:
            workers: List of model workers participating in the attack.
            prompts: List of prompt managers associated with each worker.
            loss_function: Loss function protocol used to compute candidate losses.
            main_device: PyTorch device on which aggregate losses are stored and accumulated.

        Raises:
            ValueError: If workers list is empty, or if worker and prompt manager counts mismatch,
                or if prompts contain no prompts, or if prompt counts vary across workers.
        """
        if not workers:
            raise ValueError("GCG candidate evaluation requires at least one worker")
        if len(workers) != len(prompts):
            raise ValueError("Worker and PromptManager count mismatch")
        if not prompts or len(prompts[0]) == 0:
            raise ValueError("PromptManager must contain at least one prompt")
        if any(len(p) != len(prompts[0]) for p in prompts):
            raise ValueError("All PromptManagers must have identical prompt counts")

        self._workers = workers
        self._prompts = prompts
        self._loss_function = loss_function
        self._main_device = main_device

    def evaluate_candidates(
        self,
        *,
        control_candidates_by_group: list[list[str]],
        batch_size: int,
        verbose: bool = False,
    ) -> CandidateEvaluationBatch:
        """
        Evaluate candidate strings sequentially across groups to bound VRAM.

        For each candidate group:
          - Iterates through prompts sequentially.
          - Dispatches ModelWorkerOperation.LOGITS with return_ids=True to all workers in parallel.
          - Collects (logits, token_ids) from worker result queues.
          - Computes loss using loss_function for target and control slices.
          - Accumulates losses on main_device.
          - Releases intermediate logits and token_ids immediately to bound VRAM.

        Args:
            control_candidates_by_group: List of candidate string lists per gradient shape group.
            batch_size: Number of candidate controls per batch.
            verbose: Whether to display a tqdm progress bar during evaluation.

        Returns:
            CandidateEvaluationBatch containing the evaluated candidate groups and flat loss tensor.

        Raises:
            ValueError: If batch_size is not positive, if control_candidates_by_group is empty,
                or if any group's candidate count does not match batch_size.
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        if not control_candidates_by_group:
            raise ValueError("Must provide at least one candidate group to evaluate")
        if any(len(group) != batch_size for group in control_candidates_by_group):
            raise ValueError("Each candidate group must contain exactly batch_size candidates")

        num_groups = len(control_candidates_by_group)
        loss = torch.zeros(num_groups * batch_size, device=self._main_device)
        num_prompts = len(self._prompts[0])

        with torch.no_grad():
            for j, cand in enumerate(control_candidates_by_group):
                progress = tqdm(range(num_prompts), total=num_prompts) if verbose else None
                prompt_indices = progress if progress is not None else range(num_prompts)

                for i in prompt_indices:
                    for k, worker in enumerate(self._workers):
                        worker(self._prompts[k][i], ModelWorkerOperation.LOGITS, cand, return_ids=True)

                    logits, ids = zip(*[worker.results.get() for worker in self._workers], strict=True)
                    loss[j * batch_size : (j + 1) * batch_size] += sum(
                        self._loss_function.compute_loss(
                            logits=logit,
                            token_ids=token_ids,
                            target_slice=self._prompts[k][i]._target_slice,
                            control_slice=self._prompts[k][i]._control_slice,
                        ).to(self._main_device)
                        for k, (logit, token_ids) in enumerate(zip(logits, ids, strict=True))
                    )
                    del logits, ids

                    if progress is not None:
                        progress.set_description(
                            f"loss={loss[j * batch_size : (j + 1) * batch_size].min().item() / (i + 1):.4f}"
                        )

        return CandidateEvaluationBatch(
            control_candidates_by_group=control_candidates_by_group,
            losses=loss,
        )
