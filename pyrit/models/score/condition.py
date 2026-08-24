# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass


class Condition(ABC):  # noqa: B024  root type; each scoring domain declares its own criterion
    """
    What counts as satisfied.

    A condition is a neutral predicate about evidence: it says what to detect, never
    whether detecting it is good or bad. Polarity belongs to a scorer that wraps another,
    such as ``TrueFalseInverterScorer``. Each scoring domain adds its own subclass.
    """


@dataclass(frozen=True, kw_only=True)
class MatchesObjective(Condition):
    """
    The evidence satisfies the expectation's own objective, as a judge reads it.

    This carries no text of its own. The objective lives on the ``ScoringExpectation``,
    so a scorer matching this condition reads it from there and the two can never
    disagree.
    """
