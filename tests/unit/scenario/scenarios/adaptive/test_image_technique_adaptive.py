# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``ImageTechniqueAdaptive`` scenario."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pyrit.models.identifiers import ComponentIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy
from pyrit.scenario.scenarios.adaptive.image_technique_adaptive import ImageTechniqueAdaptive
from pyrit.score import TrueFalseScorer

_IMAGE_TECHNIQUE_NAMES = {
    "blank_canvas",
    "qr_code",
    "grid_composite",
    "scene_background",
    "comic_panel",
    "image_red_teaming",
}


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


@pytest.fixture(autouse=True)
def reset_technique_registry():
    from pyrit.registry import TargetRegistry

    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    ImageTechniqueAdaptive._cached_technique_class = None
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    ImageTechniqueAdaptive._cached_technique_class = None


@pytest.fixture
def mock_runtime_env():
    with patch.dict(
        "os.environ",
        {
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


FIXTURES = ["patch_central_database", "mock_runtime_env"]


def test_version():
    assert ImageTechniqueAdaptive.VERSION == 1


def test_baseline_enabled():
    assert ImageTechniqueAdaptive.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled


def test_atomic_attack_prefix_is_unique():
    assert ImageTechniqueAdaptive._atomic_attack_prefix() == "adaptive_image"


def test_target_requirements_text_and_image_in_text_out():
    requirements = ImageTechniqueAdaptive.TARGET_REQUIREMENTS
    assert requirements.required_input_modalities == frozenset({frozenset({"text"}), frozenset({"image_path"})})
    assert requirements.required_output_modalities == frozenset({frozenset({"text"})})


def test_required_datasets_non_empty():
    assert len(ImageTechniqueAdaptive.required_datasets()) > 0


def test_default_dataset_config():
    config = ImageTechniqueAdaptive.default_dataset_config()
    assert isinstance(config, CompoundDatasetAttackConfiguration)
    assert all(child.max_dataset_size == 4 for child in config._configurations)
    assert config.dataset_names == ImageTechniqueAdaptive.required_datasets()


def test_technique_class_is_image_catalog():
    technique_class = ImageTechniqueAdaptive.get_technique_class()
    names = {technique.value for technique in technique_class.get_all_techniques()}
    assert names == _IMAGE_TECHNIQUE_NAMES


def test_default_techniques_are_a_spanning_subset():
    technique_class = ImageTechniqueAdaptive.get_technique_class()
    defaults = {technique.value for technique in technique_class.get_techniques_by_tag("default")}
    assert defaults == {"blank_canvas", "grid_composite", "comic_panel"}


@pytest.mark.usefixtures(*FIXTURES)
class TestImageTechniqueAdaptiveInstance:
    def test_instantiation_with_scorer(self, mock_objective_scorer: MagicMock):
        scenario = ImageTechniqueAdaptive(objective_scorer=mock_objective_scorer)
        assert scenario is not None

    def test_factories_are_image_only(self, mock_objective_scorer: MagicMock):
        scenario = ImageTechniqueAdaptive(objective_scorer=mock_objective_scorer)
        assert set(scenario._get_attack_technique_factories().keys()) == _IMAGE_TECHNIQUE_NAMES

    def test_rejects_text_only_target(self, mock_objective_scorer: MagicMock):
        # A text-only target lacks image input and must be rejected by the contract.
        target = MagicMock(spec=PromptTarget)
        target.configuration.capabilities.input_modalities = frozenset({frozenset({"text"})})
        target.configuration.capabilities.output_modalities = frozenset({frozenset({"text"})})
        with pytest.raises(ValueError):
            ImageTechniqueAdaptive.TARGET_REQUIREMENTS.validate(target=target)

    def test_accepts_text_and_image_target(self, mock_objective_scorer: MagicMock):
        target = MagicMock(spec=PromptTarget)
        target.configuration.capabilities.input_modalities = frozenset({frozenset({"text"}), frozenset({"image_path"})})
        target.configuration.capabilities.output_modalities = frozenset({frozenset({"text"})})
        ImageTechniqueAdaptive.TARGET_REQUIREMENTS.validate(target=target)
