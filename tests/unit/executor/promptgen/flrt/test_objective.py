# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the FLRT loss terms on hand-built tensors."""

from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch", reason="FLRT objective tests require torch")

from pyrit.executor.promptgen.flrt import (  # noqa: E402
    clamped_forcing_loss,
    fluency_loss,
    per_token_nll,
    repetition_loss,
    total_loss,
)


def _log_probs(rows: list[list[float]]) -> torch.Tensor:
    """Build a [T, V] log-probability tensor, padding unspecified entries with -20."""
    width = max(len(row) for row in rows)
    padded = [row + [-20.0] * (width - len(row)) for row in rows]
    return torch.tensor(padded)


class TestPerTokenNll:
    def test_exact_values(self) -> None:
        # Row t predicts token t + 1. Position 1 uses row 0, position 2 uses row 1.
        log_probs = _log_probs(
            [
                [math.log(0.1), math.log(0.2), math.log(0.4)],
                [math.log(0.5), math.log(0.3), math.log(0.2)],
                [math.log(0.1), math.log(0.8), math.log(0.1)],
            ]
        )
        token_ids = torch.tensor([0, 2, 1])
        positions = torch.tensor([1, 2])
        nll = per_token_nll(log_probs=log_probs, token_ids=token_ids, positions=positions)
        assert nll[0].item() == pytest.approx(-math.log(0.4), abs=1e-6)
        assert nll[1].item() == pytest.approx(-math.log(0.3), abs=1e-6)


class TestClampedForcingLoss:
    def test_clamps_solved_tokens_to_floor(self) -> None:
        log_probs = torch.full((3, 3), -20.0)
        # Position 1: nll 0.2 (below the floor). Position 2: nll 0.7 (above the floor).
        log_probs[0, 0] = -0.2
        log_probs[1, 1] = -0.7
        log_probs[2, 2] = -9.0
        token_ids = torch.tensor([9, 0, 1])
        target_positions = torch.tensor([1, 2])
        floor = -math.log(0.6)
        loss = clamped_forcing_loss(
            log_probs=log_probs, token_ids=token_ids, target_positions=target_positions, floor=floor
        )
        # Weights [1.5, 1.0] normalized to mean 1 -> [1.2, 0.8].
        expected = (floor * 1.2 + 0.7 * 0.8) / 2
        assert loss.item() == pytest.approx(expected, abs=1e-6)

    def test_first_token_weighted_one_point_five(self) -> None:
        log_probs = torch.full((4, 3), -20.0)
        log_probs[0, 0] = -1.0
        log_probs[1, 1] = -2.0
        log_probs[2, 2] = -4.0
        token_ids = torch.tensor([9, 0, 1, 2])
        target_positions = torch.tensor([1, 2, 3])
        loss = clamped_forcing_loss(log_probs=log_probs, token_ids=token_ids, target_positions=target_positions)
        # Weights [1.5, 1, 1] normalized to mean 1 -> each scaled by 3/3.5.
        expected = (1.0 * 4.5 + 2.0 * 3.0 + 4.0 * 3.0) / 10.5
        assert loss.item() == pytest.approx(expected, abs=1e-6)

    def test_single_token_target_unweighted(self) -> None:
        log_probs = torch.full((2, 3), -20.0)
        log_probs[0, 0] = -100.0
        token_ids = torch.tensor([9, 0])
        target_positions = torch.tensor([1])
        loss = clamped_forcing_loss(log_probs=log_probs, token_ids=token_ids, target_positions=target_positions)
        # One position: normalization makes the sole weight 1.0.
        assert loss.item() == pytest.approx(100.0, abs=1e-4)

    def test_all_target_positions_scored(self) -> None:
        log_probs = torch.full((4, 3), -20.0)
        log_probs[0, 0] = -1.0
        log_probs[1, 1] = -5.0
        log_probs[2, 2] = -9.0
        token_ids = torch.tensor([9, 0, 1, 2])
        target_positions = torch.tensor([1, 2, 3])
        loss = clamped_forcing_loss(log_probs=log_probs, token_ids=token_ids, target_positions=target_positions)
        # All three positions count: weights [1.5,1,1]*3/3.5 -> [9/7, 6/7, 6/7].
        expected = (1.0 * 9 + 5.0 * 6 + 9.0 * 6) / 21.0
        assert loss.item() == pytest.approx(expected, abs=1e-6)


class TestFluencyLoss:
    def test_mean_nll_exact(self) -> None:
        log_probs = torch.full((3, 3), -20.0)
        log_probs[0, 2] = -0.5
        log_probs[1, 1] = -1.5
        token_ids = torch.tensor([9, 2, 1])
        positions = torch.tensor([1, 2])
        loss = fluency_loss(log_probs=log_probs, token_ids=token_ids, positions=positions)
        assert loss.item() == pytest.approx((0.5 + 1.5) / 2, abs=1e-6)


class TestRepetitionLoss:
    def test_exact_value_with_exponent_1_5(self) -> None:
        # counts: token 1 x3 -> (3 - 1) ** 1.5; token 2 x2 -> (2 - 1) ** 1.5; token 3 x1 -> 0
        token_ids = torch.tensor([1, 1, 1, 2, 2, 3])
        loss = repetition_loss(token_ids=token_ids, exponent=1.5)
        expected = (2**1.5 + 1**1.5) / 6
        assert loss.item() == pytest.approx(expected, abs=1e-6)

    def test_exact_value_with_exponent_1(self) -> None:
        token_ids = torch.tensor([1, 1, 1, 2, 2, 3])
        loss = repetition_loss(token_ids=token_ids, exponent=1.0)
        assert loss.item() == pytest.approx((2 + 1) / 6, abs=1e-6)

    def test_no_repeats_is_zero(self) -> None:
        token_ids = torch.tensor([1, 2, 3, 4])
        assert repetition_loss(token_ids=token_ids, exponent=1.5).item() == 0.0

    def test_empty_is_zero(self) -> None:
        assert repetition_loss(token_ids=torch.tensor([], dtype=torch.long), exponent=1.5).item() == 0.0


class TestTotalLoss:
    def test_weighted_sum_exact(self) -> None:
        loss = total_loss(
            forcing=torch.tensor(1.0),
            fluency=torch.tensor(2.0),
            repetition=torch.tensor(3.0),
            fluency_weight=0.3,
            repetition_weight=0.54,
        )
        assert loss.item() == pytest.approx(1.0 + 0.3 * 2.0 + 0.54 * 3.0, abs=1e-6)
