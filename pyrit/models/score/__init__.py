# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Score types: what a scorer looks at, what it scores against, and the result.

A scorer takes two inputs — a ``Scorable`` (what to look at) and a
``ScoringExpectation`` (what to look for) — and returns ``Score`` objects. Scorables
are inert canonical data; scoring-layer resolvers acquire the evidence they name.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.score.condition import (
        Condition,
        DivergesFromRepetition,
        MatchesObjective,
        ToolCallRequirement,
        ToolsCalled,
    )
    from pyrit.models.score.expectation import (
        ScoringExpectation,
        scoring_expectation_fingerprint,
    )
    from pyrit.models.score.observation import (
        Acquisition,
        Observation,
        ObservationPayload,
        ScorerTargetResponsePayload,
        ToolEventsObservationPayload,
    )
    from pyrit.models.score.scorable import (
        ContentEntryScorable,
        ContentScorable,
        MessageScorable,
        Scorable,
        ScorableUnion,
        TraceScorable,
        scorable_from_dict,
    )
    from pyrit.models.score.score import (
        ComponentIdentifierField,
        Score,
        ScoreStatus,
        ScoreType,
        UndeterminedScoreError,
        UnvalidatedScore,
    )
    from pyrit.models.score.trace import (
        ToolExecution,
        TraceCoverage,
        TraceQuery,
        TraceQueryResult,
        TraceSpan,
        TraceSpanStatus,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "Acquisition": "pyrit.models.score.observation",
    "ComponentIdentifierField": "pyrit.models.score.score",
    "Condition": "pyrit.models.score.condition",
    "ContentEntryScorable": "pyrit.models.score.scorable",
    "ContentScorable": "pyrit.models.score.scorable",
    "DivergesFromRepetition": "pyrit.models.score.condition",
    "ScorerTargetResponsePayload": "pyrit.models.score.observation",
    "MatchesObjective": "pyrit.models.score.condition",
    "MessageScorable": "pyrit.models.score.scorable",
    "Observation": "pyrit.models.score.observation",
    "ObservationPayload": "pyrit.models.score.observation",
    "Scorable": "pyrit.models.score.scorable",
    "ScorableUnion": "pyrit.models.score.scorable",
    "Score": "pyrit.models.score.score",
    "ScoreStatus": "pyrit.models.score.score",
    "ScoreType": "pyrit.models.score.score",
    "ScoringExpectation": "pyrit.models.score.expectation",
    "ToolCallRequirement": "pyrit.models.score.condition",
    "ToolEventsObservationPayload": "pyrit.models.score.observation",
    "ToolExecution": "pyrit.models.score.trace",
    "ToolsCalled": "pyrit.models.score.condition",
    "TraceCoverage": "pyrit.models.score.trace",
    "TraceQuery": "pyrit.models.score.trace",
    "TraceQueryResult": "pyrit.models.score.trace",
    "TraceScorable": "pyrit.models.score.scorable",
    "TraceSpan": "pyrit.models.score.trace",
    "TraceSpanStatus": "pyrit.models.score.trace",
    "UndeterminedScoreError": "pyrit.models.score.score",
    "UnvalidatedScore": "pyrit.models.score.score",
    "scorable_from_dict": "pyrit.models.score.scorable",
    "scoring_expectation_fingerprint": "pyrit.models.score.expectation",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public score export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
