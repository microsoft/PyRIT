# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ViolentDurianInitializer."""

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack import RedTeamingAttack
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.score.true_false.self_ask_true_false_scorer import TrueFalseQuestionPaths
from pyrit.setup.initializers import ViolentDurianInitializer
from pyrit.setup.initializers.components.scenario_techniques import (
    build_scenario_technique_factories,
)
from pyrit.setup.initializers.components.violent_durian import (
    VIOLENT_DURIAN_SEED_PROMPT_PATH,
    VIOLENT_DURIAN_SYSTEM_PROMPT_PATH,
    build_violent_durian_factory,
)


@pytest.fixture(autouse=True)
def reset_registries():
    """Reset technique and target registries between tests."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()


@pytest.fixture
def mock_adversarial_target():
    """A mock adversarial target registered as 'adversarial_chat' so resolution succeeds."""
    target = MagicMock(spec=PromptTarget)
    target.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.register_instance(target, name="adversarial_chat")
    return target


# ---------------------------------------------------------------------------
# Not in the default catalog
# ---------------------------------------------------------------------------


def test_violent_durian_not_in_default_catalog():
    """The technique must never be part of the default scenario technique catalog."""
    names = {f.name for f in build_scenario_technique_factories()}
    assert "violent_durian" not in names


# ---------------------------------------------------------------------------
# Factory construction
# ---------------------------------------------------------------------------


def test_factory_basic_metadata():
    factory = build_violent_durian_factory()
    assert factory.name == "violent_durian"
    assert factory.attack_class is RedTeamingAttack
    assert factory.uses_adversarial is True


def test_factory_tags_exclude_core_and_default():
    factory = build_violent_durian_factory()
    assert "core" not in factory.strategy_tags
    assert "default" not in factory.strategy_tags
    assert "multi_turn" in factory.strategy_tags


def test_factory_data_paths_resolve_to_files():
    assert VIOLENT_DURIAN_SYSTEM_PROMPT_PATH.exists()
    assert VIOLENT_DURIAN_SEED_PROMPT_PATH.exists()


def test_seed_prompt_yaml_renders_objective():
    sp = SeedPrompt.from_yaml_file(VIOLENT_DURIAN_SEED_PROMPT_PATH)
    assert sp.parameters == ["objective"]
    rendered = sp.render_template_value(objective="UNIQUE_TEST_OBJECTIVE")
    assert "UNIQUE_TEST_OBJECTIVE" in rendered
    assert "durian" in rendered.lower()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_initializer_registers_violent_durian(mock_adversarial_target):
    init = ViolentDurianInitializer()
    await init.initialize_async()

    registry = AttackTechniqueRegistry.get_registry_singleton()
    assert "violent_durian" in set(registry.get_names())


async def test_registered_factory_uses_adversarial(mock_adversarial_target):
    init = ViolentDurianInitializer()
    await init.initialize_async()

    registry = AttackTechniqueRegistry.get_registry_singleton()
    factory = registry.get_factories()["violent_durian"]
    assert factory.uses_adversarial is True
    assert factory.attack_class is RedTeamingAttack


async def test_initializer_idempotent(mock_adversarial_target):
    init = ViolentDurianInitializer()
    await init.initialize_async()

    registry = AttackTechniqueRegistry.get_registry_singleton()
    first = registry.get_factories()["violent_durian"]

    await init.initialize_async()
    second = registry.get_factories()["violent_durian"]

    assert first is second


async def test_initializer_does_not_register_default_techniques(mock_adversarial_target):
    """Opt-in initializer only registers violent_durian, not the core catalog."""
    init = ViolentDurianInitializer()
    await init.initialize_async()

    registry = AttackTechniqueRegistry.get_registry_singleton()
    names = set(registry.get_names())
    assert names == {"violent_durian"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_initializer_is_discovered():
    from pyrit.registry import InitializerRegistry

    registry = InitializerRegistry()
    assert "violent_durian" in set(registry.get_names())


# ---------------------------------------------------------------------------
# Scorer data file
# ---------------------------------------------------------------------------


def test_criminal_persona_scorer_yaml_resolves():
    path = TrueFalseQuestionPaths.CRIMINAL_PERSONA.value
    assert path.exists()
