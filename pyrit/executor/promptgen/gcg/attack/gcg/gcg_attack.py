# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from tqdm.auto import tqdm

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (
    AttackPrompt,
    ModelWorkerOperation,
    MultiPromptAttack,
    PromptManager,
    get_embedding_matrix,
    get_embeddings,
)
from pyrit.executor.promptgen.gcg.default_implementations import (
    CrossEntropyLoss,
    LengthPreservingFilter,
    StandardGCGSampling,
)
from pyrit.executor.promptgen.gcg.extension_protocols import CandidateFilter, LossFunction, SamplingStrategy

logger = logging.getLogger(__name__)


def token_gradients(
    model: Any,
    input_ids: torch.Tensor,
    input_slice: slice,
    target_slice: slice,
    loss_slice: slice,
) -> torch.Tensor:
    """
    Compute gradients of the loss with respect to the coordinates.

    Args:
        model (Any): The transformer model to be used.
        input_ids (torch.Tensor): The input sequence in the form of token ids.
        input_slice (slice): The slice of the input sequence for which gradients need to be computed.
        target_slice (slice): The slice of the input sequence to be used as targets.
        loss_slice (slice): The slice of the logits to be used for computing the loss.

    Returns:
        torch.Tensor: The gradients of each token in the input_slice with respect to the loss.

    Raises:
        RuntimeError: If autograd does not produce token gradients.
    """
    embed_weights = get_embedding_matrix(model)
    one_hot = torch.zeros(
        input_ids[input_slice].shape[0], embed_weights.shape[0], device=model.device, dtype=embed_weights.dtype
    )
    one_hot.scatter_(
        1,
        input_ids[input_slice].unsqueeze(1),
        torch.ones(one_hot.shape[0], 1, device=model.device, dtype=embed_weights.dtype),
    )
    one_hot.requires_grad_()
    input_embeds = (one_hot @ embed_weights).unsqueeze(0)

    # now stitch it together with the rest of the embeddings
    embeds = get_embeddings(model, input_ids.unsqueeze(0)).detach()
    full_embeds = torch.cat([embeds[:, : input_slice.start, :], input_embeds, embeds[:, input_slice.stop :, :]], dim=1)

    logits = model(inputs_embeds=full_embeds).logits
    targets = input_ids[target_slice]
    loss = nn.CrossEntropyLoss()(logits[0, loss_slice, :], targets)

    coordinate_gradient = torch.autograd.grad(loss, one_hot, allow_unused=True)[0]
    if coordinate_gradient is None:
        raise RuntimeError("Autograd did not produce token gradients")
    return coordinate_gradient


class GCGAttackPrompt(AttackPrompt):
    """GCG-specific attack prompt that computes token gradients."""

    def grad(self, model: Any) -> torch.Tensor:
        """
        Compute token gradients for this prompt.

        Args:
            model (Any): The transformer model to compute gradients with.

        Returns:
            torch.Tensor: Gradients with respect to control tokens.
        """
        return token_gradients(
            model, self.input_ids.to(model.device), self._control_slice, self._target_slice, self._loss_slice
        )


class GCGPromptManager(PromptManager):
    """GCG-specific prompt manager that implements control token sampling."""

    def sample_control(
        self,
        grad: torch.Tensor,
        batch_size: int,
        topk: int = 256,
        temp: float = 1.0,
        allow_non_ascii: bool = True,
    ) -> torch.Tensor:
        """
        Sample new control token candidates based on gradients.

        Args:
            grad (torch.Tensor): Gradient tensor for control tokens.
            batch_size (int): Number of candidate controls to generate.
            topk (int): Number of top gradient positions to sample from. Defaults to 256.
            temp (float): Temperature for sampling. Currently unused but kept for API compatibility. Defaults to 1.0.
            allow_non_ascii (bool): Whether to allow non-ASCII tokens. Defaults to True.

        Returns:
            torch.Tensor: Batch of new candidate control token sequences.
        """
        if not allow_non_ascii:
            grad[:, self._nonascii_toks.to(grad.device)] = np.inf
        top_indices = (-grad).topk(topk, dim=1).indices
        control_toks = self.control_toks.to(grad.device)
        original_control_toks = control_toks.repeat(batch_size, 1)
        new_token_pos = torch.arange(0, len(control_toks), len(control_toks) / batch_size, device=grad.device).type(
            torch.int64
        )
        new_token_val = torch.gather(
            top_indices[new_token_pos], 1, torch.randint(0, topk, (batch_size, 1), device=grad.device)
        )
        return original_control_toks.scatter_(1, new_token_pos.unsqueeze(-1), new_token_val)


class GCGMultiPromptAttack(MultiPromptAttack):
    """GCG-specific multi-prompt attack that implements the GCG optimization step."""

    def __init__(
        self,
        goals: list[str],
        targets: list[str],
        workers: list[Any],
        control_init: str = "! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! !",
        test_prefixes: list[str] | None = None,
        logfile: str | None = None,
        managers: dict[str, Any] | None = None,
        test_goals: list[str] | None = None,
        test_targets: list[str] | None = None,
        test_workers: list[Any] | None = None,
        *,
        sampling: SamplingStrategy | None = None,
        loss: LossFunction | None = None,
        candidate_filter: CandidateFilter | None = None,
    ) -> None:
        """Initialize a GCG attack with optional algorithm extensions."""
        super().__init__(
            goals,
            targets,
            workers,
            control_init,
            test_prefixes,
            logfile,
            managers,
            test_goals,
            test_targets,
            test_workers,
        )
        self._sampling = sampling
        self._loss = loss
        self._candidate_filter = candidate_filter

    def _resolve_sampling(self) -> SamplingStrategy:
        sampling = getattr(self, "_sampling", None)
        if sampling is not None:
            return sampling
        return StandardGCGSampling()

    def _resolve_loss(self, *, target_weight: float, control_weight: float) -> LossFunction:
        loss = getattr(self, "_loss", None)
        if loss is not None:
            return loss
        return CrossEntropyLoss(target_weight=target_weight, control_weight=control_weight)

    def _resolve_candidate_filter(self, *, filter_cand: bool) -> CandidateFilter:
        candidate_filter = getattr(self, "_candidate_filter", None)
        if candidate_filter is not None:
            return candidate_filter
        return LengthPreservingFilter(enabled=filter_cand)

    def _sample_control_candidates(
        self,
        *,
        worker_index: int,
        gradient: torch.Tensor,
        batch_size: int,
        topk: int,
        temp: float,
        allow_non_ascii: bool,
    ) -> torch.Tensor:
        sampler = self._resolve_sampling()
        prompt_manager = self.prompts[worker_index]
        return sampler.sample_candidates(
            gradient=gradient,
            control_tokens=prompt_manager.control_toks,
            batch_size=batch_size,
            top_k=topk,
            temperature=temp,
            allow_non_ascii=allow_non_ascii,
            non_ascii_tokens=prompt_manager.disallowed_toks,
        )

    def _filter_control_candidates(
        self,
        *,
        worker_index: int,
        control_cand: torch.Tensor,
        filter_cand: bool,
    ) -> list[str]:
        candidate_filter = self._resolve_candidate_filter(filter_cand=filter_cand)
        return candidate_filter.filter_candidates(
            candidate_tokens=control_cand,
            tokenizer=self.workers[worker_index].tokenizer,
            current_control=self.control_str,
        )

    def _get_control_length(self, *, control: str) -> int | None:
        try:
            return len(self.workers[0].tokenizer(control).input_ids[1:])
        except (AttributeError, TypeError, ValueError):
            return None

    def step(
        self,
        *,
        batch_size: int = 1024,
        topk: int = 256,
        temp: float = 1.0,
        allow_non_ascii: bool = True,
        target_weight: float = 1,
        control_weight: float = 0.1,
        verbose: bool = False,
        filter_cand: bool = True,
    ) -> tuple[str, float]:
        """
        Execute one GCG optimization step.

        Aggregates gradients across workers, samples candidate controls,
        evaluates them, and returns the best candidate.

        Args:
            batch_size (int): Number of candidate controls per batch. Defaults to 1024.
            topk (int): Number of top gradient positions to sample from. Defaults to 256.
            temp (float): Temperature for sampling. Currently unused but kept for API compatibility. Defaults to 1.0.
            allow_non_ascii (bool): Whether to allow non-ASCII tokens. Defaults to True.
            target_weight (float): Weight for target loss. Defaults to 1.
            control_weight (float): Weight for control loss. Defaults to 0.1.
            verbose (bool): Whether to show progress bars. Defaults to False.
            filter_cand (bool): Whether to filter invalid candidates. Defaults to True.

        Returns:
            tuple[str, float]: The best control string and its normalized loss.

        Raises:
            RuntimeError: If workers produce no aggregate gradient.
            ValueError: If no model worker is configured.
        """
        if not self.workers:
            raise ValueError("GCG optimization requires at least one worker")

        main_device = self.models[0].device
        control_cands = []
        loss_function = self._resolve_loss(target_weight=target_weight, control_weight=control_weight)

        for j, worker in enumerate(self.workers):
            worker(self.prompts[j], ModelWorkerOperation.GRAD)

        # Aggregate gradients
        grad = None
        for j, worker in enumerate(self.workers):
            new_grad = worker.results.get().to(main_device)
            new_grad = new_grad / new_grad.norm(dim=-1, keepdim=True)
            if grad is None:
                grad = torch.zeros_like(new_grad)
            if grad.shape != new_grad.shape:
                with torch.no_grad():
                    control_cand = self._sample_control_candidates(
                        worker_index=j - 1,
                        gradient=grad,
                        batch_size=batch_size,
                        topk=topk,
                        temp=temp,
                        allow_non_ascii=allow_non_ascii,
                    )
                    control_cands.append(
                        self._filter_control_candidates(
                            worker_index=j - 1,
                            control_cand=control_cand,
                            filter_cand=filter_cand,
                        )
                    )
                grad = new_grad
            else:
                grad += new_grad

        if grad is None:
            raise RuntimeError("GCG workers did not produce an aggregate gradient")

        last_worker_index = len(self.workers) - 1
        with torch.no_grad():
            control_cand = self._sample_control_candidates(
                worker_index=last_worker_index,
                gradient=grad,
                batch_size=batch_size,
                topk=topk,
                temp=temp,
                allow_non_ascii=allow_non_ascii,
            )
            control_cands.append(
                self._filter_control_candidates(
                    worker_index=last_worker_index,
                    control_cand=control_cand,
                    filter_cand=filter_cand,
                )
            )
        del grad, control_cand

        # Search
        loss = torch.zeros(len(control_cands) * batch_size).to(main_device)
        with torch.no_grad():
            for j, cand in enumerate(control_cands):
                # Looping through the prompts at this level is less elegant, but
                # we can manage VRAM better this way
                progress = tqdm(range(len(self.prompts[0])), total=len(self.prompts[0])) if verbose else None
                prompt_indices = progress if progress is not None else range(len(self.prompts[0]))
                for i in prompt_indices:
                    for k, worker in enumerate(self.workers):
                        worker(self.prompts[k][i], ModelWorkerOperation.LOGITS, cand, return_ids=True)
                    logits, ids = zip(*[worker.results.get() for worker in self.workers], strict=True)
                    loss[j * batch_size : (j + 1) * batch_size] += sum(
                        loss_function.compute_loss(
                            logits=logit,
                            token_ids=token_ids,
                            target_slice=self.prompts[k][i]._target_slice,
                            control_slice=self.prompts[k][i]._control_slice,
                        ).to(main_device)
                        for k, (logit, token_ids) in enumerate(zip(logits, ids, strict=True))
                    )
                    del logits, ids

                    if progress is not None:
                        progress.set_description(
                            f"loss={loss[j * batch_size : (j + 1) * batch_size].min().item() / (i + 1):.4f}"
                        )

            min_idx = loss.argmin()
            model_idx = min_idx // batch_size
            batch_idx = min_idx % batch_size
            next_control, cand_loss = control_cands[model_idx][batch_idx], loss[min_idx]
        del control_cands, loss

        current_length = self._get_control_length(control=next_control)
        if current_length is not None:
            logger.info(f"Current length: {current_length}")
        logger.info(next_control)

        return next_control, cand_loss.item() / len(self.prompts[0]) / len(self.workers)
