# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.promptgen.gcg.config import GCGConfig, GCGDataConfig, GCGModelConfig, GCGOutputConfig

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, accelerate, etc.) not installed",
)
get_goals_and_targets = attack_manager_mod.get_goals_and_targets

data_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.data",
    reason="GCG data module requires torch (transitive via attack_manager)",
)
load_goals_and_targets = data_mod.load_goals_and_targets

run_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.experiments.run",
    reason="GCG run module not available",
)
_main_async = run_mod._main_async
_resolve_output = run_mod._resolve_output

generator_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.generator",
    reason="GCG generator module not available",
)
GCGGenerator = generator_mod.GCGGenerator


class TestLoadGoalsAndTargetsHelper:
    """Tests for the public ``load_goals_and_targets`` helper that wraps the legacy CSV loader."""

    def test_loads_goals_and_targets_from_train_csv(self) -> None:
        csv_content = "goal,target\ngoal1,target1\ngoal2,target2\ngoal3,target3\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            data = GCGDataConfig(train_data=csv_path, n_train_data=2)
            train_goals, train_targets, test_goals, test_targets = load_goals_and_targets(data=data, random_seed=42)
            assert len(train_goals) == 2
            assert len(train_targets) == 2
            assert test_goals == []
            assert test_targets == []
        finally:
            os.unlink(csv_path)

    def test_seed_is_reproducible_via_helper(self) -> None:
        csv_content = "goal,target\n" + "\n".join(f"goal{i},target{i}" for i in range(20)) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            data = GCGDataConfig(train_data=csv_path, n_train_data=10)
            g1, t1, _, _ = load_goals_and_targets(data=data, random_seed=42)
            g2, t2, _, _ = load_goals_and_targets(data=data, random_seed=42)
            assert g1 == g2
            assert t1 == t2
        finally:
            os.unlink(csv_path)


class TestGetGoalsAndTargetsLegacy:
    """Tests for the underlying get_goals_and_targets function (still used internally)."""

    def test_shuffle_is_reproducible_with_same_seed(self) -> None:
        csv_content = "goal,target\n" + "\n".join(f"goal{i},target{i}" for i in range(20)) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            params1 = MagicMock()
            params1.train_data = csv_path
            params1.n_train_data = 10
            params1.n_test_data = 0
            params1.test_data = ""
            params1.random_seed = 42

            params2 = MagicMock()
            params2.train_data = csv_path
            params2.n_train_data = 10
            params2.n_test_data = 0
            params2.test_data = ""
            params2.random_seed = 42

            goals1, targets1, _, _ = get_goals_and_targets(params1)
            goals2, targets2, _, _ = get_goals_and_targets(params2)

            assert goals1 == goals2
            assert targets1 == targets2
        finally:
            os.unlink(csv_path)

    def test_separate_test_data_file(self) -> None:
        train_csv = "goal,target\ntrain_goal1,train_target1\ntrain_goal2,train_target2\n"
        test_csv = "goal,target\ntest_goal1,test_target1\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(train_csv)
            train_path = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(test_csv)
            test_path = f.name

        try:
            params = MagicMock()
            params.train_data = train_path
            params.n_train_data = 2
            params.n_test_data = 1
            params.test_data = test_path
            params.random_seed = 42

            train_goals, train_targets, test_goals, test_targets = get_goals_and_targets(params)
            assert len(train_goals) == 2
            assert len(test_goals) == 1
            assert test_goals[0] == "test_goal1"
            assert test_targets[0] == "test_target1"
        finally:
            os.unlink(train_path)
            os.unlink(test_path)


class TestResolveOutput:
    """Tests for run.py's --output-dir override logic."""

    def test_no_override_returns_input_unchanged(self) -> None:
        original = GCGOutputConfig(result_prefix="results/run1")
        assert _resolve_output(output=original, output_dir=None) is original

    def test_override_preserves_basename(self, tmp_path: Path) -> None:
        original = GCGOutputConfig(result_prefix="results/run1")
        resolved = _resolve_output(output=original, output_dir=str(tmp_path))
        assert resolved.result_prefix == str(tmp_path / "run1")

    def test_override_falls_back_to_default_basename(self, tmp_path: Path) -> None:
        original = GCGOutputConfig(result_prefix="")
        resolved = _resolve_output(output=original, output_dir=str(tmp_path))
        assert resolved.result_prefix == str(tmp_path / "gcg_suffix")


class TestMainAsyncCli:
    """Tests for ``run.py``'s ``--config`` + ``--data`` CLI wrapper around GCGGenerator.execute_async."""

    @patch("pyrit.executor.promptgen.gcg.experiments.run._load_environment_files")
    async def test_raises_when_no_token_anywhere(self, mock_load_env: MagicMock, tmp_path: Path) -> None:
        config = GCGConfig(models=[GCGModelConfig(name="org/model")])
        config_path = tmp_path / "config.json"
        config.to_json_file(config_path)
        data_config = GCGDataConfig(train_data="some-csv", n_train_data=1)
        data_path = tmp_path / "data.json"
        data_config.to_json_file(data_path)

        with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": ""}, clear=False):
            with pytest.raises(ValueError, match="No HuggingFace token available"):
                await _main_async(str(config_path), str(data_path))

    @patch("pyrit.executor.promptgen.gcg.experiments.run._load_environment_files")
    @patch("pyrit.executor.promptgen.gcg.experiments.run.load_goals_and_targets")
    async def test_passes_loaded_goals_to_generator_and_uses_env_token(
        self,
        mock_loader: MagicMock,
        mock_load_env: MagicMock,
        tmp_path: Path,
    ) -> None:
        """env-var token fallback works, and the deserialized strategy + data
        flow into GCGGenerator.execute_async."""
        config = GCGConfig(models=[GCGModelConfig(name="org/model")])
        config_path = tmp_path / "config.json"
        config.to_json_file(config_path)
        data_config = GCGDataConfig(train_data="some-csv", n_train_data=1)
        data_path = tmp_path / "data.json"
        data_config.to_json_file(data_path)

        mock_loader.return_value = (["g1"], ["t1"], [], [])

        with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": "hf_envtoken"}):
            with patch.object(GCGGenerator, "execute_async", new_callable=AsyncMock) as mock_execute:
                mock_execute.return_value = MagicMock()
                await _main_async(str(config_path), str(data_path))

        mock_execute.assert_awaited_once()
        call_kwargs = mock_execute.await_args.kwargs
        assert call_kwargs["goals"] == ["g1"]
        assert call_kwargs["targets"] == ["t1"]
        # The loader was called with the deserialized data config
        loader_kwargs = mock_loader.call_args.kwargs
        assert loader_kwargs["data"] == data_config
