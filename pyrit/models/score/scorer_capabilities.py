# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A read-only view of what a configured scorer can judge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.models.score.condition import Condition
    from pyrit.models.score.scorable import Scorable
    from pyrit.models.score.score import ScoreType


@dataclass(frozen=True, kw_only=True)
class ScorerCapabilities:
    """
    What a configured scorer accepts and guarantees, derived from its existing contracts.

    This is not a declaration to edit. ``Scorer.capabilities`` builds it from the scorer's
    supported evidence types, its condition coverage, its result family, and one output
    guarantee, so it cannot drift from the scorer it describes.

    Attributes:
        scorable_types (frozenset[type[Scorable]]): Evidence references the scorer accepts.
        condition_types (frozenset[type[Condition]]): Condition types the scorer tree evaluates.
        result_family (ScoreType): ``"true_false"``, ``"float_scale"``, or ``"unknown"``.
        preserves_float_scale_value (bool): Whether every complete, applicable true/false
            result keeps a finite numeric value in ``[0, 1]`` for the same criterion.
    """

    scorable_types: frozenset[type[Scorable]]
    condition_types: frozenset[type[Condition]]
    result_family: ScoreType
    preserves_float_scale_value: bool = False

    def accepts_scorable_type(self, scorable_type: type[Scorable]) -> bool:
        """
        Check whether the scorer accepts an evidence family.

        Args:
            scorable_type (type[Scorable]): The evidence family the caller will supply.

        Returns:
            bool: True when that family, or a parent family it belongs to, is accepted.
        """
        return any(issubclass(scorable_type, accepted) for accepted in self.scorable_types)
