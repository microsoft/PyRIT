# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for Foundry scenario APIs used by azure-ai-evaluation.

The azure-ai-evaluation red team module uses the Foundry framework for modern attack execution:
- FoundryExecutionManager creates FoundryScenario instances per risk category
- StrategyMapper maps AttackStrategy enum → FoundryStrategy
- DatasetConfigurationBuilder produces DatasetConfiguration from RAI objectives
- ScenarioOrchestrator processes ScenarioResult and AttackResult
- RAIServiceScorer uses AttackScoringConfig for scoring configuration
"""

from pyrit.executor.attack import AttackScoringConfig
from pyrit.models import AttackOutcome, AttackResult
from pyrit.models.scenario_result import ScenarioResult
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.foundry import FoundryScenario, FoundryStrategy


class TestFoundryStrategyContract:
    """Validate FoundryStrategy availability and structure."""

    def test_foundry_strategy_class_exists(self):
        """StrategyMapper maps to FoundryStrategy values."""
        assert FoundryStrategy is not None

    def test_foundry_strategy_is_scenario_strategy(self):
        """FoundryStrategy should extend ScenarioStrategy."""
        from pyrit.scenario import ScenarioStrategy

        assert issubclass(FoundryStrategy, ScenarioStrategy)


class TestFoundryScenarioContract:
    """Validate FoundryScenario availability."""

    def test_foundry_scenario_class_exists(self):
        """ScenarioOrchestrator creates FoundryScenario instances."""
        assert FoundryScenario is not None


class TestDatasetConfigurationContract:
    """Validate DatasetConfiguration availability."""

    def test_dataset_configuration_class_exists(self):
        """DatasetConfigurationBuilder produces DatasetConfiguration."""
        assert DatasetConfiguration is not None


class TestAttackScoringConfigContract:
    """Validate AttackScoringConfig availability."""

    def test_attack_scoring_config_exists(self):
        """ScenarioOrchestrator uses AttackScoringConfig."""
        assert AttackScoringConfig is not None

    def test_attack_scoring_config_has_expected_fields(self):
        """AttackScoringConfig should accept objective_scorer and refusal_scorer."""
        config = AttackScoringConfig()
        assert hasattr(config, "objective_scorer")
        assert hasattr(config, "refusal_scorer")


class TestScenarioResultContract:
    """Validate ScenarioResult model availability."""

    def test_scenario_result_class_exists(self):
        """ScenarioOrchestrator reads ScenarioResult."""
        assert ScenarioResult is not None

    def test_attack_result_class_exists(self):
        """FoundryResultProcessor processes AttackResult."""
        assert AttackResult is not None

    def test_attack_outcome_class_exists(self):
        """FoundryResultProcessor checks AttackOutcome values."""
        assert AttackOutcome is not None
