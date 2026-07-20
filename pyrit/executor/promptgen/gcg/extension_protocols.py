# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Typing-only extension points for the Greedy Coordinate Gradient (GCG) attack.

This module defines four ``runtime_checkable`` ``Protocol``s that mark the four
algorithmic seams inside the GCG optimization loop where a future caller may
substitute custom behavior:

- ``SamplingStrategy`` — how candidate suffix token sequences are drawn from
  the per-step gradient.
- ``LossFunction`` — how each candidate suffix is scored against the target
  string.
- ``CandidateFilter`` — how proposed candidate token tensors get pruned and
  decoded into the string form consumed by the evaluation pass.
- ``SuffixInitializer`` — how the initial suffix string fed into the
  optimization loop is constructed.

The module is **typing surface only**. Concrete defaults live in
``default_implementations.py``, and orchestration wiring lives in
``GCGAlgorithmConfig`` + ``GCGMultiPromptAttack``. Keeping this module purely
protocol definitions preserves a stable extension API that can be imported
without pulling in heavy runtime dependencies.

Tensor-typed signatures are kept lazy via ``from __future__ import
annotations`` plus a ``TYPE_CHECKING`` import for ``torch`` so that
``pyrit.executor.promptgen.gcg.extension_protocols`` itself imports cleanly on
installs that only have the base ``dev`` extra (no torch). At call time the
implementations are still operating on real ``torch.Tensor`` objects — the
forward references just keep the runtime import side-effect free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import torch


@runtime_checkable
class SamplingStrategy(Protocol):
    """
    Proposes a batch of candidate suffix token sequences from the gradient.

    Invoked once per GCG optimization step, after the per-worker gradients have
    been aggregated. The implementation receives the aggregated gradient over
    the control (suffix) positions and the current control token sequence, and
    returns a batch of candidate replacement sequences for the search pass to
    evaluate.

    Implementations must preserve two invariants:

    - The returned tensor has shape ``(batch_size, control_length)`` where
      ``control_length == control_tokens.shape[0]``.
    - The returned tensor lives on the same device as ``gradient`` (the
      orchestrator does not re-locate the result).

    The current GCG implementation (top-k by ``-grad``, uniform pick within the
    top-k at one randomly-chosen position per row) lives in
    ``GCGPromptManager.sample_control``.

    References:
        ``GCGPromptManager.sample_control`` in
        ``pyrit/executor/promptgen/gcg/attack/gcg/gcg_attack.py``.
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
        """
        Sample ``batch_size`` candidate suffix token sequences.

        Args:
            gradient (torch.Tensor): Aggregated gradient over the control
                tokens with shape ``(control_length, vocab_size)`` and dtype
                matching the model's embedding matrix.
            control_tokens (torch.Tensor): The current suffix token sequence
                with shape ``(control_length,)`` and integer dtype.
            batch_size (int): Number of candidate suffix rows to return.
            top_k (int): Number of top gradient positions per control slot
                that the strategy is permitted to draw from.
            temperature (float): Sampling temperature. The current default
                sampling strategy samples uniformly within the top-k and does
                not use this value; it is part of the protocol so custom
                strategies that need it (for example, softmax weighting) can
                receive it.
            allow_non_ascii (bool): When False, the implementation must
                ensure ``non_ascii_tokens`` are excluded from the candidate
                vocabulary (typically by masking those positions of
                ``gradient`` to ``+inf`` before top-k selection).
            non_ascii_tokens (torch.Tensor): Token ids to exclude when
                ``allow_non_ascii`` is False, shape ``(num_disallowed,)``
                and integer dtype.

        Returns:
            torch.Tensor: Candidate suffix token sequences with shape
            ``(batch_size, control_length)`` on the same device as
            ``gradient``.
        """
        ...


@runtime_checkable
class LossFunction(Protocol):
    """
    Scores a batch of candidate suffixes against the target completion.

    Invoked once per worker per training prompt per candidate batch during the
    search pass. The implementation receives the model's logits for the
    candidate batch together with the input ids that produced them, plus the
    slices that locate the target and control regions inside the sequence, and
    returns a per-candidate scalar loss tensor.

    Owning the entire loss computation in one method (criterion choice,
    slicing, and any weighted combination of target/control terms) keeps the
    protocol orthogonal to the orchestrator: the caller does not need to know
    whether the implementation uses cross-entropy or something else, nor how
    target and control contributions are combined. The current GCG code path
    weights cross-entropy on the target slice by ``target_weight`` and on the
    control slice by ``control_weight`` and sums them; a custom
    ``LossFunction`` would encapsulate equivalent knobs in its own
    constructor.

    Implementations must preserve one invariant:

    - The returned tensor has shape ``(batch_size,)`` — one scalar loss per
      candidate. Lower values indicate a better candidate (the orchestrator
      selects the ``argmin``).

    References:
        ``AttackPrompt.target_loss`` and ``AttackPrompt.control_loss`` in
        ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py``, plus
        the weighted-sum aggregation inside ``GCGMultiPromptAttack.step`` in
        ``pyrit/executor/promptgen/gcg/attack/gcg/gcg_attack.py``.
    """

    def compute_loss(
        self,
        *,
        logits: torch.Tensor,
        token_ids: torch.Tensor,
        target_slice: slice,
        control_slice: slice,
    ) -> torch.Tensor:
        """
        Compute the per-candidate loss for a candidate batch.

        Args:
            logits (torch.Tensor): Model logits for the candidate batch with
                shape ``(batch_size, seq_len, vocab_size)``.
            token_ids (torch.Tensor): Input token ids the model was run on
                with shape ``(batch_size, seq_len)`` and integer dtype.
            target_slice (slice): Slice into the sequence dimension that
                identifies the target tokens (the completion being optimized
                toward).
            control_slice (slice): Slice into the sequence dimension that
                identifies the control (suffix) tokens.

        Returns:
            torch.Tensor: Per-candidate scalar loss with shape
            ``(batch_size,)``. Lower is better.
        """
        ...


@runtime_checkable
class CandidateFilter(Protocol):
    """
    Decodes and prunes a batch of candidate suffix token tensors.

    Invoked once per worker per optimization step, immediately after sampling.
    The implementation receives the raw sampled token tensor and the worker's
    tokenizer, and is expected to:

    - Decode each row into its string form (this is what the evaluation pass
      consumes — it sends the candidate strings back through the tokenizer
      together with the goal prompt).
    - Optionally drop candidates that fail some quality check.
    - Return *exactly* ``batch_size`` strings (the orchestrator allocates a
      flat loss buffer of size ``batch_size`` and does not tolerate ragged
      outputs). The current implementation pads any dropped rows by repeating
      the last accepted candidate.

    Implementations must preserve two invariants:

    - The returned list has length equal to ``candidate_tokens.shape[0]``.
    - No element of the returned list equals ``current_control`` *unless* the
      implementation explicitly allows the no-op candidate (the legacy
      filter drops it on the assumption that re-evaluating the current
      suffix wastes a slot).

    References:
        ``MultiPromptAttack.get_filtered_cands`` in
        ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py``.
    """

    def filter_candidates(
        self,
        *,
        candidate_tokens: torch.Tensor,
        tokenizer: Any,
        current_control: str,
    ) -> list[str]:
        """
        Decode and filter a batch of candidate suffix token tensors.

        Args:
            candidate_tokens (torch.Tensor): Sampled candidate suffixes with
                shape ``(batch_size, control_length)`` and integer dtype.
            tokenizer (Any): The worker's HuggingFace-style tokenizer.
                ``tokenizer.decode`` is used to render each row to text and
                ``tokenizer(text, add_special_tokens=False).input_ids`` is
                used by the default length-preserving filter to detect
                re-tokenization drift.
            current_control (str): The current suffix string. Used by the
                default filter to drop the no-op candidate.

        Returns:
            list[str]: Decoded candidate suffix strings of length exactly
            ``candidate_tokens.shape[0]``. Dropped rows are typically padded
            by repeating the last accepted candidate.
        """
        ...


@runtime_checkable
class SuffixInitializer(Protocol):
    """
    Produces the initial suffix string fed into the optimization loop.

    Invoked once at attack-setup time. The returned string is threaded through
    the existing ``control_init`` parameter of ``AttackPrompt`` /
    ``PromptManager`` / ``MultiPromptAttack`` / the per-strategy ``*Attack``
    constructors, so a custom initializer is fully decoupled from the per-step
    optimization machinery.

    The tokenizer is passed in case an implementation needs vocab access (for
    example, a random initializer that draws tokens from the model's
    vocabulary). The current default — a literal string supplied via
    ``GCGAlgorithmConfig.control_init`` — ignores the tokenizer and returns
    the configured string verbatim.

    Implementations must return a non-empty string. The downstream
    ``AttackPrompt`` constructor raises ``ValueError`` if the suffix cannot be
    located inside the chat-templated prompt, so the returned string must also
    survive tokenizer round-tripping inside the goal+control message body
    (the default twenty space-separated ``!`` tokens satisfies this for every
    chat template PyRIT has been tested against).

    References:
        ``AttackPrompt.__init__`` in
        ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py`` assigns
        ``self.control = control_init``. The same ``control_init`` parameter
        is threaded through the ``PromptManager``, ``MultiPromptAttack``,
        ``ProgressiveMultiPromptAttack``, ``IndividualPromptAttack``, and
        ``EvaluateAttack`` constructors in the same module.
    """

    def make_initial_suffix(self, *, tokenizer: Any) -> str:
        """
        Return the initial suffix string for the optimization loop.

        Args:
            tokenizer (Any): A HuggingFace-style tokenizer the implementation
                may consult (for example, to sample tokens from the
                vocabulary). The default literal-string initializer ignores
                this argument.

        Returns:
            str: The initial suffix string. Must be non-empty and must
            survive tokenizer round-tripping inside the chat-templated
            prompt body.
        """
        ...


__all__ = [
    "CandidateFilter",
    "LossFunction",
    "SamplingStrategy",
    "SuffixInitializer",
]
