# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Typed acquisition of condition-independent evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Observation, Scorable

ScorableT = TypeVar("ScorableT", bound="Scorable", contravariant=True)


class ObservationSource(Protocol[ScorableT]):
    """Acquire replayable evidence for the declared scorable type."""

    def get_identifier(self) -> ComponentIdentifier:
        """Return the nonsecret source configuration."""
        ...

    async def acquire_async(self, *, scorable: ScorableT) -> Observation:
        """Acquire one immutable observation about the selected scope."""
        ...
