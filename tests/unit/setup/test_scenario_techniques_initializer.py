# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioTechniqueInitializer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import EXECUTOR_RED_TEAM_PATH, EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.score.true_false.self_ask_true_false_scorer import TrueFalseQuestionPaths
from pyrit.setup.initializers import ScenarioTechniqueInitializer
from pyrit.setup.initializers.components.scenario_techniques import (
    build_scenario_technique_factories,
)

PERSONA_CRESCENDO_TECHNIQUE_NAMES: list[str] = [
    "crescendo_movie_director",
    "crescendo_history_lecture",
    "crescendo_journalist_interview",
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registries():
    """Reset technique and target registries between tests."""
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_instance()
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_instance()


@pytest.fixture
def mock_adversarial_target():
    """A mock adversarial target registered as 'adversarial_chat' so the initializer resolves cleanly."""
    target = MagicMock(spec=PromptTarget)
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
# Factory construction
# ---------------------------------------------------------------------------


class TestPersonaCrescendoFactories:
    """Tests for the persona-driven crescendo entries in the canonical factory list."""

    @staticmethod
    def _persona_factories(adversarial_target):
        """Build the canonical catalog and pluck out the persona variants."""
        all_factories = build_scenario_technique_factories()
        return [f for f in all_factories if f.name in PERSONA_CRESCENDO_TECHNIQUE_NAMES]

    def test_returns_three_factories(self, mock_adversarial_target):
        factories = self._persona_factories(mock_adversarial_target)
        assert len(factories) == 3

    def test_names_are_persona_variants(self, mock_adversarial_target):
        factories = self._persona_factories(mock_adversarial_target)
        names = {f.name for f in factories}
        assert names == {
            "crescendo_movie_director",
            "crescendo_history_lecture",
            "crescendo_journalist_interview",
        }

    def test_all_use_prompt_sending_attack(self, mock_adversarial_target):
        factories = self._persona_factories(mock_adversarial_target)
        for f in factories:
            assert f.attack_class is PromptSendingAttack

    def test_all_have_seed_technique_with_simulated_conversation(self, mock_adversarial_target):
        factories = self._persona_factories(mock_adversarial_target)
        for f in factories:
            assert f.seed_technique is not None
            assert f.seed_technique.has_simulated_conversation

    def test_all_tagged_core_single_turn(self, mock_adversarial_target):
        factories = self._persona_factories(mock_adversarial_target)
        for f in factories:
            assert "core" in f.strategy_tags
            assert "single_turn" in f.strategy_tags

    def test_seed_technique_num_turns_matches_canonical_default(self, mock_adversarial_target):
        """Persona variants share the canonical num_turns=3 of crescendo_simulated."""
        factories = self._persona_factories(mock_adversarial_target)
        for f in factories:
            sim = f.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.num_turns == 3

    def test_seed_technique_yaml_path_resolves_to_existing_file(self, mock_adversarial_target):
        factories = self._persona_factories(mock_adversarial_target)
        for f in factories:
            sim = f.seed_technique.simulated_conversation_config
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
        names = set(registry.instances.get_names())
        assert "crescendo_movie_director" in names
        assert "crescendo_history_lecture" in names
        assert "crescendo_journalist_interview" in names

    @pytest.mark.asyncio
    async def test_also_registers_core_techniques(self, mock_adversarial_target):
        """Initializer also registers the core factories alongside persona variants."""
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.instances.get_names())
        # Core factories from build_scenario_technique_factories()
        assert {"role_play", "many_shot", "tap", "crescendo_simulated"} <= names

    @pytest.mark.asyncio
    async def test_persona_factories_have_adversarial_config(self, mock_adversarial_target):
        """Each persona factory marks itself as adversarial (lazy-resolves a chat in create())."""
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
            assert factories[name].uses_adversarial is True

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
        first_names = set(registry.instances.get_names())
        first_factory = registry.get_factories()["crescendo_movie_director"]

        await init.initialize_async()
        second_names = set(registry.instances.get_names())
        second_factory = registry.get_factories()["crescendo_movie_director"]

        assert first_names == second_names
        # Per-name idempotency: existing factory is preserved.
        assert first_factory is second_factory

    @pytest.mark.asyncio
    async def test_falls_back_to_default_target_when_registry_empty(self):
        """With no 'adversarial_chat' in TargetRegistry, lazy resolution at create()-time
        falls back to OpenAIChatTarget(temperature=1.2).
        """
        fallback_target = MagicMock(spec=PromptTarget)
        with patch(
            "pyrit.scenario.core.scenario_target_defaults.OpenAIChatTarget",
            return_value=fallback_target,
        ) as mock_openai:
            init = ScenarioTechniqueInitializer()
            await init.initialize_async()

            # Construction is now decoupled from adversarial resolution.
            mock_openai.assert_not_called()

            # Trigger the lazy fallback path explicitly.
            registry = AttackTechniqueRegistry.get_registry_singleton()
            factories = registry.get_factories()
            for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
                config = factories[name]._build_adversarial_config()
                assert config.target is fallback_target

            mock_openai.assert_any_call(temperature=1.2)


# ---------------------------------------------------------------------------
# Violent Durian (opt-in technique in the catalog)
# ---------------------------------------------------------------------------


class TestViolentDurianTechnique:
    """Tests for the opt-in violent_durian entry in the canonical catalog."""

    @staticmethod
    def _violent_durian_factory():
        return next(f for f in build_scenario_technique_factories() if f.name == "violent_durian")

    def test_in_catalog(self):
        names = {f.name for f in build_scenario_technique_factories()}
        assert "violent_durian" in names

    def test_not_tagged_core_or_default(self):
        """Tagged multi_turn only so it is never selected by core/default scenario aggregates."""
        factory = self._violent_durian_factory()
        assert "core" not in factory.strategy_tags
        assert "default" not in factory.strategy_tags
        assert factory.strategy_tags == ["multi_turn"]

    def test_uses_red_teaming_attack_with_adversarial(self):
        factory = self._violent_durian_factory()
        assert factory.attack_class is RedTeamingAttack
        assert factory.uses_adversarial is True

    def test_data_paths_resolve_to_files(self):
        assert (EXECUTOR_RED_TEAM_PATH / "violent_durian.yaml").exists()
        assert (EXECUTOR_RED_TEAM_PATH / "violent_durian_seed_prompt.yaml").exists()

    def test_seed_prompt_yaml_renders_objective(self):
        sp = SeedPrompt.from_yaml_file(EXECUTOR_RED_TEAM_PATH / "violent_durian_seed_prompt.yaml")
        assert sp.parameters == ["objective"]
        rendered = sp.render_template_value(objective="UNIQUE_TEST_OBJECTIVE")
        assert "UNIQUE_TEST_OBJECTIVE" in rendered
        assert "durian" in rendered.lower()

    def test_criminal_persona_scorer_yaml_resolves(self):
        assert TrueFalseQuestionPaths.CRIMINAL_PERSONA.value.exists()

    @pytest.mark.asyncio
    async def test_registered_by_initializer(self, mock_adversarial_target):
        init = ScenarioTechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert "violent_durian" in set(registry.instances.get_names())


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
