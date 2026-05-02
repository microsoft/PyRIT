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
    GPT5_1_TARGET,
    GPT5_4_TARGET,
    ScorerInitializerTags,
)


class TestScorerInitializerBasic:
    """Tests for ScorerInitializer class - basic functionality."""

    def test_can_be_created(self) -> None:
        """Test that ScorerInitializer can be instantiated."""
        init = ScorerInitializer()
        assert init is not None

    def test_required_env_vars_is_empty(self) -> None:
        """Test that required env vars is empty (handles missing targets gracefully)."""
        init = ScorerInitializer()
        assert init.required_env_vars == []

    def test_description_is_non_empty(self) -> None:
        """Test that description is a non-empty string."""
        init = ScorerInitializer()
        assert isinstance(init.description, str)
        assert len(init.description) > 0


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
        self._register_mock_target(name=GPT5_4_TARGET)
        self._register_mock_target(name=GPT5_1_TARGET)

    async def test_raises_when_target_registry_empty(self) -> None:
        """Test that initialize raises RuntimeError when TargetRegistry is empty."""
        init = ScorerInitializer()
        with pytest.raises(RuntimeError, match="TargetRegistry is empty"):
            await init.initialize_async()

    async def test_registers_all_scorer_variants(self) -> None:
        """Test that all scorer variants are registered when all targets are available."""
        self._register_all_scorer_targets()
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert len(registry) == 28

    async def test_registers_gpt4o_scorers_when_only_gpt4o_targets(self) -> None:
        """Test that GPT4O-based scorers register when only GPT4O targets are available."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        # Normal mode: falls back to gpt4o refusal
        assert registry.get_instance_by_name("refusal_gpt4o_objective_strict") is not None
        # inverted_refusal uses the gpt4o refusal fallback
        assert registry.get_instance_by_name("inverted_refusal") is not None

    async def test_refusal_scorers_registered(self) -> None:
        """Test that refusal scorers are registered when gpt4o target is available."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        refusal_entries = registry.get_by_tag(tag=ScorerInitializerTags.REFUSAL)
        # 4 gpt4o prompt-template variants (gpt5_4, gpt5_1, unsafe skipped)
        assert len(refusal_entries) == 4

    async def test_acs_scorers_registered_when_env_vars_set(self) -> None:
        """Test that ACS scorers register when env vars are set."""
        self._register_mock_target(name=GPT4O_TARGET)
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        # 3 threshold + 4 harm = 7 ACS total
        assert registry.get_instance_by_name("acs_threshold_05") is not None
        assert registry.get_instance_by_name("acs_hate") is not None

        acs_entries = registry.get_by_tag(tag=ScorerInitializerTags.ACS)
        assert len(acs_entries) == 7

    async def test_acs_scorers_skipped_without_env_vars(self) -> None:
        """Test that ACS scorers are skipped when content safety env vars are missing."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("acs_threshold_01") is None
        assert registry.get_instance_by_name("acs_threshold_05") is None
        assert registry.get_instance_by_name("acs_hate") is None

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

    async def test_gracefully_skips_scorers_with_missing_target(self) -> None:
        """Test that scorers are skipped with a warning when their target is not in the registry."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        # Refusal variants requiring missing targets should be skipped
        assert registry.get_instance_by_name("refusal_gpt5_4") is None
        assert registry.get_instance_by_name("refusal_gpt5_1") is None
        assert registry.get_instance_by_name("refusal_gpt4o_unsafe") is None
        # But gpt4o-based ones should register
        assert registry.get_instance_by_name("refusal_gpt4o_objective_lenient") is not None

    async def test_default_tag_registers_all_current_scorers(self) -> None:
        """Test that default tag registers all current scorers."""
        self._register_all_scorer_targets()
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        init.params = {"tags": ["default"]}
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        assert len(registry) == 28


class TestScorerInitializerGetInfo:
    """Tests for ScorerInitializer.get_info_async method."""

    async def test_get_info_returns_expected_structure(self) -> None:
        """Test that get_info_async returns expected structure."""
        info = await ScorerInitializer.get_info_async()

        assert isinstance(info, dict)
        assert info["class"] == "ScorerInitializer"
        assert "description" in info
        assert isinstance(info["description"], str)


@pytest.mark.usefixtures("patch_central_database")
class TestScorerInitializerBestObjective:
    """Tests for _tag_best_objective tagging behavior."""

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

    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_tags_best_scorer(self, mock_find_metrics) -> None:
        """Test that _tag_best_objective tags the scorer with highest F1."""
        self._register_mock_target(name=GPT4O_TARGET)

        mock_metrics = MagicMock()
        mock_metrics.f1_score = 0.85
        mock_find_metrics.return_value = mock_metrics

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        results = registry.get_by_tag(tag=ScorerInitializerTags.BEST_OBJECTIVE)
        assert len(results) >= 1

    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_no_metrics_falls_back_to_category(self, mock_find_metrics) -> None:
        """Test that best objective falls back to composite category when no metrics."""
        self._register_mock_target(name=GPT4O_TARGET)
        mock_find_metrics.return_value = None

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        # Should fall back to tagging a composite scorer as best_objective
        results = registry.get_by_tag(tag=ScorerInitializerTags.BEST_OBJECTIVE)
        # Falls back to first composite if available (inverted_refusal)
        composite_entries = registry.get_by_tag(tag=ScorerInitializerTags.OBJECTIVE_COMPOSITE)
        if composite_entries:
            assert len(results) >= 1
        else:
            assert len(results) == 0

    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_picks_highest_f1(self, mock_find_metrics) -> None:
        """Test that the scorer with the highest F1 score gets tagged."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        def mock_metrics_by_hash(*, eval_hash: str, file_path=None) -> MagicMock | None:
            metrics = MagicMock()
            if "refusal" in eval_hash.lower() if eval_hash else False:
                metrics.f1_score = 0.5
                return metrics
            metrics.f1_score = 0.9
            return metrics

        mock_find_metrics.side_effect = mock_metrics_by_hash

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        results = registry.get_by_tag(tag=ScorerInitializerTags.BEST_OBJECTIVE)
        assert len(results) == 1
        assert ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER in results[0].tags

    @patch("pyrit.setup.initializers.components.scorers.find_objective_metrics_by_eval_hash")
    async def test_best_objective_does_not_add_extra_entry(self, mock_find_metrics) -> None:
        """Test that tagging best objective doesn't increase registry count."""
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


@pytest.mark.usefixtures("patch_central_database")
class TestScorerInitializerCategoryTags:
    """Tests for per-category tagging and best-in-category selection."""

    CONTENT_SAFETY_ENV_VARS: dict[str, str] = {
        "AZURE_CONTENT_SAFETY_API_ENDPOINT": "https://test.cognitiveservices.azure.com",
        "AZURE_CONTENT_SAFETY_API_KEY": "test_safety_key",
    }

    def setup_method(self) -> None:
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()

    def teardown_method(self) -> None:
        ScorerRegistry.reset_instance()
        TargetRegistry.reset_instance()
        for var in self.CONTENT_SAFETY_ENV_VARS:
            os.environ.pop(var, None)

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

    async def test_scale_scorers_tagged_with_scale_category(self) -> None:
        """Test that scale scorers get the SCALE category tag."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        scale_entries = registry.get_by_tag(tag=ScorerInitializerTags.SCALE)
        assert len(scale_entries) >= 1

    async def test_acs_threshold_scorers_tagged_separately(self) -> None:
        """Test that ACS threshold scorers get both ACS and ACS_THRESHOLD tags."""
        self._register_mock_target(name=GPT4O_TARGET)
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        threshold_entries = registry.get_by_tag(tag=ScorerInitializerTags.ACS_THRESHOLD)
        assert len(threshold_entries) == 3
        for entry in threshold_entries:
            assert ScorerInitializerTags.ACS in entry.tags

    async def test_acs_harm_scorers_tagged_separately(self) -> None:
        """Test that ACS harm scorers get both ACS and ACS_HARM tags."""
        self._register_mock_target(name=GPT4O_TARGET)
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        harm_entries = registry.get_by_tag(tag=ScorerInitializerTags.ACS_HARM)
        assert len(harm_entries) == 4
        for entry in harm_entries:
            assert ScorerInitializerTags.ACS in entry.tags

    async def test_likert_scorers_tagged_with_likert_category(self) -> None:
        """Test that likert scorers get the LIKERT category tag."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        likert_entries = registry.get_by_tag(tag=ScorerInitializerTags.LIKERT)
        expected_count = sum(1 for s in LikertScalePaths if s.evaluation_files is not None)
        assert len(likert_entries) == expected_count

    async def test_task_achieved_scorers_tagged(self) -> None:
        """Test that multiple task_achieved variants are registered."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        task_entries = registry.get_by_tag(tag=ScorerInitializerTags.TASK_ACHIEVED)
        assert len(task_entries) == 2

    async def test_composite_scorers_tagged(self) -> None:
        """Test that compound objective scorers get the OBJECTIVE_COMPOSITE tag."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT4O_TEMP9_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        composite_entries = registry.get_by_tag(tag=ScorerInitializerTags.OBJECTIVE_COMPOSITE)
        assert len(composite_entries) >= 1

    async def test_best_refusal_tags_preferred_scorer(self) -> None:
        """Test that BEST_REFUSAL tags the preferred refusal scorer when available."""
        self._register_mock_target(name=GPT4O_TARGET)
        self._register_mock_target(name=GPT5_4_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        best = registry.get_by_tag(tag=ScorerInitializerTags.BEST_REFUSAL)
        assert len(best) == 1
        assert best[0].name == "refusal_gpt5_4"

    async def test_best_refusal_falls_back_when_preferred_missing(self) -> None:
        """Test that BEST_REFUSAL falls back to first available when preferred is missing."""
        self._register_mock_target(name=GPT4O_TARGET)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        best = registry.get_by_tag(tag=ScorerInitializerTags.BEST_REFUSAL)
        assert len(best) == 1
        # Should be one of the gpt4o refusal variants
        assert ScorerInitializerTags.REFUSAL in best[0].tags

    async def test_best_acs_threshold_tagged(self) -> None:
        """Test that BEST_ACS_THRESHOLD tags the preferred ACS threshold scorer."""
        self._register_mock_target(name=GPT4O_TARGET)
        os.environ.update(self.CONTENT_SAFETY_ENV_VARS)

        init = ScorerInitializer()
        await init.initialize_async()

        registry = ScorerRegistry.get_registry_singleton()
        best = registry.get_by_tag(tag=ScorerInitializerTags.BEST_ACS_THRESHOLD)
        assert len(best) == 1
        assert best[0].name == "acs_threshold_05"
