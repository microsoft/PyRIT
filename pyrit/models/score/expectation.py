# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, SerializeAsAny, TypeAdapter, field_validator, model_validator

from pyrit.models.score.condition import _CONDITION_TYPES, Condition

if TYPE_CHECKING:
    from pydantic import GetJsonSchemaHandler, ValidationInfo
    from pydantic.json_schema import JsonSchemaValue
    from pydantic_core import CoreSchema

_PERSISTED_VALIDATION_CONTEXT = "require_scoring_expectation_schema_version"


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
    schema_version: Literal[1] = 1

    objective: str | None = None
    conditions: tuple[SerializeAsAny[Condition], ...] = ()

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
        return tuple(Condition.model_validate(item) if isinstance(item, dict) else item for item in value)

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

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """
        Describe every registered condition subtype in the generated schema.

        Returns:
            JsonSchemaValue: The expectation schema with a discriminated condition union.
        """
        schema = handler(core_schema)
        resolved_schema = handler.resolve_ref_schema(schema)
        condition_schemas = [
            handler(TypeAdapter(condition_class).core_schema) for _, condition_class in sorted(_CONDITION_TYPES.items())
        ]
        for condition_schema in condition_schemas:
            required_fields = condition_schema.setdefault("required", [])
            if "condition_type" not in required_fields:
                required_fields.append("condition_type")
        resolved_schema["properties"]["conditions"]["items"] = {
            "discriminator": {"propertyName": "condition_type"},
            "oneOf": condition_schemas,
        }
        return schema


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
