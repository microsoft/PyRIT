# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Optional observation lifecycle: a baseline at attempt start, finalized at scoring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Observation, ScoringExpectation, SurfaceBaseline, SurfaceScorable


@runtime_checkable
class SurfaceLifecycleSource(Protocol):
    """
    A surface source that can record a baseline and finalize a snapshot against it.

    Lifecycle support is optional. A source without it still acquires snapshots, and a
    scorer can evaluate presence. A criterion that requires content change remains
    undetermined without a baseline. Lifecycle snapshots do not record write events.
    """

    def get_identifier(self) -> ComponentIdentifier:
        """Return the nonsecret source configuration."""
        ...

    async def acquire_async(self, *, scorable: SurfaceScorable) -> Observation:
        """Acquire one snapshot without a baseline."""
        ...

    async def start_observing_async(self, *, scorable: SurfaceScorable) -> SurfaceBaseline:
        """Record what the named locations hold before the attempt acts."""
        ...

    async def finalize_async(self, *, scorable: SurfaceScorable, baseline: SurfaceBaseline) -> Observation:
        """Acquire a snapshot that carries the baseline it is compared with."""
        ...


@runtime_checkable
class AttemptObserver(Protocol):
    """
    A scorer that observes evidence across one attack attempt.

    An attack calls ``start_observing_async`` once its attempt ID is allocated and before
    it sends anything, and ``stop_observing`` when the attempt ends, whatever the outcome.
    """

    async def start_observing_async(self, *, attempt_id: str, expectation: ScoringExpectation) -> None:
        """Record baselines for the attempt's routed conditions."""
        ...

    def stop_observing(self, *, attempt_id: str) -> None:
        """Release what was recorded for the attempt."""
        ...
