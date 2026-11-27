# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the FLRT token edit operations."""

from __future__ import annotations

import random

import pytest

from pyrit.executor.promptgen.flrt import (
    choose_update_type,
    constrain_update_probabilities,
    propose_candidates,
)

_PROBABILITIES = (0.25, 0.25, 0.25, 0.25)


class TestConstrainUpdateProbabilities:
    def test_unconstrained_when_within_bounds(self) -> None:
        result = constrain_update_probabilities(probabilities=_PROBABILITIES, n_tokens=10, min_tokens=1, max_tokens=150)
        assert result == {"delete": 0.25, "insert": 0.25, "swap": 0.25, "edge": 0.25}

    def test_only_insertions_at_or_below_min_tokens(self) -> None:
        result = constrain_update_probabilities(probabilities=_PROBABILITIES, n_tokens=1, min_tokens=1, max_tokens=150)
        assert result["delete"] == 0.0
        assert result["swap"] == 0.0
        assert result["insert"] == 0.25
        assert result["edge"] == 0.25

    def test_no_insertions_at_or_above_max_tokens(self) -> None:
        result = constrain_update_probabilities(
            probabilities=_PROBABILITIES, n_tokens=150, min_tokens=1, max_tokens=150
        )
        assert result["insert"] == 0.0
        assert result["edge"] == 0.0
        assert result["delete"] == 0.25
        assert result["swap"] == 0.25

    def test_empty_attack_disables_delete_and_swap(self) -> None:
        result = constrain_update_probabilities(probabilities=_PROBABILITIES, n_tokens=0, min_tokens=0, max_tokens=150)
        assert result["delete"] == 0.0
        assert result["swap"] == 0.0
        assert result["insert"] == 0.25
        assert result["edge"] == 0.25

    def test_all_disabled_raises(self) -> None:
        with pytest.raises(ValueError, match="All token edits are disabled"):
            constrain_update_probabilities(probabilities=(0.5, 0.0, 0.5, 0.0), n_tokens=0, min_tokens=1, max_tokens=150)


class TestChooseUpdateType:
    def test_never_selects_zero_probability_type(self) -> None:
        rng = random.Random(0)
        constrained = {"delete": 0.0, "insert": 1.0, "swap": 0.0, "edge": 0.0}
        assert choose_update_type(rng=rng, probabilities=constrained) == "insert"

    def test_deterministic_given_seed(self) -> None:
        constrained = dict(zip(("delete", "insert", "swap", "edge"), _PROBABILITIES, strict=True))
        first = [choose_update_type(rng=random.Random(42), probabilities=constrained) for _ in range(5)]
        second = [choose_update_type(rng=random.Random(42), probabilities=constrained) for _ in range(5)]
        assert first == second

    def test_all_types_reachable(self) -> None:
        rng = random.Random(0)
        constrained = dict(zip(("delete", "insert", "swap", "edge"), _PROBABILITIES, strict=True))
        seen = {choose_update_type(rng=rng, probabilities=constrained) for _ in range(200)}
        assert seen == {"delete", "insert", "swap", "edge"}


class TestProposeDeletions:
    def test_deletes_one_token_per_candidate(self) -> None:
        rng = random.Random(0)
        candidates = propose_candidates(
            token_ids=[1, 2, 3], update_type="delete", n_candidates=2, token_proposals=[], rng=rng
        )
        assert len(candidates) == 2
        for candidate in candidates:
            assert len(candidate) == 2
            assert all(t in [1, 2, 3] for t in candidate)

    def test_delete_every_position_when_candidates_exceed_length(self) -> None:
        rng = random.Random(0)
        candidates = propose_candidates(
            token_ids=[1, 2], update_type="delete", n_candidates=5, token_proposals=[], rng=rng
        )
        assert sorted(tuple(c) for c in candidates) == [(1,), (2,)]

    def test_delete_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot delete"):
            propose_candidates(
                token_ids=[], update_type="delete", n_candidates=1, token_proposals=[], rng=random.Random(0)
            )


class TestProposeSwaps:
    def test_swaps_in_a_proposed_token(self) -> None:
        rng = random.Random(0)
        proposals = [[10], [11], [12], [13]]
        candidates = propose_candidates(
            token_ids=[1, 2, 3], update_type="swap", n_candidates=5, token_proposals=proposals, rng=rng
        )
        assert len(candidates) == 5
        for candidate in candidates:
            assert len(candidate) == 3
            differing = [i for i, (old, new) in enumerate(zip([1, 2, 3], candidate, strict=True)) if old != new]
            assert len(differing) == 1
            assert candidate[differing[0]] == proposals[differing[0]][0]

    def test_swap_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot swap"):
            propose_candidates(
                token_ids=[], update_type="swap", n_candidates=1, token_proposals=[[]], rng=random.Random(0)
            )

    def test_wrong_proposal_row_count_raises(self) -> None:
        with pytest.raises(ValueError, match="len\\(token_ids\\) \\+ 1 rows"):
            propose_candidates(
                token_ids=[1, 2], update_type="swap", n_candidates=1, token_proposals=[[9], [9]], rng=random.Random(0)
            )


class TestProposeInsertions:
    def test_inserts_one_token_per_candidate(self) -> None:
        rng = random.Random(0)
        proposals = [[10], [11], [12], [13]]
        candidates = propose_candidates(
            token_ids=[1, 2, 3], update_type="insert", n_candidates=5, token_proposals=proposals, rng=rng
        )
        assert len(candidates) == 5
        for candidate in candidates:
            assert len(candidate) == 4
            inserted = [t for t in candidate if t not in [1, 2, 3]]
            assert len(inserted) == 1
            assert inserted[0] in [10, 11, 12, 13]

    def test_insert_preserves_original_order(self) -> None:
        rng = random.Random(0)
        proposals = [[10], [11], [12], [13]]
        candidates = propose_candidates(
            token_ids=[1, 2, 3], update_type="insert", n_candidates=5, token_proposals=proposals, rng=rng
        )
        for candidate in candidates:
            remaining = [t for t in candidate if t in [1, 2, 3]]
            assert remaining == [1, 2, 3]

    def test_edge_only_appends_at_end(self) -> None:
        rng = random.Random(0)
        proposals = [[10], [11], [12], [13]]
        candidates = propose_candidates(
            token_ids=[1, 2, 3], update_type="edge", n_candidates=5, token_proposals=proposals, rng=rng
        )
        for candidate in candidates:
            assert candidate[:3] == [1, 2, 3]
            assert candidate[3] == 13

    def test_insert_into_empty_attack(self) -> None:
        rng = random.Random(0)
        candidates = propose_candidates(
            token_ids=[], update_type="insert", n_candidates=3, token_proposals=[[7]], rng=rng
        )
        assert candidates == [[7], [7], [7]]
