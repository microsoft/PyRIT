# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A read-only view of what a configured scorer can judge."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.models.score.condition import Condition
    from pyrit.models.score.scorable import Scorable


class ScorerCapabilityName(str, Enum):
    """
    An output guarantee a scorer can make and a consumer can require.

    Scorers declare the result family they return; consumers such as attacks list the
    guarantees they rely on in ``ScorerRequirements`` instead of checking scorer classes.
    """

    #: Each scoring call returns exactly one true/false verdict.
    TRUE_FALSE = "true_false"
    #: Each result is a float-scale value in ``[0, 1]``.
    FLOAT_SCALE = "float_scale"
    #: Each complete, applicable true/false verdict keeps a finite ``[0, 1]`` float value
    #: for the same criterion, so callers can rank by it.
    PRESERVES_FLOAT_SCALE_VALUE = "preserves_float_scale_value"


@dataclass(frozen=True, kw_only=True)
class ScorerCapabilities:
    """
    What a configured scorer accepts and guarantees, derived from its existing contracts.

    This is not a declaration to edit. ``Scorer.capabilities`` builds it from the scorer's
    supported evidence types, its condition coverage, and the output guarantees its class
    and configuration make, so it cannot drift from the scorer it describes.

    Attributes:
        scorable_types (frozenset[type[Scorable]]): Evidence references the scorer accepts.
        condition_types (frozenset[type[Condition]]): Condition types the scorer tree evaluates.
        supported (frozenset[ScorerCapabilityName]): The output guarantees the scorer makes.
    """

    scorable_types: frozenset[type[Scorable]]
    condition_types: frozenset[type[Condition]]
    supported: frozenset[ScorerCapabilityName] = field(default_factory=frozenset)

    def includes(self, *, capability: ScorerCapabilityName) -> bool:
        """
        Check whether the scorer makes an output guarantee.

        Args:
            capability (ScorerCapabilityName): The guarantee to check.

        Returns:
            bool: True when the scorer makes it.
        """
        return capability in self.supported

    def accepts_scorable_type(self, scorable_type: type[Scorable]) -> bool:
        """
        Check whether the scorer accepts an evidence family.

        Args:
            scorable_type (type[Scorable]): The evidence family the caller will supply.

        Returns:
            bool: True when that family, or a parent family it belongs to, is accepted.
        """
        return any(issubclass(scorable_type, accepted) for accepted in self.scorable_types)
