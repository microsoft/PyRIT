# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pyrit.models import Message


class TargetSendContext(Protocol):
    """Internal contract coordinating one target send with caller-owned state."""

    conversation_id: str

    @property
    def target_invocation_count(self) -> int:
        """Number of sends that reached target-specific execution."""
        ...

    def begin_send(self) -> None:
        """Acquire caller-owned state for one complete send."""
        ...

    def select_history(self, *, messages: list[Message]) -> list[Message]:
        """Select the caller-approved persisted history for this send."""
        ...

    def mark_target_invoked(self) -> None:
        """Record that target-specific execution has begun."""
        ...

    def finish_send(self, *, succeeded: bool) -> None:
        """Release caller-owned state after the send and record successful delivery."""
        ...
