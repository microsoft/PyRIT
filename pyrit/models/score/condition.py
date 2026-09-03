# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, model_validator

#: Maps each condition's stable discriminator to its type. A condition is persisted under
#: its ``condition_type`` discriminator rather than its import path, so a stored score survives
#: a class rename or a module move. Populated by ``Condition.__pydantic_init_subclass__``.
_CONDITION_TYPES: dict[str, type[Condition]] = {}


class Condition(BaseModel):
    """
    What counts as satisfied.

    A condition is a neutral predicate about evidence: it says what to detect, never
    whether detecting it is good or bad. Polarity belongs to a scorer that wraps another,
    such as ``TrueFalseInverterScorer``. Each scoring domain adds its own subclass.

    A concrete subclass declares a ``condition_type`` field as a single-value ``Literal`` with
    a matching default. That default is the stable discriminator persisted with the condition
    and carried in REST payloads, so the type survives serialization without its import path.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    condition_type: str

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """
        Register the subclass under its stable discriminator.

        Args:
            **kwargs (Any): Forwarded to ``super().__pydantic_init_subclass__``.

        Raises:
            TypeError: If the discriminator declaration is invalid.
            ValueError: If the discriminator already names a different condition.
        """
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("condition_type")
        literal_values = (
            get_args(field.annotation)
            if field is not None and get_origin(field.annotation) is Literal
            else ()
        )
        if len(literal_values) != 1 or not isinstance(literal_values[0], str) or not literal_values[0]:
            raise TypeError(
                f"{cls.__name__}.condition_type must be a single non-empty string Literal with a matching default."
            )
        discriminator = literal_values[0]
        if field.default != discriminator:
            raise TypeError(
                f"{cls.__name__}.condition_type must default to its Literal value {discriminator!r}."
            )
        registered = _CONDITION_TYPES.get(discriminator)
        if registered is not None and registered is not cls:
            raise ValueError(
                f"Condition discriminator {discriminator!r} is already registered to "
                f"{registered.__name__}; give {cls.__name__} a distinct condition_type."
            )
        _CONDITION_TYPES[discriminator] = cls

    @model_validator(mode="after")
    def _reject_base_condition(self) -> Condition:
        """
        Reject the untyped registry root as a concrete condition.

        Returns:
            Condition: The validated concrete condition.

        Raises:
            ValueError: If the registry root is instantiated directly.
        """
        if type(self) is Condition:
            raise ValueError("Condition is an abstract registry root and cannot be instantiated directly.")
        return self


class MatchesObjective(Condition):
    """
    The evidence satisfies the expectation's own objective, as a judge reads it.

    This carries no text of its own. The objective lives on the ``ScoringExpectation``,
    so a scorer matching this condition reads it from there and the two can never
    disagree.
    """

    condition_type: Literal["matches_objective"] = "matches_objective"


def condition_from_dict(value: dict[str, Any]) -> Condition:
    """
    Rebuild a condition from its serialized, discriminator-tagged dict.

    Args:
        value (dict[str, Any]): A dict carrying ``condition_type`` and the condition's fields.

    Returns:
        Condition: The reconstructed condition.

    Raises:
        ValueError: If the discriminator is missing, invalid, or names no registered condition type.
    """
    discriminator = value.get("condition_type")
    if not isinstance(discriminator, str):
        raise ValueError("Condition requires a string condition_type discriminator.")
    condition_type = _CONDITION_TYPES.get(discriminator)
    if condition_type is None:
        raise ValueError(f"Unknown condition_type {discriminator!r}.")
    return condition_type.model_validate(value)
