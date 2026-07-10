# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core import AttackScoringConfig
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    resolve_adversarial_system_prompt,
)
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TestAttackScoringConfig:
    """Test AttackScoringConfig validation functionality."""

    def test_init_with_valid_objective_scorer(self):
        """Test initialization with a valid TrueFalseScorer for objective_scorer."""
        mock_scorer = MagicMock(spec=TrueFalseScorer)

        config = AttackScoringConfig(objective_scorer=mock_scorer)

        assert config.objective_scorer == mock_scorer

    def test_init_with_valid_refusal_scorer(self):
        """Test initialization with a valid TrueFalseScorer for refusal_scorer."""
        mock_scorer = MagicMock(spec=TrueFalseScorer)

        config = AttackScoringConfig(refusal_scorer=mock_scorer)

        assert config.refusal_scorer == mock_scorer

    def test_init_with_both_valid_scorers(self):
        """Test initialization with valid TrueFalseScorers for both objective and refusal scorers."""
        mock_objective_scorer = MagicMock(spec=TrueFalseScorer)
        mock_refusal_scorer = MagicMock(spec=TrueFalseScorer)

        config = AttackScoringConfig(objective_scorer=mock_objective_scorer, refusal_scorer=mock_refusal_scorer)

        assert config.objective_scorer == mock_objective_scorer
        assert config.refusal_scorer == mock_refusal_scorer

    def test_init_raises_error_for_non_true_false_objective_scorer(self):
        """Test that initialization raises ValueError for non-TrueFalseScorer objective_scorer."""
        mock_scorer = MagicMock(spec=Scorer)

        with pytest.raises(ValueError, match="Objective scorer must be a TrueFalseScorer"):
            AttackScoringConfig(objective_scorer=mock_scorer)

    def test_init_raises_error_for_non_true_false_refusal_scorer(self):
        """Test that initialization raises ValueError for non-TrueFalseScorer refusal_scorer."""
        mock_scorer = MagicMock(spec=Scorer)

        with pytest.raises(ValueError, match="Refusal scorer must be a TrueFalseScorer"):
            AttackScoringConfig(refusal_scorer=mock_scorer)

    def test_init_with_none_scorers(self):
        """Test initialization with None for both scorers (default behavior)."""
        config = AttackScoringConfig()

        assert config.objective_scorer is None
        assert config.refusal_scorer is None

    def test_init_with_auxiliary_scorers(self):
        """Test initialization with auxiliary scorers."""
        mock_aux_scorer_1 = MagicMock(spec=Scorer)
        mock_aux_scorer_2 = MagicMock(spec=Scorer)

        config = AttackScoringConfig(auxiliary_scorers=[mock_aux_scorer_1, mock_aux_scorer_2])

        assert len(config.auxiliary_scorers) == 2
        assert config.auxiliary_scorers[0] == mock_aux_scorer_1
        assert config.auxiliary_scorers[1] == mock_aux_scorer_2

    def test_init_with_use_score_as_feedback_false(self):
        """Test initialization with use_score_as_feedback set to False."""
        config = AttackScoringConfig(use_score_as_feedback=False)

        assert config.use_score_as_feedback is False


class TestResolveAdversarialSystemPrompt:
    """Tests for resolve_adversarial_system_prompt."""

    def test_inline_string_is_trusted_and_wrapped(self):
        """An inline string is wrapped in a Jinja SeedPrompt declaring the required parameters."""
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt="persona {{ objective }}")
        seed = resolve_adversarial_system_prompt(
            config=config,
            default_system_prompt_path="unused.yaml",
            required_parameters=["objective"],
        )
        assert seed.value == "persona {{ objective }}"
        assert "objective" in (seed.parameters or [])

    def test_explicit_seedprompt_with_required_params_returned_as_is(self):
        """An explicitly provided SeedPrompt declaring the required params is returned unchanged."""
        provided = SeedPrompt(value="persona {{ objective }}", data_type="text", parameters=["objective"])
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt=provided)
        seed = resolve_adversarial_system_prompt(
            config=config,
            default_system_prompt_path="unused.yaml",
            required_parameters=["objective"],
        )
        assert seed is provided

    def test_explicit_seedprompt_missing_required_params_raises(self):
        """An explicit SeedPrompt missing a required parameter raises ValueError."""
        provided = SeedPrompt(value="persona", data_type="text", parameters=[])
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt=provided)
        with pytest.raises(ValueError, match="missing required parameters"):
            resolve_adversarial_system_prompt(
                config=config,
                default_system_prompt_path="unused.yaml",
                required_parameters=["objective"],
            )

    def test_explicit_seedprompt_missing_params_uses_custom_error_message(self):
        """A custom error_message overrides the default missing-parameters message."""
        provided = SeedPrompt(value="persona", data_type="text", parameters=[])
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt=provided)
        with pytest.raises(ValueError, match="must declare objective"):
            resolve_adversarial_system_prompt(
                config=config,
                default_system_prompt_path="unused.yaml",
                required_parameters=["objective"],
                error_message="must declare objective",
            )
