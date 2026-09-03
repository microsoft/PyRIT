# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from abc import ABC
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

#: Maps each condition's stable discriminator to its type. A condition is persisted under
#: its ``CONDITION_TYPE`` rather than its import path, so a stored score survives a class
#: rename or a module move. Populated by ``Condition.__init_subclass__``.
_CONDITION_TYPES: dict[str, type[Condition]] = {}


class Condition(ABC):  # noqa: B024  root type; each scoring domain declares its own criterion
    """
    What counts as satisfied.

    A condition is a neutral predicate about evidence: it says what to detect, never
    whether detecting it is good or bad. Polarity belongs to a scorer that wraps another,
    such as ``TrueFalseInverterScorer``. Each scoring domain adds its own subclass.
    """

    #: Stable discriminator persisted with the condition. A subclass may set it explicitly
    #: to pin the wire value; otherwise the class name is used. Assigned on every subclass by
    #: ``__init_subclass__``, so reading it is always safe.
    CONDITION_TYPE: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Register the subclass under its stable discriminator.

        Args:
            **kwargs (Any): Forwarded to ``super().__init_subclass__``.

        Raises:
            ValueError: If the discriminator is empty, or already names a different condition.
        """
        super().__init_subclass__(**kwargs)
        discriminator = cls.__dict__.get("CONDITION_TYPE") or cls.__name__
        if not discriminator:
            raise ValueError(f"{cls.__name__} declares an empty CONDITION_TYPE discriminator.")
        registered = _CONDITION_TYPES.get(discriminator)
        if registered is not None and registered is not cls:
            raise ValueError(
                f"Condition discriminator {discriminator!r} is already registered to "
                f"{registered.__name__}; give {cls.__name__} a distinct CONDITION_TYPE."
            )
        cls.CONDITION_TYPE = discriminator
        _CONDITION_TYPES[discriminator] = cls

    def to_persisted_dict(self) -> dict[str, Any]:
        """
        Return this condition's fields as a JSON-native dict.

        The default handles dataclass conditions whose fields already survive JSON
        serialization unchanged. A condition carrying non-JSON fields (enums, tuples,
        nested objects) must override this and ``from_persisted_dict``.

        Returns:
            dict[str, Any]: The condition's fields, ready to serialize.

        Raises:
            TypeError: If a field does not round-trip through JSON unchanged, which means the
                default cannot persist it faithfully.
        """
        fields = asdict(cast("DataclassInstance", self))
        if json.loads(json.dumps(fields)) != fields:
            raise TypeError(
                f"{type(self).__name__} has fields that do not survive a JSON round trip. "
                "Override to_persisted_dict and from_persisted_dict to persist them."
            )
        return fields

    @classmethod
    def from_persisted_dict(cls, value: dict[str, Any]) -> Condition:
        """
        Rebuild a condition from the fields produced by ``to_persisted_dict``.

        Args:
            value (dict[str, Any]): The persisted fields, without the discriminator.

        Returns:
            Condition: The reconstructed condition.
        """
        return cls(**value)


@dataclass(frozen=True, kw_only=True)
class MatchesObjective(Condition):
    """
    The evidence satisfies the expectation's own objective, as a judge reads it.

    This carries no text of its own. The objective lives on the ``ScoringExpectation``,
    so a scorer matching this condition reads it from there and the two can never
    disagree.
    """

    CONDITION_TYPE: ClassVar[str] = "matches_objective"


def condition_to_dict(condition: Condition) -> dict[str, Any]:
    """
    Serialize a condition to a discriminator-tagged dict.

    Args:
        condition (Condition): The condition to serialize.

    Returns:
        dict[str, Any]: ``{'condition_type': <discriminator>, **fields}``.

    Raises:
        ValueError: If the condition declares a field named ``condition_type``, which is
            reserved for the discriminator.
    """
    fields = condition.to_persisted_dict()
    if "condition_type" in fields:
        raise ValueError(
            f"{type(condition).__name__} declares a reserved field name 'condition_type'; "
            "the key is reserved for the discriminator."
        )
    return {"condition_type": condition.CONDITION_TYPE, **fields}


def condition_from_dict(value: dict[str, Any]) -> Condition:
    """
    Rebuild a condition from a discriminator-tagged dict.

    Args:
        value (dict[str, Any]): A dict produced by ``condition_to_dict``.

    Returns:
        Condition: The reconstructed condition.

    Raises:
        ValueError: If the discriminator names no registered condition type.
    """
    discriminator = value["condition_type"]
    condition_type = _CONDITION_TYPES.get(discriminator)
    if condition_type is None:
        raise ValueError(f"Unknown condition_type {discriminator!r}.")
    fields = {key: field_value for key, field_value in value.items() if key != "condition_type"}
    return condition_type.from_persisted_dict(fields)
