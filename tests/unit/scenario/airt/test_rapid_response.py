# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RapidResponse scenario (refactored from ContentHarms)."""

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import (
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import (
    build_technique_factories,
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


def _technique_class():
    """Get the dynamically-generated RapidResponseTechnique class."""
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique

    return _build_rapid_response_technique()


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
    ``build_technique_factories`` does not fall back to
    ``OpenAIChatTarget``.
    """
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique

    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_rapid_response_technique.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_rapid_response_technique.cache_clear()


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


def _make_seed_groups(name: str) -> list[AttackSeedGroup]:
    """Create two seed attack groups for a given category.

    Groups are objective-only so they stay compatible with simulated-conversation
    techniques (e.g. context_compliance, role_play_*), which generate their own
    prepended conversation and reject seed groups that already carry a prompt at
    sequence 0.
    """
    return [
        AttackSeedGroup(seeds=[SeedObjective(value=f"{name} objective 1")]),
        AttackSeedGroup(seeds=[SeedObjective(value=f"{name} objective 2")]),
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

    def test_get_technique_class(self, mock_objective_scorer):
        strat = _technique_class()
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            assert RapidResponse()._technique_class is strat

    def test_get_default_technique_returns_default(self, mock_objective_scorer):
        strat = _technique_class()
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            assert RapidResponse()._default_technique == strat.DEFAULT

    def test_default_dataset_config_has_all_harm_datasets(self, mock_objective_scorer):
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            config = RapidResponse()._default_dataset_config
        assert isinstance(config, CompoundDatasetAttackConfiguration)
        names = config.dataset_names
        expected = [f"airt_{cat}" for cat in ALL_HARM_CATEGORIES]
        for name in expected:
            assert name in names
        assert len(names) == 7

    def test_default_dataset_config_max_dataset_size(self, mock_objective_scorer):
        with patch(
            "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer", return_value=mock_objective_scorer
        ):
            config = RapidResponse()._default_dataset_config
        assert all(child.max_dataset_size == 4 for child in config._configurations)

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
    @patch.object(
        CompoundDatasetAttackConfiguration,
        "get_attack_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value=ALL_HARM_SEED_GROUPS,
    )
    async def test_initialization_defaults_to_default_technique(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse()
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()
        # DEFAULT expands to PromptSending + ManyShot → 2 composites
        assert len(scenario._scenario_techniques) == 2

    async def test_initialize_raises_when_no_datasets(self, mock_objective_target, mock_objective_scorer):
        """Dataset resolution fails from empty memory."""
        scenario = RapidResponse(
            objective_scorer=mock_objective_scorer,
        )
        # Neutralize the provider fetch so the empty-memory path raises loudly instead of fetching
        # the real default dataset from the provider.
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(ValueError, match="could not be loaded"):
                await scenario.initialize_async()

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(
        CompoundDatasetAttackConfiguration,
        "get_attack_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value=ALL_HARM_SEED_GROUPS,
    )
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
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "memory_labels": labels,
            }
        )
        await scenario.initialize_async()
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
    """Tests for _get_atomic_attacks_async with various techniques."""

    async def _init_and_get_attacks(
        self,
        *,
        mock_objective_target,
        mock_objective_scorer,
        techniques=None,
        seed_groups: dict[str, list[AttackSeedGroup]] | None = None,
    ):
        """Helper: initialize scenario and return atomic attacks."""
        groups = seed_groups or {"hate": _make_seed_groups("hate")}
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = RapidResponse(
                objective_scorer=mock_objective_scorer,
            )
            init_kwargs = {"objective_target": mock_objective_target, "include_baseline": False}
            if techniques:
                init_kwargs["scenario_techniques"] = techniques
            scenario.set_params_from_args(args=init_kwargs)
            await scenario.initialize_async()
            return scenario._atomic_attacks

    async def test_default_technique_produces_role_play_and_many_shot(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        # role_play_movie_script is now a simulated-conversation PromptSendingAttack.
        assert ManyShotJailbreakAttack in technique_classes
        assert any(
            a.attack_technique.seed_technique is not None
            and a.attack_technique.seed_technique.has_simulated_conversation
            for a in attacks
        )

    async def test_single_turn_technique_produces_single_turn_attacks(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class().SINGLE_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        # Every core technique tagged ``single_turn`` in the scenario-technique catalog must appear.
        # context_compliance and role_play_* are now simulated-conversation PromptSendingAttacks,
        # so assert on the simulated-conversation seed rather than a dedicated class.
        assert PromptSendingAttack in technique_classes
        assert any(
            a.attack_technique.seed_technique is not None
            and a.attack_technique.seed_technique.has_simulated_conversation
            for a in attacks
        )
        # And no multi-turn-only attack should leak in.
        assert ManyShotJailbreakAttack not in technique_classes
        assert TreeOfAttacksWithPruningAttack not in technique_classes

    async def test_multi_turn_technique_produces_multi_turn_attacks(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class().MULTI_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert len(technique_classes) >= 2
        assert {ManyShotJailbreakAttack, TreeOfAttacksWithPruningAttack} <= technique_classes

    async def test_all_technique_produces_attacks_for_every_technique(
        self, mock_objective_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class().ALL],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        # Should include all known core techniques. context_compliance and role_play_* variants
        # are simulated-conversation PromptSendingAttacks, asserted via the seed technique.
        assert {
            ManyShotJailbreakAttack,
            TreeOfAttacksWithPruningAttack,
        } <= technique_classes
        assert any(
            a.attack_technique.seed_technique is not None
            and a.attack_technique.seed_technique.has_simulated_conversation
            for a in attacks
        )

    async def test_single_technique_selection(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class()("role_play_movie_script")],
        )
        assert len(attacks) > 0
        for a in attacks:
            assert isinstance(a.attack_technique.attack, PromptSendingAttack)
            assert a.attack_technique.seed_technique is not None
            assert a.attack_technique.seed_technique.has_simulated_conversation

    async def test_technique_converters_are_threaded_to_factory_create(
        self, mock_objective_target, mock_objective_scorer
    ):
        """``technique_converters`` passed to ``initialize_async`` reach ``factory.create`` for the keyed technique."""
        from pyrit.converter import Base64Converter

        strat = _technique_class()
        role_play = strat("role_play_movie_script")
        converter = Base64Converter()
        captured: list[object] = []
        original_create = AttackTechniqueFactory.create

        def _spy_create(self, **kwargs):
            captured.append(kwargs.get("extra_request_converters"))
            return original_create(self, **kwargs)

        groups = {"hate": _make_seed_groups("hate")}
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackTechniqueFactory, "create", _spy_create),
        ):
            scenario = RapidResponse(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "include_baseline": False,
                    "scenario_techniques": [role_play],
                    "technique_converters": {role_play.value: [converter]},
                }
            )
            await scenario.initialize_async()

        # ROLE_PLAY was selected with a converter modifier, so every resulting factory.create
        # call must receive the extra request converter.
        assert captured
        for extra in captured:
            assert extra is not None
            assert len(extra) == 1

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
            scenario._build_scenario_context(seed_groups_by_dataset={})

    async def test_unknown_technique_skipped_with_warning(self, mock_objective_target, mock_objective_scorer):
        """If a technique name has no factory, it's skipped (not an error)."""
        groups = {"hate": _make_seed_groups("hate")}

        # Reset the registry and register only prompt_sending — the other techniques
        # (role_play, many_shot, tap) won't have factories.
        AttackTechniqueRegistry.reset_registry_singleton()
        RapidResponse._cached_technique_class = None
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_technique(
            name="prompt_sending",
            factory=AttackTechniqueFactory(
                name="prompt_sending",
                attack_class=PromptSendingAttack,
                technique_tags=["core", "single_turn"],
            ),
            tags=["core", "single_turn"],
        )

        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = RapidResponse(
                objective_scorer=mock_objective_scorer,
            )
            # Select ALL which includes role_play, many_shot, tap — none have factories
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [_technique_class().ALL],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            attacks = scenario._atomic_attacks
            # Only prompt_sending should have produced attacks
            assert len(attacks) == 1
            assert isinstance(attacks[0].attack_technique.attack, PromptSendingAttack)

    async def test_attacks_include_seed_groups(self, mock_objective_target, mock_objective_scorer):
        """Each atomic attack carries the correct seed groups."""
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class()("role_play_movie_script")],
        )
        for a in attacks:
            assert len(a.objectives) > 0


# ===========================================================================
# Core techniques factory tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCoreTechniques:
    """Tests for shared AttackTechniqueFactory builders in techniques/core.py."""

    def test_instance_returns_all_factories(self, mock_objective_scorer):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert {"role_play_movie_script", "many_shot", "tap"} <= set(factories.keys())
        assert factories["role_play_movie_script"].attack_class is PromptSendingAttack
        assert factories["many_shot"].attack_class is ManyShotJailbreakAttack
        assert factories["tap"].attack_class is TreeOfAttacksWithPruningAttack

    def test_factories_use_default_adversarial_when_none(self, mock_objective_scorer):
        """Factories that need an adversarial chat mark themselves as adversarial.

        The default adversarial target is resolved lazily inside ``create()``;
        it is not baked into the factory at construction time.
        """
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert factories["role_play_movie_script"].uses_adversarial is True
        assert factories["tap"].uses_adversarial is True
        assert factories["role_play_movie_script"]._adversarial_chat is None
        assert factories["tap"]._adversarial_chat is None

    def test_factories_always_use_default_adversarial(self, mock_objective_scorer):
        """Factories defer adversarial wiring to create()-time lazy resolution."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()

        assert factories["role_play_movie_script"]._adversarial_chat is None
        assert factories["tap"]._adversarial_chat is None


# ===========================================================================
# ===========================================================================
# Registry integration tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRegistryIntegration:
    """Tests for AttackTechniqueRegistry wiring via build_technique_factories."""

    def test_registry_populated_by_autouse_fixture(self):
        """The autouse fixture registers all canonical scenario techniques."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        names = set(registry.instances.get_names())
        assert {"role_play_movie_script", "many_shot", "tap"} <= names

    def test_register_from_factories_idempotent(self):
        """Calling register_from_factories twice does not duplicate entries."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        expected = len(build_technique_factories())
        registry.register_from_factories(build_technique_factories())
        assert len(registry.instances) == expected

    def test_register_preserves_custom_preregistered(self):
        """Pre-registered custom techniques are not overwritten by re-registration."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        custom_factory = AttackTechniqueFactory(name="role_play_movie_script", attack_class=PromptSendingAttack)
        registry.register_technique(name="role_play_movie_script", factory=custom_factory, tags=["custom"])

        registry.register_from_factories(build_technique_factories())
        assert registry.get_factories()["role_play_movie_script"] is custom_factory

    def test_get_factories_returns_dict(self):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert isinstance(factories, dict)
        assert {"role_play_movie_script", "many_shot", "tap"} <= set(factories.keys())
        assert factories["role_play_movie_script"].attack_class is PromptSendingAttack

    def test_tags_assigned_correctly(self):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        single_turn = {e.name for e in registry.instances.get_by_tag(tag="single_turn")}
        multi_turn = {e.name for e in registry.instances.get_by_tag(tag="multi_turn")}
        assert {"role_play_movie_script"} <= single_turn
        assert {"many_shot", "tap"} <= multi_turn


# ===========================================================================
# build_technique_factories tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildScenarioTechniqueFactories:
    """Tests for build_technique_factories() — the canonical factory catalog."""

    def test_returns_nonempty_factory_list(self):
        factories = build_technique_factories()
        assert len(factories) >= 4
        names = [f.name for f in factories]
        assert len(names) == len(set(names)), "Duplicate technique names"

    def test_adversarial_factories_have_adversarial_config(self):
        """Factories that need an adversarial chat advertise it via uses_adversarial.

        The config itself is resolved lazily at create()-time.
        """
        by_name = {f.name: f for f in build_technique_factories()}
        assert by_name["role_play_movie_script"].uses_adversarial is True
        assert by_name["tap"].uses_adversarial is True
        assert by_name["role_play_movie_script"]._adversarial_chat is None
        assert by_name["tap"]._adversarial_chat is None

    def test_non_adversarial_factories_have_no_adversarial_config(self):
        by_name = {f.name: f for f in build_technique_factories()}
        assert by_name["many_shot"]._adversarial_chat is None

    def test_crescendo_simulated_has_seed_technique(self):
        by_name = {f.name: f for f in build_technique_factories()}
        assert by_name["crescendo_simulated"].seed_technique is not None

    def test_crescendo_simulated_has_adversarial_chat(self):
        by_name = {f.name: f for f in build_technique_factories()}
        assert by_name["crescendo_simulated"].uses_adversarial is True

    def test_role_play_movie_script_has_simulated_conversation(self):
        by_name = {f.name: f for f in build_technique_factories()}
        seed_technique = by_name["role_play_movie_script"].seed_technique
        assert seed_technique is not None
        assert seed_technique.has_simulated_conversation


# ===========================================================================
# AttackTechniqueFactory tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestAttackTechniqueFactoryBasics:
    """Tests for the AttackTechniqueFactory construction surface."""

    def test_simple_factory(self):
        factory = AttackTechniqueFactory(name="test", attack_class=PromptSendingAttack, technique_tags=["single_turn"])
        assert factory.name == "test"
        assert factory.attack_class is PromptSendingAttack
        assert factory.technique_tags == ["single_turn"]
        assert factory.adversarial_chat is None

    def test_adversarial_config_rejected_in_attack_kwargs(self):
        """attack_adversarial_config in attack_kwargs raises ValueError at factory construction."""
        with pytest.raises(ValueError, match="attack_adversarial_config"):
            AttackTechniqueFactory(
                name="bad",
                attack_class=PromptSendingAttack,
                attack_kwargs={"attack_adversarial_config": "oops"},
            )
