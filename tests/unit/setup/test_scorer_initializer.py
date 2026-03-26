# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import MagicMock, patch

import pytest

from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score import LikertScalePaths
from pyrit.setup.initializers import ScorerInitializer
from pyrit.setup.initializers.components.scorers import (
    GPT4O_TARGET,
    GPT4O_TEMP0_TARGET,
    GPT4O_TEMP9_TARGET,
    GPT4O_UNSAFE_TARGET,
    GPT4O_UNSAFE_TEMP0_TARGET,
    GPT4O_UNSAFE_TEMP9_TARGET,
)


class TestScorerInitializerBasic:
    """Tests for ScorerInitializer class - basic functionality."""

    def test_can_be_created(self) -> None:
        """Test that ScorerInitializer can be instantiated."""
        init = ScorerInitializer()
        assert init is not None
        assert init.name == "Scorer Initializer"

    def test_required_env_vars_is_empty(self) -> None:
        """Test that required env vars is empty (handles missing targets gracefully)."""
        init = ScorerInitializer()
        assert init.required_env_vars == []

    def test_description_is_non_empty(self) -> None:
        """Test that description is a non-empty string."""
        init = ScorerInitializer()
        assert isinstance(init.description, str)
        assert len(init.description) > 0

    def test_execution_order_is_two(self) -> None:
        """Test that execution_order is 2 (runs after target initializer)."""
        init = ScorerInitializer()
        assert init.execution_order == 2


@pytest.mark.usefixtures("patch_central_database")
class TestScorerInitializerInitialize:
    """Tests for ScorerInitializer.initialize_async method."""

    CONTENT_SAFETY_ENV_VARS: dict[str, str] = {
        "AZURE_CONTENT_SAFETY_API_ENDPOINT": "https://test.cognitiveservices.azure.com",
        "AZURE_CONTENT_SAFETY_API_KEY": "test_safety_key",
    }

    def setup_method(self) -> None:
        """Reset registries before each test."""
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()
        self._clear_env_vars()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()
        self._clear_env_vars()

    def _clear_env_vars(self) -> None:
        """Clear content safety environment variables."""
        for var in self.CONTENT_SAFETY_ENV_VARS:
            if var in os.environ:
                del os.environ[var]

    def _register_mock_target(self, *, name: str) -> OpenAIChatTarget:
        """Register a mock OpenAIChatTarget in the TargetRegistry."""
        target = MagicMock(spec=OpenAIChatTarget)
        target._temperature = None
        target._endpoint = f"https://test-{name}.openai.azure.com"
        target._api_key = "test_key"
        target._model_name = "test-model"
        target._underlying_model = "gpt-4o"
        registry = TargetRegistry.get_registry_singleton()
        registry.register_instance(target, name=name)
        return target

    def _register_all_scorer_targets(self) -> None:
        """Register all targets that scorers depend on."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP0_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)
        self._register_mock_target(name=GPT4O_UNSAFE_TARGET)
        self._register_mock_target(name=GPT4O_UNSAFE_TEMP0_TARGET)
        self._register_mock_target(name=GPT4O_UNSAFE_TEMP9_TARGET)

    @pytest.mark.asyncio
    async def test_raises_when_target_registry_empty(self) -> None:
        """Test that initialize raises RuntimeError when TargetRegistry is empty."""
        init = ScorerInitializer()
        with pytest.raises(RuntimeError, match="TargetRegistry is empty"):
            await init.initialize_async()

    @pytest.mark.asyncio
    async def test_registers_all_scorers_when_all_targets_and_acs_available(self) -> None:
        """Test that all scorers are registered when all targets and ACS env vars are set."""
        self._register_all_scorer_targets()
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert len(registry) == 24

    @pytest.mark.asyncio
    async def test_registers_gpt4o_scorers_when_only_gpt4o_targets(self) -> None:
        """Test that GPT4O-based scorers register when only GPT4O targets are available."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("refusal_gpt4o") is not None
        assert registry.get_instance_by_name("inverted_refusal_gpt4o") is not None
        # Unsafe-only scorers should not be registered
        assert registry.get_instance_by_name("inverted_refusal_gpt4o_unsafe") is None

    @pytest.mark.asyncio
    async def test_refusal_scorer_registered(self) -> None:
        """Test that refusal_gpt4o is registered and retrievable."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        scorer = registry.get_instance_by_name("refusal_gpt4o")
        assert scorer is not None

    @pytest.mark.asyncio
    async def test_acs_scorers_registered_when_env_vars_set(self) -> None:
        """Test that ACS scorers register when content safety env vars are set."""
        self._register_mock_target(name=GPT4O_TARGET)
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("acs_threshold_05") is not None
        assert registry.get_instance_by_name("acs_hate") is not None

    @pytest.mark.asyncio
    async def test_acs_scorers_skipped_without_env_vars(self) -> None:
        """Test that ACS scorers are skipped when content safety env vars are missing."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("acs_threshold_01") is None
        assert registry.get_instance_by_name("acs_threshold_05") is None
        assert registry.get_instance_by_name("acs_hate") is None

    @pytest.mark.asyncio
    async def test_likert_scorers_registered(self) -> None:
        """Test that likert scorers are registered for LikertScalePaths with evaluation files."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        for scale in LikertScalePaths:
            if scale.evaluation_files is not None:
                expected_name = f"likert_{scale.name.lower().removesuffix('_scale')}_gpt4o"
                scorer = registry.get_instance_by_name(expected_name)
                assert scorer is not None, f"Likert scorer '{expected_name}' not found in registry"

    @pytest.mark.asyncio
    async def test_gracefully_skips_scorers_with_missing_target(self) -> None:
        """Test that scorers are skipped with a warning when their target is not in the registry."""
        # Only register GPT4O (not unsafe) — unsafe scorers should be skipped
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("inverted_refusal_gpt4o_unsafe") is None
        assert registry.get_instance_by_name("inverted_refusal_gpt4o_unsafe_temp9") is None
        assert registry.get_instance_by_name("refusal_gpt4o") is not None

    @pytest.mark.asyncio
    async def test_default_tag_registers_all_current_scorers(self) -> None:
        """Test that tags=['default'] registers all current scorers (all are tagged default)."""
        self._register_all_scorer_targets()
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        init.params = {"tags": ["default"]}
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert len(registry) == 24


class TestScorerInitializerGetInfo:
    """Tests for ScorerInitializer.get_info_async method."""

    @pytest.mark.asyncio
    async def test_get_info_returns_expected_structure(self) -> None:
        """Test that get_info_async returns expected structure."""
        info = await ScorerInitializer.get_info_async()

        assert isinstance(info, dict)
        assert info["name"] == "Scorer Initializer"
        assert info["class"] == "ScorerInitializer"
        assert "description" in info
        assert isinstance(info["description"], str)

    @pytest.mark.asyncio
    async def test_get_info_execution_order_is_two(self) -> None:
        """Test that get_info reports execution_order of 2."""
        info = await ScorerInitializer.get_info_async()
        assert info["execution_order"] == 2


@pytest.mark.usefixtures("patch_central_database")
class TestScorerInitializerBestObjectiveF1:
    """Tests for _register_best_objective_f1 tagging behavior."""

    def setup_method(self) -> None:
        """Reset registries before each test."""
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()

    def _register_mock_target(self, *, name: str) -> OpenAIChatTarget:
        """Register a mock OpenAIChatTarget in the TargetRegistry."""
        target = MagicMock(spec=OpenAIChatTarget)
        target._temperature = None
        target._endpoint = f"https://test-{name}.openai.azure.com"
        target._api_key = "test_key"
        target._model_name = "test-model"
        target._underlying_model = "gpt-4o"
        registry = TargetRegistry.get_registry_singleton()
        registry.register_instance(target, name=name)
        return target

    @pytest.mark.asyncio
    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_f1_tags_best_scorer(self, mock_find_metrics) -> None:
        """Test that _register_best_objective_f1 tags the scorer with highest F1."""
        self._register_mock_target(name=GPT4O_TARGET)

        mock_metrics = MagicMock()
        mock_metrics.f1_score = 0.85
        mock_find_metrics.return_value = mock_metrics

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        results = registry.get_by_tag(tag="best_objective_f1")
        assert len(results) >= 1

    @pytest.mark.asyncio
    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_f1_no_metrics_skips_tagging(self, mock_find_metrics) -> None:
        """Test that no scorer is tagged when no metrics are available."""
        self._register_mock_target(name=GPT4O_TARGET)
        mock_find_metrics.return_value = None

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        results = registry.get_by_tag(tag="best_objective_f1")
        assert len(results) == 0

    @pytest.mark.asyncio
    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_f1_picks_highest_f1(self, mock_find_metrics) -> None:
        """Test that the scorer with the highest F1 score gets tagged."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        def mock_metrics_by_hash(*, eval_hash: str) -> MagicMock | None:
            metrics = MagicMock()
            if "refusal" in eval_hash.lower() if eval_hash else False:
                metrics.f1_score = 0.5
                return metrics
            # Default: return higher F1 for all scorers that have a hash
            metrics.f1_score = 0.9
            return metrics

        mock_find_metrics.side_effect = mock_metrics_by_hash

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        results = registry.get_by_tag(tag="best_objective_f1")
        assert len(results) == 1
        # Should also have the default_objective_scorer tag
        assert "default_objective_scorer" in results[0].tags

    @pytest.mark.asyncio
    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_f1_does_not_add_extra_entry(self, mock_find_metrics) -> None:
        """Test that tagging best_objective_f1 doesn't increase registry count."""
        self._register_mock_target(name=GPT4O_TARGET)

        mock_metrics = MagicMock()
        mock_metrics.f1_score = 0.85
        mock_find_metrics.return_value = mock_metrics

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        count_with_tag = len(registry)

        # Reset and run without metrics to get baseline count
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()
        self._register_mock_target(name=GPT4O_TARGET)
        mock_find_metrics.return_value = None

        init2 = ScorerInitializer()
        await init2.initialize_async()

        registry2 = ScorerRegistry.get_registry_singleton()
        count_without_tag = len(registry2)

        assert count_with_tag == count_without_tag
