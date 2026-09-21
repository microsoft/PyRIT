# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for :class:`GCGGenerator` lifecycle, identity, and validation."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.promptgen.gcg.config import (
    GCGAlgorithmConfig,
    GCGModelConfig,
    GCGOutputConfig,
    GCGStrategyConfig,
)

generator_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.generator",
    reason="GCG optional dependencies (torch, transformers, etc.) not installed",
)
GCGGenerator = generator_mod.GCGGenerator
GCGContext = generator_mod.GCGContext
GCGResult = generator_mod.GCGResult

from unit.executor.promptgen.gcg.trajectory_stubs import (  # noqa: E402
    TrajectoryPromptManager,
    TrajectoryWorker,
)

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
        import torch.multiprocessing as mp  # type: ignore[ty:unresolved-import]

        with patch.object(mp, "set_start_method") as mock_set:
            GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        mock_set.assert_not_called()


class TestEnsureSpawnStartMethod:
    """Tests for the lazily-applied spawn-method guard used before workers are spawned."""

    def test_sets_spawn_when_unset(self) -> None:
        import torch.multiprocessing as mp  # type: ignore[ty:unresolved-import]

        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])
        with (
            patch.object(mp, "get_start_method", return_value=None) as mock_get,
            patch.object(mp, "set_start_method") as mock_set,
        ):
            gen._ensure_spawn_start_method()
        mock_get.assert_called_once_with(allow_none=True)
        mock_set.assert_called_once_with("spawn")

    def test_noop_when_already_spawn(self) -> None:
        import torch.multiprocessing as mp  # type: ignore[ty:unresolved-import]

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

        import torch.multiprocessing as mp  # type: ignore[ty:unresolved-import]

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


class TestToAttackParamsToken:
    """The HuggingFace token forwarded to model loading."""

    def test_token_is_none_when_not_supplied(self) -> None:
        # An empty string makes huggingface_hub send "Authorization: Bearer " with no
        # credential, which httpx rejects as an illegal header value. None means anonymous,
        # which is what loading a public model needs.
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)])

        params = gen._to_attack_params(context=GCGContext(goals=["g"], targets=["t"]))

        assert params.token is None

    def test_token_is_forwarded_when_supplied(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)], hf_token="hf_example")

        params = gen._to_attack_params(context=GCGContext(goals=["g"], targets=["t"]))

        assert params.token == "hf_example"


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

        targets = ["Sure, here is how to do it"] * 100
        result, _ = GCGGenerator._apply_target_augmentation(
            train_targets=targets, test_targets=[], np_rng=np.random.default_rng(42)
        )
        num_changed = sum(1 for orig, aug in zip(targets, result, strict=False) if orig != aug)
        assert num_changed > 0


class TestExtensionWiring:
    def test_create_attack_uses_suffix_initializer_when_configured(self) -> None:
        class _SuffixInitStub:
            def __init__(self) -> None:
                self.calls: list[object] = []

            def make_initial_suffix(self, *, tokenizer: object) -> str:
                self.calls.append(tokenizer)
                return "initialized suffix"

        suffix_init = _SuffixInitStub()
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(suffix_init=suffix_init),
        )
        worker = MagicMock()
        worker.tokenizer = MagicMock()

        with patch.object(generator_mod, "IndividualPromptAttack") as mock_individual:
            gen._create_attack(
                params=MagicMock(),
                managers={"MPA": MagicMock()},
                train_goals=["g"],
                train_targets=["t"],
                test_goals=[],
                test_targets=[],
                workers=[worker],
                test_workers=[],
                logfile_path="out.json",
            )

        assert suffix_init.calls == [worker.tokenizer]
        assert mock_individual.call_args.kwargs["control_init"] == "initialized suffix"

    def test_resolve_control_init_returns_default_when_suffix_init_not_configured(self) -> None:
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(control_init="seed control"),
        )

        assert gen._resolve_control_init(workers=[]) == "seed control"

    def test_resolve_control_init_raises_when_suffix_init_requires_workers(self) -> None:
        """Test _resolve_control_init raises ValueError when suffix_init configured but no workers."""

        class _SuffixInitStub:
            def make_initial_suffix(self, *, tokenizer: object) -> str:
                return "initialized suffix"

        suffix_init = _SuffixInitStub()
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(suffix_init=suffix_init),
        )

        with pytest.raises(ValueError, match="Cannot resolve suffix_init without at least one worker"):
            gen._resolve_control_init(workers=[])

    async def test_perform_async_binds_algorithm_extensions_into_mpa_factory(self, tmp_path: Path) -> None:
        class _SamplingStub:
            def sample_candidates(
                self,
                *,
                gradient: Any,
                control_tokens: Any,
                batch_size: int,
                top_k: int,
                temperature: float,
                allow_non_ascii: bool,
                non_ascii_tokens: Any,
            ) -> Any:
                return control_tokens

        class _LossStub:
            def compute_loss(
                self,
                *,
                logits: Any,
                token_ids: Any,
                target_slice: slice,
                control_slice: slice,
            ) -> Any:
                return logits

        class _FilterStub:
            def filter_candidates(
                self,
                *,
                candidate_tokens: Any,
                tokenizer: Any,
                current_control: str,
            ) -> list[str]:
                return [current_control]

        sampling = _SamplingStub()
        loss = _LossStub()
        candidate_filter = _FilterStub()
        gen = GCGGenerator(
            models=[GCGModelConfig(name=_LLAMA_2)],
            algorithm=GCGAlgorithmConfig(
                sampling=sampling,
                loss=loss,
                candidate_filter=candidate_filter,
            ),
            output=GCGOutputConfig(result_prefix=str(tmp_path / "gcg")),
        )
        context = GCGContext(
            goals=["g"],
            targets=["t"],
            workers=[MagicMock()],
            test_workers=[],
        )
        fake_attack = MagicMock()

        with (
            patch.object(gen, "_create_attack", return_value=fake_attack) as mock_create_attack,
            patch.object(gen, "_build_logfile_path", return_value=str(tmp_path / "result.json")),
            patch.object(gen, "_read_result", return_value=GCGResult(final_suffix="x")),
            patch("pyrit.executor.promptgen.gcg.generator.asyncio.to_thread", new=AsyncMock(return_value=None)),
        ):
            await gen._perform_async(context=context)

        managers = mock_create_attack.call_args.kwargs["managers"]
        mpa_factory = managers["MPA"]
        assert isinstance(mpa_factory, partial)
        assert mpa_factory.func is generator_mod.attack_lib.GCGMultiPromptAttack
        assert mpa_factory.keywords["sampling"] is sampling
        assert mpa_factory.keywords["loss"] is loss
        assert mpa_factory.keywords["candidate_filter"] is candidate_filter


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


class TestBuildLogfilePath:
    def test_same_prefix_yields_distinct_paths(self, tmp_path: Path) -> None:
        gen = _make_generator(output_dir=tmp_path)
        assert gen._build_logfile_path() != gen._build_logfile_path()

    def test_path_is_prefix_timestamp_and_short_id(self, tmp_path: Path) -> None:
        path = Path(_make_generator(output_dir=tmp_path)._build_logfile_path())
        assert path.parent == tmp_path
        assert re.fullmatch(r"gcg_\d{8}-\d{6}_[0-9a-f]{8}\.json", path.name), path.name

    def test_explicit_logfile_is_returned_unchanged(self) -> None:
        gen = GCGGenerator(models=[GCGModelConfig(name=_LLAMA_2)], output=GCGOutputConfig(logfile="fixed.json"))
        assert gen._build_logfile_path() == "fixed.json"


_TRAJECTORY_STRATEGIES = {
    "individual": GCGStrategyConfig(anneal=True),
    "progressive": GCGStrategyConfig(transfer=True, progressive_goals=True, anneal=True),
}


def _trajectory_generator(*, output_dir: Path, strategy: GCGStrategyConfig, seed: int) -> GCGGenerator:
    return GCGGenerator(
        models=[GCGModelConfig(name=_LLAMA_2)],
        algorithm=GCGAlgorithmConfig(
            n_steps=4,
            test_steps=1,
            batch_size=4,
            topk=6,
            allow_non_ascii=True,
            control_init="1 2 3",
            control_weight=0.0,
            random_seed=seed,
        ),
        strategy=strategy,
        output=GCGOutputConfig(result_prefix=str(output_dir / "gcg"), verbose=False),
    )


class TestConcurrentGenerators:
    @pytest.mark.parametrize("strategy", list(_TRAJECTORY_STRATEGIES), ids=list(_TRAJECTORY_STRATEGIES))
    async def test_generators_sharing_a_prefix_reproduce_isolated_results(self, tmp_path: Path, strategy: str) -> None:
        """Two ``execute_async`` calls overlapping in one process, with the same ``result_prefix``,
        write distinct logfiles and each return exactly the result they return when run alone.

        Only worker creation and the prompt manager are stubbed; the RNG bundle, the outer
        attack, the GCG step loop, and the logfile round-trip are all real. A barrier in the
        stub worker forces the two runs to interleave step by step.
        """
        goals, targets = ["goal 0", "goal 1"], ["10 11", "12 13"]

        def make(seed: int) -> GCGGenerator:
            return _trajectory_generator(output_dir=tmp_path, strategy=_TRAJECTORY_STRATEGIES[strategy], seed=seed)

        def summary(result: GCGResult) -> tuple[str, list[str], list[float]]:
            return result.final_suffix, result.control_history, [round(loss, 6) for loss in result.loss_history]

        with (
            patch.object(generator_mod, "get_workers") as get_workers,
            patch.object(generator_mod.attack_lib, "GCGPromptManager", TrajectoryPromptManager),
        ):
            get_workers.return_value = ([TrajectoryWorker(0)], [])
            baseline_42 = summary(await make(42).execute_async(goals=goals, targets=targets))
            baseline_7 = summary(await make(7).execute_async(goals=goals, targets=targets))
            assert baseline_42 != baseline_7

            barrier = threading.Barrier(2, timeout=10)
            get_workers.side_effect = [([TrajectoryWorker(0, barrier=barrier)], []) for _ in range(2)]
            result_42, result_7 = await asyncio.gather(
                make(42).execute_async(goals=goals, targets=targets),
                make(7).execute_async(goals=goals, targets=targets),
            )

        assert result_42.log_path != result_7.log_path
        assert summary(result_42) == baseline_42
        assert summary(result_7) == baseline_7

    async def test_augmentation_draws_from_the_run_seeded_numpy_stream(self, tmp_path: Path) -> None:
        """Target augmentation consumes the run's own NumPy generator, seeded from ``random_seed``."""
        import numpy as np

        targets = ["10 11", "12 13"]
        with (
            patch.object(generator_mod, "get_workers", return_value=([TrajectoryWorker(0)], [])),
            patch.object(generator_mod.attack_lib, "GCGPromptManager", TrajectoryPromptManager),
            patch.object(
                GCGGenerator, "_apply_target_augmentation", wraps=GCGGenerator._apply_target_augmentation
            ) as augment,
        ):
            generator = _trajectory_generator(
                output_dir=tmp_path, strategy=_TRAJECTORY_STRATEGIES["individual"], seed=42
            )
            await generator.execute_async(goals=["goal 0", "goal 1"], targets=targets)

        expected = np.random.default_rng(42)
        for _ in targets:
            expected.random()
        assert augment.call_args.kwargs["np_rng"].bit_generator.state == expected.bit_generator.state
