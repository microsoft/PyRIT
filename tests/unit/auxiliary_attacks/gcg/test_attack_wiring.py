# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests that exercise the full attack class wiring without mocking manager classes.

These tests catch kwarg mismatches between IndividualPromptAttack/ProgressiveMultiPromptAttack
and MultiPromptAttack.__init__(), and template compatibility issues in _update_ids().
"""

from unittest.mock import MagicMock, patch

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
torch = pytest.importorskip("torch", reason="torch not installed")

gcg_attack_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.gcg.gcg_attack",
    reason="GCG optional dependencies not installed",
)

IndividualPromptAttack = attack_manager_mod.IndividualPromptAttack
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack
MultiPromptAttack = attack_manager_mod.MultiPromptAttack
GCGAttackPrompt = gcg_attack_mod.GCGAttackPrompt
GCGPromptManager = gcg_attack_mod.GCGPromptManager
GCGMultiPromptAttack = gcg_attack_mod.GCGMultiPromptAttack

train_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.train",
    reason="GCG train module not available",
)
Generator = train_mod.GreedyCoordinateGradientAdversarialSuffixGenerator

MANAGERS = {
    "AP": GCGAttackPrompt,
    "PM": GCGPromptManager,
    "MPA": GCGMultiPromptAttack,
}


def _make_mock_worker() -> MagicMock:
    """Create a mock worker with required attributes for attack construction."""
    worker = MagicMock()
    worker.model.name_or_path = "test-model"
    worker.tokenizer.name_or_path = "test-tokenizer"
    worker.conv_template.name = "test-template"
    return worker


class TestAttackClassWiring:
    """Tests that verify attack classes can be constructed with real manager classes.

    These catch kwarg mismatches that mocked tests miss.
    """

    def test_individual_attack_creates_mpa_without_error(self) -> None:
        """IndividualPromptAttack.run() should create MultiPromptAttack without TypeError.

        This catches the mpa_kwargs bug where dead kwargs (deterministic, lr, etc.)
        were passed to MultiPromptAttack.__init__() which didn't accept them.
        """
        worker = _make_mock_worker()

        # Create IndividualPromptAttack with the real GCG manager classes
        attack = IndividualPromptAttack(
            goals=["test goal"],
            targets=["test target"],
            workers=[worker],
            control_init="! ! !",
            managers=MANAGERS,
            mpa_lr=0.01,
            mpa_batch_size=64,
            mpa_n_steps=5,
        )

        # The run() method creates MultiPromptAttack internally.
        # Patch the MPA's run() to avoid actually running the attack,
        # but let __init__ execute with real classes to catch kwarg issues.
        with patch.object(GCGMultiPromptAttack, "run", return_value=("control", 0.5, 1)):
            attack.run(
                n_steps=1,
                batch_size=64,
                topk=256,
                temp=1,
                allow_non_ascii=False,
                target_weight=1.0,
                control_weight=0.0,
                anneal=False,
                test_steps=1,
                incr_control=False,
                stop_on_success=False,
                verbose=False,
                filter_cand=True,
            )

    def test_progressive_attack_creates_mpa_without_error(self) -> None:
        """ProgressiveMultiPromptAttack.run() should create MultiPromptAttack without TypeError."""
        worker = _make_mock_worker()

        attack = ProgressiveMultiPromptAttack(
            goals=["test goal"],
            targets=["test target"],
            workers=[worker],
            progressive_goals=False,
            progressive_models=False,
            control_init="! ! !",
            managers=MANAGERS,
            mpa_lr=0.01,
            mpa_batch_size=64,
            mpa_n_steps=5,
        )

        with patch.object(GCGMultiPromptAttack, "run", return_value=("control", 0.5, 1)):
            attack.run(
                n_steps=1,
                batch_size=64,
                topk=256,
                temp=1,
                allow_non_ascii=False,
                target_weight=1.0,
                control_weight=0.0,
                anneal=False,
                test_steps=1,
                incr_control=False,
                stop_on_success=False,
                verbose=False,
                filter_cand=True,
            )

    def test_create_attack_individual_wires_correctly(self) -> None:
        """_create_attack with transfer=False should produce an IndividualPromptAttack
        that can create internal MPA instances without error."""
        worker = _make_mock_worker()

        params = Generator._build_params(
            transfer=False,
            control_init="! ! !",
            result_prefix="test",
            learning_rate=0.01,
            batch_size=64,
            n_steps=5,
        )

        attack = Generator._create_attack(
            params=params,
            managers=MANAGERS,
            train_goals=["test goal"],
            train_targets=["test target"],
            test_goals=[],
            test_targets=[],
            workers=[worker],
            test_workers=[],
        )

        assert isinstance(attack, IndividualPromptAttack)

        # Verify internal MPA creation works
        with patch.object(GCGMultiPromptAttack, "run", return_value=("control", 0.5, 1)):
            attack.run(
                n_steps=1,
                batch_size=64,
                topk=256,
                temp=1,
                allow_non_ascii=False,
                target_weight=1.0,
                control_weight=0.0,
                anneal=False,
                test_steps=1,
                incr_control=False,
                stop_on_success=False,
                verbose=False,
                filter_cand=True,
            )
