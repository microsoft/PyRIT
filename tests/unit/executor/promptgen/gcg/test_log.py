# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import subprocess
from unittest.mock import MagicMock, patch

import pytest

log_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.experiments.log",
    reason="GCG optional dependencies not installed",
)
get_gpu_memory = log_mod.get_gpu_memory
log_gpu_memory = log_mod.log_gpu_memory
log_loss = log_mod.log_loss
log_params = log_mod.log_params
log_table_summary = log_mod.log_table_summary
log_train_goals = log_mod.log_train_goals


class TestLogParams:
    """Tests for the log_params function."""

    def test_logs_default_param_keys(self) -> None:
        """Should extract default parameter keys without error."""
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

        # Should not raise
        log_params(params=params)

    def test_logs_custom_param_keys(self) -> None:
        """Should accept custom parameter keys."""
        params = MagicMock()
        params.to_dict.return_value = {
            "model_name": "test_model",
            "batch_size": 256,
        }

        # Should not raise
        log_params(params=params, param_keys=["model_name", "batch_size"])


class TestLogTrainGoals:
    """Tests for the log_train_goals function."""

    def test_logs_goals(self) -> None:
        """Should log training goals without error."""
        log_train_goals(train_goals=["goal1", "goal2", "goal3"])

    def test_logs_empty_goals(self) -> None:
        """Should handle empty goals list."""
        log_train_goals(train_goals=[])


class TestLogLoss:
    """Tests for the log_loss function."""

    def test_logs_loss(self) -> None:
        """Should log loss without error."""
        log_loss(step=5, loss=0.123)


class TestLogTableSummary:
    """Tests for the log_table_summary function."""

    def test_logs_table_summary(self) -> None:
        """Should log summary without error."""
        log_table_summary(losses=[0.5, 0.3, 0.1], controls=["ctrl1", "ctrl2", "ctrl3"], n_steps=3)

    def test_logs_empty_summary(self) -> None:
        """Should handle empty losses and controls."""
        log_table_summary(losses=[], controls=[], n_steps=0)


class TestGpuMemoryLogging:
    """Tests for GPU memory query and logging.

    Lives here (not test_lifecycle.py) so the tests don't transitively
    depend on the GCG `train` module (which requires `torch`, `accelerate`,
    only installed with the `gcg` extra). The log module itself only
    uses stdlib imports, so these tests run in any CI environment.
    """

    @patch("pyrit.executor.promptgen.gcg.experiments.log.sp")
    def test_get_gpu_memory_parses_nvidia_smi(self, mock_sp: MagicMock) -> None:
        """Should parse nvidia-smi output into a dict of GPU -> free memory."""
        mock_sp.check_output.return_value = b"memory.free [MiB]\n8000 MiB\n16000 MiB\n"
        result = get_gpu_memory()
        assert result == {"gpu1_free_memory": 8000, "gpu2_free_memory": 16000}

    @patch("pyrit.executor.promptgen.gcg.experiments.log.sp")
    def test_get_gpu_memory_single_gpu(self, mock_sp: MagicMock) -> None:
        """Should handle single GPU output."""
        mock_sp.check_output.return_value = b"memory.free [MiB]\n24000 MiB\n"
        result = get_gpu_memory()
        assert result == {"gpu1_free_memory": 24000}

    @patch("pyrit.executor.promptgen.gcg.experiments.log.sp")
    def test_log_gpu_memory_logs_via_logging(self, mock_sp: MagicMock) -> None:
        """Should log GPU memory info without error on the success path."""
        mock_sp.check_output.return_value = b"memory.free [MiB]\n8000 MiB\n16000 MiB\n"
        # Should not raise
        log_gpu_memory(step=5)

    @patch("pyrit.executor.promptgen.gcg.experiments.log.sp")
    def test_log_gpu_memory_swallows_nvidia_smi_failure(self, mock_sp: MagicMock) -> None:
        """Should swallow exceptions when nvidia-smi is not available.

        Covers the except branch of `log_gpu_memory` -- callers (like the
        train loop) should never crash because the runtime happens not to
        have nvidia-smi.
        """
        mock_sp.check_output.side_effect = subprocess.CalledProcessError(1, "nvidia-smi")
        # Must not raise
        log_gpu_memory(step=5)

    @patch("pyrit.executor.promptgen.gcg.experiments.log.sp")
    def test_get_gpu_memory_handles_nvidia_smi_failure(self, mock_sp: MagicMock) -> None:
        """`get_gpu_memory` itself should propagate the exception (only
        `log_gpu_memory` is expected to swallow it)."""
        mock_sp.check_output.side_effect = subprocess.CalledProcessError(1, "nvidia-smi")
        with pytest.raises(subprocess.CalledProcessError):
            get_gpu_memory()
