# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.score.fallback_scorer import _FallbackScorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TrueFalseFallbackScorer(_FallbackScorer, TrueFalseScorer):
    """
    Use a second true/false scorer only when the primary cannot reach a verdict.

    Both children must support the same condition types and return exactly one score
    when applicable. They must evaluate the same evidence, categories, and criterion.
    A completed False is a judgment, not a reason to call the fallback.
    Callers must configure equivalent criteria; matching condition types and categories
    alone cannot establish semantic equivalence.
    """

    def __init__(self, *, scorer: TrueFalseScorer, fallback_scorer: TrueFalseScorer) -> None:
        """
        Initialize a true/false fallback pair.

        Args:
            scorer: The primary true/false evaluator.
            fallback_scorer: The evaluator used only for an undetermined primary result.

        Raises:
            ValueError: If either child has the wrong family, their condition types differ,
                or both arguments refer to the same object.
        """
        super().__init__(scorer=scorer, fallback_scorer=fallback_scorer, scorer_type=TrueFalseScorer)
