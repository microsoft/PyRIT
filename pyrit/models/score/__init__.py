# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Score types: what is scored, what it is scored against, and the result.

A scorer takes two inputs — a ``Scorable`` (what to look at) and a
``ScoringExpectation`` (what to look for) — and returns ``Score`` objects.
"""

from pyrit.models.score.expectation import Condition, OutputMatches, ScoringExpectation, ToolCalled, ToolSequence
from pyrit.models.score.scorable import (
    ContentScorable,
    ConversationScorable,
    MessageReferenceScorable,
    MessageScorable,
    Scorable,
    SurfaceScorable,
    TraceScorable,
    Volatility,
)
from pyrit.models.score.score import ComponentIdentifierField, Score, ScoreType, UnvalidatedScore
from pyrit.models.score.scoring_scope import ScoringScope

__all__ = [
    "ComponentIdentifierField",
    "Condition",
    "ContentScorable",
    "ConversationScorable",
    "MessageReferenceScorable",
    "MessageScorable",
    "OutputMatches",
    "Scorable",
    "Score",
    "ScoreType",
    "ScoringExpectation",
    "ScoringScope",
    "SurfaceScorable",
    "ToolCalled",
    "ToolSequence",
    "TraceScorable",
    "UnvalidatedScore",
    "Volatility",
]
