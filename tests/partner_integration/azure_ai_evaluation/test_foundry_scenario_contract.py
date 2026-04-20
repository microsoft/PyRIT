# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for Foundry scenario APIs used by azure-ai-evaluation.

The azure-ai-evaluation red team module uses the scenario framework for attack execution:
- FoundryExecutionManager creates RedTeamAgent instances per risk category
- StrategyMapper maps AttackStrategy enum → FoundryStrategy
- DatasetConfigurationBuilder produces DatasetConfiguration from RAI objectives
- ScenarioOrchestrator processes ScenarioResult and AttackResult
- RAIServiceScorer uses AttackScoringConfig for scoring configuration
"""

from pyrit.executor.attack import AttackScoringConfig
from pyrit.scenario import ScenarioStrategy
from pyrit.scenario.foundry import FoundryStrategy, RedTeamAgent


class TestRedTeamStrategyContract:
    """Validate FoundryStrategy availability and structure."""

    def test_foundry_strategy_is_scenario_strategy(self):
        """FoundryStrategy should extend ScenarioStrategy."""
        assert issubclass(FoundryStrategy, ScenarioStrategy)


class TestRedTeamScenarioContract:
    """Validate RedTeamAgent importability."""

    def test_red_team_agent_importable(self):
        """ScenarioOrchestrator creates RedTeamAgent instances."""
        assert RedTeamAgent is not None


class TestDatasetConfigurationContract:
    """Validate DatasetConfiguration importability."""

    def test_dataset_configuration_importable(self):
        """DatasetConfigurationBuilder produces DatasetConfiguration."""
        from pyrit.scenario import DatasetConfiguration  # noqa: F811

        assert DatasetConfiguration is not None


class TestAttackScoringConfigContract:
    """Validate AttackScoringConfig availability."""

    def test_attack_scoring_config_has_expected_fields(self):
        """AttackScoringConfig should accept objective_scorer and refusal_scorer."""
        config = AttackScoringConfig()
        assert hasattr(config, "objective_scorer")
        assert hasattr(config, "refusal_scorer")


class TestScenarioResultContract:
    """Validate ScenarioResult and AttackResult importability."""

    def test_scenario_result_importable(self):
        """ScenarioOrchestrator reads ScenarioResult."""
        from pyrit.models.scenario_result import ScenarioResult  # noqa: F811

        assert ScenarioResult is not None

    def test_attack_result_importable(self):
        """FoundryResultProcessor processes AttackResult."""
        from pyrit.models import AttackResult

        assert AttackResult is not None

    def test_attack_outcome_importable(self):
        """FoundryResultProcessor checks AttackOutcome values."""
        from pyrit.models import AttackOutcome

        assert AttackOutcome is not None
