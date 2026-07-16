# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Default concrete implementations of the four GCG extension protocols.

Each class in this module reproduces the byte-identical behavior of the
legacy GCG attack code path it replaces:

- ``StandardGCGSampling`` reproduces ``GCGPromptManager.sample_control``.
- ``CrossEntropyLoss`` reproduces ``AttackPrompt.target_loss`` and
  ``AttackPrompt.control_loss`` combined via the weighted sum applied
  inside ``GCGMultiPromptAttack.step``.
- ``LengthPreservingFilter`` reproduces ``MultiPromptAttack.get_filtered_cands``.
- ``LiteralStringInit`` reproduces the literal-string ``control_init``
  parameter threaded through the attack constructors.

The defaults are *not* wired into ``GCGMultiPromptAttack`` here. They are
shipped ahead of wiring so the strategy objects can already be constructed
and inspected, and so the wiring change is a pure orchestration edit.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


class StandardGCGSampling:
    """Top-k by ``-gradient``, uniform pick within top-k at one random position per row.

    The standard GCG sampling rule: for each of ``batch_size`` candidate
    rows, pick one of the ``control_length`` positions, then replace the
    token at that position with a uniformly-sampled token id from the top-k
    smallest-gradient (most-promising) candidates at that position. The
    ``temperature`` argument is part of the protocol but is unused by this
    sampler, which always samples uniformly within the top-k.

    Reproduces ``GCGPromptManager.sample_control`` from
    ``pyrit/executor/promptgen/gcg/attack/gcg/gcg_attack.py`` byte-for-byte.
    """

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
        """Sample ``batch_size`` candidate suffix token sequences.

        Args:
            gradient (torch.Tensor): Aggregated gradient over the control
                tokens with shape ``(control_length, vocab_size)``. Mutated
                in-place when ``allow_non_ascii`` is False (the disallowed
                token positions are set to ``+inf``), matching legacy
                behavior.
            control_tokens (torch.Tensor): Current suffix token sequence
                with shape ``(control_length,)``.
            batch_size (int): Number of candidate suffix rows to return.
            top_k (int): Number of top gradient positions per control slot
                drawn from.
            temperature (float): Sampling temperature. Unused by this
                implementation; kept to match the protocol signature.
            allow_non_ascii (bool): When False, mask the ``non_ascii_tokens``
                positions of ``gradient`` to ``+inf`` so they fall out of
                the top-k.
            non_ascii_tokens (torch.Tensor): Token ids to exclude when
                ``allow_non_ascii`` is False.

        Returns:
            torch.Tensor: Candidate suffix token sequences with shape
            ``(batch_size, control_length)`` on the same device as
            ``gradient``.
        """
        if not allow_non_ascii:
            gradient[:, non_ascii_tokens.to(gradient.device)] = np.inf
        top_indices = (-gradient).topk(top_k, dim=1).indices
        control_tokens = control_tokens.to(gradient.device)
        original_control_tokens = control_tokens.repeat(batch_size, 1)
        new_token_pos = torch.arange(
            0,
            len(control_tokens),
            len(control_tokens) / batch_size,
            device=gradient.device,
        ).type(torch.int64)
        new_token_val = torch.gather(
            top_indices[new_token_pos],
            1,
            torch.randint(0, top_k, (batch_size, 1), device=gradient.device),
        )
        return original_control_tokens.scatter_(1, new_token_pos.unsqueeze(-1), new_token_val)


class CrossEntropyLoss:
    """Weighted token-level cross-entropy on the target and control slices.

    Per candidate: ``target_weight * CE(target_slice) + control_weight *
    CE(control_slice)``, where each cross-entropy term is reduced over its
    slice with ``.mean(dim=-1)`` to give one scalar per candidate. The
    ``.mean(dim=-1)`` reduction matches where the legacy orchestrator
    applies it: ``GCGMultiPromptAttack.step`` calls
    ``target_loss(...).mean(dim=-1)`` outside the per-prompt loss method,
    so the ``LossFunction`` protocol places the per-candidate scalar
    reduction inside the implementation.

    When ``control_weight == 0`` the control term is skipped entirely,
    matching the legacy ``if control_weight != 0:`` guard inside ``step``.
    The same skip is applied when ``target_weight == 0`` for symmetry.

    Reproduces ``AttackPrompt.target_loss`` + ``AttackPrompt.control_loss``
    from ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py``,
    combined per ``GCGMultiPromptAttack.step`` in
    ``pyrit/executor/promptgen/gcg/attack/gcg/gcg_attack.py``.
    """

    def __init__(self, *, target_weight: float = 1.0, control_weight: float = 0.0) -> None:
        """Initialize the cross-entropy loss with target / control weights.

        Args:
            target_weight (float): Weight on the target-slice cross-entropy.
                Defaults to 1.0.
            control_weight (float): Weight on the control-slice
                cross-entropy. Defaults to 0.0 (target-only signal).

        Raises:
            ValueError: If either weight is negative, or if both are zero.
        """
        if target_weight < 0 or control_weight < 0:
            raise ValueError(
                "CrossEntropyLoss target_weight and control_weight must be >= 0, "
                f"got target_weight={target_weight}, control_weight={control_weight}."
            )
        if target_weight == 0 and control_weight == 0:
            raise ValueError(
                "CrossEntropyLoss requires at least one of target_weight or control_weight to be > 0; "
                "with both at 0 the loss is identically zero and provides no signal."
            )
        self._target_weight = target_weight
        self._control_weight = control_weight

    def compute_loss(
        self,
        *,
        logits: torch.Tensor,
        token_ids: torch.Tensor,
        target_slice: slice,
        control_slice: slice,
    ) -> torch.Tensor:
        """Compute the per-candidate weighted cross-entropy loss.

        Args:
            logits (torch.Tensor): Model logits for the candidate batch
                with shape ``(batch_size, seq_len, vocab_size)``.
            token_ids (torch.Tensor): Input token ids the model was run on
                with shape ``(batch_size, seq_len)``.
            target_slice (slice): Slice into the sequence dimension that
                identifies the target tokens.
            control_slice (slice): Slice into the sequence dimension that
                identifies the control (suffix) tokens.

        Returns:
            torch.Tensor: Per-candidate scalar loss with shape
            ``(batch_size,)``.
        """
        criterion = nn.CrossEntropyLoss(reduction="none")
        total: torch.Tensor | None = None

        if self._target_weight > 0:
            target_loss_slice = slice(target_slice.start - 1, target_slice.stop - 1)
            target_term = criterion(
                logits[:, target_loss_slice, :].transpose(1, 2),
                token_ids[:, target_slice],
            ).mean(dim=-1)
            total = self._target_weight * target_term

        if self._control_weight > 0:
            control_loss_slice = slice(control_slice.start - 1, control_slice.stop - 1)
            control_term = criterion(
                logits[:, control_loss_slice, :].transpose(1, 2),
                token_ids[:, control_slice],
            ).mean(dim=-1)
            weighted_control = self._control_weight * control_term
            total = weighted_control if total is None else total + weighted_control

        # Constructor guarantees at least one weight is > 0, so ``total`` is
        # always assigned. The check is kept for the type checker.
        if total is None:
            raise RuntimeError(
                "CrossEntropyLoss.compute_loss produced no terms; "
                "this indicates a corrupted instance with both weights at 0."
            )
        return total


class LengthPreservingFilter:
    """Decodes each candidate token row and drops any whose decoded string
    either (a) equals ``current_control`` or (b) re-tokenizes to a different
    token count, padding dropped rows by repeating the last accepted
    candidate.

    The ``filter`` constructor parameter selects between filtering (legacy
    ``filter_cand=True`` branch) and passthrough decode-only mode (legacy
    ``filter_cand=False`` branch).

    Also performs the legacy out-of-vocab clamping: tokens above
    ``tokenizer.vocab_size`` are replaced in-place by the id of ``"!"``,
    matching the safety pass at the top of ``get_filtered_cands``.

    Reproduces ``MultiPromptAttack.get_filtered_cands`` from
    ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py``.
    """

    def __init__(self, *, filter: bool = True) -> None:
        """Initialize the filter.

        Args:
            filter (bool): When True, drop candidates that equal
                ``current_control`` or re-tokenize to a different length,
                padding the result with the last accepted candidate. When
                False, decode every row and return them all unchanged.
                Defaults to True.
        """
        self._filter = filter

    def filter_candidates(
        self,
        *,
        candidate_tokens: torch.Tensor,
        tokenizer: Any,
        current_control: str,
    ) -> list[str]:
        """Decode and filter a batch of candidate suffix token tensors.

        Args:
            candidate_tokens (torch.Tensor): Sampled candidate suffixes
                with shape ``(batch_size, control_length)``. Mutated
                in-place by the out-of-vocab clamp, matching legacy
                behavior.
            tokenizer (Any): HuggingFace-style tokenizer. ``tokenizer.decode``
                renders each row to text; ``tokenizer(text,
                add_special_tokens=False).input_ids`` is used to detect
                re-tokenization drift; ``tokenizer("!").input_ids[0]``
                provides the replacement id for out-of-vocab clamping.
            current_control (str): Current suffix string. When ``filter``
                is True, candidates that decode to this string are dropped.

        Returns:
            list[str]: Decoded candidate suffix strings of length exactly
            ``candidate_tokens.shape[0]``.
        """
        logger.info("Masking out of range token_id.")
        vocab_size = tokenizer.vocab_size
        candidate_tokens[candidate_tokens > vocab_size] = tokenizer("!").input_ids[0]

        candidates: list[str] = []
        for i in range(candidate_tokens.shape[0]):
            decoded_str = tokenizer.decode(
                candidate_tokens[i], skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            if self._filter:
                if decoded_str != current_control and len(
                    tokenizer(decoded_str, add_special_tokens=False).input_ids
                ) == len(candidate_tokens[i]):
                    candidates.append(decoded_str)
            else:
                candidates.append(decoded_str)

        if self._filter:
            candidates = candidates + [candidates[-1]] * (len(candidate_tokens) - len(candidates))
        return candidates


class LiteralStringInit:
    """Returns the configured literal suffix verbatim; ignores the tokenizer.

    Encapsulates the current ``control_init`` plumbing — a literal string
    threaded through ``AttackPrompt.__init__``, ``PromptManager.__init__``,
    ``MultiPromptAttack.__init__``, and the per-strategy ``*Attack``
    constructors — so that custom initializers that do need the tokenizer
    (for example, a random vocabulary sampler) can be swapped in without
    changing those constructor signatures.

    Reproduces the literal-string ``control_init`` parameter assignment
    (``self.control = control_init``) inside ``AttackPrompt.__init__`` in
    ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py``.
    """

    def __init__(self, *, suffix: str) -> None:
        """Initialize the literal-string suffix initializer.

        Args:
            suffix (str): The literal suffix string to return on every
                call to ``make_initial_suffix``. Must be non-empty.

        Raises:
            ValueError: If ``suffix`` is the empty string.
        """
        if not suffix:
            raise ValueError("LiteralStringInit.suffix must be a non-empty string.")
        self._suffix = suffix

    def make_initial_suffix(self, *, tokenizer: Any) -> str:
        """Return the configured suffix string.

        Args:
            tokenizer (Any): Ignored. Present to match the protocol
                signature so custom initializers that need vocabulary
                access can be substituted without changing call sites.

        Returns:
            str: The literal suffix string supplied at construction.
        """
        return self._suffix


__all__ = [
    "CrossEntropyLoss",
    "LengthPreservingFilter",
    "LiteralStringInit",
    "StandardGCGSampling",
]
