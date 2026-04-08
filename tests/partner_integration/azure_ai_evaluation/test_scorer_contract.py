# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for Scorer and TrueFalseScorer interfaces used by azure-ai-evaluation.

The azure-ai-evaluation red team module extends these classes:
- AzureRAIServiceTrueFalseScorer extends Scorer
- RAIServiceScorer extends TrueFalseScorer

Both are critical for scoring attack results.
"""

from pyrit.score import ScorerPromptValidator
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TestScorerContract:
    """Validate Scorer base class interface stability."""

    def test_scorer_has_score_piece_async(self):
        """Scorer subclasses must implement _score_piece_async."""
        assert hasattr(Scorer, "_score_piece_async")

    def test_scorer_has_validate_return_scores(self):
        """Scorer subclasses must implement validate_return_scores."""
        assert hasattr(Scorer, "validate_return_scores")

    def test_scorer_has_get_scorer_metrics(self):
        """Scorer subclasses must implement get_scorer_metrics."""
        assert hasattr(Scorer, "get_scorer_metrics")


class TestTrueFalseScorerContract:
    """Validate TrueFalseScorer interface stability."""

    def test_true_false_scorer_extends_scorer(self):
        """RAIServiceScorer extends TrueFalseScorer which extends Scorer."""
        assert issubclass(TrueFalseScorer, Scorer)

    def test_true_false_scorer_has_validate_return_scores(self):
        """TrueFalseScorer implements validate_return_scores."""
        assert hasattr(TrueFalseScorer, "validate_return_scores")


class TestScorerUtilities:
    """Validate scorer utility classes used by azure-ai-evaluation."""

    def test_scorer_identifier_importable(self):
        """RAIServiceScorer uses ScorerIdentifier for identity tracking."""
        from pyrit.identifiers import ScorerIdentifier

        assert ScorerIdentifier is not None

    def test_scorer_prompt_validator_instantiable(self):
        """ScorerPromptValidator should accept supported_data_types kwarg."""
        validator = ScorerPromptValidator(supported_data_types=["text"])
        assert validator is not None
