# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

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

    model_config = ConfigDict(frozen=True)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """
        Register the subclass under its stable discriminator.

        Args:
            **kwargs (Any): Forwarded to ``super().__pydantic_init_subclass__``.

        Raises:
            ValueError: If the discriminator already names a different condition.
        """
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("condition_type")
        default = field.default if field is not None else None
        discriminator = default if isinstance(default, str) and default else cls.__name__
        registered = _CONDITION_TYPES.get(discriminator)
        if registered is not None and registered is not cls:
            raise ValueError(
                f"Condition discriminator {discriminator!r} is already registered to "
                f"{registered.__name__}; give {cls.__name__} a distinct condition_type."
            )
        _CONDITION_TYPES[discriminator] = cls


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
        ValueError: If the discriminator names no registered condition type.
    """
    discriminator = value["condition_type"]
    condition_type = _CONDITION_TYPES.get(discriminator)
    if condition_type is None:
        raise ValueError(f"Unknown condition_type {discriminator!r}.")
    return condition_type.model_validate(value)
