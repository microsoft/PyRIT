# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Describe the public scorer classes from their class-level contracts, for documentation.

Nothing here constructs a scorer, so it needs no credentials or targets. A configured scorer's
``capabilities`` remain the authority, because some contracts depend on configuration: the
wrapped scorers of a composite, or whether a message scorer's validator requires an objective.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

import pyrit.score as score_package
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

#: True/false scorers that override the float feedback hook, and when they keep the value.
#: A test requires this to name exactly the classes that override the hook.
FLOAT_FEEDBACK_GUARANTEES: dict[str, str] = {
    "FloatScaleThresholdScorer": "Always",
    "TrueFalseFallbackScorer": "When both of its scorers do",
}

_WRAPPED = "Same as its wrapped scorers"


@dataclass(frozen=True)
class ScorerClassSummary:
    """
    One public scorer class, described from its class-level contracts.

    Attributes:
        name (str): The class name.
        result (str): ``true/false`` or ``float scale``.
        evidence (str): The evidence it accepts.
        conditions (str): The condition type it evaluates.
        float_feedback (str): Whether a true/false result keeps a float value for ranking.
    """

    name: str
    result: str
    evidence: str
    conditions: str
    float_feedback: str


def _overrides(cls: type[Scorer], *, name: str) -> bool:
    """
    Report whether a class below ``Scorer`` defines ``name`` itself.

    Returns:
        bool: True when some subclass in the MRO overrides the base method.
    """
    return any(name in vars(klass) for klass in cls.__mro__ if klass is not Scorer and issubclass(klass, Scorer))


def _wraps_scorers(cls: type[Scorer]) -> bool:
    return _overrides(cls, name="_get_child_scorers")


def _describe_evidence(cls: type[Scorer]) -> str:
    if cls.SUPPORTED_SCORABLE_TYPES:
        return ", ".join(sorted(scorable.__name__ for scorable in cls.SUPPORTED_SCORABLE_TYPES))
    return _WRAPPED if _wraps_scorers(cls) else "None declared"


def _describe_conditions(cls: type[Scorer]) -> str:
    if _wraps_scorers(cls):
        return _WRAPPED
    if cls.CONDITION_TYPE is not None:
        return cls.CONDITION_TYPE.__name__
    if _overrides(cls, name="_get_condition_type"):
        return "MatchesObjective, when configured to judge an objective"
    return "None"


def _describe_float_feedback(cls: type[Scorer]) -> str:
    if not issubclass(cls, TrueFalseScorer):
        return "Not applicable"
    return FLOAT_FEEDBACK_GUARANTEES.get(cls.__name__, "No")


def public_scorer_classes() -> list[type[Scorer]]:
    """
    List the concrete scorer classes exported from ``pyrit.score``.

    Returns:
        list[type[Scorer]]: The classes, sorted by name.
    """
    classes = []
    for name in sorted(score_package.__all__):
        candidate = getattr(score_package, name)
        if inspect.isclass(candidate) and issubclass(candidate, Scorer) and not inspect.isabstract(candidate):
            classes.append(candidate)
    return classes


def describe_scorer_classes() -> list[ScorerClassSummary]:
    """
    Describe every concrete public scorer class without constructing one.

    Returns:
        list[ScorerClassSummary]: One summary per class, sorted by name.
    """
    return [
        ScorerClassSummary(
            name=cls.__name__,
            result="true/false"
            if issubclass(cls, TrueFalseScorer)
            else "float scale"
            if issubclass(cls, FloatScaleScorer)
            else "other",
            evidence=_describe_evidence(cls),
            conditions=_describe_conditions(cls),
            float_feedback=_describe_float_feedback(cls),
        )
        for cls in public_scorer_classes()
    ]


def render_scorer_capability_table() -> str:
    """
    Render the scorer summaries as a Markdown table.

    Returns:
        str: The table, with a header row and one row per scorer class.
    """
    lines = [
        "| Scorer | Result | Evidence | Conditions | Float feedback |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| `{row.name}` | {row.result} | {row.evidence} | {row.conditions} | {row.float_feedback} |"
        for row in describe_scorer_classes()
    )
    return "\n".join(lines)
