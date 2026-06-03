# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for :class:`GCGGenerator` lifecycle, identity, and validation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from pyrit.auxiliary_attacks.gcg.config import (
    GCGAlgorithmConfig,
    GCGModelConfig,
    GCGOutputConfig,
    GCGStrategyConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

generator_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.generator",
    reason="GCG optional dependencies (torch, transformers, etc.) not installed",
)
GCGGenerator = generator_mod.GCGGenerator
GCGContext = generator_mod.GCGContext
GCGResult = generator_mod.GCGResult


_LLAMA_2 = "meta-llama/Llama-2-7b-chat-hf"


def _make_generator(*, output_dir: Path | None = None, **algorithm_overrides) -> GCGGenerator:
    output = GCGOutputConfig(result_prefix=str(output_dir / "gcg") if output_dir else "")
    return GCGGenerator(
        models=[GCGModelConfig(name=_LLAMA_2)],
        algorithm=GCGAlgorithmConfig(**algorithm_overrides) if algorithm_overrides else None,
        output=output,
    )


class TestGCGGeneratorInit:
    def test_empty_models_raises(self) -> None:
        with pytest.raises(ValueError, match="must contain at least one"):
            GCGGenerator(models=[])

    def test_minimal_init_uses_dataclass_defaults(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        assert gen._algorithm.n_steps == 500
        assert gen._strategy.transfer is False
        assert gen._output.verbose is True

    def test_test_models_default_empty(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        assert gen._test_models == []

    def test_init_does_not_touch_global_multiprocessing_state(self) -> None:
        """Regression: __init__ used to call torch.multiprocessing.set_start_method,
        which crashed under coverage runs when an earlier test had already pinned a
        non-spawn context. Worker spawn config now happens in _setup_async."""
        import torch.multiprocessing as mp

        with patch.object(mp, "set_start_method") as mock_set:
            GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        mock_set.assert_not_called()


class TestEnsureSpawnStartMethod:
    """Tests for the lazily-applied spawn-method guard used before workers are spawned."""

    def test_sets_spawn_when_unset(self) -> None:
        import torch.multiprocessing as mp

        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with (
            patch.object(mp, "get_start_method", return_value=None) as mock_get,
            patch.object(mp, "set_start_method") as mock_set,
        ):
            gen._ensure_spawn_start_method()
        mock_get.assert_called_once_with(allow_none=True)
        mock_set.assert_called_once_with("spawn")

    def test_noop_when_already_spawn(self) -> None:
        import torch.multiprocessing as mp

        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with (
            patch.object(mp, "get_start_method", return_value="spawn"),
            patch.object(mp, "set_start_method") as mock_set,
        ):
            gen._ensure_spawn_start_method()
        mock_set.assert_not_called()

    def test_warns_and_does_not_crash_when_already_other(self, caplog) -> None:
        """Used to raise 'context has already been set' — now we warn and continue."""
        import logging

        import torch.multiprocessing as mp

        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with (
            patch.object(mp, "get_start_method", return_value="fork"),
            patch.object(mp, "set_start_method") as mock_set,
            caplog.at_level(logging.WARNING, logger=generator_mod.logger.name),
        ):
            gen._ensure_spawn_start_method()
        mock_set.assert_not_called()
        assert any("fork" in r.message and "spawn" in r.message for r in caplog.records)


class TestBuildIdentifier:
    def test_identifier_exposes_models_and_hyperparams(self) -> None:
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(n_steps=42, batch_size=64, topk=128),
            strategy=GCGStrategyConfig(transfer=True, progressive_goals=True),
        )
        ident = gen._build_identifier()
        assert ident.params["models"] == [_LLAMA_2]
        assert ident.params["test_models"] == []
        assert ident.params["n_steps"] == 42
        assert ident.params["batch_size"] == 64
        assert ident.params["topk"] == 128
        assert ident.params["transfer"] is True
        assert ident.params["progressive_goals"] is True


class TestValidateContext:
    def test_empty_goals_raises(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with pytest.raises(ValueError, match="goals must be non-empty"):
            gen._validate_context(context=GCGContext(goals=[], targets=["t"]))

    def test_empty_targets_raises(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with pytest.raises(ValueError, match="targets must be non-empty"):
            gen._validate_context(context=GCGContext(goals=["g"], targets=[]))

    def test_train_length_mismatch_raises(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with pytest.raises(ValueError, match="goals/targets length mismatch"):
            gen._validate_context(context=GCGContext(goals=["g1", "g2"], targets=["t1"]))

    def test_test_length_mismatch_raises(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with pytest.raises(ValueError, match="test_goals/test_targets length mismatch"):
            gen._validate_context(
                context=GCGContext(
                    goals=["g"],
                    targets=["t"],
                    test_goals=["tg1", "tg2"],
                    test_targets=["tt1"],
                )
            )


@pytest.fixture
def patched_get_workers():
    """Patch the heavy worker spawn so lifecycle tests don't try to load real models."""
    with patch.object(generator_mod, "get_workers") as mock:
        yield mock


class TestExecuteAsyncLifecycle:
    """End-to-end tests of execute_async via the strategy lifecycle."""

    async def test_workers_stopped_after_successful_run(self, tmp_path: Path, patched_get_workers: MagicMock) -> None:
        worker1 = MagicMock(name="worker1")
        worker2 = MagicMock(name="worker2_test")
        patched_get_workers.return_value = ([worker1], [worker2])

        gen = _make_generator(output_dir=tmp_path)
        with (
            patch.object(gen, "_create_attack") as mock_create,
            patch.object(gen, "_read_result") as mock_read,
        ):
            mock_attack = MagicMock()
            mock_create.return_value = mock_attack
            mock_read.return_value = GCGResult(final_suffix="abc", final_loss=0.5, step_count=1)

            result = await gen.execute_async(goals=["g"], targets=["t"])

        assert result.final_suffix == "abc"
        worker1.stop.assert_called_once()
        worker2.stop.assert_called_once()
        mock_attack.run.assert_called_once()

    async def test_workers_stopped_when_attack_run_raises(self, tmp_path: Path, patched_get_workers: MagicMock) -> None:
        """The previously-known leak-on-failure bug: now fixed via _teardown_async."""
        worker = MagicMock(name="worker_leaked_before_fix")
        patched_get_workers.return_value = ([worker], [])

        gen = _make_generator(output_dir=tmp_path)
        with patch.object(gen, "_create_attack") as mock_create:
            mock_attack = MagicMock()
            mock_attack.run.side_effect = RuntimeError("simulated failure")
            mock_create.return_value = mock_attack

            with pytest.raises(Exception, match="simulated failure"):
                await gen.execute_async(goals=["g"], targets=["t"])

        worker.stop.assert_called_once()

    async def test_workers_stopped_when_setup_partially_succeeds(
        self, tmp_path: Path, patched_get_workers: MagicMock
    ) -> None:
        """Even if get_workers returns partial state and something else fails, teardown stops what we have."""
        worker = MagicMock(name="worker")
        patched_get_workers.return_value = ([worker], [])

        gen = _make_generator(output_dir=tmp_path)
        with patch.object(gen, "_create_attack", side_effect=RuntimeError("create failed")):
            with pytest.raises(Exception, match="create failed"):
                await gen.execute_async(goals=["g"], targets=["t"])

        worker.stop.assert_called_once()


class TestApplyTargetAugmentation:
    def test_returns_same_length_lists(self) -> None:
        train, test = GCGGenerator._apply_target_augmentation(
            train_targets=["Sure, here is a bomb"],
            test_targets=["Sure, here is a virus", "Sure, here is a weapon"],
        )
        assert len(train) == 1
        assert len(test) == 2

    def test_augmentation_modifies_at_least_some_targets(self) -> None:
        import numpy as np

        np.random.seed(42)
        targets = ["Sure, here is how to do it"] * 100
        result, _ = GCGGenerator._apply_target_augmentation(train_targets=targets, test_targets=[])
        num_changed = sum(1 for orig, aug in zip(targets, result, strict=False) if orig != aug)
        assert num_changed > 0


class TestReadResult:
    def test_reads_final_suffix_and_loss(self, tmp_path: Path) -> None:
        log_path = tmp_path / "result.json"
        log_path.write_text(
            json.dumps(
                {
                    "controls": ["! ! !", "a b !", "a b c"],
                    "losses": [1.0, 0.7, 0.3],
                }
            )
        )
        result = GCGGenerator._read_result(logfile_path=str(log_path), memory_labels={"k": "v"})
        assert result.final_suffix == "a b c"
        assert result.final_loss == 0.3
        assert result.step_count == 3
        assert result.loss_history == [1.0, 0.7, 0.3]
        assert result.control_history == ["! ! !", "a b !", "a b c"]
        assert result.memory_labels == {"k": "v"}
        assert result.log_path == str(log_path)

    def test_missing_file_returns_empty_result(self, tmp_path: Path) -> None:
        result = GCGGenerator._read_result(logfile_path=str(tmp_path / "does-not-exist.json"), memory_labels={})
        assert result.final_suffix == ""
        assert result.step_count == 0
        assert result.log_path is None

    def test_empty_controls_returns_nan_loss(self, tmp_path: Path) -> None:
        import math

        log_path = tmp_path / "empty.json"
        log_path.write_text(json.dumps({"controls": [], "losses": []}))
        result = GCGGenerator._read_result(logfile_path=str(log_path), memory_labels={})
        assert result.final_suffix == ""
        assert math.isnan(result.final_loss)
