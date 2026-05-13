# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated. Will be removed in v0.16.0 along with the corresponding
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
from pyrit.scenario.core import BaselineDefaultPolicy, Scenario, ScenarioStrategy
from pyrit.score import Scorer

_TEST_SCORER_ID = ComponentIdentifier(class_name="MockScorer", class_module="tests.unit.scenarios")


class _LegacyStrategy(ScenarioStrategy):
    TEST = ("test", {"concrete"})
    ALL = ("all", {"all"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _LegacyScenario(Scenario):
    """Minimal Scenario stand-in for exercising the deprecated baseline kwargs."""

    BASELINE_DEFAULT_POLICY: ClassVar[BaselineDefaultPolicy] = BaselineDefaultPolicy.Enabled

    def __init__(self, **kwargs):
        kwargs.setdefault("strategy_class", _LegacyStrategy)
        if "objective_scorer" not in kwargs:
            mock_scorer = MagicMock(spec=Scorer)
            mock_scorer.get_identifier.return_value = _TEST_SCORER_ID
            mock_scorer.get_scorer_metrics.return_value = None
            kwargs["objective_scorer"] = mock_scorer
        kwargs.setdefault("version", 1)
        super().__init__(**kwargs)

    @classmethod
    def get_strategy_class(cls):
        return _LegacyStrategy

    @classmethod
    def get_default_strategy(cls):
        return _LegacyStrategy.ALL

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        return DatasetConfiguration()

    async def _get_atomic_attacks_async(self):
        return []


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
        assert "v0.16.0" in msg
        assert scenario._legacy_include_baseline is False

    def test_base_kwarg_omitted_emits_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scenario = _LegacyScenario()

        assert not any(issubclass(w.category, DeprecationWarning) for w in caught)
        assert scenario._legacy_include_baseline is None

    async def test_legacy_value_drives_initialize_when_runtime_kwarg_omitted(self, mock_objective_target):
        """Constructor-time False suppresses the baseline that BASELINE_DEFAULT_POLICY=Enabled would add."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            scenario = _LegacyScenario(include_default_baseline=False)

        with patch.object(_LegacyScenario, "default_dataset_config", return_value=DatasetConfiguration()):
            await scenario.initialize_async(objective_target=mock_objective_target)

        assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)

    async def test_runtime_kwarg_wins_over_legacy_value(self, mock_objective_target):
        """Explicit runtime include_baseline overrides any constructor-time legacy value."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            scenario = _LegacyScenario(include_default_baseline=True)

        with patch.object(_LegacyScenario, "default_dataset_config", return_value=DatasetConfiguration()):
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)

        assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)


class TestSubclassBaselineKwargDeprecation:
    """Cover the deprecated ``include_baseline`` constructor kwarg on user-facing subclasses."""

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
        assert "v0.16.0" in str(deprecations[0].message)
        assert scenario._legacy_include_baseline is False
