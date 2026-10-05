# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from pyrit.models.score.condition import ConditionTuple  # noqa: TC001  (runtime Pydantic annotation)

if TYPE_CHECKING:
    from pydantic import ValidationInfo

_PERSISTED_VALIDATION_CONTEXT = "require_scoring_expectation_schema_version"


class ScoringExpectation(BaseModel):
    """
    What a scorer scores against.

    An expectation is a single parameter, so a question authored in a technique
    configuration or a seed can reach a scorer through an attack that knows nothing
    about it. It has two independent axes.

    ``objective`` carries optional scoring context. It can differ from the attack
    objective that drives adversarial prompts. Scorers may read it for framing or
    use it as criterion text through ``MatchesObjective``.

    ``conditions`` carry the criteria: typed objects routed by type to the scorers that
    match them. Attacks forward them without inspecting them. A typed leaf accepts
    exactly one of its declared type; wrappers validate coverage and route subsets
    to their children. Their tuple order is part of the persisted expectation and its
    fingerprint. ``SerializeAsAny`` keeps each condition serialized as its own subtype,
    so subclass fields survive a round trip.

    Seeds can author these criteria; execution parameters transport the resolved
    expectation. Seed types and condition types need not map one-to-one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Version of the persisted shape. Bumped only when the serialized dict changes in a way an
    #: older reader cannot understand; the validator rejects any other value on load.
    schema_version: Literal[1] = 1

    objective: str | None = None
    conditions: ConditionTuple = ()

    @staticmethod
    def validate_type(value: object) -> None:
        """
        Check a runtime input without parsing or copying it.

        Args:
            value (object): The expectation input.

        Raises:
            TypeError: If the input is not a ``ScoringExpectation`` or None.
        """
        if value is not None and not isinstance(value, ScoringExpectation):
            raise TypeError("expectation must be a ScoringExpectation or None.")

    @model_validator(mode="before")
    @classmethod
    def _require_persisted_schema_version(cls, value: Any, info: ValidationInfo) -> Any:
        """
        Require the version field when validating a persisted representation.

        Args:
            value (Any): The incoming expectation representation.
            info (ValidationInfo): Pydantic validation metadata.

        Returns:
            Any: The unchanged representation.

        Raises:
            ValueError: If a persisted representation omits its schema version.
        """
        if (
            info.context
            and info.context.get(_PERSISTED_VALIDATION_CONTEXT)
            and (not isinstance(value, dict) or "schema_version" not in value)
        ):
            raise ValueError("Persisted ScoringExpectation requires an explicit schema_version.")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def _check_schema_version(cls, value: Any) -> Any:
        """
        Reject a schema version this model cannot read without coercion.

        Args:
            value (Any): The incoming schema version.

        Returns:
            Any: The validated version.

        Raises:
            ValueError: If the version is not the exact integer this model understands.
        """
        if type(value) is not int or value != 1:
            raise ValueError(f"Unsupported ScoringExpectation schema_version {value!r}; expected 1.")
        return value

    @classmethod
    def model_validate_persisted(cls, value: Any) -> ScoringExpectation:
        """
        Validate an expectation loaded from its durable, versioned representation.

        Args:
            value (Any): The persisted expectation representation.

        Returns:
            ScoringExpectation: The validated expectation.
        """
        return cls.model_validate(value, context={_PERSISTED_VALIDATION_CONTEXT: True})


def scoring_expectation_fingerprint(exp: ScoringExpectation) -> str:
    """
    Return a stable content fingerprint of an expectation.

    The fingerprint is the lowercase SHA-256 hex of the canonical JSON serialization
    (sorted keys, compact separators). JSON object key order does not affect the digest,
    but condition tuple order is preserved and remains significant.

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
