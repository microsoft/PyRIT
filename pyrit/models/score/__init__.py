# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Score types: what a scorer is scored against, and the result.

A scorer takes two inputs — a ``Scorable`` (what to look at) and a
``ScoringExpectation`` (what to look for) — and returns ``Score`` objects. Scorables
resolve themselves against memory, so they live in ``pyrit.score`` rather than here.
"""

from pyrit.models.score.expectation import ScoringExpectation
from pyrit.models.score.score import ComponentIdentifierField, Score, ScoreType, UnvalidatedScore

__all__ = [
    "ComponentIdentifierField",
    "Score",
    "ScoreType",
    "ScoringExpectation",
    "UnvalidatedScore",
]
