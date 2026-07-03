# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Cyber scenario (refactored to technique registry pattern)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import RedTeamingAttack
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConfiguration
from pyrit.scenario.scenarios.airt.cyber import Cyber
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _strategy_class():
    """Get the dynamically-generated CyberStrategy class."""
    from pyrit.scenario.scenarios.airt.cyber import _build_cyber_strategy

    return _build_cyber_strategy()


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
    """Reset registries, populate scenario factories, and clear cached strategy class.

    Registers a mock adversarial target under ``adversarial_chat`` in
    ``TargetRegistry`` so ``build_scenario_technique_factories`` can resolve
    it without falling back to ``OpenAIChatTarget`` (which would require
    central memory).
    """
    from pyrit.registry import TargetRegistry
    from pyrit.scenario.scenarios.airt.cyber import _build_cyber_strategy

    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cyber_strategy.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    target_registry = TargetRegistry.get_registry_singleton()
    target_registry.instances.register(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_scenario_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cyber_strategy.cache_clear()


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


def _make_seed_groups(name: str) -> list[SeedAttackGroup]:
    """Create two seed attack groups for a given category."""
    return [
        SeedAttackGroup(seeds=[SeedObjective(value=f"{name} objective 1"), SeedPrompt(value=f"{name} prompt 1")]),
        SeedAttackGroup(seeds=[SeedObjective(value=f"{name} objective 2"), SeedPrompt(value=f"{name} prompt 2")]),
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

    def test_get_strategy_class(self):
        strat = _strategy_class()
        assert Cyber()._strategy_class is strat

    def test_get_default_strategy_returns_all(self):
        strat = _strategy_class()
        assert Cyber()._default_strategy == strat.ALL

    def test_default_dataset_config_has_malware_dataset(self):
        config = Cyber()._default_dataset_config
        assert isinstance(config, DatasetConfiguration)
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
    async def test_initialization_defaults_to_all_strategy(
        self,
        _mock_groups,
        mock_objective_target,
        mock_objective_scorer,
    ):
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        await scenario.initialize_async(objective_target=mock_objective_target)
        # ALL expands to red_teaming (the only registered Cyber technique); a
        # PromptSendingAttack baseline is added separately via the baseline
        # policy, not as a strategy.
        assert len(scenario._scenario_strategies) == 1

    async def test_initialize_raises_when_no_datasets(self, mock_objective_target, mock_objective_scorer):
        """Dataset resolution fails from empty memory."""
        scenario = Cyber(objective_scorer=mock_objective_scorer)
        # Neutralize the provider fetch so the empty-memory path raises loudly instead of fetching
        # the real default dataset from the provider.
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            with pytest.raises(ValueError, match="could not be loaded"):
                await scenario.initialize_async(objective_target=mock_objective_target)

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
        await scenario.initialize_async(objective_target=mock_objective_target, memory_labels=labels)
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
        await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=20)
        assert scenario._max_concurrency == 20


# ===========================================================================
# Attack generation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberAttackGeneration:
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
        groups = seed_groups or {"malware": _make_seed_groups("malware")}
        with patch.object(
            DatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = Cyber(objective_scorer=mock_objective_scorer)
            init_kwargs = {"objective_target": mock_objective_target, "include_baseline": False}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            return scenario._atomic_attacks

    async def test_all_strategy_produces_red_teaming(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class().ALL],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RedTeamingAttack}

    async def test_multi_turn_strategy_produces_red_teaming(self, mock_objective_target, mock_objective_scorer):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[_strategy_class().MULTI_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RedTeamingAttack}

    async def test_default_strategy_produces_red_teaming(self, mock_objective_target, mock_objective_scorer):
        """Default (ALL) should produce RedTeaming. PromptSendingAttack baseline is
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
            strategies=[_strategy_class()("red_teaming")],
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
            strategies=[_strategy_class()("red_teaming")],
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
    """Tests for CyberStrategy lazy resolution from __init__.py."""

    def test_cyber_strategy_resolves_from_module(self):
        from pyrit.scenario.scenarios.airt import CyberStrategy

        assert CyberStrategy is _strategy_class()


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
        registry.register_from_factories(build_scenario_technique_factories())
        registry.register_from_factories(build_scenario_technique_factories())
        assert len([n for n in registry.instances.get_names() if n == "red_teaming"]) == 1
