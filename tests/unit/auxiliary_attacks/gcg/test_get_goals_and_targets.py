# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from unittest.mock import MagicMock

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
get_goals_and_targets = attack_manager_mod.get_goals_and_targets


class TestGetGoalsAndTargets:
    """Tests for the get_goals_and_targets function."""

    def test_returns_empty_lists_when_no_data(self) -> None:
        """Should return empty lists when no train_data is provided."""
        params = MagicMock()
        params.train_data = ""
        params.goals = []
        params.targets = []
        params.test_goals = []
        params.test_targets = []

        train_goals, train_targets, test_goals, test_targets = get_goals_and_targets(params)
        assert train_goals == []
        assert train_targets == []
        assert test_goals == []
        assert test_targets == []

    def test_loads_from_csv_file(self) -> None:
        """Should load goals and targets from a CSV file."""
        csv_content = "goal,target\ngoal1,target1\ngoal2,target2\ngoal3,target3\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            params = MagicMock()
            params.train_data = csv_path
            params.n_train_data = 2
            params.n_test_data = 0
            params.test_data = ""
            params.random_seed = 42
            params.test_goals = []
            params.test_targets = []

            train_goals, train_targets, test_goals, test_targets = get_goals_and_targets(params)
            assert len(train_goals) == 2
            assert len(train_targets) == 2
            assert test_goals == []
            assert test_targets == []
        finally:
            os.unlink(csv_path)

    def test_loads_test_data_from_train_data_split(self) -> None:
        """Should split training data for test when no separate test_data provided."""
        csv_content = "goal,target\ngoal1,target1\ngoal2,target2\ngoal3,target3\ngoal4,target4\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            params = MagicMock()
            params.train_data = csv_path
            params.n_train_data = 2
            params.n_test_data = 1
            params.test_data = ""
            params.random_seed = 42

            train_goals, train_targets, test_goals, test_targets = get_goals_and_targets(params)
            assert len(train_goals) == 2
            assert len(train_targets) == 2
            assert len(test_goals) == 1
            assert len(test_targets) == 1
        finally:
            os.unlink(csv_path)

    def test_raises_on_mismatched_lengths(self) -> None:
        """Should raise ValueError when goals and targets have different lengths."""
        params = MagicMock()
        params.train_data = ""
        params.goals = ["goal1", "goal2"]
        params.targets = ["target1"]
        params.test_goals = []
        params.test_targets = []

        with pytest.raises(ValueError, match="train_goals"):
            get_goals_and_targets(params)

    def test_csv_without_goal_column(self) -> None:
        """Should use empty strings for goals when CSV has no goal column."""
        csv_content = "target\ntarget1\ntarget2\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            params = MagicMock()
            params.train_data = csv_path
            params.n_train_data = 2
            params.n_test_data = 0
            params.test_data = ""
            params.random_seed = 42

            train_goals, train_targets, _, _ = get_goals_and_targets(params)
            assert len(train_goals) == 2
            assert all(g == "" for g in train_goals)
            assert len(train_targets) == 2
        finally:
            os.unlink(csv_path)
