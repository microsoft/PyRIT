# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RapidResponse scenario (refactored from ContentHarms)."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import (
    ContextComplianceAttack,
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse,
)
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.components.scenario_techniques import (
    build_scenario_technique_factories,
)

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
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_strategy

    return _build_rapid_response_strategy()


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
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockAdversarialTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset registries, register a mock adversarial target, and populate factories.

    The mock target satisfies the ``adversarial_chat`` slot so
    ``build_scenario_technique_factories`` does not fall back to
    ``OpenAIChatTarget``.
    """
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_strategy

    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    _build_rapid_response_strategy.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_scenario_technique_factories())
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    _build_rapid_response_strategy.cache_clear()


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

    def test_get_strategy_class(self, mock_objective_scorer):
        strat = _strategy_class()
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            assert RapidResponse()._strategy_class is strat

    def test_get_default_strategy_returns_default(self, mock_objective_scorer):
        strat = _strategy_class()
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            assert RapidResponse()._default_strategy == strat.DEFAULT

    def test_default_dataset_config_has_all_harm_datasets(self, mock_objective_scorer):
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            config = RapidResponse()._default_dataset_config
        assert isinstance(config, DatasetConfiguration)
        names = config.get_default_dataset_names()
        expected = [f"airt_{cat}" for cat in ALL_HARM_CATEGORIES]
        for name in expected:
            assert name in names
        assert len(names) == 7

    def test_default_dataset_config_max_dataset_size(self, mock_objective_scorer):
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            config = RapidResponse()._default_dataset_config
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
            init_kwargs = {"objective_target": mock_objective_target, "include_baseline": False}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            return await scenario._get_atomic_attacks_async()

    async def test_default_strategy_produces_role_play_and_many_shot(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RolePlayAttack, ManyShotJailbreakAttack}

    async def test_single_turn_strategy_produces_single_turn_attacks(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class().SINGLE_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        # Every core technique tagged ``single_turn`` in the scenario-technique catalog must appear.
        # PromptSendingAttack is intentionally excluded from the catalog (provided by the baseline
        # policy instead) and include_baseline=False here, so it should not appear.
        assert {RolePlayAttack, ContextComplianceAttack} <= technique_classes
        assert PromptSendingAttack not in technique_classes
        # And no multi-turn-only attack should leak in.
        assert ManyShotJailbreakAttack not in technique_classes
        assert TreeOfAttacksWithPruningAttack not in technique_classes

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
        # Should include all known core techniques. PromptSendingAttack is intentionally
        # excluded from the catalog (provided by the baseline policy instead) and
        # include_baseline=False here, so it should not appear.
        assert {
            RolePlayAttack,
            ManyShotJailbreakAttack,
            TreeOfAttacksWithPruningAttack,
        } <= technique_classes
        assert PromptSendingAttack not in technique_classes

    async def test_single_technique_selection(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class()("role_play")],
        )
        assert len(attacks) > 0
        for a in attacks:
            assert isinstance(a.attack_technique.attack, RolePlayAttack)

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
        # DEFAULT = RolePlay + ManyShot = 2 techniques, 2 datasets → 4
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

        # Reset the registry and register only prompt_sending — the other techniques
        # (role_play, many_shot, tap) won't have factories.
        AttackTechniqueRegistry.reset_instance()
        RapidResponse._cached_strategy_class = None
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_technique(
            name="prompt_sending",
            factory=AttackTechniqueFactory(
                name="prompt_sending",
                attack_class=PromptSendingAttack,
                strategy_tags=["core", "single_turn"],
            ),
            tags=["core", "single_turn"],
        )

        with patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups):
            scenario = RapidResponse(
                objective_scorer=mock_objective_scorer,
            )
            # Select ALL which includes role_play, many_shot, tap — none have factories
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[_strategy_class().ALL],
                include_baseline=False,
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
            strategies=[_strategy_class()("role_play")],
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
        assert {"role_play", "many_shot", "tap"} <= set(factories.keys())
        assert factories["role_play"].attack_class is RolePlayAttack
        assert factories["many_shot"].attack_class is ManyShotJailbreakAttack
        assert factories["tap"].attack_class is TreeOfAttacksWithPruningAttack

    def test_factories_use_default_adversarial_when_none(self, mock_objective_scorer):
        """Factories that need an adversarial chat mark themselves as adversarial.

        The default adversarial target is resolved lazily inside ``create()``;
        it is not baked into the factory at construction time.
        """
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()
        assert factories["role_play"].uses_adversarial is True
        assert factories["tap"].uses_adversarial is True
        assert factories["role_play"]._adversarial_chat is None
        assert factories["tap"]._adversarial_chat is None

    def test_factories_always_use_default_adversarial(self, mock_objective_scorer):
        """Factories defer adversarial wiring to create()-time lazy resolution."""
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()

        assert factories["role_play"]._adversarial_chat is None
        assert factories["tap"]._adversarial_chat is None


# ===========================================================================
# Deprecated alias tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestDeprecatedAliases:
    """Tests for backward-compatible ContentHarms aliases."""

    def test_content_harms_is_rapid_response(self):
        with pytest.warns(DeprecationWarning, match="ContentHarms"):
            from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        assert ContentHarms is RapidResponse

    def test_content_harms_strategy_is_rapid_response_strategy(self):
        with pytest.warns(DeprecationWarning, match="ContentHarmsStrategy"):
            from pyrit.scenario.scenarios.airt.content_harms import ContentHarmsStrategy

        assert ContentHarmsStrategy is _strategy_class()

    def test_content_harms_instance_name_is_rapid_response(self, mock_objective_scorer):
        """ContentHarms() creates a RapidResponse with name 'RapidResponse'."""
        with pytest.warns(DeprecationWarning, match="ContentHarms"):
            from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        scenario = ContentHarms(
            objective_scorer=mock_objective_scorer,
        )
        assert scenario.name == "RapidResponse"
        assert isinstance(scenario, RapidResponse)

    def test_content_harms_via_airt_package_emits_deprecation_warning(self):
        """Importing ``ContentHarms`` from the parent ``airt`` package emits the warning."""
        with pytest.warns(DeprecationWarning, match="ContentHarms"):
            from pyrit.scenario.scenarios.airt import ContentHarms

        assert ContentHarms is RapidResponse

    def test_content_harms_strategy_via_airt_package_emits_deprecation_warning(self):
        """Importing ``ContentHarmsStrategy`` from the parent ``airt`` package emits the warning."""
        with pytest.warns(DeprecationWarning, match="ContentHarmsStrategy"):
            from pyrit.scenario.scenarios.airt import ContentHarmsStrategy

        assert ContentHarmsStrategy is _strategy_class()


# ===========================================================================
# Registry integration tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRegistryIntegration:
    """Tests for AttackTechniqueRegistry wiring via build_scenario_technique_factories."""

    def test_registry_populated_by_autouse_fixture(self):
        """The autouse fixture registers all canonical scenario techniques."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.get_names())
        assert {"role_play", "many_shot", "tap"} <= names

    def test_register_from_factories_idempotent(self):
        """Calling register_from_factories twice does not duplicate entries."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        expected = len(build_scenario_technique_factories())
        registry.register_from_factories(build_scenario_technique_factories())
        assert len(registry) == expected

    def test_register_preserves_custom_preregistered(self):
        """Pre-registered custom techniques are not overwritten by re-registration."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        custom_factory = AttackTechniqueFactory(name="role_play", attack_class=PromptSendingAttack)
        registry.register_technique(name="role_play", factory=custom_factory, tags=["custom"])

        registry.register_from_factories(build_scenario_technique_factories())
        assert registry.get_factories()["role_play"] is custom_factory

    def test_get_factories_returns_dict(self):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert isinstance(factories, dict)
        assert {"role_play", "many_shot", "tap"} <= set(factories.keys())
        assert factories["role_play"].attack_class is RolePlayAttack

    def test_scenario_base_class_reads_from_registry(self, mock_objective_scorer):
        """Scenario._get_attack_technique_factories() reads from the registry."""
        scenario = RapidResponse(objective_scorer=mock_objective_scorer)
        factories = scenario._get_attack_technique_factories()
        assert {"role_play", "many_shot", "tap"} <= set(factories.keys())

    def test_tags_assigned_correctly(self):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        single_turn = {e.name for e in registry.get_by_tag(tag="single_turn")}
        multi_turn = {e.name for e in registry.get_by_tag(tag="multi_turn")}
        assert {"role_play"} <= single_turn
        assert {"many_shot", "tap"} <= multi_turn


# ===========================================================================
# build_scenario_technique_factories tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildScenarioTechniqueFactories:
    """Tests for build_scenario_technique_factories() — the canonical factory catalog."""

    def test_returns_nonempty_factory_list(self):
        factories = build_scenario_technique_factories()
        assert len(factories) >= 4
        names = [f.name for f in factories]
        assert len(names) == len(set(names)), "Duplicate technique names"

    def test_adversarial_factories_have_adversarial_config(self):
        """Factories that need an adversarial chat advertise it via uses_adversarial.

        The config itself is resolved lazily at create()-time.
        """
        by_name = {f.name: f for f in build_scenario_technique_factories()}
        assert by_name["role_play"].uses_adversarial is True
        assert by_name["tap"].uses_adversarial is True
        assert by_name["role_play"]._adversarial_chat is None
        assert by_name["tap"]._adversarial_chat is None

    def test_non_adversarial_factories_have_no_adversarial_config(self):
        by_name = {f.name: f for f in build_scenario_technique_factories()}
        assert by_name["many_shot"]._adversarial_chat is None

    def test_crescendo_simulated_has_seed_technique(self):
        by_name = {f.name: f for f in build_scenario_technique_factories()}
        assert by_name["crescendo_simulated"].seed_technique is not None

    def test_crescendo_simulated_has_adversarial_chat(self):
        by_name = {f.name: f for f in build_scenario_technique_factories()}
        assert by_name["crescendo_simulated"].uses_adversarial is True

    def test_extra_kwargs_preserved_on_role_play(self):
        by_name = {f.name: f for f in build_scenario_technique_factories()}
        assert "role_play_definition_path" in (by_name["role_play"]._attack_kwargs or {})


# ===========================================================================
# AttackTechniqueFactory tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestAttackTechniqueFactoryBasics:
    """Tests for the AttackTechniqueFactory construction surface."""

    def test_simple_factory(self):
        factory = AttackTechniqueFactory(name="test", attack_class=PromptSendingAttack, strategy_tags=["single_turn"])
        assert factory.name == "test"
        assert factory.attack_class is PromptSendingAttack
        assert factory.strategy_tags == ["single_turn"]
        assert factory.adversarial_chat is None

    def test_adversarial_config_rejected_in_attack_kwargs(self):
        """attack_adversarial_config in attack_kwargs raises ValueError at factory construction."""
        with pytest.raises(ValueError, match="attack_adversarial_config"):
            AttackTechniqueFactory(
                name="bad",
                attack_class=RolePlayAttack,
                attack_kwargs={"attack_adversarial_config": "oops"},
            )
