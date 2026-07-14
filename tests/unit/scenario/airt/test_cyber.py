# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Cyber scenario (refactored to technique registry pattern)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import RedTeamingAttack
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.scenarios.airt.cyber import Cyber
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import (
    build_technique_factories,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _technique_class():
    """Get the dynamically-generated CyberTechnique class."""
    from pyrit.scenario.scenarios.airt.cyber import _build_cyber_technique

    return _build_cyber_technique()


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
    """Reset registries, populate scenario factories, and clear cached technique class.

    Registers a mock adversarial target under ``adversarial_chat`` in
    ``TargetRegistry`` so ``build_technique_factories`` can resolve
    it without falling back to ``OpenAIChatTarget`` (which would require
    central memory).
    """
    from pyrit.registry import TargetRegistry
    from pyrit.scenario.scenarios.airt.cyber import _build_cyber_technique

    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cyber_technique.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    target_registry = TargetRegistry.get_registry_singleton()
    target_registry.instances.register(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cyber_technique.cache_clear()


@pytest.fixture
def mock_runtime_env():
    """Set minimal env vars needed for OpenAIChatTarget fallback via @apply_defaults."""
    with patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


def _make_seed_groups(name: str) -> list[AttackSeedGroup]:
    """Create two seed attack groups for a given category."""
    return [
        AttackSeedGroup(seeds=[SeedObjective(value=f"{name} objective 1"), SeedPrompt(value=f"{name} prompt 1")]),
        AttackSeedGroup(seeds=[SeedObjective(value=f"{name} objective 2"), SeedPrompt(value=f"{name} prompt 2")]),
    ]


FIXTURES = ["patch_central_database", "mock_runtime_env"]


# ===========================================================================
# Initialization / class-level tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberBasic:
    """Tests for Cyber initialization and class properties."""

    def test_version_is_2(self):
        assert Cyber.VERSION == 2

    def test_get_technique_class(self):
        strat = _technique_class()
        assert Cyber()._technique_class is strat

    def test_get_default_technique_returns_default(self):
        strat = _technique_class()
        assert Cyber()._default_technique == strat.DEFAULT

    def test_default_aggregate_expands_to_red_teaming(self):
        """DEFAULT must be non-empty and select the single curated technique.

        Guards against wiring the ``default`` aggregate to a tag ``red_teaming`` lacks
        (e.g. the catalog ``default`` tag), which would silently make DEFAULT empty and
        collapse the default run to baseline-only.
        """
        strat = _technique_class()
        assert "default" in strat.get_aggregate_tags()
        default_members = strat.expand({strat.DEFAULT})
        assert default_members == [strat("red_teaming")]

    def test_default_matches_all(self):
        """DEFAULT must expand to exactly the same techniques as ALL.

        Cyber curates a single technique, so its DEFAULT run is a no-op alias of ALL. Asserting
        equality (not just subset) guards against a future technique landing in ALL but being
        silently excluded from DEFAULT (or vice versa) once the aggregate wiring changes.
        """
        strat = _technique_class()
        assert set(strat.expand({strat.DEFAULT})) == set(strat.expand({strat.ALL}))

    def test_default_dataset_config_has_malware_dataset(self):
        config = Cyber()._default_dataset_config
        # Concrete DatasetAttackConfiguration (not the base) so the scenario's async
        # get_attack_groups_by_dataset_async() resolve path is available.
        assert isinstance(config, DatasetAttackConfiguration)
        names = config.dataset_names
        assert "airt_malware" in names
        assert len(names) == 1

    def test_default_dataset_config_max_dataset_size(self):
        config = Cyber()._default_dataset_config
        assert config.max_dataset_size == 4

    def test_initialization_with_custom_scorer(self, mock_objective_scorer):
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        assert scenario._objective_scorer == mock_objective_scorer

    def test_initialization_with_default_scorer(self):
        scenario = Cyber()
        assert scenario._objective_scorer_identifier is not None

    def test_scenario_name_is_cyber(self, mock_objective_scorer):
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        assert scenario.name == "Cyber"

    @patch.object(
        DatasetAttackConfiguration,
        "get_attack_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value={"malware": _make_seed_groups("malware")},
    )
    async def test_initialization_defaults_to_default_technique(
        self,
        _mock_groups,
        mock_objective_target,
        mock_objective_scorer,
    ):
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()
        # DEFAULT expands to red_teaming (the only registered Cyber technique); a
        # PromptSendingAttack baseline is added separately via the baseline
        # policy, not as a technique.
        assert len(scenario._scenario_techniques) == 1

    async def test_initialize_raises_when_no_datasets(self, mock_objective_target, mock_objective_scorer):
        """Dataset resolution fails from empty memory."""
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        # Neutralize the provider fetch so the empty-memory path raises loudly instead of fetching
        # the real default dataset from the provider.
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(ValueError, match="could not be loaded"):
                await scenario.initialize_async()

    @patch.object(
        DatasetAttackConfiguration,
        "get_attack_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value={"malware": _make_seed_groups("malware")},
    )
    async def test_memory_labels_stored(
        self,
        _mock_groups,
        mock_objective_target,
        mock_objective_scorer,
    ):
        labels = {"test_run": "123"}
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "memory_labels": labels,
            }
        )
        await scenario.initialize_async()
        assert scenario._memory_labels == labels

    @patch.object(
        DatasetAttackConfiguration,
        "get_attack_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value={"malware": _make_seed_groups("malware")},
    )
    async def test_initialize_async_with_max_concurrency(
        self,
        _mock_groups,
        mock_objective_target,
        mock_objective_scorer,
    ):
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 20,
            }
        )
        await scenario.initialize_async()
        assert scenario._max_concurrency == 20


# ===========================================================================
# Attack generation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberAttackGeneration:
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
        groups = seed_groups or {"malware": _make_seed_groups("malware")}
        with patch.object(
            DatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = Cyber(objective_scorer=mock_objective_scorer)
            init_kwargs = {"objective_target": mock_objective_target, "include_baseline": False}
            if techniques:
                init_kwargs["scenario_techniques"] = techniques
            scenario.set_params_from_args(args=init_kwargs)
            await scenario.initialize_async()
            return scenario._atomic_attacks

    async def test_all_technique_produces_red_teaming(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class().ALL],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RedTeamingAttack}

    async def test_multi_turn_technique_produces_red_teaming(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class().MULTI_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RedTeamingAttack}

    async def test_default_technique_produces_red_teaming(self, mock_objective_target, mock_objective_scorer):
        """Default (DEFAULT) should produce RedTeaming. PromptSendingAttack baseline is
        prepended automatically by BaselineAttackPolicy.Enabled when
        include_baseline=True (the helper here uses include_baseline=False)."""
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RedTeamingAttack}

    async def test_single_technique_selection(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class()("red_teaming")],
        )
        assert len(attacks) > 0
        for a in attacks:
            assert isinstance(a.attack_technique.attack, RedTeamingAttack)

    async def test_atomic_attack_names_are_unique(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        names = [a.atomic_attack_name for a in attacks]
        assert len(names) == len(set(names))
        for name in names:
            assert "_" in name

    async def test_attacks_include_seed_groups(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            techniques=[_technique_class()("red_teaming")],
        )
        for a in attacks:
            assert len(a.objectives) > 0

    async def test_raises_when_not_initialized(self, mock_objective_scorer):
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        with pytest.raises(ValueError, match="Scenario not properly initialized"):
            scenario._build_scenario_context(seed_groups_by_dataset={})


# ===========================================================================
# Dynamic export tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberDynamicExport:
    """Tests for CyberTechnique lazy resolution from __init__.py."""

    def test_cyber_technique_resolves_from_module(self):
        from pyrit.scenario.scenarios.airt import CyberTechnique

        assert CyberTechnique is _technique_class()


# ===========================================================================
# Registry integration tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberRegistryIntegration:
    """Tests for attack technique registry wiring via Cyber scenario."""

    def test_cyber_factories_include_red_teaming(self, mock_objective_scorer):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories_or_raise()
        # Cyber selects red_teaming from the registry; the PromptSendingAttack baseline
        # is contributed at runtime by BaselineAttackPolicy.Enabled, not by this dict.
        assert "red_teaming" in factories
        assert factories["red_teaming"].attack_class is RedTeamingAttack

    def test_red_teaming_factory_has_adversarial_config(self, mock_objective_scorer):
        """red_teaming factory advertises uses_adversarial (config resolved lazily at create())."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories_or_raise()
        assert factories["red_teaming"].uses_adversarial is True
        assert factories["red_teaming"]._adversarial_chat is None

    def test_register_idempotent(self):
        """Registering the scenario technique factories twice doesn't duplicate entries."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(build_technique_factories())
        registry.register_from_factories(build_technique_factories())
        assert len([n for n in registry.instances.get_names() if n == "red_teaming"]) == 1
