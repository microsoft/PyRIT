# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for Scorer and TrueFalseScorer interfaces used by azure-ai-evaluation.

The azure-ai-evaluation red team module extends these classes:
- AzureRAIServiceTrueFalseScorer extends Scorer
- RAIServiceScorer extends TrueFalseScorer

Both are critical for scoring attack results.

Scorer is now agnostic about what it scores: it takes a scorable and requires
``_score_scorable_async``. Every message-shaped hook, ``_score_piece_async`` included, moved
to ``MessageScorer``. A scorer that implements ``_score_piece_async`` must therefore extend
``MessageScorer`` instead of ``Scorer``. ``TrueFalseScorer`` already does, so
``RAIServiceScorer`` needs no change; ``AzureRAIServiceTrueFalseScorer`` does.
"""

from pyrit.score import ScorerPromptValidator
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TestScorerContract:
    """Validate Scorer base class interface stability."""

    def test_scorer_requires_score_scorable_async(self):
        """Scorer subclasses must implement _score_scorable_async."""
        assert "_score_scorable_async" in Scorer.__abstractmethods__

    def test_message_scorer_has_score_piece_async(self):
        """Message-shaped scorers implement _score_piece_async and must extend MessageScorer."""
        assert hasattr(MessageScorer, "_score_piece_async")
        assert not hasattr(Scorer, "_score_piece_async")

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

    def test_true_false_scorer_extends_message_scorer(self):
        """RAIServiceScorer keeps its _score_piece_async hook through MessageScorer."""
        assert issubclass(TrueFalseScorer, MessageScorer)

    def test_true_false_scorer_has_validate_return_scores(self):
        """TrueFalseScorer implements validate_return_scores."""
        assert hasattr(TrueFalseScorer, "validate_return_scores")


class TestScorerUtilities:
    """Validate scorer utility classes used by azure-ai-evaluation."""

    def test_scorer_identifier_importable(self):
        """RAIServiceScorer uses ScorerEvaluationIdentifier for identity tracking."""
        from pyrit.models.identifiers import ScorerEvaluationIdentifier

        assert ScorerEvaluationIdentifier is not None

    def test_scorer_prompt_validator_instantiable(self):
        """ScorerPromptValidator should accept supported_data_types kwarg."""
        validator = ScorerPromptValidator(supported_data_types=["text"])
        assert validator is not None
