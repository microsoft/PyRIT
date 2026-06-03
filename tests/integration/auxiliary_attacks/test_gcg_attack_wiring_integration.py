# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Integration tests for GCG attack-class wiring.

These tests construct real ``IndividualPromptAttack`` / ``ProgressiveMultiPromptAttack``
instances and exercise their wiring all the way down through
``GCGAttackPrompt._update_ids``, which calls ``tokenizer.apply_chat_template`` and
walks character positions via ``char_to_token``. They live here (not under
``tests/unit/``) because they require a real HuggingFace tokenizer (gpt2) to
back the chat-template pipeline — mocking that out would defeat the test's
purpose, which is to catch kwarg-mismatch and template-compatibility bugs that
mocked tests miss.

Network dependency: the gpt2 tokenizer is fetched from HuggingFace on first
use (and then cached). This is why these tests run only in the integration tier
(``make integration-test``), not in the PR-time unit-test matrix.

Requires: torch, transformers (GCG optional deps).
Skipped via importorskip when deps are not installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
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

generator_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.generator",
    reason="GCG optional dependencies (torch, transformers, etc.) not installed",
)

from pyrit.auxiliary_attacks.gcg.config import (  # noqa: E402
    GCGAlgorithmConfig,
    GCGModelConfig,
    GCGOutputConfig,
    GCGStrategyConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

IndividualPromptAttack = attack_manager_mod.IndividualPromptAttack
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack
MultiPromptAttack = attack_manager_mod.MultiPromptAttack
GCGAttackPrompt = gcg_attack_mod.GCGAttackPrompt
GCGPromptManager = gcg_attack_mod.GCGPromptManager
GCGMultiPromptAttack = gcg_attack_mod.GCGMultiPromptAttack
GCGGenerator = generator_mod.GCGGenerator
GCGContext = generator_mod.GCGContext

MANAGERS = {
    "AP": GCGAttackPrompt,
    "PM": GCGPromptManager,
    "MPA": GCGMultiPromptAttack,
}

_LLAMA_2 = "meta-llama/Llama-2-7b-chat-hf"


def _make_mock_worker_with_real_tokenizer() -> MagicMock:
    """Worker mock backed by a real gpt2 tokenizer.

    The wiring tests construct real ``GCGAttackPrompt`` instances which call
    ``tokenizer.apply_chat_template`` and then walk character positions in the
    rendered prompt. We need a real string + a tokenizer that can answer
    ``char_to_token`` queries on it, so we back the mock with the smallest
    workable real HF tokenizer (gpt2) plus an explicit llama-2-style chat
    template (gpt2 ships without one).
    """
    from transformers import AutoTokenizer

    real_tokenizer = AutoTokenizer.from_pretrained("gpt2")
    real_tokenizer.pad_token = real_tokenizer.eos_token
    real_tokenizer.chat_template = (
        "{%- for m in messages -%}"
        "{%- if m['role'] == 'user' -%}"
        "[INST] {{ m['content'] }} [/INST] "
        "{%- elif m['role'] == 'assistant' -%}"
        "{{ m['content'] }}"
        "{%- endif -%}"
        "{%- endfor -%}"
    )

    worker = MagicMock()
    worker.model.name_or_path = "test-model"
    worker.tokenizer = real_tokenizer
    return worker


class TestAttackClassWiring:
    """Verify attack classes can be constructed and run with real manager classes.

    Catches kwarg mismatches that mocked tests miss.
    """

    def test_individual_attack_creates_mpa_without_error(self) -> None:
        """IndividualPromptAttack.run() should create MultiPromptAttack without TypeError.

        This catches the mpa_kwargs bug where dead kwargs (deterministic, lr, etc.)
        were passed to MultiPromptAttack.__init__() which didn't accept them.
        """
        worker = _make_mock_worker_with_real_tokenizer()

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
        worker = _make_mock_worker_with_real_tokenizer()

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


class TestCreateAttackWiring:
    """Construct real attack classes via :meth:`GCGGenerator._create_attack` to catch kwarg mismatches."""

    def test_transfer_false_returns_individual(self, tmp_path: Path) -> None:
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(n_steps=5, batch_size=64, control_init="! ! !"),
            output=GCGOutputConfig(result_prefix=str(tmp_path / "gcg")),
        )
        worker = _make_mock_worker_with_real_tokenizer()
        context = GCGContext(goals=["g"], targets=["t"])
        params = gen._to_attack_params(context=context)

        attack = gen._create_attack(
            params=params,
            managers=MANAGERS,
            train_goals=["g"],
            train_targets=["t"],
            test_goals=[],
            test_targets=[],
            workers=[worker],
            test_workers=[],
            logfile_path=str(tmp_path / "log.json"),
        )
        assert isinstance(attack, IndividualPromptAttack)

    def test_transfer_true_returns_progressive(self, tmp_path: Path) -> None:
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(n_steps=5, batch_size=64, control_init="! ! !"),
            strategy=GCGStrategyConfig(transfer=True, progressive_goals=True, progressive_models=True),
            output=GCGOutputConfig(result_prefix=str(tmp_path / "gcg")),
        )
        worker = _make_mock_worker_with_real_tokenizer()
        context = GCGContext(goals=["g"], targets=["t"])
        params = gen._to_attack_params(context=context)

        attack = gen._create_attack(
            params=params,
            managers=MANAGERS,
            train_goals=["g"],
            train_targets=["t"],
            test_goals=[],
            test_targets=[],
            workers=[worker],
            test_workers=[],
            logfile_path=str(tmp_path / "log.json"),
        )
        assert isinstance(attack, ProgressiveMultiPromptAttack)
