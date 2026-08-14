# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCalled:
    """The named tool appears in the evidence."""

    name: str
    arguments: dict[str, str] | None = None


@dataclass(frozen=True)
class ToolSequence:
    """The named tools appear, in this relative order."""

    names: tuple[str, ...]


@dataclass(frozen=True)
class OutputMatches:
    """The response contains or equals a ground-truth answer."""

    value: str


Condition = ToolCalled | ToolSequence | OutputMatches


@dataclass(frozen=True)
class ScoringExpectation:
    """
    What a scorer scores against.

    An expectation is a single parameter that attacks forward without inspecting it,
    so a question authored in a technique configuration or a seed can reach a scorer
    through an attack that knows nothing about it.

    Conditions are neutral about polarity: a condition says what to detect, never
    whether detecting it is good or bad. Wrap a scorer in ``TrueFalseInverterScorer``
    to express the negative case.
    """

    objective: str | None = None
    conditions: tuple[Condition, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)
