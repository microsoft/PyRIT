# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Typed signal for attacks cancelled after they started sending to the objective target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from pyrit.models import AttackResult

_DEFAULT_REASON = "Cancelled after a request was sent to the objective target; its response is unknown."


@dataclass(frozen=True)
class AttackCancellation:
    """
    A typed record that an attack was cancelled after it started sending to the objective target.

    The attack carries this on ``AttackResult.metadata`` so the attempt keeps an audit trail.
    Results carrying it use ``AttackOutcome.UNDETERMINED``: no verdict was reached, and what the
    objective target did with an in-flight request is unknown. The row records an interrupted
    attempt rather than a measurement, so scenario resume treats its objective as still pending.
    """

    reason: str = _DEFAULT_REASON

    #: Key under which the signal travels in ``AttackResult.metadata``.
    METADATA_KEY: ClassVar[str] = "attack_cancelled"

    def to_metadata(self) -> dict[str, Any]:
        """
        Render the signal for ``AttackResult.metadata``.

        Returns:
            dict[str, Any]: The metadata fragment marking the result as cancelled.
        """
        return {self.METADATA_KEY: True}

    @classmethod
    def from_result(cls, *, result: AttackResult) -> AttackCancellation | None:
        """
        Read the signal back off a result.

        Args:
            result (AttackResult): The result to inspect.

        Returns:
            AttackCancellation | None: The recorded cancellation, or ``None`` when the result
            carries no cancellation signal.
        """
        if result.metadata.get(cls.METADATA_KEY) is not True:
            return None
        return cls(reason=result.outcome_reason or _DEFAULT_REASON)
