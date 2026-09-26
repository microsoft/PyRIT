# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.score.fallback_scorer import _FallbackScorer
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer


class FloatScaleFallbackScorer(_FallbackScorer, FloatScaleScorer):
    """
    Use a second float-scale scorer only when the primary cannot reach a verdict.

    Both children must support the same condition types and return exactly one score
    when applicable. They must evaluate the same evidence, categories, and criterion.
    Callers must configure comparable rubrics and numeric scales; matching condition
    types and categories alone cannot establish semantic equivalence.
    """

    def __init__(self, *, scorer: FloatScaleScorer, fallback_scorer: FloatScaleScorer) -> None:
        """
        Initialize a float-scale fallback pair.

        Args:
            scorer: The primary float-scale evaluator.
            fallback_scorer: The evaluator used only for an undetermined primary result.

        Raises:
            ValueError: If either child has the wrong family, their condition types differ,
                or both arguments refer to the same object.
        """
        super().__init__(scorer=scorer, fallback_scorer=fallback_scorer, scorer_type=FloatScaleScorer)
