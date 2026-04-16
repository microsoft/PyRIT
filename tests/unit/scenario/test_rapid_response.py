# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RapidResponse scenario (refactored from ContentHarms)."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry, TechniqueSpec
from pyrit.scenario import ScenarioCompositeStrategy
from pyrit.scenario.core.scenario_techniques import (
    SCENARIO_TECHNIQUES,
    ScenarioTechniqueRegistrar,
    get_default_adversarial_target,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse,
    RapidResponseStrategy,
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
    """Reset the AttackTechniqueRegistry and TargetRegistry singletons between tests."""
    from pyrit.registry import TargetRegistry

    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()


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
        SeedAttackGroup(
            seeds=[SeedObjective(value=f"{name} objective 1"), SeedPrompt(value=f"{name} prompt 1")]
        ),
        SeedAttackGroup(
            seeds=[SeedObjective(value=f"{name} objective 2"), SeedPrompt(value=f"{name} prompt 2")]
        ),
    ]


ALL_HARM_CATEGORIES = ["hate", "fairness", "violence", "sexual", "harassment", "misinformation", "leakage"]

ALL_HARM_SEED_GROUPS = {cat: _make_seed_groups(cat) for cat in ALL_HARM_CATEGORIES}


FIXTURES = ["patch_central_database", "mock_runtime_env"]


# ===========================================================================
# Strategy enum tests
# ===========================================================================


class TestRapidResponseStrategy:
    """Tests for the RapidResponseStrategy enum."""

    def test_technique_members_exist(self):
        """All four technique members are accessible."""
        assert RapidResponseStrategy.PromptSending.value == "prompt_sending"
        assert RapidResponseStrategy.RolePlay.value == "role_play"
        assert RapidResponseStrategy.ManyShot.value == "many_shot"
        assert RapidResponseStrategy.TAP.value == "tap"

    def test_aggregate_members_exist(self):
        """All four aggregate members are accessible."""
        assert RapidResponseStrategy.ALL.value == "all"
        assert RapidResponseStrategy.DEFAULT.value == "default"
        assert RapidResponseStrategy.SINGLE_TURN.value == "single_turn"
        assert RapidResponseStrategy.MULTI_TURN.value == "multi_turn"

    def test_total_member_count(self):
        """4 aggregates + 4 techniques = 8 members."""
        assert len(list(RapidResponseStrategy)) == 8

    def test_non_aggregate_count(self):
        """get_all_strategies returns only the 4 technique members."""
        non_aggregate = RapidResponseStrategy.get_all_strategies()
        assert len(non_aggregate) == 4

    def test_aggregate_tags(self):
        tags = RapidResponseStrategy.get_aggregate_tags()
        assert tags == {"all", "default", "single_turn", "multi_turn"}

    def test_default_expands_to_prompt_sending_and_many_shot(self):
        """DEFAULT aggregate should expand to PromptSending + ManyShot."""
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.DEFAULT})
        values = {s.value for s in expanded}
        assert values == {"prompt_sending", "many_shot"}

    def test_single_turn_expands_to_prompt_sending_and_role_play(self):
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.SINGLE_TURN})
        values = {s.value for s in expanded}
        assert values == {"prompt_sending", "role_play"}

    def test_multi_turn_expands_to_many_shot_and_tap(self):
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.MULTI_TURN})
        values = {s.value for s in expanded}
        assert values == {"many_shot", "tap"}

    def test_all_expands_to_all_techniques(self):
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.ALL})
        values = {s.value for s in expanded}
        assert values == {"prompt_sending", "role_play", "many_shot", "tap"}

    def test_strategy_values_are_unique(self):
        values = [s.value for s in RapidResponseStrategy]
        assert len(values) == len(set(values))

    def test_invalid_strategy_value_raises(self):
        with pytest.raises(ValueError):
            RapidResponseStrategy("nonexistent")

    def test_invalid_strategy_name_raises(self):
        with pytest.raises(KeyError):
            RapidResponseStrategy["Nonexistent"]


# ===========================================================================
# Initialization / class-level tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRapidResponseBasic:
    """Tests for RapidResponse initialization and class properties."""

    def test_version_is_2(self):
        assert RapidResponse.VERSION == 2

    def test_get_strategy_class(self):
        assert RapidResponse.get_strategy_class() is RapidResponseStrategy

    def test_get_default_strategy_returns_default(self):
        assert RapidResponse.get_default_strategy() == RapidResponseStrategy.DEFAULT

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
    def test_initialization_minimal(self, mock_get_scorer, mock_adversarial_target, mock_objective_scorer):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target)
        assert scenario._adversarial_chat == mock_adversarial_target
        assert scenario.name == "RapidResponse"

    def test_initialization_with_custom_scorer(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario._objective_scorer == mock_objective_scorer

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    def test_no_adversarial_chat_stored_when_not_provided(self, mock_get_scorer, mock_objective_scorer):
        """When adversarial_chat is not provided, it stays None (factories own the default)."""
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse()
        assert scenario._adversarial_chat is None

    @pytest.mark.asyncio
    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=ALL_HARM_SEED_GROUPS)
    async def test_initialization_defaults_to_default_strategy(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target)
        await scenario.initialize_async(objective_target=mock_objective_target)
        # DEFAULT expands to PromptSending + ManyShot → 2 composites
        assert len(scenario._scenario_composites) == 2

    @pytest.mark.asyncio
    async def test_initialize_raises_when_no_datasets(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Dataset resolution fails from empty memory."""
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)

    @pytest.mark.asyncio
    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=ALL_HARM_SEED_GROUPS)
    async def test_memory_labels_stored(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        labels = {"test_run": "123"}
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target)
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
        mock_adversarial_target,
        mock_objective_scorer,
        strategies: list[RapidResponseStrategy] | None = None,
        seed_groups: dict[str, list[SeedAttackGroup]] | None = None,
    ):
        """Helper: initialize scenario and return atomic attacks."""
        groups = seed_groups or {"hate": _make_seed_groups("hate")}
        with patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups):
            scenario = RapidResponse(
                adversarial_chat=mock_adversarial_target,
                objective_scorer=mock_objective_scorer,
            )
            init_kwargs = {"objective_target": mock_objective_target}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            return await scenario._get_atomic_attacks_async()

    @pytest.mark.asyncio
    async def test_default_strategy_produces_prompt_sending_and_many_shot(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {PromptSendingAttack, ManyShotJailbreakAttack}

    @pytest.mark.asyncio
    async def test_single_turn_strategy_produces_prompt_sending_and_role_play(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.SINGLE_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {PromptSendingAttack, RolePlayAttack}

    @pytest.mark.asyncio
    async def test_multi_turn_strategy_produces_many_shot_and_tap(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.MULTI_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {ManyShotJailbreakAttack, TreeOfAttacksWithPruningAttack}

    @pytest.mark.asyncio
    async def test_all_strategy_produces_all_four_techniques(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.ALL],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {
            PromptSendingAttack,
            RolePlayAttack,
            ManyShotJailbreakAttack,
            TreeOfAttacksWithPruningAttack,
        }

    @pytest.mark.asyncio
    async def test_single_technique_selection(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.PromptSending],
        )
        assert len(attacks) > 0
        for a in attacks:
            assert isinstance(a.attack_technique.attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attack_count_is_techniques_times_datasets(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """With 2 datasets and DEFAULT (2 techniques), expect 4 atomic attacks."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        # DEFAULT = PromptSending + ManyShot = 2 techniques, 2 datasets → 4
        assert len(attacks) == 4

    @pytest.mark.asyncio
    async def test_atomic_attack_names_group_by_harm_category(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """_build_atomic_attack_name groups by dataset (harm category), not technique."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        names = {a.atomic_attack_name for a in attacks}
        assert names == {"hate", "violence"}

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="Scenario not properly initialized"):
            await scenario._get_atomic_attacks_async()

    @pytest.mark.asyncio
    async def test_unknown_technique_skipped_with_warning(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
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
            patch.object(ScenarioTechniqueRegistrar, "register"),
        ):
            scenario = RapidResponse(
                adversarial_chat=mock_adversarial_target,
                objective_scorer=mock_objective_scorer,
            )
            # Select ALL which includes role_play, many_shot, tap — none have factories
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[RapidResponseStrategy.ALL],
            )
            attacks = await scenario._get_atomic_attacks_async()
            # Only prompt_sending should have produced attacks
            assert len(attacks) == 1
            assert isinstance(attacks[0].attack_technique.attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attacks_include_seed_groups(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Each atomic attack carries the correct seed groups."""
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.PromptSending],
        )
        for a in attacks:
            assert len(a.objectives) > 0


# ===========================================================================
# _build_atomic_attack_name tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildAtomicAttackName:
    def test_rapid_response_groups_by_seed_group_name(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        result = scenario._build_atomic_attack_name(technique_name="prompt_sending", seed_group_name="hate")
        assert result == "hate"

    def test_rapid_response_ignores_technique_name(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        r1 = scenario._build_atomic_attack_name(technique_name="prompt_sending", seed_group_name="hate")
        r2 = scenario._build_atomic_attack_name(technique_name="tap", seed_group_name="hate")
        assert r1 == r2 == "hate"


# ===========================================================================
# Core techniques factory tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCoreTechniques:
    """Tests for shared AttackTechniqueFactory builders in scenario_techniques.py."""

    def test_instance_returns_all_four_factories(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target, objective_scorer=mock_objective_scorer)
        factories = scenario.get_attack_technique_factories()
        assert set(factories.keys()) == {"prompt_sending", "role_play", "many_shot", "tap"}
        assert factories["prompt_sending"].attack_class is PromptSendingAttack
        assert factories["role_play"].attack_class is RolePlayAttack
        assert factories["many_shot"].attack_class is ManyShotJailbreakAttack
        assert factories["tap"].attack_class is TreeOfAttacksWithPruningAttack

    def test_factories_use_default_adversarial_when_none(self, mock_objective_scorer):
        """When no adversarial_chat is passed, factories use get_default_adversarial_target."""
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario.get_attack_technique_factories()
        # role_play and tap should have attack_adversarial_config baked in
        assert "attack_adversarial_config" in factories["role_play"]._attack_kwargs
        assert "attack_adversarial_config" in factories["tap"]._attack_kwargs

    def test_factories_use_custom_adversarial_when_provided(self, mock_adversarial_target, mock_objective_scorer):
        """When adversarial_chat is provided, the registrar bakes it into factories."""
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target, objective_scorer=mock_objective_scorer)
        factories = scenario.get_attack_technique_factories()

        # The registrar bakes the custom adversarial target directly into factories
        rp_kwargs = factories["role_play"]._attack_kwargs
        assert rp_kwargs["attack_adversarial_config"].target is mock_adversarial_target

        tap_kwargs = factories["tap"]._attack_kwargs
        assert tap_kwargs["attack_adversarial_config"].target is mock_adversarial_target


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

        assert ContentHarmsStrategy is RapidResponseStrategy

    def test_content_harms_instance_name_is_rapid_response(self, mock_adversarial_target, mock_objective_scorer):
        """ContentHarms() creates a RapidResponse with name 'RapidResponse'."""
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        scenario = ContentHarms(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario.name == "RapidResponse"
        assert isinstance(scenario, RapidResponse)


# ===========================================================================
# Registry integration tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRegistryIntegration:
    """Tests for AttackTechniqueRegistry wiring via ScenarioTechniqueRegistrar."""

    def test_registrar_populates_registry(self, mock_adversarial_target):
        """After calling register(), all 4 techniques are in registry."""
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.get_names())
        assert names == {"prompt_sending", "role_play", "many_shot", "tap"}

    def test_registrar_idempotent(self, mock_adversarial_target):
        """Calling register() twice doesn't duplicate entries."""
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert len(registry) == 4

    def test_registrar_preserves_custom(self, mock_adversarial_target):
        """Pre-registered custom techniques aren't overwritten."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        custom_factory = AttackTechniqueFactory(attack_class=PromptSendingAttack)
        registry.register_technique(name="role_play", factory=custom_factory, tags=["custom"])

        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()

        # role_play should still be the custom factory
        factories = registry.get_factories()
        assert factories["role_play"] is custom_factory
        # Other 3 should have been registered normally
        assert len(factories) == 4

    def test_get_factories_returns_dict(self, mock_adversarial_target):
        """get_factories() returns a dict of name → factory."""
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert isinstance(factories, dict)
        assert set(factories.keys()) == {"prompt_sending", "role_play", "many_shot", "tap"}
        assert factories["prompt_sending"].attack_class is PromptSendingAttack

    def test_scenario_base_class_reads_from_registry(self, mock_adversarial_target, mock_objective_scorer):
        """Scenario.get_attack_technique_factories() triggers registration and reads from registry."""
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target, objective_scorer=mock_objective_scorer)
        factories = scenario.get_attack_technique_factories()

        # Should have all 4 core techniques from the registry
        assert set(factories.keys()) == {"prompt_sending", "role_play", "many_shot", "tap"}

        # Registry should also have them
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert set(registry.get_names()) == {"prompt_sending", "role_play", "many_shot", "tap"}

    def test_tags_assigned_correctly(self, mock_adversarial_target):
        """Core techniques have correct tags (single_turn / multi_turn)."""
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        registry = AttackTechniqueRegistry.get_registry_singleton()

        single_turn = {e.name for e in registry.get_by_tag(tag="single_turn")}
        multi_turn = {e.name for e in registry.get_by_tag(tag="multi_turn")}

        assert single_turn == {"prompt_sending", "role_play"}
        assert multi_turn == {"many_shot", "tap"}


# ===========================================================================
# ScenarioTechniqueRegistrar tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestScenarioTechniqueRegistrar:
    """Tests for the declarative ScenarioTechniqueRegistrar class."""

    def test_registrar_populates_all_four_techniques(self):
        """Registrar with default adversarial registers all 4 techniques."""
        ScenarioTechniqueRegistrar().register()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert set(registry.get_names()) == {"prompt_sending", "role_play", "many_shot", "tap"}

    def test_registrar_with_custom_adversarial(self, mock_adversarial_target):
        """Custom adversarial_chat is baked into adversarial-needing factories."""
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()

        # role_play and tap should have the mock adversarial target baked in
        rp_kwargs = factories["role_play"]._attack_kwargs
        assert rp_kwargs["attack_adversarial_config"].target is mock_adversarial_target

        tap_kwargs = factories["tap"]._attack_kwargs
        assert tap_kwargs["attack_adversarial_config"].target is mock_adversarial_target

    def test_registrar_idempotent(self, mock_adversarial_target):
        """Calling register() twice does not duplicate or overwrite entries."""
        registrar = ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target)
        registrar.register()
        registrar.register()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert len(registry) == 4

    def test_registrar_preserves_custom_preregistered(self, mock_adversarial_target):
        """Pre-registered custom techniques are not overwritten by registrar."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        custom_factory = AttackTechniqueFactory(attack_class=PromptSendingAttack)
        registry.register_technique(name="role_play", factory=custom_factory, tags=["custom"])

        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        # role_play should still be the custom factory
        assert registry.get_factories()["role_play"] is custom_factory
        assert len(registry) == 4

    def test_registrar_assigns_correct_tags(self, mock_adversarial_target):
        """Tags from TechniqueSpec are applied correctly."""
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register()
        registry = AttackTechniqueRegistry.get_registry_singleton()

        single_turn = {e.name for e in registry.get_by_tag(tag="single_turn")}
        multi_turn = {e.name for e in registry.get_by_tag(tag="multi_turn")}
        assert single_turn == {"prompt_sending", "role_play"}
        assert multi_turn == {"many_shot", "tap"}

    def test_registrar_custom_techniques_list(self, mock_adversarial_target):
        """Registrar accepts a custom list of TechniqueSpecs."""
        custom_specs = [
            TechniqueSpec(name="custom_attack", attack_class=PromptSendingAttack, tags=["custom"]),
        ]
        ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target).register(techniques=custom_specs)
        registry = AttackTechniqueRegistry.get_registry_singleton()
        assert set(registry.get_names()) == {"custom_attack"}

    def test_registrar_adversarial_lazy_resolution(self):
        """Adversarial target is not resolved until register() accesses it."""
        registrar = ScenarioTechniqueRegistrar()
        # No env var resolution yet — just creating the registrar
        assert registrar._adversarial_chat is None

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


# ===========================================================================
# TechniqueSpec tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestTechniqueSpec:
    """Tests for the TechniqueSpec dataclass."""

    def test_simple_spec(self):
        spec = TechniqueSpec(name="test", attack_class=PromptSendingAttack, tags=["single_turn"])
        assert spec.name == "test"
        assert spec.attack_class is PromptSendingAttack
        assert spec.tags == ["single_turn"]
        assert spec.extra_kwargs_builder is None

    def test_extra_kwargs_builder(self, mock_adversarial_target):
        builder = lambda _adv: {"role_play_definition_path": "/custom/path.yaml"}
        spec = TechniqueSpec(
            name="complex",
            attack_class=RolePlayAttack,
            tags=["single_turn"],
            extra_kwargs_builder=builder,
        )
        registrar = ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target)
        factory = registrar.build_factory(spec)
        assert factory._attack_kwargs["role_play_definition_path"] == "/custom/path.yaml"
        assert "attack_adversarial_config" in factory._attack_kwargs

    def test_build_factory_no_adversarial(self, mock_adversarial_target):
        """Non-adversarial spec should not have attack_adversarial_config."""
        spec = TechniqueSpec(name="simple", attack_class=PromptSendingAttack, tags=[])
        registrar = ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target)
        factory = registrar.build_factory(spec)
        assert "attack_adversarial_config" not in (factory._attack_kwargs or {})

    def test_SCENARIO_TECHNIQUES_list_has_four_entries(self):
        assert len(SCENARIO_TECHNIQUES) == 4
        names = {s.name for s in SCENARIO_TECHNIQUES}
        assert names == {"prompt_sending", "role_play", "many_shot", "tap"}

    def test_frozen_spec(self):
        """TechniqueSpec is frozen (immutable)."""
        spec = TechniqueSpec(name="test", attack_class=PromptSendingAttack)
        with pytest.raises(AttributeError):
            spec.name = "modified"

    def test_adversarial_auto_detected_from_signature(self, mock_adversarial_target):
        """Adversarial config is injected based on attack class signature, not a manual flag."""
        registrar = ScenarioTechniqueRegistrar(adversarial_chat=mock_adversarial_target)

        # RolePlayAttack accepts attack_adversarial_config → should be injected
        rp_spec = TechniqueSpec(name="rp", attack_class=RolePlayAttack, tags=[])
        rp_factory = registrar.build_factory(rp_spec)
        assert "attack_adversarial_config" in rp_factory._attack_kwargs

        # PromptSendingAttack does NOT accept it → should not be injected
        ps_spec = TechniqueSpec(name="ps", attack_class=PromptSendingAttack, tags=[])
        ps_factory = registrar.build_factory(ps_spec)
        assert "attack_adversarial_config" not in (ps_factory._attack_kwargs or {})
