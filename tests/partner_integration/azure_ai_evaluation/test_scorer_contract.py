# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for Scorer and TrueFalseScorer interfaces used by azure-ai-evaluation.

The azure-ai-evaluation red team module extends these classes:
- AzureRAIServiceTrueFalseScorer extends Scorer
- RAIServiceScorer extends TrueFalseScorer

Both are critical for scoring attack results.

Scorer is now agnostic about what it scores: it takes a scorable and requires
``_score_scorable_async``. Every message-shaped hook, ``_score_piece_async`` included, moved
to ``MessageScorer``. ``TrueFalseScorer`` now defines the score-value axis only, so a scorer
that implements ``_score_piece_async`` must extend ``MessageTrueFalseScorer``, which combines
both axes. Both ``AzureRAIServiceTrueFalseScorer`` and ``RAIServiceScorer`` need that change.
"""

from pyrit.score import MessageTrueFalseScorer, ScorerPromptValidator
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

    def test_true_false_scorer_defines_the_score_value_axis_only(self):
        """TrueFalseScorer says what a score value looks like, not what evidence it reads."""
        assert not issubclass(TrueFalseScorer, MessageScorer)
        assert not hasattr(TrueFalseScorer, "_score_piece_async")

    def test_message_true_false_scorer_combines_both_axes(self):
        """RAIServiceScorer keeps its _score_piece_async hook through MessageTrueFalseScorer."""
        assert issubclass(MessageTrueFalseScorer, TrueFalseScorer)
        assert issubclass(MessageTrueFalseScorer, MessageScorer)
        assert hasattr(MessageTrueFalseScorer, "_score_piece_async")

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
