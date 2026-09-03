# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, SerializeAsAny, field_validator

from pyrit.models.score.condition import Condition, condition_from_dict


class ScoringExpectation(BaseModel):
    """
    What a scorer scores against.

    An expectation is a single parameter, so a question authored in a technique
    configuration or a seed can reach a scorer through an attack that knows nothing
    about it. It has two independent axes.

    ``objective`` carries the intent: prose describing what the run is trying to do.
    Components read it for framing — an adversarial target renders it into a system
    prompt, a report prints it — and none of them match it.

    ``conditions`` carry the criteria: typed objects routed by type to the scorers that
    match them. Attacks forward them without inspecting them, and a scorer matches at
    most one of them. ``SerializeAsAny`` keeps each condition serialized as its own
    subtype, so subclass fields survive a round trip.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Version of the persisted shape. Bumped only when the serialized dict changes in a way an
    #: older reader cannot understand; the validator rejects any other value on load.
    schema_version: int = 1

    objective: str | None = None
    conditions: tuple[SerializeAsAny[Condition], ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        """
        Reject a serialized expectation this version cannot read.

        Args:
            value (int): The incoming schema version.

        Returns:
            int: The validated version.

        Raises:
            ValueError: If the version is not the one this model understands.
        """
        if value != 1:
            raise ValueError(f"Unsupported ScoringExpectation schema_version {value!r}; expected 1.")
        return value

    @field_validator("conditions", mode="before")
    @classmethod
    def _rebuild_conditions(cls, value: Any) -> Any:
        """
        Rebuild serialized conditions into their concrete subtypes.

        Args:
            value (Any): The incoming conditions: an iterable of ``Condition`` instances or of
                serialized, discriminator-tagged dicts.

        Returns:
            Any: A tuple of conditions, with any dict routed through the condition registry.

        Raises:
            ValueError: If a serialized condition names an unknown discriminator.
        """
        if value is None:
            return ()
        return tuple(condition_from_dict(item) if isinstance(item, dict) else item for item in value)


def scoring_expectation_fingerprint(exp: ScoringExpectation) -> str:
    """
    Return a stable content fingerprint of an expectation.

    The fingerprint is the lowercase SHA-256 hex of the canonical JSON serialization
    (sorted keys, compact separators), so two expectations with the same objective and
    conditions hash identically regardless of construction order.

    Args:
        exp (ScoringExpectation): The expectation to fingerprint.

    Returns:
        str: The lowercase SHA-256 hex digest.
    """
    serialized = json.dumps(
        exp.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
