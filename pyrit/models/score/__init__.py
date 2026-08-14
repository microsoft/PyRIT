# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Score types: what is scored, what it is scored against, and the result.

A scorer takes two inputs — a ``Scorable`` (what to look at) and a
``ScoringExpectation`` (what to look for) — and returns ``Score`` objects.
"""

from pyrit.models.score.expectation import ScoringExpectation
from pyrit.models.score.scorable import ContentScorable, MessageReferenceScorable, MessageScorable, Scorable
from pyrit.models.score.score import ComponentIdentifierField, Score, ScoreType, UnvalidatedScore

__all__ = [
    "ComponentIdentifierField",
    "ContentScorable",
    "MessageReferenceScorable",
    "MessageScorable",
    "Scorable",
    "Score",
    "ScoreType",
    "ScoringExpectation",
    "UnvalidatedScore",
]
