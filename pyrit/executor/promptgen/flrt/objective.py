# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Loss terms of the FLRT objective.

Version one implements the fluent token-optimization objective from the FLRT
paper ([@thompson2024flrt](https://arxiv.org/abs/2407.17447)) without the
distillation term:

- the clamped forcing loss ``L_F`` over all target tokens, clamped at
  ``-ln(0.6)`` per token, with the first target token weighted 1.5x
  (weights normalized to mean 1);
- the fluency penalty ``L_XE``: the mean per-token cross entropy of the
  prompt under the victim model;
- the repetition penalty ``L_Rep``: ``sum((count_v - 1) ** 1.5) / M`` over
  tokens appearing more than once in the user input. The reference
  implementation counts repeats over the whole rendered prompt; this version
  scopes counting to the user message because the chat template scaffold is
  tokenizer machinery rather than attack content.

All functions operate on plain tensors so they can be unit tested without a
model.
"""

from __future__ import annotations

import math

import torch

#: Per-token loss floor applied to the forcing term: ``-ln(0.6)``. A forced
#: token predicted with probability >= 0.6 contributes the flat floor value,
#: so the optimizer stops spending effort on tokens that are already solved.
DEFAULT_FORCING_FLOOR: float = -math.log(0.6)


def per_token_nll(*, log_probs: torch.Tensor, token_ids: torch.Tensor, positions: torch.Tensor) -> torch.Tensor:
    """
    Negative log probability of each token at the given sequence positions.

    Args:
        log_probs (torch.Tensor): ``[seq_len, vocab]`` log-softmaxed logits
            where row ``t`` predicts token ``t + 1``.
        token_ids (torch.Tensor): ``[seq_len]`` token ids of the sequence.
        positions (torch.Tensor): Sequence positions to score. Every position
            must be >= 1.

    Returns:
        torch.Tensor: ``[-log p(token_ids[p] | tokens before p)]`` for each
            position ``p``.
    """
    rows = log_probs[positions - 1]
    return -rows.gather(1, token_ids[positions, None]).squeeze(1)


def clamped_forcing_loss(
    *,
    log_probs: torch.Tensor,
    token_ids: torch.Tensor,
    target_positions: torch.Tensor,
    floor: float = DEFAULT_FORCING_FLOOR,
) -> torch.Tensor:
    """
    Clamped forcing loss over all target positions.

    Each forced token's negative log probability is clamped from below at
    ``floor``, so well-solved tokens stop contributing optimization pressure.
    Following the reference implementation, the first target token is weighted
    1.5x relative to the others and the weights are normalized to mean 1.
    (Forcing length envelopes and teacher matching return with the distillation
    follow-up.)

    Args:
        log_probs (torch.Tensor): ``[seq_len, vocab]`` log-softmaxed logits.
        token_ids (torch.Tensor): ``[seq_len]`` token ids of the sequence.
        target_positions (torch.Tensor): Sequence positions of the target
            tokens, in order.
        floor (float): Loss floor, ``-ln(p_threshold)``. Defaults to
            ``-ln(0.6)``.

    Returns:
        torch.Tensor: Scalar weighted mean of the clamped per-token losses.
    """
    nll = per_token_nll(log_probs=log_probs, token_ids=token_ids, positions=target_positions).clamp(min=floor)
    n_positions = target_positions.shape[0]
    weights = torch.ones(n_positions, dtype=nll.dtype, device=nll.device)
    weights[0] = 1.5
    weights = weights * n_positions / weights.sum()
    return (nll * weights).mean()


def fluency_loss(*, log_probs: torch.Tensor, token_ids: torch.Tensor, positions: torch.Tensor) -> torch.Tensor:
    """
    Mean per-token cross entropy of the prompt under the victim model.

    Args:
        log_probs (torch.Tensor): ``[seq_len, vocab]`` log-softmaxed logits.
        token_ids (torch.Tensor): ``[seq_len]`` token ids of the sequence.
        positions (torch.Tensor): Prompt positions to average over. Every
            position must be >= 1.

    Returns:
        torch.Tensor: Scalar mean negative log probability.
    """
    return per_token_nll(log_probs=log_probs, token_ids=token_ids, positions=positions).mean()


def repetition_loss(*, token_ids: torch.Tensor, exponent: float) -> torch.Tensor:
    """
    Token-repetition penalty ``sum((count_v - 1) ** exponent) / M``.

    Args:
        token_ids (torch.Tensor): Token ids of the user input region.
        exponent (float): Exponent applied to each token's repeat count.

    Returns:
        torch.Tensor: Scalar penalty; ``0`` when nothing repeats.
    """
    if token_ids.numel() == 0:
        return token_ids.new_zeros((), dtype=torch.float32)
    counts = torch.bincount(token_ids)
    repeats = (counts[counts > 1] - 1).to(torch.float32)
    return (repeats**exponent).sum() / token_ids.numel()


def total_loss(
    *,
    forcing: torch.Tensor,
    fluency: torch.Tensor,
    repetition: torch.Tensor,
    fluency_weight: float,
    repetition_weight: float,
) -> torch.Tensor:
    """
    Compute the combined per-candidate loss ``L_F + C_XE * L_XE + C_rep * L_Rep``.

    Args:
        forcing (torch.Tensor): ``clamped_forcing_loss`` output.
        fluency (torch.Tensor): ``fluency_loss`` output.
        repetition (torch.Tensor): ``repetition_loss`` output.
        fluency_weight (float): ``C_XE``.
        repetition_weight (float): ``C_rep``.

    Returns:
        torch.Tensor: Scalar combined loss.
    """
    return forcing + fluency_weight * fluency + repetition_weight * repetition


__all__ = [
    "DEFAULT_FORCING_FLOOR",
    "clamped_forcing_loss",
    "fluency_loss",
    "per_token_nll",
    "repetition_loss",
    "total_loss",
]
