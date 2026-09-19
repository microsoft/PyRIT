# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from build_scripts.export_adversarial_benchmark_result import DEFAULT_BENCHMARK_STORE_PATH
from build_scripts.run_adversarial_benchmark import (
    _build_export_command,
    parse_args,
    run_adversarial_benchmark_async,
)
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark

# --- parse_args ---


def test_parse_args_required_arguments_and_defaults() -> None:
    args = parse_args(["--objective-target", "openai_chat", "--adversarial-targets", "gpt-4o"])

    assert args.objective_target == "openai_chat"
    assert args.adversarial_targets == ["gpt-4o"]
    assert args.max_concurrency == 4
    assert args.force is False
    assert args.benchmark_store_path is None
    assert args.output_dir is None


def test_parse_args_multiple_adversarial_targets() -> None:
    args = parse_args(["--objective-target", "openai_chat", "--adversarial-targets", "gpt-4o", "gpt-4o-mini", "claude"])

    assert args.adversarial_targets == ["gpt-4o", "gpt-4o-mini", "claude"]


def test_parse_args_optional_overrides(tmp_path: Path) -> None:
    store_path = tmp_path / "store.jsonl"
    output_dir = tmp_path / "out"

    args = parse_args(
        [
            "--objective-target",
            "openai_chat",
            "--adversarial-targets",
            "gpt-4o",
            "--max-concurrency",
            "8",
            "--force",
            "--benchmark-store-path",
            str(store_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert args.max_concurrency == 8
    assert args.force is True
    assert args.benchmark_store_path == store_path
    assert args.output_dir == output_dir


def test_parse_args_missing_objective_target_exits() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--adversarial-targets", "gpt-4o"])


def test_parse_args_missing_adversarial_targets_exits() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--objective-target", "openai_chat"])


# --- _build_export_command ---


def test_build_export_command_includes_required_flags(tmp_path: Path) -> None:
    command = _build_export_command(
        scenario_result_id="abc-123",
        output_dir=tmp_path / "out",
        benchmark_store_path=None,
    )

    assert command[1:4] == ["-m", "build_scripts.export_adversarial_benchmark_result", "--scenario-result-id"]
    assert "abc-123" in command
    assert "--output-dir" in command
    assert str(tmp_path / "out") in command
    assert "--update-benchmark-store" in command
    assert "--benchmark-store-path" not in command


def test_build_export_command_includes_benchmark_store_path_override_when_provided(tmp_path: Path) -> None:
    store_path = tmp_path / "custom_store.jsonl"

    command = _build_export_command(
        scenario_result_id="abc-123",
        output_dir=tmp_path / "out",
        benchmark_store_path=store_path,
    )

    assert "--benchmark-store-path" in command
    assert str(store_path) in command


# --- run_adversarial_benchmark_async ---


async def _run_with_mocked_benchmark(**kwargs: object) -> tuple[object, AsyncMock, MagicMock, MagicMock]:
    """Run run_adversarial_benchmark_async with PyRIT init and AdversarialBenchmark mocked out."""
    with (
        patch("build_scripts.run_adversarial_benchmark.initialize_pyrit_async", new_callable=AsyncMock) as init,
        patch("build_scripts.run_adversarial_benchmark.AdversarialBenchmark") as benchmark_cls,
    ):
        benchmark = MagicMock(spec=AdversarialBenchmark)
        benchmark.initialize_async = AsyncMock()
        benchmark.run_async = AsyncMock(return_value="scenario-result")
        benchmark_cls.return_value = benchmark

        result = await run_adversarial_benchmark_async(
            objective_target=kwargs.get("objective_target", "openai_chat"),
            adversarial_targets=kwargs.get("adversarial_targets", ["gpt-4o"]),
            max_concurrency=kwargs.get("max_concurrency", 4),
            force=kwargs.get("force", False),
            benchmark_store_path=kwargs.get("benchmark_store_path"),
        )
        return result, init, benchmark_cls, benchmark


async def test_run_adversarial_benchmark_async_default_enables_use_cached_and_store_filter() -> None:
    result, init, benchmark_cls, benchmark = await _run_with_mocked_benchmark()

    assert result == "scenario-result"
    assert init.await_count == 1
    assert benchmark_cls.call_count == 1
    _, ctor_kwargs = benchmark_cls.call_args
    assert ctor_kwargs["use_cached"] is True
    assert ctor_kwargs["benchmark_store_path"] == DEFAULT_BENCHMARK_STORE_PATH

    assert benchmark.set_params_from_args.call_count == 1
    _, set_params_kwargs = benchmark.set_params_from_args.call_args
    assert set_params_kwargs["args"]["objective_target"] == "openai_chat"
    assert set_params_kwargs["args"]["adversarial_targets"] == ["gpt-4o"]
    assert set_params_kwargs["args"]["max_concurrency"] == 4
    assert benchmark.initialize_async.await_count == 1
    assert benchmark.run_async.await_count == 1


async def test_run_adversarial_benchmark_async_force_disables_both_caches() -> None:
    _, _, benchmark_cls, _ = await _run_with_mocked_benchmark(force=True)

    _, ctor_kwargs = benchmark_cls.call_args
    assert ctor_kwargs["use_cached"] is False
    assert ctor_kwargs["benchmark_store_path"] is None


async def test_run_adversarial_benchmark_async_force_ignores_benchmark_store_path_override(tmp_path: Path) -> None:
    override = tmp_path / "custom_store.jsonl"

    _, _, benchmark_cls, _ = await _run_with_mocked_benchmark(force=True, benchmark_store_path=override)

    _, ctor_kwargs = benchmark_cls.call_args
    assert ctor_kwargs["use_cached"] is False
    assert ctor_kwargs["benchmark_store_path"] is None


async def test_run_adversarial_benchmark_async_uses_benchmark_store_path_override_when_not_forcing(
    tmp_path: Path,
) -> None:
    override = tmp_path / "custom_store.jsonl"

    _, _, benchmark_cls, _ = await _run_with_mocked_benchmark(benchmark_store_path=override)

    _, ctor_kwargs = benchmark_cls.call_args
    assert ctor_kwargs["use_cached"] is True
    assert ctor_kwargs["benchmark_store_path"] == override


async def test_run_adversarial_benchmark_async_initializes_with_three_initializers() -> None:
    _, init, _, _ = await _run_with_mocked_benchmark()

    _, init_kwargs = init.call_args
    assert init_kwargs["memory_db_type"] is not None
    assert len(init_kwargs["initializers"]) == 3
