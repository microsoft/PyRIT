# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

# Note: GPU-memory tests live in test_log.py since they only need the log
# module (stdlib imports). Anything that touches the train module needs
# the gcg extra installed (ml_collections, torch, etc.) so we skip the
# whole module when those imports fail.
log_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.log",
    reason="GCG optional dependencies (mlflow, etc.) not installed",
)

train_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.train",
    reason="GCG train module not available",
)
Generator = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator


class TestGenerateSuffixLifecycle:
    """Tests for generate_suffix worker lifecycle management."""

    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_workers")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.get_goals_and_targets")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_gpu_memory")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_params")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.log_train_goals")
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.attack_lib")
    def test_workers_stopped_after_training(
        self,
        mock_attack_lib: MagicMock,
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
    @patch("pyrit.auxiliary_attacks.gcg.experiments.train.attack_lib")
    def test_workers_not_stopped_on_training_failure(
        self,
        mock_attack_lib: MagicMock,
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
