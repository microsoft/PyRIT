# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import numpy as np
import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
IndividualPromptAttack = attack_manager_mod.IndividualPromptAttack
MultiPromptAttack = attack_manager_mod.MultiPromptAttack
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack


class TestFilterMpaKwargs:
    """Tests for the filter_mpa_kwargs static method."""

    def test_filters_mpa_prefixed_kwargs(self) -> None:
        """Should extract kwargs starting with 'mpa_' and strip the prefix."""
        result = ProgressiveMultiPromptAttack.filter_mpa_kwargs(
            mpa_batch_size=512,
            mpa_lr=0.01,
            other_param="ignored",
        )
        assert result == {"batch_size": 512, "lr": 0.01}

    def test_returns_empty_dict_when_no_mpa_kwargs(self) -> None:
        """Should return empty dict when no mpa_ prefixed kwargs are present."""
        result = ProgressiveMultiPromptAttack.filter_mpa_kwargs(
            batch_size=512,
            lr=0.01,
        )
        assert result == {}

    def test_individual_filter_matches_progressive(self) -> None:
        """IndividualPromptAttack.filter_mpa_kwargs should behave the same."""
        result = IndividualPromptAttack.filter_mpa_kwargs(
            mpa_n_steps=100,
            mpa_deterministic=True,
        )
        assert result == {"n_steps": 100, "deterministic": True}


class TestMultiPromptAttackParseResults:
    """Tests for MultiPromptAttack.parse_results method."""

    def _create_minimal_attack(
        self,
        *,
        n_train_workers: int,
        n_train_goals: int,
    ) -> MultiPromptAttack:
        """Create a MultiPromptAttack with minimal mock state for parse_results testing."""
        attack = object.__new__(MultiPromptAttack)
        # parse_results only uses len(self.workers) and len(self.goals)
        attack.workers = [None] * n_train_workers
        attack.goals = [""] * n_train_goals
        return attack

    def test_parse_results_basic(self) -> None:
        """Should correctly partition results into in-distribution/out-of-distribution quadrants."""
        attack = self._create_minimal_attack(n_train_workers=2, n_train_goals=2)

        # 4 workers (2 train + 2 test), 4 goals (2 train + 2 test)
        results = np.array(
            [
                [1, 0, 1, 1],  # train worker 1
                [0, 1, 0, 1],  # train worker 2
                [1, 1, 0, 0],  # test worker 1
                [0, 0, 1, 1],  # test worker 2
            ]
        )

        id_id, id_od, od_id, od_od = attack.parse_results(results)
        # id_id: train workers x train goals = results[:2, :2].sum() = 1+0+0+1 = 2
        assert id_id == 2
        # id_od: train workers x test goals = results[:2, 2:].sum() = 1+1+0+1 = 3
        assert id_od == 3
        # od_id: test workers x train goals = results[2:, :2].sum() = 1+1+0+0 = 2
        assert od_id == 2
        # od_od: test workers x test goals = results[2:, 2:].sum() = 0+0+1+1 = 2
        assert od_od == 2

    def test_parse_results_all_zeros(self) -> None:
        """Should handle all-zero results."""
        attack = self._create_minimal_attack(n_train_workers=1, n_train_goals=1)
        results = np.zeros((2, 2))

        id_id, id_od, od_id, od_od = attack.parse_results(results)
        assert id_id == 0
        assert id_od == 0
        assert od_id == 0
        assert od_od == 0
