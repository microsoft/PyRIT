# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for TechniqueInitializer and the technique group catalogs."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import EXECUTOR_RED_TEAM_PATH, EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack import PAIRAttack, PromptSendingAttack, RedTeamingAttack
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.score.true_false.self_ask_true_false_scorer import TrueFalseQuestionPaths
from pyrit.setup.initializers import TechniqueInitializer
from pyrit.setup.initializers.techniques import (
    build_technique_factories,
    core,
    extra,
)

CORE_TECHNIQUE_NAMES: list[str] = [
    "role_play_movie_script",
    "role_play_video_game",
    "role_play_trivia_game",
    "role_play_persuasion",
    "role_play_persuasion_written",
    "many_shot",
    "tap",
    "crescendo_simulated",
    "red_teaming",
    "context_compliance",
    "crescendo_movie_director",
    "crescendo_history_lecture",
    "crescendo_journalist_interview",
    "flip",
]

EXTRA_TECHNIQUE_NAMES: list[str] = ["pair", "violent_durian"]

PERSONA_CRESCENDO_TECHNIQUE_NAMES: list[str] = [
    "crescendo_movie_director",
    "crescendo_history_lecture",
    "crescendo_journalist_interview",
]

ROLE_PLAY_TECHNIQUE_NAMES: list[str] = [
    "role_play_movie_script",
    "role_play_video_game",
    "role_play_trivia_game",
    "role_play_persuasion",
    "role_play_persuasion_written",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registries():
    """Reset technique and target registries between tests."""
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


@pytest.fixture
def mock_adversarial_target():
    """A mock adversarial target registered as 'adversarial_chat' so resolution succeeds."""
    target = MagicMock(spec=PromptTarget)
    target.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(target, name="adversarial_chat")
    return target


# ---------------------------------------------------------------------------
# Group catalogs (core.py / extra.py)
# ---------------------------------------------------------------------------


class TestCoreGroupCatalog:
    """Tests for ``core.get_technique_factories()``."""

    def test_returns_expected_names(self):
        names = {f.name for f in core.get_technique_factories()}
        assert names == set(CORE_TECHNIQUE_NAMES)

    def test_factories_do_not_bake_in_group_tag(self):
        """The ``core`` group tag is injected by build_technique_factories, not baked in here."""
        for factory in core.get_technique_factories():
            assert "core" not in factory.technique_tags
            assert "extra" not in factory.technique_tags


class TestExtraGroupCatalog:
    """Tests for ``extra.get_technique_factories()``."""

    def test_returns_expected_names(self):
        names = {f.name for f in extra.get_technique_factories()}
        assert names == set(EXTRA_TECHNIQUE_NAMES)

    def test_factories_do_not_bake_in_group_tag(self):
        for factory in extra.get_technique_factories():
            assert "extra" not in factory.technique_tags
            assert "core" not in factory.technique_tags

    def test_violent_durian_has_max_turns_three(self):
        factory = next(f for f in extra.get_technique_factories() if f.name == "violent_durian")
        assert factory._attack_kwargs == {"max_turns": 3}

    def test_pair_uses_pair_attack(self):
        factory = next(f for f in extra.get_technique_factories() if f.name == "pair")
        assert factory.attack_class is PAIRAttack


# ---------------------------------------------------------------------------
# build_technique_factories (the protocol aggregator)
# ---------------------------------------------------------------------------


class TestBuildTechniqueFactories:
    """Tests for the group-selection and tag-injection behavior."""

    def test_core_group_injects_core_tag(self):
        factories = build_technique_factories(groups=["core"])
        assert {f.name for f in factories} == set(CORE_TECHNIQUE_NAMES)
        for factory in factories:
            assert "core" in factory.technique_tags

    def test_extra_group_injects_extra_tag(self):
        factories = build_technique_factories(groups=["extra"])
        assert {f.name for f in factories} == set(EXTRA_TECHNIQUE_NAMES)
        for factory in factories:
            assert "extra" in factory.technique_tags

    def test_default_returns_all_groups(self):
        names = {f.name for f in build_technique_factories()}
        assert names == set(CORE_TECHNIQUE_NAMES) | set(EXTRA_TECHNIQUE_NAMES)

    def test_unknown_group_raises(self):
        with pytest.raises(ValueError, match="Unknown technique group"):
            build_technique_factories(groups=["does_not_exist"])

    def test_factory_names_are_unique(self):
        names = [f.name for f in build_technique_factories()]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# TechniqueInitializer class metadata
# ---------------------------------------------------------------------------


class TestTechniqueInitializerBasic:
    """Tests for TechniqueInitializer class metadata."""

    def test_can_be_created(self):
        assert TechniqueInitializer() is not None

    def test_required_env_vars_is_empty(self):
        assert TechniqueInitializer().required_env_vars == []

    def test_description_is_nonempty_string(self):
        description = TechniqueInitializer().description
        assert isinstance(description, str)
        assert description

    def test_tags_parameter_defaults_to_core(self):
        params = {p.name: p for p in TechniqueInitializer().supported_parameters}
        assert "tags" in params
        assert params["tags"].default == ["core"]


# ---------------------------------------------------------------------------
# Persona-driven crescendo factories (a subset of the core group)
# ---------------------------------------------------------------------------


class TestPersonaCrescendoFactories:
    """Tests for the persona-driven crescendo entries in the core catalog."""

    @staticmethod
    def _persona_factories():
        all_factories = build_technique_factories(groups=["core"])
        return [f for f in all_factories if f.name in PERSONA_CRESCENDO_TECHNIQUE_NAMES]

    def test_returns_three_factories(self):
        assert len(self._persona_factories()) == 3

    def test_all_use_prompt_sending_attack(self):
        for f in self._persona_factories():
            assert f.attack_class is PromptSendingAttack

    def test_all_have_seed_technique_with_simulated_conversation(self):
        for f in self._persona_factories():
            assert f.seed_technique is not None
            assert f.seed_technique.has_simulated_conversation

    def test_all_tagged_core_single_turn(self):
        for f in self._persona_factories():
            assert "core" in f.technique_tags
            assert "single_turn" in f.technique_tags

    def test_seed_technique_num_turns_matches_canonical_default(self):
        """Persona variants share the canonical num_turns=3 of crescendo_simulated."""
        for f in self._persona_factories():
            sim = f.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.num_turns == 3

    def test_seed_technique_yaml_path_resolves_to_existing_file(self):
        for f in self._persona_factories():
            sim = f.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.adversarial_chat_system_prompt_path.exists()


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
# Role-play factories (simulated-conversation personas in the core group)
# ---------------------------------------------------------------------------


class TestRolePlayFactories:
    """Tests for the role-play simulated-conversation entries in the core catalog."""

    @staticmethod
    def _role_play_factories():
        all_factories = build_technique_factories(groups=["core"])
        return [f for f in all_factories if f.name in ROLE_PLAY_TECHNIQUE_NAMES]

    def test_returns_five_factories(self):
        assert len(self._role_play_factories()) == len(ROLE_PLAY_TECHNIQUE_NAMES)

    def test_all_use_prompt_sending_attack(self):
        for f in self._role_play_factories():
            assert f.attack_class is PromptSendingAttack

    def test_all_have_seed_technique_with_simulated_conversation(self):
        for f in self._role_play_factories():
            assert f.seed_technique is not None
            assert f.seed_technique.has_simulated_conversation

    def test_all_tagged_core_single_turn_light(self):
        for f in self._role_play_factories():
            assert "core" in f.technique_tags
            assert "single_turn" in f.technique_tags
            assert "light" in f.technique_tags

    def test_seed_technique_num_turns_matches_role_play_default(self):
        for f in self._role_play_factories():
            sim = f.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.num_turns == 2

    def test_seed_technique_yaml_path_resolves_to_existing_file(self):
        for f in self._role_play_factories():
            sim = f.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.adversarial_chat_system_prompt_path.exists()

    def test_all_use_role_play_next_message_prompt(self):
        for f in self._role_play_factories():
            sim = f.seed_technique.simulated_conversation_config
            assert sim is not None
            assert sim.next_message_system_prompt_path is not None
            assert sim.next_message_system_prompt_path.name == "role_play_next_message.yaml"
            assert sim.next_message_system_prompt_path.exists()


class TestRolePlayYamls:
    """Tests for the role-play persona YAML files."""

    @pytest.mark.parametrize("technique_name", ROLE_PLAY_TECHNIQUE_NAMES)
    def test_yaml_loads_with_required_parameters(self, technique_name):
        path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / "role_play" / f"{technique_name}.yaml"
        sp = SeedPrompt.from_yaml_with_required_parameters(
            template_path=path,
            required_parameters=["objective", "max_turns"],
        )
        assert sp.parameters == ["objective", "max_turns"]

    @pytest.mark.parametrize("technique_name", ROLE_PLAY_TECHNIQUE_NAMES)
    def test_yaml_renders_with_objective_and_max_turns(self, technique_name):
        path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / "role_play" / f"{technique_name}.yaml"
        sp = SeedPrompt.from_yaml_with_required_parameters(
            template_path=path,
            required_parameters=["objective", "max_turns"],
        )
        rendered = sp.render_template_value(objective="UNIQUE_TEST_OBJECTIVE", max_turns=7)
        assert "UNIQUE_TEST_OBJECTIVE" in rendered
        assert "7" in rendered

    @pytest.mark.parametrize("technique_name", ROLE_PLAY_TECHNIQUE_NAMES)
    def test_yaml_has_no_em_or_en_dashes(self, technique_name):
        """Author convention: persona YAMLs avoid em-dashes and en-dashes."""
        path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / "role_play" / f"{technique_name}.yaml"
        text = path.read_text(encoding="utf-8")
        # Literal em-dash and en-dash characters used as needles for absence assertions on the YAMLs
        assert "–" not in text, f"{technique_name}.yaml contains an en-dash"
        assert "—" not in text, f"{technique_name}.yaml contains an em-dash"


# ---------------------------------------------------------------------------
# Initializer registration
# ---------------------------------------------------------------------------


class TestTechniqueInitializerRegistration:
    """Tests that initialize_async wires factories into the registry per the tags param."""

    async def test_default_registers_only_core(self, mock_adversarial_target):
        init = TechniqueInitializer()
        await init.initialize_async()

        names = set(AttackTechniqueRegistry.get_registry_singleton().instances.get_names())
        assert set(CORE_TECHNIQUE_NAMES) <= names
        assert "pair" not in names
        assert "violent_durian" not in names

    async def test_registered_core_factory_carries_core_tag(self, mock_adversarial_target):
        init = TechniqueInitializer()
        await init.initialize_async()

        factory = AttackTechniqueRegistry.get_registry_singleton().get_factories()["role_play_movie_script"]
        assert "core" in factory.technique_tags

    async def test_extra_tag_registers_extra_techniques(self, mock_adversarial_target):
        init = TechniqueInitializer()
        init.params = {"tags": ["extra"]}
        await init.initialize_async()

        names = set(AttackTechniqueRegistry.get_registry_singleton().instances.get_names())
        assert {"pair", "violent_durian"} <= names

    async def test_all_tag_registers_everything(self, mock_adversarial_target):
        init = TechniqueInitializer()
        init.params = {"tags": ["all"]}
        await init.initialize_async()

        names = set(AttackTechniqueRegistry.get_registry_singleton().instances.get_names())
        assert (set(CORE_TECHNIQUE_NAMES) | set(EXTRA_TECHNIQUE_NAMES)) <= names

    async def test_persona_factories_carry_seed_technique(self, mock_adversarial_target):
        init = TechniqueInitializer()
        await init.initialize_async()

        factories = AttackTechniqueRegistry.get_registry_singleton().get_factories()
        for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
            assert factories[name].seed_technique is not None

    async def test_idempotent(self, mock_adversarial_target):
        """Calling initialize_async twice does not duplicate or overwrite entries."""
        init = TechniqueInitializer()
        await init.initialize_async()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        first_names = set(registry.instances.get_names())
        first_factory = registry.get_factories()["crescendo_movie_director"]

        await init.initialize_async()
        second_names = set(registry.instances.get_names())
        second_factory = registry.get_factories()["crescendo_movie_director"]

        assert first_names == second_names
        assert first_factory is second_factory

    async def test_falls_back_to_default_target_when_registry_empty(self):
        """With no 'adversarial_chat' in TargetRegistry, lazy resolution at create()-time
        falls back to OpenAIChatTarget(temperature=1.2).
        """
        fallback_target = MagicMock(spec=PromptTarget)
        with patch(
            "pyrit.scenario.core.scenario_target_defaults.OpenAIChatTarget",
            return_value=fallback_target,
        ) as mock_openai:
            init = TechniqueInitializer()
            await init.initialize_async()

            mock_openai.assert_not_called()

            registry = AttackTechniqueRegistry.get_registry_singleton()
            factories = registry.get_factories()
            for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES:
                config = factories[name]._build_adversarial_config()
                assert config.target is fallback_target

            mock_openai.assert_any_call(temperature=1.2)


# ---------------------------------------------------------------------------
# Violent Durian (opt-in extra technique)
# ---------------------------------------------------------------------------


class TestViolentDurianTechnique:
    """Tests for the opt-in violent_durian entry in the extra catalog."""

    @staticmethod
    def _violent_durian_factory():
        return next(f for f in build_technique_factories(groups=["extra"]) if f.name == "violent_durian")

    def test_in_extra_catalog(self):
        names = {f.name for f in build_technique_factories(groups=["extra"])}
        assert "violent_durian" in names

    def test_tagged_extra_not_core_or_default(self):
        factory = self._violent_durian_factory()
        assert "core" not in factory.technique_tags
        assert "default" not in factory.technique_tags
        assert set(factory.technique_tags) == {"multi_turn", "extra"}

    def test_uses_red_teaming_attack_with_adversarial(self):
        factory = self._violent_durian_factory()
        assert factory.attack_class is RedTeamingAttack
        assert factory.uses_adversarial is True

    def test_has_max_turns_three(self):
        factory = self._violent_durian_factory()
        assert factory._attack_kwargs == {"max_turns": 3}

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

    async def test_registered_when_extra_selected(self, mock_adversarial_target):
        init = TechniqueInitializer()
        init.params = {"tags": ["extra"]}
        await init.initialize_async()

        assert "violent_durian" in set(AttackTechniqueRegistry.get_registry_singleton().instances.get_names())


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestTechniqueInitializerDiscovery:
    """Tests that the initializer is auto-discovered by InitializerRegistry."""

    def test_initializer_is_discovered(self):
        from pyrit.registry import InitializerRegistry

        registry = InitializerRegistry()
        names = set(registry.get_class_names())
        assert "technique" in names
