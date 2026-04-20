# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

log_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.log",
    reason="GCG optional dependencies (mlflow, etc.) not installed",
)
log_loss = log_mod.log_loss
log_params = log_mod.log_params
log_table_summary = log_mod.log_table_summary
log_train_goals = log_mod.log_train_goals


class TestLogParams:
    """Tests for the log_params function."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_default_param_keys(self, mock_mlflow: MagicMock) -> None:
        """Should log the default parameter keys to MLflow."""
        params = MagicMock()
        params.to_dict.return_value = {
            "model_name": "test_model",
            "transfer": False,
            "n_train_data": 50,
            "n_test_data": 10,
            "n_steps": 100,
            "batch_size": 512,
            "extra_param": "ignored",
        }

        log_params(params=params)

        mock_mlflow.log_params.assert_called_once()
        logged_params = mock_mlflow.log_params.call_args[0][0]
        assert logged_params == {
            "model_name": "test_model",
            "transfer": False,
            "n_train_data": 50,
            "n_test_data": 10,
            "n_steps": 100,
            "batch_size": 512,
        }

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_custom_param_keys(self, mock_mlflow: MagicMock) -> None:
        """Should log only the specified parameter keys."""
        params = MagicMock()
        params.to_dict.return_value = {
            "model_name": "test_model",
            "batch_size": 256,
        }

        log_params(params=params, param_keys=["model_name", "batch_size"])

        logged_params = mock_mlflow.log_params.call_args[0][0]
        assert logged_params == {"model_name": "test_model", "batch_size": 256}


class TestLogTrainGoals:
    """Tests for the log_train_goals function."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_goals_as_text(self, mock_mlflow: MagicMock) -> None:
        """Should log training goals joined by newlines."""
        goals = ["goal1", "goal2", "goal3"]

        log_train_goals(train_goals=goals)

        mock_mlflow.log_text.assert_called_once()
        logged_text = mock_mlflow.log_text.call_args[0][0]
        assert logged_text == "goal1\ngoal2\ngoal3"

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_empty_goals(self, mock_mlflow: MagicMock) -> None:
        """Should handle empty goals list."""
        log_train_goals(train_goals=[])

        mock_mlflow.log_text.assert_called_once()
        logged_text = mock_mlflow.log_text.call_args[0][0]
        assert logged_text == ""


class TestLogLoss:
    """Tests for the log_loss function."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_loss_metric(self, mock_mlflow: MagicMock) -> None:
        """Should log loss as an MLflow metric."""
        log_loss(step=5, loss=0.123)

        mock_mlflow.log_metric.assert_called_once_with("loss", 0.123, step=5, synchronous=False)

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_loss_synchronously(self, mock_mlflow: MagicMock) -> None:
        """Should support synchronous logging."""
        log_loss(step=1, loss=0.5, synchronous=True)

        mock_mlflow.log_metric.assert_called_once_with("loss", 0.5, step=1, synchronous=True)


class TestLogTableSummary:
    """Tests for the log_table_summary function."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    def test_logs_table_with_correct_data(self, mock_mlflow: MagicMock) -> None:
        """Should log a table with step numbers, losses, and controls."""
        losses = [0.5, 0.3, 0.1]
        controls = ["ctrl1", "ctrl2", "ctrl3"]

        log_table_summary(losses=losses, controls=controls, n_steps=3)

        mock_mlflow.log_table.assert_called_once()
        logged_data = mock_mlflow.log_table.call_args[0][0]
        assert logged_data["step"] == [1, 2, 3]
        assert logged_data["loss"] == [0.5, 0.3, 0.1]
        assert logged_data["control"] == ["ctrl1", "ctrl2", "ctrl3"]
