# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Score types: what a scorer looks at, what it scores against, and the result.

A scorer takes two inputs — a ``Scorable`` (what to look at) and a
``ScoringExpectation`` (what to look for) — and returns ``Score`` objects. Scorables
are inert canonical data; scoring-layer resolvers acquire the evidence they name.
"""

from pyrit.models.score.expectation import ScoringExpectation
from pyrit.models.score.scorable import ContentScorable, MessageScorable, Scorable
from pyrit.models.score.score import ComponentIdentifierField, Score, ScoreType, UnvalidatedScore

__all__ = [
    "ComponentIdentifierField",
    "ContentScorable",
    "MessageScorable",
    "Scorable",
    "Score",
    "ScoreType",
    "ScoringExpectation",
    "UnvalidatedScore",
]
