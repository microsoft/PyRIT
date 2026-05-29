# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated. Will be removed in 0.16.0 along with the corresponding
``include_default_baseline`` / ``include_baseline`` constructor shims in
``Scenario`` and its subclasses (``Cyber``, ``Jailbreak``, ``Scam``,
``RedTeamAgent``, ``Encoding``).
"""

import warnings
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import BaselineAttackPolicy, Scenario, ScenarioStrategy
from pyrit.score import TrueFalseScorer

_TEST_SCORER_ID = ComponentIdentifier(class_name="MockScorer", class_module="tests.unit.scenarios")


class _LegacyStrategy(ScenarioStrategy):
    TEST = ("test", {"concrete"})
    ALL = ("all", {"all"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _LegacyScenario(Scenario):
    """Minimal Scenario stand-in for exercising the deprecated baseline kwargs."""

    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    def __init__(self, **kwargs):
        kwargs.setdefault("strategy_class", _LegacyStrategy)
        kwargs.setdefault("default_strategy", _LegacyStrategy.ALL)
        kwargs.setdefault("default_dataset_config", DatasetConfiguration())
        if "objective_scorer" not in kwargs:
            mock_scorer = MagicMock(spec=TrueFalseScorer)
            mock_scorer.get_identifier.return_value = _TEST_SCORER_ID
            mock_scorer.get_scorer_metrics.return_value = None
            kwargs["objective_scorer"] = mock_scorer
        kwargs.setdefault("version", 1)
        super().__init__(**kwargs)

    async def _get_atomic_attacks_async(self):
        atomic_attacks = []
        if self._include_baseline:
            groups_by_dataset = self._dataset_config.get_seed_attack_groups()
            all_seed_groups = [g for groups in groups_by_dataset.values() for g in groups]
            atomic_attacks.append(self._build_baseline_atomic_attack(seed_groups=all_seed_groups))
        return atomic_attacks


@pytest.fixture
def mock_objective_target():
    target = MagicMock()
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockTarget", class_module="test")
    return target


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioBaseDeprecation:
    """Cover the deprecated ``Scenario(include_default_baseline=...)`` base kwarg."""

    def test_base_kwarg_emits_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scenario = _LegacyScenario(include_default_baseline=False)

        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecations) == 1
        msg = str(deprecations[0].message)
        assert "include_default_baseline" in msg
        assert "0.16.0" in msg
        assert scenario._legacy_include_baseline is False

    def test_base_kwarg_omitted_emits_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scenario = _LegacyScenario()

        assert not any(issubclass(w.category, DeprecationWarning) for w in caught)
        assert scenario._legacy_include_baseline is None

    async def test_legacy_value_drives_initialize_when_runtime_kwarg_omitted(self, mock_objective_target):
        """Constructor-time False suppresses the baseline that BASELINE_ATTACK_POLICY=Enabled would add."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            scenario = _LegacyScenario(include_default_baseline=False)

        with patch.object(_LegacyScenario, "default_dataset_config", create=True, return_value=DatasetConfiguration()):
            await scenario.initialize_async(objective_target=mock_objective_target)

        assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)

    async def test_runtime_kwarg_wins_over_legacy_value(self, mock_objective_target):
        """Explicit runtime include_baseline overrides any constructor-time legacy value."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            scenario = _LegacyScenario(include_default_baseline=True)

        with patch.object(_LegacyScenario, "default_dataset_config", create=True, return_value=DatasetConfiguration()):
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)

        assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)


class TestSubclassBaselineKwargDeprecation:
    """Cover the deprecated ``include_baseline`` constructor kwarg on user-facing subclasses."""

    @pytest.fixture(autouse=True)
    def _populate_registry(self):
        """Populate the technique registry so Cyber/RapidResponse-style subclasses can build their strategy enum."""
        from pyrit.prompt_target import PromptTarget
        from pyrit.registry import TargetRegistry
        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
        from pyrit.scenario.scenarios.airt.cyber import Cyber
        from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories

        AttackTechniqueRegistry.reset_instance()
        TargetRegistry.reset_instance()
        Cyber._cached_strategy_class = None

        adv_target = MagicMock(spec=PromptTarget)
        adv_target.capabilities.includes.return_value = True
        TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")

        AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_scenario_technique_factories())
        yield
        AttackTechniqueRegistry.reset_instance()
        TargetRegistry.reset_instance()
        Cyber._cached_strategy_class = None

    @pytest.mark.parametrize(
        "import_path, class_name, needs_adversarial_chat",
        [
            ("pyrit.scenario.scenarios.airt.cyber", "Cyber", False),
            ("pyrit.scenario.scenarios.airt.jailbreak", "Jailbreak", False),
            ("pyrit.scenario.scenarios.airt.scam", "Scam", True),
            ("pyrit.scenario.scenarios.garak.encoding", "Encoding", False),
        ],
    )
    def test_subclass_kwarg_emits_deprecation_warning(
        self, import_path, class_name, needs_adversarial_chat, patch_central_database
    ):
        from pyrit.prompt_target import PromptTarget
        from pyrit.score import TrueFalseScorer

        module = __import__(import_path, fromlist=[class_name])
        cls = getattr(module, class_name)

        # Spec'd against TrueFalseScorer so AttackScoringConfig validators accept it.
        mock_scorer = MagicMock(spec=TrueFalseScorer)
        mock_scorer.get_identifier.return_value = _TEST_SCORER_ID
        mock_scorer.get_scorer_metrics.return_value = None

        extra_kwargs = {}
        if needs_adversarial_chat:
            mock_target = MagicMock(spec=PromptTarget)
            mock_target.get_identifier.return_value = ComponentIdentifier(class_name="MockTarget", class_module="test")
            extra_kwargs["adversarial_chat"] = mock_target

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scenario = cls(objective_scorer=mock_scorer, include_baseline=False, **extra_kwargs)

        deprecations = [
            w for w in caught if issubclass(w.category, DeprecationWarning) and class_name in str(w.message)
        ]
        assert len(deprecations) >= 1, f"{class_name} did not emit a DeprecationWarning naming the class"
        assert "0.16.0" in str(deprecations[0].message)
        assert scenario._legacy_include_baseline is False


@pytest.mark.usefixtures("patch_central_database")
class TestLegacyAndRuntimePathsEquivalentUnderMaxDatasetSize:
    """ADO 9012: the deprecated constructor path and the new initialize_async path must
    produce the same baseline atomic attack under max_dataset_size."""

    async def test_paths_produce_matching_objective_sets(self, mock_objective_target):
        from pyrit.models import SeedGroup, SeedObjective

        seed_groups = [SeedGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(10)]

        # Both paths share the same patched sample, so each scenario's single
        # resolution call returns ``stable_sample``.
        stable_sample = seed_groups[:3]

        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample",
            return_value=stable_sample,
        ):
            config_legacy = DatasetConfiguration(seed_groups=seed_groups, max_dataset_size=3)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                legacy = _LegacyScenario(include_default_baseline=True)
            await legacy.initialize_async(objective_target=mock_objective_target, dataset_config=config_legacy)

            config_runtime = DatasetConfiguration(seed_groups=seed_groups, max_dataset_size=3)
            runtime = _LegacyScenario()
            await runtime.initialize_async(
                objective_target=mock_objective_target,
                dataset_config=config_runtime,
                include_baseline=True,
            )

        assert legacy._atomic_attacks[0].atomic_attack_name == "baseline"
        assert runtime._atomic_attacks[0].atomic_attack_name == "baseline"
        assert set(legacy._atomic_attacks[0].objectives) == set(runtime._atomic_attacks[0].objectives)
