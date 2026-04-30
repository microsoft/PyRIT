# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RapidResponse scenario (refactored from ContentHarms)."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import (
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry, AttackTechniqueSpec
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario_techniques import (
    SCENARIO_TECHNIQUES,
    build_scenario_techniques,
    get_default_adversarial_target,
    register_scenario_techniques,
)
from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse,
)
from pyrit.score import TrueFalseScorer

# ---------------------------------------------------------------------------
# Synthetic many-shot examples — prevents reading the real JSON during tests
# ---------------------------------------------------------------------------
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"test question {i}", "answer": f"test answer {i}"} for i in range(100)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _strategy_class():
    """Get the dynamically-generated RapidResponseStrategy class."""
    return RapidResponse.get_strategy_class()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_adversarial_target():
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = _mock_id("MockAdversarialTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset the AttackTechniqueRegistry, TargetRegistry, and cached strategy class between tests."""
    from pyrit.registry import TargetRegistry

    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    RapidResponse._cached_strategy_class = None
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    RapidResponse._cached_strategy_class = None


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    """Prevent ManyShotJailbreakAttack from loading the full bundled dataset."""
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


@pytest.fixture
def mock_runtime_env():
    """Set minimal env vars needed for OpenAIChatTarget fallback via @apply_defaults."""
    with patch.dict(
        "os.environ",
        {
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


def _make_seed_groups(name: str) -> list[SeedAttackGroup]:
    """Create two seed attack groups for a given category."""
    return [
        SeedAttackGroup(seeds=[SeedObjective(value=f"{name} objective 1"), SeedPrompt(value=f"{name} prompt 1")]),
        SeedAttackGroup(seeds=[SeedObjective(value=f"{name} objective 2"), SeedPrompt(value=f"{name} prompt 2")]),
    ]


ALL_HARM_CATEGORIES = ["hate", "fairness", "violence", "sexual", "harassment", "misinformation", "leakage"]

ALL_HARM_SEED_GROUPS = {cat: _make_seed_groups(cat) for cat in ALL_HARM_CATEGORIES}


FIXTURES = ["patch_central_database", "mock_runtime_env"]


# ===========================================================================
# Initialization / class-level tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRapidResponseBasic:
    """Tests for RapidResponse initialization and class properties."""

    def test_version_is_2(self):
        assert RapidResponse.VERSION == 2

    def test_get_strategy_class(self):
        strat = _strategy_class()
        assert RapidResponse.get_strategy_class() is strat

    def test_get_default_strategy_returns_default(self):
        strat = _strategy_class()
        assert RapidResponse.get_default_strategy() == strat.DEFAULT

    def test_default_dataset_config_has_all_harm_datasets(self):
        config = RapidResponse.default_dataset_config()
        assert isinstance(config, DatasetConfiguration)
        names = config.get_default_dataset_names()
        expected = [f"airt_{cat}" for cat in ALL_HARM_CATEGORIES]
        for name in expected:
            assert name in names
        assert len(names) == 7

    def test_default_dataset_config_max_dataset_size(self):
        config = RapidResponse.default_dataset_config()
        assert config.max_dataset_size == 4

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    def test_initialization_minimal(self, mock_get_scorer, mock_objective_scorer):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse()
        assert scenario.name == "RapidResponse"

    def test_initialization_with_custom_scorer(self, mock_objective_scorer):
        scenario = RapidResponse(
            objective_scorer=mock_objective_scorer,
        )
        assert scenario._objective_scorer == mock_objective_scorer

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=ALL_HARM_SEED_GROUPS)
    async def test_initialization_defaults_to_default_strategy(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse()
        await scenario.initialize_async(objective_target=mock_objective_target)
        # DEFAULT expands to PromptSending + ManyShot → 2 composites
        assert len(scenario._scenario_strategies) == 2

    async def test_initialize_raises_when_no_datasets(self, mock_objective_target, mock_objective_scorer):
        """Dataset resolution fails from empty memory."""
        scenario = RapidResponse(
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=ALL_HARM_SEED_GROUPS)
    async def test_memory_labels_stored(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        labels = {"test_run": "123"}
        scenario = RapidResponse()
        await scenario.initialize_async(objective_target=mock_objective_target, memory_labels=labels)
        assert scenario._memory_labels == labels

    @pytest.mark.parametrize("harm_category", ALL_HARM_CATEGORIES)
    def test_harm_category_prompt_file_exists(self, harm_category):
        harm_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
        assert (harm_path / f"{harm_category}.prompt").exists()


# ===========================================================================
# Attack generation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRapidResponseAttackGeneration:
    """Tests for _get_atomic_attacks_async with various strategies."""

    async def _init_and_get_attacks(
        self,
        *,
        mock_objective_target,
        mock_objective_scorer,
        strategies=None,
        seed_groups: dict[str, list[SeedAttackGroup]] | None = None,
    ):
        """Helper: initialize scenario and return atomic attacks."""
        groups = seed_groups or {"hate": _make_seed_groups("hate")}
        with patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups):
            scenario = RapidResponse(
                objective_scorer=mock_objective_scorer,
            )
            init_kwargs = {"objective_target": mock_objective_target}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            return await scenario._get_atomic_attacks_async()

    async def test_default_strategy_produces_prompt_sending_and_many_shot(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {PromptSendingAttack, ManyShotJailbreakAttack}

    async def test_single_turn_strategy_produces_prompt_sending_and_role_play(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class().SINGLE_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {PromptSendingAttack, RolePlayAttack}

    async def test_multi_turn_strategy_produces_multi_turn_attacks(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class().MULTI_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert len(technique_classes) >= 2
        assert {ManyShotJailbreakAttack, TreeOfAttacksWithPruningAttack} <= technique_classes

    async def test_all_strategy_produces_attacks_for_every_technique(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class().ALL],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        # Should include all known core techniques
        assert {
            PromptSendingAttack,
            RolePlayAttack,
            ManyShotJailbreakAttack,
            TreeOfAttacksWithPruningAttack,
        } <= technique_classes

    async def test_single_technique_selection(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class()("prompt_sending")],
        )
        assert len(attacks) > 0
        for a in attacks:
            assert isinstance(a.attack_technique.attack, PromptSendingAttack)

    async def test_attack_count_is_techniques_times_datasets(self, mock_objective_target, mock_objective_scorer):
        """With 2 datasets and DEFAULT (2 techniques), expect 4 atomic attacks."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        # DEFAULT = PromptSending + ManyShot = 2 techniques, 2 datasets → 4
        assert len(attacks) == 4

    async def test_atomic_attack_names_are_unique_compound_keys(self, mock_objective_target, mock_objective_scorer):
        """Each AtomicAttack has a unique compound atomic_attack_name for resume correctness."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        names = [a.atomic_attack_name for a in attacks]
        # All names must be unique
        assert len(names) == len(set(names))
        # Names are compound: technique_dataset
        for name in names:
            assert "_" in name

    async def test_display_groups_by_harm_category(self, mock_objective_target, mock_objective_scorer):
        """display_group groups by dataset (harm category), not technique."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        display_groups = {a.display_group for a in attacks}
        assert display_groups == {"hate", "violence"}

    async def test_raises_when_not_initialized(self, mock_objective_scorer):
        scenario = RapidResponse(
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="Scenario not properly initialized"):
            await scenario._get_atomic_attacks_async()

    async def test_unknown_technique_skipped_with_warning(self, mock_objective_target, mock_objective_scorer):
        """If a technique name has no factory, it's skipped (not an error)."""
        groups = {"hate": _make_seed_groups("hate")}

        # Register only prompt_sending in the registry — the other techniques
        # (role_play, many_shot, tap) won't have factories.
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_technique(
            name="prompt_sending",
            factory=AttackTechniqueFactory(attack_class=PromptSendingAttack),
            tags=["single_turn"],
        )

        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups),
            patch(
                "pyrit.scenario.core.scenario_techniques.register_scenario_techniques",
            ),
        ):
            scenario = RapidResponse(
                objective_scorer=mock_objective_scorer,
            )
            # Select ALL which includes role_play, many_shot, tap — none have factories
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[_strategy_class().ALL],
            )
            attacks = await scenario._get_atomic_attacks_async()
            # Only prompt_sending should have produced attacks
            assert len(attacks) == 1
            assert isinstance(attacks[0].attack_technique.attack, PromptSendingAttack)

    async def test_attacks_include_seed_groups(self, mock_objective_target, mock_objective_scorer):
        """Each atomic attack carries the correct seed groups."""
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class()("prompt_sending")],
        )
        for a in attacks:
            assert len(a.objectives) > 0


# ===========================================================================
# _build_display_group tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildDisplayGroup:
    def test_rapid_response_groups_by_seed_group_name(self, mock_objective_scorer):
        scenario = RapidResponse(
            objective_scorer=mock_objective_scorer,
        )
        result = scenario._build_display_group(technique_name="prompt_sending", seed_group_name="hate")
        assert result == "hate"

    def test_rapid_response_ignores_technique_name(self, mock_objective_scorer):
        scenario = RapidResponse(
            objective_scorer=mock_objective_scorer,
        )
        r1 = scenario._build_display_group(technique_name="prompt_sending", seed_group_name="hate")
        r2 = scenario._build_display_group(technique_name="tap", seed_group_name="hate")
        assert r1 == r2 == "hate"


# ===========================================================================
# Core techniques factory tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCoreTechniques:
    """Tests for shared AttackTechniqueFactory builders in scenario_techniques.py."""

    def test_instance_returns_all_factories(self, mock_objective_scorer):
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()
        assert {"prompt_sending", "role_play", "many_shot", "tap"} <= set(factories.keys())
        assert factories["prompt_sending"].attack_class is PromptSendingAttack
        assert factories["role_play"].attack_class is RolePlayAttack
        assert factories["many_shot"].attack_class is ManyShotJailbreakAttack
        assert factories["tap"].attack_class is TreeOfAttacksWithPruningAttack

    def test_factories_use_default_adversarial_when_none(self, mock_objective_scorer):
        """Factories use get_default_adversarial_target for adversarial config."""
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()
        # role_play and tap should have adversarial_config as first-class field
        assert factories["role_play"]._adversarial_config is not None
        assert factories["tap"]._adversarial_config is not None

    def test_factories_always_use_default_adversarial(self, mock_objective_scorer):
        """Registry always bakes default adversarial target from get_default_adversarial_target."""
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()

        # Factories have an adversarial config as first-class field
        assert factories["role_play"]._adversarial_config is not None
        assert factories["tap"]._adversarial_config is not None


# ===========================================================================
# Deprecated alias tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestDeprecatedAliases:
    """Tests for backward-compatible ContentHarms aliases."""

    def test_content_harms_is_rapid_response(self):
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        assert ContentHarms is RapidResponse

    def test_content_harms_strategy_is_rapid_response_strategy(self):
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarmsStrategy

        assert ContentHarmsStrategy is _strategy_class()

    def test_content_harms_instance_name_is_rapid_response(self, mock_objective_scorer):
        """ContentHarms() creates a RapidResponse with name 'RapidResponse'."""
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        scenario = ContentHarms(
            objective_scorer=mock_objective_scorer,
        )
        assert scenario.name == "RapidResponse"
        assert isinstance(scenario, RapidResponse)


# ===========================================================================
# Registry integration tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRegistryIntegration:
    """Tests for AttackTechniqueRegistry wiring via register_scenario_techniques."""

    def test_register_populates_registry(self, mock_adversarial_target):
        """After calling register_scenario_techniques(), all 4 techniques are in registry."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.get_names())
        assert {"prompt_sending", "role_play", "many_shot", "tap"} <= names

    def test_register_idempotent(self, mock_adversarial_target):
        """Calling register_scenario_techniques() twice doesn't duplicate entries."""
        register_scenario_techniques()
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert len(registry) == len(SCENARIO_TECHNIQUES)

    def test_register_preserves_custom(self, mock_adversarial_target):
        """Pre-registered custom techniques aren't overwritten."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        custom_factory = AttackTechniqueFactory(attack_class=PromptSendingAttack)
        registry.register_technique(name="role_play", factory=custom_factory, tags=["custom"])

        register_scenario_techniques()

        # role_play should still be the custom factory
        factories = registry.get_factories()
        assert factories["role_play"] is custom_factory
        assert len(factories) == len(SCENARIO_TECHNIQUES)

    def test_get_factories_returns_dict(self, mock_adversarial_target):
        """get_factories() returns a dict of name → factory."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert isinstance(factories, dict)
        assert {"prompt_sending", "role_play", "many_shot", "tap"} <= set(factories.keys())
        assert factories["prompt_sending"].attack_class is PromptSendingAttack

    def test_scenario_base_class_reads_from_registry(self, mock_objective_scorer):
        """Scenario._get_attack_technique_factories() triggers registration and reads from registry."""
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()

        # Should have all core techniques from the registry
        assert {"prompt_sending", "role_play", "many_shot", "tap"} <= set(factories.keys())

        # Registry should also have them
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert {"prompt_sending", "role_play", "many_shot", "tap"} <= set(registry.get_names())

    def test_tags_assigned_correctly(self, mock_adversarial_target):
        """Core techniques have correct tags (single_turn / multi_turn)."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()

        single_turn = {e.name for e in registry.get_by_tag(tag="single_turn")}
        multi_turn = {e.name for e in registry.get_by_tag(tag="multi_turn")}

        assert {"prompt_sending", "role_play"} <= single_turn
        assert {"many_shot", "tap"} <= multi_turn


# ===========================================================================
# Registration and factory-from-spec tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRegistrationAndFactoryFromSpec:
    """Tests for register_scenario_techniques and AttackTechniqueRegistry.build_factory_from_spec."""

    def test_register_populates_all_techniques(self):
        """register_scenario_techniques registers all catalog techniques."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registered = set(registry.get_names())
        expected = {s.name for s in SCENARIO_TECHNIQUES}
        assert registered == expected

    def test_register_with_custom_adversarial_uses_default(self, mock_adversarial_target):
        """Registry always bakes default adversarial target, not caller-specific."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()

        # role_play and tap should have an adversarial config as first-class field
        assert factories["role_play"]._adversarial_config is not None
        assert factories["tap"]._adversarial_config is not None

    def test_register_idempotent(self, mock_adversarial_target):
        """Calling register_scenario_techniques() twice does not duplicate or overwrite entries."""
        register_scenario_techniques()
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert len(registry) == len(SCENARIO_TECHNIQUES)

    def test_register_preserves_custom_preregistered(self, mock_adversarial_target):
        """Pre-registered custom techniques are not overwritten."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        custom_factory = AttackTechniqueFactory(attack_class=PromptSendingAttack)
        registry.register_technique(name="role_play", factory=custom_factory, tags=["custom"])

        register_scenario_techniques()
        # role_play should still be the custom factory
        assert registry.get_factories()["role_play"] is custom_factory
        assert len(registry) == len(SCENARIO_TECHNIQUES)

    def test_register_assigns_correct_tags(self, mock_adversarial_target):
        """Tags from AttackTechniqueSpec are applied correctly."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()

        single_turn = {e.name for e in registry.get_by_tag(tag="single_turn")}
        multi_turn = {e.name for e in registry.get_by_tag(tag="multi_turn")}
        assert {"prompt_sending", "role_play"} <= single_turn
        assert {"many_shot", "tap"} <= multi_turn

    def test_register_from_specs_custom_list(self, mock_adversarial_target):
        """register_from_specs accepts a custom list of AttackTechniqueSpecs."""
        custom_specs = [
            AttackTechniqueSpec(name="custom_attack", attack_class=PromptSendingAttack, strategy_tags=["custom"]),
        ]
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_specs(custom_specs)
        assert set(registry.get_names()) == {"custom_attack"}

    def test_get_default_adversarial_target_from_registry(self, mock_adversarial_target):
        """get_default_adversarial_target returns registry entry when available."""
        from pyrit.registry import TargetRegistry

        target_registry = TargetRegistry.get_registry_singleton()
        target_registry.register(name="adversarial_chat", instance=mock_adversarial_target)
        result = get_default_adversarial_target()
        assert result is mock_adversarial_target

    def test_get_default_adversarial_target_fallback(self):
        """get_default_adversarial_target falls back to OpenAIChatTarget when not in registry."""
        result = get_default_adversarial_target()
        assert isinstance(result, OpenAIChatTarget)
        assert result._temperature == 1.2

    def test_get_default_adversarial_target_capability_check(self):
        """get_default_adversarial_target rejects targets without multi-turn support."""
        from pyrit.registry import TargetRegistry

        target_registry = TargetRegistry.get_registry_singleton()
        # Register a plain PromptTarget (lacks multi-turn capability)
        mock_target = MagicMock(spec=PromptTarget)
        mock_target.capabilities.includes.return_value = False
        target_registry.register(name="adversarial_chat", instance=mock_target)
        with pytest.raises(ValueError, match="must support multi-turn"):
            get_default_adversarial_target()


# ===========================================================================
# build_scenario_techniques tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildScenarioTechniques:
    """Tests for build_scenario_techniques() — the runtime spec transform."""

    def test_returns_same_count_as_static_catalog(self):
        specs = build_scenario_techniques()
        assert len(specs) == len(SCENARIO_TECHNIQUES)

    def test_adversarial_specs_get_target(self):
        specs = build_scenario_techniques()
        by_name = {s.name: s for s in specs}
        assert by_name["role_play"].adversarial_chat is not None
        assert by_name["tap"].adversarial_chat is not None

    def test_non_adversarial_specs_unchanged(self):
        specs = build_scenario_techniques()
        by_name = {s.name: s for s in specs}
        assert by_name["prompt_sending"].adversarial_chat is None
        assert by_name["many_shot"].adversarial_chat is None

    def test_crescendo_simulated_has_seed_technique(self):
        """crescendo_simulated spec declares a seed_technique."""
        by_name = {s.name: s for s in SCENARIO_TECHNIQUES}
        spec = by_name["crescendo_simulated"]
        assert spec.seed_technique is not None

    def test_crescendo_simulated_factory_has_adversarial_chat(self, mock_adversarial_target):
        """After build_scenario_techniques, crescendo_simulated gets adversarial_chat from default."""
        register_scenario_techniques()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        factory = factories["crescendo_simulated"]
        assert factory.adversarial_chat is not None

    def test_extra_kwargs_preserved(self):
        specs = build_scenario_techniques()
        by_name = {s.name: s for s in specs}
        assert "role_play_definition_path" in by_name["role_play"].extra_kwargs

    def test_derived_from_static_catalog(self):
        """build_scenario_techniques is a transform of SCENARIO_TECHNIQUES — names match."""
        runtime_names = {s.name for s in build_scenario_techniques()}
        static_names = {s.name for s in SCENARIO_TECHNIQUES}
        assert runtime_names == static_names

    def test_adversarial_chat_key_resolves_from_registry(self, mock_adversarial_target):
        """When adversarial_chat_key is set, it resolves the target from TargetRegistry."""
        from pyrit.registry import TargetRegistry

        registry = TargetRegistry.get_registry_singleton()
        registry.register_instance(mock_adversarial_target, name="custom_adversarial")

        original = SCENARIO_TECHNIQUES.copy()
        custom_spec = AttackTechniqueSpec(
            name="tap",
            attack_class=TreeOfAttacksWithPruningAttack,
            strategy_tags=["core", "multi_turn"],
            adversarial_chat_key="custom_adversarial",
        )
        try:
            SCENARIO_TECHNIQUES.clear()
            SCENARIO_TECHNIQUES.append(custom_spec)

            specs = build_scenario_techniques()
            assert specs[0].adversarial_chat is mock_adversarial_target
        finally:
            SCENARIO_TECHNIQUES.clear()
            SCENARIO_TECHNIQUES.extend(original)

    def test_adversarial_chat_key_missing_raises(self):
        """When adversarial_chat_key references a missing registry entry, ValueError is raised."""
        original = SCENARIO_TECHNIQUES.copy()
        custom_spec = AttackTechniqueSpec(
            name="tap",
            attack_class=TreeOfAttacksWithPruningAttack,
            strategy_tags=["core", "multi_turn"],
            adversarial_chat_key="nonexistent_key",
        )
        try:
            SCENARIO_TECHNIQUES.clear()
            SCENARIO_TECHNIQUES.append(custom_spec)

            with pytest.raises(ValueError, match="no such entry exists in TargetRegistry"):
                build_scenario_techniques()
        finally:
            SCENARIO_TECHNIQUES.clear()
            SCENARIO_TECHNIQUES.extend(original)


# ===========================================================================
# AttackTechniqueSpec tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestAttackTechniqueSpec:
    """Tests for the AttackTechniqueSpec dataclass."""

    def test_simple_spec(self):
        spec = AttackTechniqueSpec(name="test", attack_class=PromptSendingAttack, strategy_tags=["single_turn"])
        assert spec.name == "test"
        assert spec.attack_class is PromptSendingAttack
        assert spec.strategy_tags == ["single_turn"]
        assert spec.adversarial_chat is None
        assert spec.extra_kwargs == {}

    def test_extra_kwargs(self, mock_adversarial_target):
        spec = AttackTechniqueSpec(
            name="complex",
            attack_class=RolePlayAttack,
            strategy_tags=["single_turn"],
            adversarial_chat=mock_adversarial_target,
            extra_kwargs={"role_play_definition_path": "/custom/path.yaml"},
        )
        factory = AttackTechniqueRegistry.build_factory_from_spec(spec)
        assert factory._attack_kwargs["role_play_definition_path"] == "/custom/path.yaml"
        assert factory._adversarial_config is not None

    def test_build_factory_no_adversarial_injected_when_attack_does_not_accept_it(self, mock_adversarial_target):
        """adversarial config is stored on factory but not injected into attack_kwargs for non-adversarial attacks."""
        spec = AttackTechniqueSpec(
            name="simple",
            attack_class=PromptSendingAttack,
            strategy_tags=[],
            adversarial_chat=mock_adversarial_target,
        )
        factory = AttackTechniqueRegistry.build_factory_from_spec(spec)
        # Config is stored as first-class field (available via factory.adversarial_chat)
        assert factory._adversarial_config is not None
        # But NOT injected into attack_kwargs since PromptSendingAttack doesn't accept it
        assert "attack_adversarial_config" not in (factory._attack_kwargs or {})

    def test_extra_kwargs_reserved_key_raises(self):
        """attack_adversarial_config must not appear in extra_kwargs."""
        spec = AttackTechniqueSpec(
            name="bad",
            attack_class=RolePlayAttack,
            strategy_tags=[],
            extra_kwargs={"attack_adversarial_config": "oops"},
        )
        with pytest.raises(ValueError, match="attack_adversarial_config"):
            AttackTechniqueRegistry.build_factory_from_spec(spec)

    def test_adversarial_config_rejected_in_attack_kwargs(self):
        """attack_adversarial_config in attack_kwargs raises ValueError at factory construction."""
        with pytest.raises(ValueError, match="attack_adversarial_config"):
            AttackTechniqueFactory(
                attack_class=RolePlayAttack,
                attack_kwargs={"attack_adversarial_config": "oops"},
            )

    def test_scenario_techniques_list_nonempty_with_unique_names(self):
        assert len(SCENARIO_TECHNIQUES) >= 1
        names = [s.name for s in SCENARIO_TECHNIQUES]
        assert len(names) == len(set(names)), "Duplicate technique names in SCENARIO_TECHNIQUES"

    def test_frozen_spec(self):
        """AttackTechniqueSpec is frozen (immutable)."""
        spec = AttackTechniqueSpec(name="test", attack_class=PromptSendingAttack)
        with pytest.raises(AttributeError):
            spec.name = "modified"

    def test_adversarial_injected_when_attack_accepts_it(self, mock_adversarial_target):
        """Adversarial config is injected based on attack class signature."""
        # RolePlayAttack accepts attack_adversarial_config → injected as first-class field
        rp_spec = AttackTechniqueSpec(
            name="rp", attack_class=RolePlayAttack, strategy_tags=[], adversarial_chat=mock_adversarial_target
        )
        rp_factory = AttackTechniqueRegistry.build_factory_from_spec(rp_spec)
        assert rp_factory._adversarial_config is not None

        # PromptSendingAttack does NOT accept it → config stored but not in attack_kwargs
        ps_spec = AttackTechniqueSpec(
            name="ps", attack_class=PromptSendingAttack, strategy_tags=[], adversarial_chat=mock_adversarial_target
        )
        ps_factory = AttackTechniqueRegistry.build_factory_from_spec(ps_spec)
        assert ps_factory._adversarial_config is not None
        assert "attack_adversarial_config" not in (ps_factory._attack_kwargs or {})

    def test_adversarial_chat_and_key_both_set_raises(self, mock_adversarial_target):
        """Setting both adversarial_chat and adversarial_chat_key raises ValueError at construction."""
        with pytest.raises(ValueError, match="mutually exclusive"):
            AttackTechniqueSpec(
                name="tap",
                attack_class=TreeOfAttacksWithPruningAttack,
                strategy_tags=["core", "multi_turn"],
                adversarial_chat=mock_adversarial_target,
                adversarial_chat_key="some_key",
            )
