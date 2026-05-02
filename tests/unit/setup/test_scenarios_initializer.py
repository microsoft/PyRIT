# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioTechniqueInitializer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import SeedPrompt
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.setup.initializers import ScenarioTechniqueInitializer
from pyrit.setup.initializers.components.scenarios import (
    CRESCENDO_HISTORY_LECTURE,
    CRESCENDO_JOURNALIST_INTERVIEW,
    CRESCENDO_MOVIE_DIRECTOR,
    PERSONA_CRESCENDO_TECHNIQUE_NAMES,
    build_persona_crescendo_specs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    """A mock adversarial target registered as 'adversarial_chat' so build_scenario_techniques resolves cleanly."""
    target = MagicMock(spec=PromptChatTarget)
    # capabilities check inside get_default_adversarial_target requires multi_turn support
    target.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.register_instance(target, name="adversarial_chat")
    return target


# ---------------------------------------------------------------------------
# Initializer class metadata
# ---------------------------------------------------------------------------


class TestScenarioTechniqueInitializerBasic:
    """Tests for ScenarioTechniqueInitializer class metadata."""

    def test_can_be_created(self):
        init = ScenarioTechniqueInitializer()
        assert init is not None

    def test_required_env_vars_is_empty(self):
        init = ScenarioTechniqueInitializer()
        assert init.required_env_vars == []

    def test_description_from_docstring(self):
        init = ScenarioTechniqueInitializer()
        assert isinstance(init.description, str)
        assert "persona-driven crescendo" in init.description


# ---------------------------------------------------------------------------
# Spec construction
# ---------------------------------------------------------------------------


class TestPersonaCrescendoSpecs:
    """Tests for build_persona_crescendo_specs."""

    def test_returns_three_specs(self):
        specs = build_persona_crescendo_specs()
        assert len(specs) == 3

    def test_names_are_persona_variants(self):
        specs = build_persona_crescendo_specs()
        names = {s.name for s in specs}
        assert names == {
            CRESCENDO_MOVIE_DIRECTOR,
            CRESCENDO_HISTORY_LECTURE,
            CRESCENDO_JOURNALIST_INTERVIEW,
        }

    def test_all_use_prompt_sending_attack(self):
        specs = build_persona_crescendo_specs()
        for spec in specs:
            assert spec.attack_class is PromptSendingAttack

    def test_all_have_seed_technique_with_simulated_conversation(self):
        specs = build_persona_crescendo_specs()
        for spec in specs:
            assert spec.seed_technique is not None
            assert spec.seed_technique.has_simulated_conversation

    def test_all_tagged_core_single_turn(self):
        specs = build_persona_crescendo_specs()
        for spec in specs:
            assert "core" in spec.strategy_tags
            assert "single_turn" in spec.strategy_tags

    def test_seed_technique_num_turns_matches_canonical_default(self):
        """Persona variants share the canonical num_turns=3 of crescendo_simulated."""
        specs = build_persona_crescendo_specs()
        for spec in specs:
            sim = spec.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.num_turns == 3

    def test_seed_technique_yaml_path_resolves_to_existing_file(self):
        specs = build_persona_crescendo_specs()
        for spec in specs:
            sim = spec.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.adversarial_chat_system_prompt_path.exists()


# ---------------------------------------------------------------------------
# YAML schema and rendering
# ---------------------------------------------------------------------------


class TestPersonaCrescendoYamls:
    """Tests for the persona-driven crescendo YAML files."""

    @pytest.mark.parametrize("technique_name", PERSONA_CRESCENDO_TECHNIQUE_NAMES)
    def test_yaml_loads_with_required_parameters(self, technique_name):
        path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / f"{technique_name}.yaml"
        sp = SeedPrompt.from_yaml_with_required_parameters(
            template_path=path,
            required_parameters=["objective", "max_turns"],
        )
        assert sp.parameters == ["objective", "max_turns"]

    @pytest.mark.parametrize("technique_name", PERSONA_CRESCENDO_TECHNIQUE_NAMES)
    def test_yaml_renders_with_objective_and_max_turns(self, technique_name):
        path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / f"{technique_name}.yaml"
        sp = SeedPrompt.from_yaml_with_required_parameters(
            template_path=path,
            required_parameters=["objective", "max_turns"],
        )
        rendered = sp.render_template_value(objective="UNIQUE_TEST_OBJECTIVE", max_turns=7)
        assert "UNIQUE_TEST_OBJECTIVE" in rendered
        assert "7" in rendered

    @pytest.mark.parametrize("technique_name", PERSONA_CRESCENDO_TECHNIQUE_NAMES)
    def test_yaml_has_no_em_or_en_dashes(self, technique_name):
        """Author convention: persona YAMLs avoid em-dashes and en-dashes."""
        path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / f"{technique_name}.yaml"
        text = path.read_text(encoding="utf-8")
        # Literal em-dash and en-dash characters used as needles for absence assertions on the YAMLs
        assert "–" not in text, f"{technique_name}.yaml contains an en-dash"
        assert "—" not in text, f"{technique_name}.yaml contains an em-dash"


# ---------------------------------------------------------------------------
# Initializer registration
# ---------------------------------------------------------------------------


class TestScenarioTechniqueInitializerRegistration:
    """Tests that initialize_async wires persona variants into the registry."""

    @pytest.mark.asyncio
    async def test_registers_all_three_persona_techniques(self, mock_adversarial_target):
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.get_names())
        assert CRESCENDO_MOVIE_DIRECTOR in names
        assert CRESCENDO_HISTORY_LECTURE in names
        assert CRESCENDO_JOURNALIST_INTERVIEW in names

    @pytest.mark.asyncio
    async def test_also_registers_core_techniques(self, mock_adversarial_target):
        """Initializer first calls register_scenario_techniques() to ensure core specs land."""
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.get_names())
        # Core specs from PR #1665 era catalog
        assert {"prompt_sending", "role_play", "many_shot", "tap", "crescendo_simulated"} <= names

    @pytest.mark.asyncio
    async def test_persona_factories_have_adversarial_config(self, mock_adversarial_target):
        """Each persona factory has an adversarial config baked in (mirrors crescendo_simulated)."""
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
            assert factories[name].adversarial_chat is not None

    @pytest.mark.asyncio
    async def test_persona_factories_carry_seed_technique(self, mock_adversarial_target):
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
            assert factories[name].seed_technique is not None

    @pytest.mark.asyncio
    async def test_idempotent(self, mock_adversarial_target):
        """Calling initialize_async twice does not duplicate or overwrite entries."""
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        first_names = set(registry.get_names())
        first_factory = registry.get_factories()[CRESCENDO_MOVIE_DIRECTOR]

        await init.initialize_async()
        second_names = set(registry.get_names())
        second_factory = registry.get_factories()[CRESCENDO_MOVIE_DIRECTOR]

        assert first_names == second_names
        # Per-name idempotency: existing factory is preserved.
        assert first_factory is second_factory

    @pytest.mark.asyncio
    async def test_falls_back_to_default_target_when_registry_empty(self):
        """With no 'adversarial_chat' in TargetRegistry, the fallback constructs an OpenAIChatTarget."""
        # Patch OpenAIChatTarget at the import site inside scenario_techniques
        # (which is what get_default_adversarial_target calls), so the test does
        # not depend on OPENAI_CHAT_MODEL or any other env var being set.
        fallback_target = MagicMock(spec=PromptChatTarget)
        with patch(
            "pyrit.scenario.core.scenario_techniques.OpenAIChatTarget",
            return_value=fallback_target,
        ) as mock_openai:
            init = ScenarioTechniqueInitializer()
            await init.initialize_async()

            # Fallback was taken: OpenAIChatTarget(temperature=1.2) was called
            # at least once during get_default_adversarial_target resolution.
            mock_openai.assert_any_call(temperature=1.2)

            registry = AttackTechniqueRegistry.get_registry_singleton()
            factories = registry.get_factories()
            for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
                assert factories[name].adversarial_chat is fallback_target


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestScenarioTechniqueInitializerDiscovery:
    """Tests that the initializer is auto-discovered by InitializerRegistry."""

    def test_initializer_is_discovered(self):
        from pyrit.registry import InitializerRegistry

        registry = InitializerRegistry()
        names = set(registry.get_names())
        assert "scenario_technique" in names
