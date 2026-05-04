# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the unified Parameter dataclass."""

import pytest

from pyrit.common import Parameter


class TestParameter:
    """Tests for pyrit.common.Parameter."""

    def test_minimal_construction(self) -> None:
        """Parameter requires only name and description."""
        p = Parameter(name="x", description="some param")

        assert p.name == "x"
        assert p.description == "some param"
        assert p.default is None
        assert p.param_type is None
        assert p.choices is None

    def test_full_construction(self) -> None:
        """All fields can be supplied."""
        p = Parameter(
            name="max_turns",
            description="turn cap",
            default=5,
            param_type=int,
            choices=(1, 5, 10),
        )

        assert p.default == 5
        assert p.param_type is int
        assert p.choices == (1, 5, 10)

    def test_parameter_is_hashable(self) -> None:
        """Frozen dataclass means Parameters can live in sets and dict keys."""
        p = Parameter(name="x", description="d")

        # If hash() raised, this set construction would fail.
        assert {p} == {p}

    def test_choices_list_is_normalized_to_tuple(self) -> None:
        """A list passed for choices is coerced to a tuple to keep the dataclass hashable."""
        p = Parameter(name="x", description="d", choices=["a", "b", "c"])

        assert p.choices == ("a", "b", "c")
        assert isinstance(p.choices, tuple)

        # And the resulting Parameter is still hashable.
        _ = hash(p)

    def test_choices_none_stays_none(self) -> None:
        """Default None choices is preserved (no spurious tuple coercion)."""
        p = Parameter(name="x", description="d")

        assert p.choices is None

    def test_choices_coerced_to_int_param_type(self) -> None:
        """Stringy int choices are coerced so argparse and runtime both see ints."""
        p = Parameter(name="x", description="d", param_type=int, choices=("1", "5", "10"))

        assert p.choices == (1, 5, 10)
        assert all(isinstance(c, int) for c in p.choices)

    def test_choices_coerced_to_bool_param_type(self) -> None:
        p = Parameter(name="x", description="d", param_type=bool, choices=("true", "false"))

        assert p.choices == (True, False)

    def test_choices_uncoercible_left_unchanged(self) -> None:
        """Uncoercible choices are left as-is so _validate_declarations can surface a clear error."""
        p = Parameter(name="x", description="d", param_type=int, choices=("not-a-number", "5"))

        # Original tuple preserved. The downstream validator emits the friendly
        # "scenario X parameter Y choice Z is not coercible" error.
        assert p.choices == ("not-a-number", "5")

    def test_choices_skipped_for_none_param_type(self) -> None:
        """When param_type is None (raw passthrough) choices stay as-declared."""
        p = Parameter(name="x", description="d", choices=("a", "b"))

        assert p.choices == ("a", "b")

    def test_list_param_type_accepted(self) -> None:
        """``param_type=list[str]`` is accepted (GenericAlias, not type)."""
        p = Parameter(name="datasets", description="d", param_type=list[str])

        assert p.param_type == list[str]

    def test_parameter_is_immutable(self) -> None:
        """Frozen dataclass rejects field assignment after construction."""
        p = Parameter(name="x", description="d")

        with pytest.raises((AttributeError, TypeError)):
            p.name = "y"  # type: ignore[misc]


class TestCoerceValuePassthroughDeepcopy:
    """``coerce_value`` deep-copies raw passthrough values for ``param_type=None``."""

    def test_param_type_none_returns_distinct_object(self) -> None:
        """A mutable raw value must not share identity with the coerced result."""
        from pyrit.common.parameter import coerce_value

        raw = ["a", "b"]
        coerced = coerce_value(param=Parameter(name="opts", description="d"), raw_value=raw)

        assert coerced == raw
        assert coerced is not raw

        raw.append("c")
        assert coerced == ["a", "b"]
