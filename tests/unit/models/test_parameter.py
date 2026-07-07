# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the unified Parameter model and its coercion methods."""

from enum import Enum
from typing import Literal

import pytest
from pydantic import ValidationError

from pyrit.common import REQUIRED_VALUE
from pyrit.models import Parameter
from pyrit.models.parameter import (
    ComponentType,
    RegistryReference,
    _is_scalar_param_type,
    display_choices,
)


class _Speed(Enum):
    FAST = "fast"
    SLOW = "slow"


class _Unsupported:
    """Stand-in for an arbitrary (non-scalar, non-registry) constructor type."""


class TestParameter:
    """Tests for the five-field pyrit.common.Parameter."""

    def test_minimal_construction(self) -> None:
        p = Parameter(name="x", description="some param")

        assert p.name == "x"
        assert p.description == "some param"
        assert p.default is None
        assert p.param_type is None
        assert p.reference is None

    def test_full_construction(self) -> None:
        p = Parameter(
            name="max_turns",
            description="turn cap",
            default=5,
            param_type=Literal[1, 5, 10],
        )

        assert p.default == 5
        assert p.param_type == Literal[1, 5, 10]

    def test_parameter_is_hashable(self) -> None:
        """Frozen dataclass means Parameters can live in sets and dict keys."""
        p = Parameter(name="x", description="d")

        assert {p} == {p}

    def test_list_param_type_accepted(self) -> None:
        """``param_type=list[str]`` is accepted (GenericAlias, not type)."""
        p = Parameter(name="datasets", description="d", param_type=list[str])

        assert p.param_type == list[str]

    def test_parameter_is_immutable(self) -> None:
        p = Parameter(name="x", description="d")

        with pytest.raises(ValidationError):
            p.name = "y"  # type: ignore[misc]


class TestParameterSerialization:
    """``Parameter.model_dump`` projects the live type into JSON-friendly display fields."""

    def test_scalar_with_default(self) -> None:
        dumped = Parameter(name="n", description="d", default=5, param_type=int).model_dump()

        assert dumped == {
            "name": "n",
            "description": "d",
            "default": "5",
            "type_name": "int",
            "required": False,
            "choices": None,
            "is_list": False,
        }

    def test_excludes_live_only_fields(self) -> None:
        dumped = Parameter(name="n", description="d", param_type=int).model_dump()

        assert "param_type" not in dumped
        assert "reference" not in dumped
        assert "destination" not in dumped

    def test_required_default_serializes_to_none(self) -> None:
        p = Parameter(name="mode", description="d", default=REQUIRED_VALUE, param_type=Literal["a", "b"])
        dumped = p.model_dump()

        assert dumped["required"] is True
        assert dumped["default"] is None
        assert dumped["type_name"] == "str"
        assert dumped["choices"] == ["a", "b"]

    def test_enum_default_serializes_to_member_value(self) -> None:
        dumped = Parameter(name="speed", description="d", default=_Speed.FAST, param_type=_Speed).model_dump()

        assert dumped["default"] == "fast"
        assert dumped["choices"] == ["fast", "slow"]

    def test_list_type_is_flagged(self) -> None:
        dumped = Parameter(name="tags", description="d", default=["x"], param_type=list[str]).model_dump()

        assert dumped["type_name"] == "list[str]"
        assert dumped["is_list"] is True
        assert dumped["default"] == ["x"]

    def test_list_default_serializes_elementwise(self) -> None:
        """A list default is preserved as a list of display strings, not flattened to ``"['1', '2']"``."""
        dumped = Parameter(name="nums", description="d", default=[1, 2], param_type=list[int]).model_dump()

        assert dumped["default"] == ["1", "2"]

    def test_optional_scalar_unwraps_to_base_name(self) -> None:
        """``Optional[int]`` renders the base scalar name, matching choices/coercion."""
        dumped = Parameter(name="n", description="d", param_type=int | None).model_dump()

        assert dumped["type_name"] == "int"


class TestIsScalarParamType:
    """``_is_scalar_param_type`` recognizes plain and constrained scalars."""

    @pytest.mark.parametrize("annotation", [str, int, float, bool, Literal["a", "b"], _Speed])
    def test_scalar_forms(self, annotation: object) -> None:
        assert _is_scalar_param_type(annotation) is True

    @pytest.mark.parametrize("annotation", [None, list[str], list[int], _Unsupported])
    def test_non_scalar_forms(self, annotation: object) -> None:
        assert _is_scalar_param_type(annotation) is False


class TestDisplayChoices:
    """``display_choices`` derives the allowed set from a constrained-scalar type."""

    def test_literal_returns_args(self) -> None:
        assert display_choices(Literal["fast", "slow"]) == ("fast", "slow")

    def test_optional_literal_unwrapped(self) -> None:
        assert display_choices(Literal["a", "b"] | None) == ("a", "b")

    def test_enum_returns_member_values(self) -> None:
        assert display_choices(_Speed) == ("fast", "slow")

    @pytest.mark.parametrize("annotation", [None, str, int, list[str]])
    def test_unconstrained_returns_none(self, annotation: object) -> None:
        assert display_choices(annotation) is None


class TestIsStringCoercible:
    """``Parameter.is_string_coercible`` reflects whether a string token can supply the value."""

    @pytest.mark.parametrize("param_type", [str, int, float, bool, Literal["a", "b"]])
    def test_coercible_value_types(self, param_type: object) -> None:
        p = Parameter(name="x", description="d", param_type=param_type)
        assert p.is_string_coercible is True

    @pytest.mark.parametrize("param_type", [None, list[str], _Speed, _Unsupported])
    def test_non_coercible_value_types(self, param_type: object) -> None:
        p = Parameter(name="x", description="d", param_type=param_type)
        assert p.is_string_coercible is False

    def test_reference_is_never_coercible(self) -> None:
        p = Parameter(
            name="target",
            description="d",
            reference=RegistryReference(component_type=ComponentType.TARGET),
        )
        assert p.is_string_coercible is False


class TestIsReferenceTo:
    """``Parameter.is_reference_to`` is the single predicate for "points at this component family"."""

    def test_matching_component_type_is_true(self) -> None:
        p = Parameter(
            name="converter_target",
            description="d",
            reference=RegistryReference(component_type=ComponentType.TARGET),
        )
        assert p.is_reference_to(ComponentType.TARGET) is True

    def test_other_component_type_is_false(self) -> None:
        p = Parameter(
            name="converter_target",
            description="d",
            reference=RegistryReference(component_type=ComponentType.TARGET),
        )
        assert p.is_reference_to(ComponentType.SCORER) is False

    def test_non_reference_is_false(self) -> None:
        p = Parameter(name="x", description="d", param_type=int)
        assert p.is_reference_to(ComponentType.TARGET) is False


class TestCoerceValueScalars:
    """``Parameter.coerce_value`` coerces plain scalars."""

    def test_int(self) -> None:
        p = Parameter(name="n", description="d", param_type=int)
        assert p.coerce_value("5") == 5

    def test_float(self) -> None:
        p = Parameter(name="r", description="d", param_type=float)
        assert p.coerce_value("0.25") == 0.25

    def test_bool(self) -> None:
        p = Parameter(name="flag", description="d", param_type=bool)
        assert p.coerce_value("yes") is True

    def test_str_passthrough(self) -> None:
        p = Parameter(name="s", description="d", param_type=str)
        assert p.coerce_value("hello") == "hello"

    def test_int_invalid_raises(self) -> None:
        p = Parameter(name="n", description="d", param_type=int)
        with pytest.raises(ValueError, match="could not be coerced to int"):
            p.coerce_value("not-a-number")

    def test_bool_invalid_raises(self) -> None:
        p = Parameter(name="flag", description="d", param_type=bool)
        with pytest.raises(ValueError, match="boolean"):
            p.coerce_value("maybe")


class TestCoerceValueConstrainedScalars:
    """``Parameter.coerce_value`` validates membership for Literal / Enum."""

    def test_literal_member(self) -> None:
        p = Parameter(name="speed", description="d", param_type=Literal["fast", "slow"])
        assert p.coerce_value("fast") == "fast"

    def test_literal_coerces_to_member_type(self) -> None:
        p = Parameter(name="n", description="d", param_type=Literal[1, 5, 10])
        result = p.coerce_value("5")
        assert result == 5
        assert isinstance(result, int)

    def test_literal_invalid_raises(self) -> None:
        p = Parameter(name="speed", description="d", param_type=Literal["fast", "slow"])
        with pytest.raises(ValueError, match="one of"):
            p.coerce_value("medium")

    def test_enum_by_value(self) -> None:
        p = Parameter(name="speed", description="d", param_type=_Speed)
        assert p.coerce_value("fast") is _Speed.FAST

    def test_enum_by_member(self) -> None:
        p = Parameter(name="speed", description="d", param_type=_Speed)
        assert p.coerce_value(_Speed.SLOW) is _Speed.SLOW

    def test_enum_invalid_raises(self) -> None:
        p = Parameter(name="speed", description="d", param_type=_Speed)
        with pytest.raises(ValueError, match="one of"):
            p.coerce_value("medium")


class TestCoerceValueLists:
    """``Parameter.coerce_value`` coerces each element of a ``list[...]`` param."""

    def test_list_str(self) -> None:
        p = Parameter(name="ds", description="d", param_type=list[str])
        assert p.coerce_value(["a", "b"]) == ["a", "b"]

    def test_list_int(self) -> None:
        p = Parameter(name="ns", description="d", param_type=list[int])
        assert p.coerce_value(["1", "2", "3"]) == [1, 2, 3]

    def test_list_literal_membership(self) -> None:
        p = Parameter(name="modes", description="d", param_type=list[Literal["a", "b"]])
        assert p.coerce_value(["a", "b", "a"]) == ["a", "b", "a"]

    def test_list_literal_invalid_raises(self) -> None:
        p = Parameter(name="modes", description="d", param_type=list[Literal["a", "b"]])
        with pytest.raises(ValueError, match="one of"):
            p.coerce_value(["a", "z"])

    def test_non_list_value_raises(self) -> None:
        p = Parameter(name="ds", description="d", param_type=list[str])
        with pytest.raises(ValueError, match="expects a list"):
            p.coerce_value("not-a-list")


class TestCoerceValuePassthrough:
    """Reference / arbitrary / None param_types pass through unchanged."""

    def test_param_type_none_returns_distinct_object(self) -> None:
        raw = ["a", "b"]
        coerced = Parameter(name="opts", description="d").coerce_value(raw)

        assert coerced == raw
        assert coerced is not raw

        raw.append("c")
        assert coerced == ["a", "b"]

    def test_unsupported_type_with_value_passes_through(self) -> None:
        sentinel = _Unsupported()
        p = Parameter(name="obj", description="d", param_type=_Unsupported)
        assert p.coerce_value(sentinel) is sentinel

    def test_reference_param_passes_value_through(self) -> None:
        """A reference parameter never coerces — the registry resolves it by name."""
        p = Parameter(
            name="converter_target",
            description="d",
            reference=RegistryReference(component_type=ComponentType.TARGET),
        )
        assert p.coerce_value("my_target") == "my_target"

    def test_opaque_param_passes_value_through_by_identity(self) -> None:
        """An opaque parameter returns the live object unchanged — never coerced or copied."""
        live = {"converter": object()}
        p = Parameter(name="strategy_converters", description="d", opaque=True)
        assert p.coerce_value(live) is live

    def test_opaque_param_does_not_deepcopy_none(self) -> None:
        """Opaque takes precedence over the ``param_type=None`` deep-copy passthrough."""
        raw = ["a", "b"]
        coerced = Parameter(name="cfg", description="d", opaque=True).coerce_value(raw)
        assert coerced is raw


class TestValidate:
    """``Parameter.validate`` accepts supported forms and tolerates defaulted others."""

    @pytest.mark.parametrize(
        "param_type",
        [None, str, int, float, bool, Literal["a", "b"], _Speed, list[str], list[int], list[Literal["a", "b"]]],
    )
    def test_supported_forms_ok(self, param_type: object) -> None:
        Parameter(name="x", description="d", param_type=param_type).validate()

    def test_unsupported_without_default_raises(self) -> None:
        p = Parameter(name="x", description="d", param_type=_Unsupported)
        with pytest.raises(ValueError, match="unsupported param_type"):
            p.validate()

    def test_unsupported_with_default_tolerated(self) -> None:
        p = Parameter(name="x", description="d", param_type=_Unsupported, default=_Unsupported())
        p.validate()

    def test_reference_param_is_valid(self) -> None:
        p = Parameter(
            name="target",
            description="d",
            reference=RegistryReference(component_type=ComponentType.TARGET),
        )
        p.validate()

    def test_opaque_param_is_valid_without_param_type_or_default(self) -> None:
        """An opaque parameter needs neither a ``param_type`` nor a default to validate."""
        Parameter(name="strategy_converters", description="d", opaque=True).validate()


class TestCoercionParity:
    """Derivation feeds ``coerce_value`` the unwrapped type, so coercion round-trips."""

    @pytest.mark.parametrize(
        "annotation, raw, expected",
        [
            (int, "7", 7),
            (float, "1.5", 1.5),
            (bool, "true", True),
            (Literal["a", "b"], "b", "b"),
            (Literal[1, 2], "2", 2),
            (int | None, "9", 9),
        ],
    )
    def test_derived_param_coerces(self, annotation: object, raw: str, expected: object) -> None:
        from pyrit.registry.resolution import derive_parameters

        class _Holder:
            def __init__(self, *, value=None) -> None:
                self.value = value

        _Holder.__init__.__annotations__["value"] = annotation
        param = next(p for p in derive_parameters(cls=_Holder) if p.name == "value")

        assert param.coerce_value(raw) == expected
