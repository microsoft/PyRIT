# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
get_goals_and_targets = attack_manager_mod.get_goals_and_targets

run_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.experiments.run",
    reason="GCG run module not available",
)
_load_yaml_to_dict = run_mod._load_yaml_to_dict
run_trainer = run_mod.run_trainer

CONFIGS_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "..",
    "pyrit",
    "auxiliary_attacks",
    "gcg",
    "experiments",
    "configs",
)


class TestLoadYamlToDict:
    """Tests for YAML config loading."""

    def test_loads_valid_yaml(self) -> None:
        """Should parse a valid YAML file into a dict."""
        content = "n_steps: 100\nbatch_size: 256\ntransfer: False\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            path = f.name

        try:
            result = _load_yaml_to_dict(path)
            assert result == {"n_steps": 100, "batch_size": 256, "transfer": False}
        finally:
            os.unlink(path)

    def test_loads_list_values(self) -> None:
        """Should handle YAML list values correctly."""
        content = 'model_paths: ["model/a", "model/b"]\ndevices: ["cuda:0", "cuda:1"]\n'
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            path = f.name

        try:
            result = _load_yaml_to_dict(path)
            assert result["model_paths"] == ["model/a", "model/b"]
            assert result["devices"] == ["cuda:0", "cuda:1"]
        finally:
            os.unlink(path)

    def test_raises_on_missing_file(self) -> None:
        """Should raise FileNotFoundError for nonexistent config."""
        with pytest.raises(FileNotFoundError):
            _load_yaml_to_dict("/nonexistent/config.yaml")


class TestRealConfigFiles:
    """Tests that the shipped YAML config files parse correctly and have expected keys."""

    @pytest.fixture()
    def config_files(self) -> list[str]:
        """Return list of all YAML config files shipped with GCG."""
        configs_dir = os.path.normpath(CONFIGS_DIR)
        if not os.path.isdir(configs_dir):
            pytest.skip(f"Config directory not found: {configs_dir}")
        return [os.path.join(configs_dir, f) for f in os.listdir(configs_dir) if f.endswith(".yaml")]

    def test_all_configs_parse_without_error(self, config_files: list[str]) -> None:
        """Every shipped YAML config should parse into a non-empty dict."""
        assert len(config_files) > 0, "No config files found"
        for path in config_files:
            result = _load_yaml_to_dict(path)
            assert isinstance(result, dict), f"{path} did not parse to dict"
            assert len(result) > 0, f"{path} parsed to empty dict"

    def test_all_configs_have_required_keys(self, config_files: list[str]) -> None:
        """Every config should have the minimum required keys for GCG."""
        required_keys = {
            "tokenizer_paths",
            "model_paths",
            "conversation_templates",
            "devices",
        }
        for path in config_files:
            config = _load_yaml_to_dict(path)
            missing = required_keys - set(config.keys())
            assert not missing, f"{os.path.basename(path)} missing keys: {missing}"

    def test_individual_vs_transfer_configs_differ(self, config_files: list[str]) -> None:
        """Individual configs should have transfer=False, transfer configs transfer=True."""
        for path in config_files:
            config = _load_yaml_to_dict(path)
            basename = os.path.basename(path)
            if basename.startswith("individual_"):
                assert config.get("transfer") is False, f"{basename} should have transfer=False"
            elif basename.startswith("transfer_"):
                assert config.get("transfer") is True or config.get("progressive_goals") is True, (
                    f"{basename} should use transfer or progressive_goals"
                )


class TestGetGoalsAndTargetsAdditional:
    """Additional tests for get_goals_and_targets beyond the existing file."""

    def test_shuffle_is_reproducible_with_same_seed(self) -> None:
        """Same random_seed should produce the same goal/target ordering."""
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

    def test_different_seeds_produce_different_ordering(self) -> None:
        """Different seeds should (almost certainly) produce different orderings."""
        csv_content = "goal,target\n" + "\n".join(f"goal{i},target{i}" for i in range(50)) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            params1 = MagicMock()
            params1.train_data = csv_path
            params1.n_train_data = 50
            params1.n_test_data = 0
            params1.test_data = ""
            params1.random_seed = 42

            params2 = MagicMock()
            params2.train_data = csv_path
            params2.n_train_data = 50
            params2.n_test_data = 0
            params2.test_data = ""
            params2.random_seed = 99

            goals1, _, _, _ = get_goals_and_targets(params1)
            goals2, _, _, _ = get_goals_and_targets(params2)

            assert goals1 != goals2, "Different seeds should produce different orderings"
        finally:
            os.unlink(csv_path)

    def test_separate_test_data_file(self) -> None:
        """Should load test data from a separate CSV file when provided."""
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

    def test_n_train_data_limits_output(self) -> None:
        """n_train_data should cap the number of returned training examples."""
        csv_content = "goal,target\n" + "\n".join(f"goal{i},target{i}" for i in range(100)) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = f.name

        try:
            params = MagicMock()
            params.train_data = csv_path
            params.n_train_data = 5
            params.n_test_data = 0
            params.test_data = ""
            params.random_seed = 42

            goals, targets, _, _ = get_goals_and_targets(params)
            assert len(goals) == 5
            assert len(targets) == 5
        finally:
            os.unlink(csv_path)


class TestRunTrainerValidation:
    """Tests for run_trainer input validation (no actual model loading)."""

    def test_raises_on_unsupported_model_name(self) -> None:
        """Should raise ValueError for unsupported model names."""
        with pytest.raises(ValueError, match="Model name not supported"):
            run_trainer(model_name="nonexistent_model")

    @patch.dict("os.environ", {"HUGGINGFACE_TOKEN": ""}, clear=False)
    @patch("pyrit.auxiliary_attacks.gcg.experiments.run._load_environment_files")
    def test_raises_without_hf_token(self, mock_load_env: MagicMock) -> None:
        """Should raise ValueError when HUGGINGFACE_TOKEN is not set."""
        with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": ""}, clear=False):
            with pytest.raises(ValueError, match="HUGGINGFACE_TOKEN"):
                run_trainer(model_name="phi_3_mini")
