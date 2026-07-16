# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``ScenarioContext`` frozen dataclass.

``ScenarioContext`` is the immutable bundle the base ``Scenario`` builds in
``initialize_async`` and hands to ``_build_atomic_attacks_async``. These tests
pin the field surface, defaults, and immutability that scenario authors rely on.
"""

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario_context import ScenarioContext


def _context(**overrides) -> ScenarioContext:
    kwargs = {
        "objective_target": MagicMock(spec=PromptTarget),
        "scenario_techniques": [],
        "dataset_config": MagicMock(spec=DatasetConfiguration),
    }
    kwargs.update(overrides)
    return ScenarioContext(**kwargs)


def test_required_fields_are_stored():
    target = MagicMock(spec=PromptTarget)
    techniques = [MagicMock()]
    dataset_config = MagicMock(spec=DatasetConfiguration)
    context = ScenarioContext(
        objective_target=target,
        scenario_techniques=techniques,
        dataset_config=dataset_config,
    )
    assert context.objective_target is target
    assert context.scenario_techniques is techniques
    assert context.dataset_config is dataset_config


def test_memory_labels_defaults_to_empty_dict():
    context = _context()
    assert context.memory_labels == {}


def test_memory_labels_default_is_not_shared_between_instances():
    first = _context()
    second = _context()
    assert first.memory_labels is not second.memory_labels


def test_include_baseline_defaults_to_false():
    assert _context().include_baseline is False


def test_is_frozen():
    context = _context()
    with pytest.raises(AttributeError):
        context.objective_target = MagicMock(spec=PromptTarget)
