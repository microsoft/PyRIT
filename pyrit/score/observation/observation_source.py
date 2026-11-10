# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Typed acquisition of condition-independent evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Observation, Scorable

ScorableT = TypeVar("ScorableT", bound="Scorable", contravariant=True)


@runtime_checkable
class ObservationSource(Protocol[ScorableT]):
    """
    Acquire replayable evidence for the declared scorable type.

    Runtime-checkable so the ``SourceRegistry`` can recognize source classes and
    check registered instances structurally; sources do not share a base class.
    """

    def get_identifier(self) -> ComponentIdentifier:
        """Return the nonsecret source configuration."""
        ...

    async def acquire_async(self, *, scorable: ScorableT) -> Observation:
        """Acquire one immutable observation about the selected scope."""
        ...
