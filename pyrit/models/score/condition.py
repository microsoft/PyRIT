# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast, get_args, get_origin

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from pydantic.config import ExtraValues
    from typing_extensions import Self

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

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """
        Validate a condition, dispatching the registry root to its concrete subtype.

        Args:
            obj (Any): The condition instance or discriminator-tagged representation.
            strict (bool | None): Whether Pydantic uses strict validation.
            extra (ExtraValues | None): How Pydantic handles extra fields.
            from_attributes (bool | None): Whether Pydantic reads object attributes.
            context (Any | None): Context supplied to Pydantic validators.
            by_alias (bool | None): Whether Pydantic accepts field aliases.
            by_name (bool | None): Whether Pydantic accepts field names.

        Returns:
            Self: The validated concrete condition.

        Raises:
            ValueError: If the abstract root receives an invalid or unknown discriminator.
        """
        if cls is Condition and isinstance(obj, dict):
            discriminator = obj.get("condition_type")
            if not isinstance(discriminator, str):
                raise ValueError("Condition requires a string condition_type discriminator.")
            condition_type = _CONDITION_TYPES.get(discriminator)
            if condition_type is None:
                raise ValueError(f"Unknown condition_type {discriminator!r}.")
            return cast(
                "Self",
                condition_type.model_validate(
                    obj,
                    strict=strict,
                    extra=extra,
                    from_attributes=from_attributes,
                    context=context,
                    by_alias=by_alias,
                    by_name=by_name,
                ),
            )
        return super().model_validate(
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


class MatchesObjective(Condition):
    """
    The evidence satisfies the expectation's own objective, as a judge reads it.

    This carries no text of its own. The objective lives on the ``ScoringExpectation``,
    so a scorer matching this condition reads it from there and the two can never
    disagree.
    """

    condition_type: Literal["matches_objective"] = "matches_objective"
