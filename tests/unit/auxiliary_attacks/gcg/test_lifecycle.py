# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import subprocess
from unittest.mock import MagicMock, patch

import pytest

log_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.log",
    reason="GCG optional dependencies (mlflow, etc.) not installed",
)
log_gpu_memory = log_mod.log_gpu_memory
get_gpu_memory = log_mod.get_gpu_memory

train_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.train",
    reason="GCG train module not available",
)
Generator = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator


class TestGpuMemoryLogging:
    """Tests for GPU memory query and logging."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.sp")
    def test_get_gpu_memory_parses_nvidia_smi(self, mock_sp: MagicMock) -> None:
        """Should parse nvidia-smi output into a dict of GPU -> free memory."""
        mock_sp.check_output.return_value = b"memory.free [MiB]\n8000 MiB\n16000 MiB\n"
        result = get_gpu_memory()
        assert result == {"gpu1_free_memory": 8000, "gpu2_free_memory": 16000}

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.sp")
    def test_get_gpu_memory_single_gpu(self, mock_sp: MagicMock) -> None:
        """Should handle single GPU output."""
        mock_sp.check_output.return_value = b"memory.free [MiB]\n24000 MiB\n"
        result = get_gpu_memory()
        assert result == {"gpu1_free_memory": 24000}

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.mlflow")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.sp")
    def test_log_gpu_memory_logs_to_mlflow(self, mock_sp: MagicMock, mock_mlflow: MagicMock) -> None:
        """Should log each GPU's free memory as an MLflow metric."""
        mock_sp.check_output.return_value = b"memory.free [MiB]\n8000 MiB\n16000 MiB\n"
        log_gpu_memory(step=5)

        assert mock_mlflow.log_metric.call_count == 2
        calls = mock_mlflow.log_metric.call_args_list
        assert calls[0].args == ("gpu1_free_memory", 8000)
        assert calls[0].kwargs["step"] == 5
        assert calls[1].args == ("gpu2_free_memory", 16000)

    @patch("pyrit.auxiliary_attacks.gcg.experiments.log.sp")
    def test_get_gpu_memory_handles_nvidia_smi_failure(self, mock_sp: MagicMock) -> None:
        """Should propagate exception when nvidia-smi is not available."""
        mock_sp.check_output.side_effect = subprocess.CalledProcessError(1, "nvidia-smi")
        with pytest.raises(subprocess.CalledProcessError):
            get_gpu_memory()


class TestGenerateSuffixLifecycle:
    """Tests for generate_suffix MLflow and worker lifecycle management."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_workers")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_goals_and_targets")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_gpu_memory")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_params")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_train_goals")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.mlflow")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.attack_lib")
    def test_mlflow_run_started_before_training(
        self,
        mock_attack_lib: MagicMock,
        mock_mlflow: MagicMock,
        mock_log_train_goals: MagicMock,
        mock_log_params: MagicMock,
        mock_log_gpu_memory: MagicMock,
        mock_get_goals: MagicMock,
        mock_get_workers: MagicMock,
    ) -> None:
        """MLflow run should be started before any training begins."""
        mock_get_goals.return_value = (["goal1"], ["target1"], [], [])
        mock_worker = MagicMock()
        mock_worker.model.name_or_path = "test-model"
        mock_worker.tokenizer.name_or_path = "test-tokenizer"
        mock_worker.conv_template.name = "test-template"
        mock_get_workers.return_value = ([mock_worker], [])

        mock_attack_instance = MagicMock()
        mock_attack_lib.GCGAttackPrompt = MagicMock
        mock_attack_lib.GCGPromptManager = MagicMock
        mock_attack_lib.GCGMultiPromptAttack = MagicMock

        # Patch _create_attack to avoid IndividualPromptAttack's logfile writing
        with patch.object(Generator, "_create_attack", return_value=mock_attack_instance):
            generator = Generator.__new__(Generator)
            generator.generate_suffix(
                tokenizer_paths=["test/path"],
                model_paths=["test/path"],
                conversation_templates=["llama-2"],
                train_data="",
                n_steps=1,
            )

        mock_mlflow.start_run.assert_called_once()

    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_workers")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_goals_and_targets")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_gpu_memory")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_params")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_train_goals")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.mlflow")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.attack_lib")
    def test_workers_stopped_after_training(
        self,
        mock_attack_lib: MagicMock,
        mock_mlflow: MagicMock,
        mock_log_train_goals: MagicMock,
        mock_log_params: MagicMock,
        mock_log_gpu_memory: MagicMock,
        mock_get_goals: MagicMock,
        mock_get_workers: MagicMock,
    ) -> None:
        """All workers should be stopped after training completes."""
        mock_get_goals.return_value = (["goal1"], ["target1"], [], [])
        mock_worker1 = MagicMock()
        mock_worker1.model.name_or_path = "test-model-1"
        mock_worker1.tokenizer.name_or_path = "test-tokenizer-1"
        mock_worker1.conv_template.name = "test-template-1"
        mock_worker2 = MagicMock()
        mock_worker2.model.name_or_path = "test-model-2"
        mock_worker2.tokenizer.name_or_path = "test-tokenizer-2"
        mock_worker2.conv_template.name = "test-template-2"
        mock_get_workers.return_value = ([mock_worker1], [mock_worker2])

        mock_attack_instance = MagicMock()
        mock_attack_lib.GCGAttackPrompt = MagicMock
        mock_attack_lib.GCGPromptManager = MagicMock
        mock_attack_lib.GCGMultiPromptAttack = MagicMock

        with patch.object(Generator, "_create_attack", return_value=mock_attack_instance):
            generator = Generator.__new__(Generator)
            generator.generate_suffix(
                tokenizer_paths=["test/path"],
                model_paths=["test/path"],
                conversation_templates=["llama-2"],
                train_data="",
                n_steps=1,
            )

        mock_worker1.stop.assert_called_once()
        mock_worker2.stop.assert_called_once()

    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_workers")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_goals_and_targets")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_gpu_memory")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_params")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_train_goals")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.mlflow")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.attack_lib")
    def test_workers_not_stopped_on_training_failure(
        self,
        mock_attack_lib: MagicMock,
        mock_mlflow: MagicMock,
        mock_log_train_goals: MagicMock,
        mock_log_params: MagicMock,
        mock_log_gpu_memory: MagicMock,
        mock_get_goals: MagicMock,
        mock_get_workers: MagicMock,
    ) -> None:
        """BUG CHARACTERIZATION: Workers are NOT stopped when attack.run() raises.

        This documents the current (buggy) behavior — workers leak on failure.
        A future fix should ensure workers are cleaned up even on exceptions.
        """
        mock_get_goals.return_value = (["goal1"], ["target1"], [], [])
        mock_worker = MagicMock()
        mock_worker.model.name_or_path = "test-model"
        mock_worker.tokenizer.name_or_path = "test-tokenizer"
        mock_worker.conv_template.name = "test-template"
        mock_get_workers.return_value = ([mock_worker], [])

        mock_attack_instance = MagicMock()
        mock_attack_instance.run.side_effect = RuntimeError("Simulated failure")
        mock_attack_lib.GCGAttackPrompt = MagicMock
        mock_attack_lib.GCGPromptManager = MagicMock
        mock_attack_lib.GCGMultiPromptAttack = MagicMock

        with patch.object(Generator, "_create_attack", return_value=mock_attack_instance):
            generator = Generator.__new__(Generator)
            with pytest.raises(RuntimeError, match="Simulated failure"):
                generator.generate_suffix(
                    tokenizer_paths=["test/path"],
                    model_paths=["test/path"],
                    conversation_templates=["llama-2"],
                    train_data="",
                    n_steps=1,
                )

        # Workers are NOT stopped on failure — this is a bug we'll fix later
        mock_worker.stop.assert_not_called()
