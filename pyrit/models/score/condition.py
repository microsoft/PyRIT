# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast, get_args, get_origin

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SerializeAsAny, TypeAdapter, model_validator

from pyrit.models.score._trace_validation import ToolName  # noqa: TC001 (runtime-required by Pydantic)

if TYPE_CHECKING:
    from typing import Self

    from pydantic import GetJsonSchemaHandler
    from pydantic.config import ExtraValues
    from pydantic.json_schema import JsonSchemaValue
    from pydantic_core import CoreSchema

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
            get_args(field.annotation) if field is not None and get_origin(field.annotation) is Literal else ()
        )
        if len(literal_values) != 1 or not isinstance(literal_values[0], str) or not literal_values[0]:
            raise TypeError(
                f"{cls.__name__}.condition_type must be a single non-empty string Literal with a matching default."
            )
        discriminator = literal_values[0]
        if field.default != discriminator:
            raise TypeError(f"{cls.__name__}.condition_type must default to its Literal value {discriminator!r}.")
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


class ToolCallRequirement(BaseModel):
    """One exact, case-sensitive tool name required as execution evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ToolName


class ToolsCalled(Condition):
    """Require every named tool to have an execution attempt, regardless of success."""

    condition_type: Literal["tools_called"] = "tools_called"
    tools: tuple[ToolCallRequirement, ...]

    @model_validator(mode="after")
    def _validate_tools(self) -> ToolsCalled:
        """
        Require a nonempty set of distinct exact names.

        Returns:
            ToolsCalled: The validated condition.

        Raises:
            ValueError: If there are no tools or an exact name repeats.
        """
        if not self.tools:
            raise ValueError("ToolsCalled requires at least one tool.")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("ToolsCalled requires each tool name once.")
        return self


class DivergesFromRepetition(Condition):
    """The evidence continues with other content after repeating the literal text."""

    condition_type: Literal["diverges_from_repetition"] = "diverges_from_repetition"
    text: str = Field(min_length=1, pattern=r"\S")


class AnswerMatches(Condition):
    """The evidence answers a question with the expected choice label or answer text."""

    condition_type: Literal["answer_matches"] = "answer_matches"
    correct_answer: str = Field(min_length=1)
    #: A nonempty choice label. It is not cross-checked against choices in prompt text.
    correct_answer_label: str | None = Field(default=None, min_length=1)


def _parse_conditions(value: Any) -> Any:
    """
    Rebuild discriminator-tagged conditions without losing subclass fields.

    Returns:
        Any: The conditions with serialized entries resolved to concrete types.

    Raises:
        ValueError: If the input is not a condition collection or names an unknown type.
    """
    if value is None:
        return ()
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, Iterable):
        raise ValueError("conditions must be an iterable of typed conditions.")
    return tuple(Condition.model_validate(item) if isinstance(item, dict) else item for item in value)


class _ConditionTupleSchema:
    """Describe the registered condition types on each condition-list field."""

    def __get_pydantic_json_schema__(self, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        """Return the tuple schema with a discriminated union of concrete conditions."""
        schema = handler(core_schema)
        condition_schemas = [
            handler(TypeAdapter(condition_class).core_schema) for _, condition_class in sorted(_CONDITION_TYPES.items())
        ]
        for condition_schema in condition_schemas:
            required_fields = condition_schema.setdefault("required", [])
            if "condition_type" not in required_fields:
                required_fields.append("condition_type")
        schema["items"] = {
            "discriminator": {"propertyName": "condition_type"},
            "oneOf": condition_schemas,
        }
        return schema


ConditionTuple = Annotated[
    tuple[SerializeAsAny[Condition], ...],
    BeforeValidator(_parse_conditions),
    _ConditionTupleSchema(),
]
