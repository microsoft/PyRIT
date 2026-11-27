# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Token edit operations for the FLRT attack.

FLRT mutates the current-best attack in a single token position per candidate.
Each optimization step first samples one mutation type, then produces
``n_candidates`` new attacks of that type:

- ``delete``: remove a token at a random position.
- ``insert``: insert a proposed token before a random position (or at the end).
- ``swap``: replace a token at a random position with a proposed token.
- ``edge``: insert a proposed token at the end of the attack.

Proposed tokens come from sampling the victim model's next-token distribution
without replacement (the BEAST-style proposals described in section 5.1 of the
FLRT paper). A proposal table with ``len(token_ids) + 1`` rows is expected:
row ``i`` proposes tokens that could occupy position ``i`` of the attack, so it
serves both swaps (rows ``0..len-1``) and insertions (rows ``0..len``).

These functions are pure Python so they can be unit tested without torch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    import random
    from collections.abc import Sequence

UpdateType: TypeAlias = Literal["delete", "insert", "swap", "edge"]

_UPDATE_TYPES: tuple[UpdateType, ...] = ("delete", "insert", "swap", "edge")


def constrain_update_probabilities(
    *,
    probabilities: Sequence[float],
    n_tokens: int,
    min_tokens: int,
    max_tokens: int,
) -> dict[UpdateType, float]:
    """
    Zero out mutation probabilities that would violate the length bounds.

    At or below ``min_tokens`` only insertions are allowed (matching the
    reference behavior, which also disables swaps so a short attack must
    grow). At or above ``max_tokens`` insertions are disabled.

    Args:
        probabilities (Sequence[float]): Probabilities ordered
            ``(delete, insert, swap, edge)``.
        n_tokens (int): Current attack length in tokens.
        min_tokens (int): Minimum allowed attack length.
        max_tokens (int): Maximum allowed attack length.

    Returns:
        dict[UpdateType, float]: The constrained probabilities keyed by
            mutation type.

    Raises:
        ValueError: If every mutation is disabled by the bounds.
    """
    constrained = dict(zip(_UPDATE_TYPES, probabilities, strict=True))
    if n_tokens <= min_tokens:
        constrained["delete"] = 0.0
        constrained["swap"] = 0.0
    if n_tokens >= max_tokens:
        constrained["insert"] = 0.0
        constrained["edge"] = 0.0
    if n_tokens == 0:
        constrained["delete"] = 0.0
        constrained["swap"] = 0.0
    if sum(constrained.values()) <= 0:
        raise ValueError(
            "All token edits are disabled by the length bounds: "
            f"n_tokens={n_tokens}, min_tokens={min_tokens}, max_tokens={max_tokens}."
        )
    return constrained


def choose_update_type(*, rng: random.Random, probabilities: dict[UpdateType, float]) -> UpdateType:
    """
    Sample one mutation type according to the constrained probabilities.

    Args:
        rng (random.Random): Randomness source.
        probabilities (dict[UpdateType, float]): Constrained probabilities from
            ``constrain_update_probabilities``.

    Returns:
        UpdateType: The sampled mutation type.
    """
    return rng.choices(_UPDATE_TYPES, weights=[probabilities[t] for t in _UPDATE_TYPES], k=1)[0]


def propose_candidates(
    *,
    token_ids: Sequence[int],
    update_type: UpdateType,
    n_candidates: int,
    token_proposals: Sequence[Sequence[int]],
    rng: random.Random,
) -> list[list[int]]:
    """
    Produce ``n_candidates`` single-edit mutations of ``token_ids``.

    Args:
        token_ids (Sequence[int]): Current attack token ids.
        update_type (UpdateType): The mutation type sampled for this step.
        n_candidates (int): Maximum number of candidates to produce.
        token_proposals (Sequence[Sequence[int]]): Proposed token ids per
            position. Row ``i`` proposes replacements for position ``i`` and
            insertions before position ``i``; the final row proposes tokens to
            append at the end. Must have ``len(token_ids) + 1`` rows for
            insert/swap/edge updates. Unused for ``delete``.
        rng (random.Random): Randomness source.

    Returns:
        list[list[int]]: The mutated token id sequences.

    Raises:
        ValueError: If a mutation cannot be applied to ``token_ids``.
    """
    if update_type == "delete":
        return _propose_deletions(token_ids=token_ids, n_candidates=n_candidates, rng=rng)
    if len(token_proposals) != len(token_ids) + 1:
        raise ValueError(
            f"token_proposals must have len(token_ids) + 1 rows, got {len(token_proposals)} rows "
            f"for {len(token_ids)} tokens."
        )
    if update_type == "swap":
        return _propose_swaps(token_ids=token_ids, n_candidates=n_candidates, token_proposals=token_proposals, rng=rng)
    only_edges = update_type == "edge"
    return _propose_insertions(
        token_ids=token_ids,
        n_candidates=n_candidates,
        token_proposals=token_proposals,
        rng=rng,
        only_edges=only_edges,
    )


def _propose_deletions(*, token_ids: Sequence[int], n_candidates: int, rng: random.Random) -> list[list[int]]:
    if not token_ids:
        raise ValueError("Cannot delete a token from an empty attack.")
    positions = rng.sample(range(len(token_ids)), k=min(n_candidates, len(token_ids)))
    return [[t for i, t in enumerate(token_ids) if i != pos] for pos in positions]


def _propose_swaps(
    *,
    token_ids: Sequence[int],
    n_candidates: int,
    token_proposals: Sequence[Sequence[int]],
    rng: random.Random,
) -> list[list[int]]:
    if not token_ids:
        raise ValueError("Cannot swap a token in an empty attack.")
    candidates = []
    for _ in range(n_candidates):
        pos = rng.randrange(len(token_ids))
        proposal = token_proposals[pos][rng.randrange(len(token_proposals[pos]))]
        new_ids = list(token_ids)
        new_ids[pos] = proposal
        candidates.append(new_ids)
    return candidates


def _propose_insertions(
    *,
    token_ids: Sequence[int],
    n_candidates: int,
    token_proposals: Sequence[Sequence[int]],
    rng: random.Random,
    only_edges: bool,
) -> list[list[int]]:
    candidates = []
    for _ in range(n_candidates):
        pos = len(token_ids) if only_edges else rng.randrange(len(token_ids) + 1)
        proposal = token_proposals[pos][rng.randrange(len(token_proposals[pos]))]
        candidates.append([*token_ids[:pos], proposal, *token_ids[pos:]])
    return candidates


__all__ = [
    "UpdateType",
    "choose_update_type",
    "constrain_update_probabilities",
    "propose_candidates",
]
