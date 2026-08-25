# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Score types: what a scorer looks at, what it scores against, and the result.

A scorer takes two inputs — a ``Scorable`` (what to look at) and a
``ScoringExpectation`` (what to look for) — and returns ``Score`` objects. Scorables
are inert canonical data; scoring-layer resolvers acquire the evidence they name.
"""

from pyrit.models.score.condition import Condition, MatchesObjective
from pyrit.models.score.expectation import ScoringExpectation
from pyrit.models.score.scorable import (
    ContentEntryScorable,
    ContentScorable,
    MessageScorable,
    Scorable,
    ScorableUnion,
    scorable_from_dict,
    storable_scorable,
)
from pyrit.models.score.score import (
    ComponentIdentifierField,
    Score,
    ScoreStatus,
    ScoreType,
    UndeterminedScoreError,
    UnvalidatedScore,
)

__all__ = [
    "ComponentIdentifierField",
    "Condition",
    "ContentEntryScorable",
    "ContentScorable",
    "MatchesObjective",
    "MessageScorable",
    "Scorable",
    "ScorableUnion",
    "Score",
    "ScoreStatus",
    "ScoreType",
    "ScoringExpectation",
    "UndeterminedScoreError",
    "UnvalidatedScore",
    "scorable_from_dict",
    "storable_scorable",
]
