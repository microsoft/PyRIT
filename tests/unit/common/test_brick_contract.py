# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.common.brick_contract import (
    enforce_keyword_only_init,
    forward_init_parameters,
    init_parameters_are_forwarded,
)


class _FakeBase:
    """Standalone base class used to drive the helper in isolation."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        enforce_keyword_only_init(cls, base_name="_FakeBase")


def test_forward_init_parameters_marks_variadic_constructor() -> None:
    @forward_init_parameters
    def _init(self: object, **kwargs: object) -> None:
        pass

    assert init_parameters_are_forwarded(_init)


def test_forward_init_parameters_rejects_non_variadic_constructor() -> None:
    with pytest.raises(TypeError, match=r"requires a constructor that accepts \*\*kwargs"):

        @forward_init_parameters
        def _init(self: object, *, value: str) -> None:
            pass


def test_compliant_keyword_only_init_passes() -> None:
    class Compliant(_FakeBase):
        def __init__(self, *, foo: str, bar: int = 0) -> None:
            self.foo = foo
            self.bar = bar

    instance = Compliant(foo="hello", bar=3)
    assert instance.foo == "hello"
    assert instance.bar == 3


def test_self_only_init_passes() -> None:
    class SelfOnly(_FakeBase):
        def __init__(self) -> None:
            pass

    assert SelfOnly() is not None


def test_inherited_init_is_not_double_checked() -> None:
    """Subclasses without their own __init__ inherit the (compliant) parent."""

    class Parent(_FakeBase):
        def __init__(self, *, foo: str = "") -> None:
            self.foo = foo

    class Child(Parent):
        pass

    assert Child(foo="x").foo == "x"


def test_positional_init_raises_typeerror() -> None:
    with pytest.raises(TypeError) as excinfo:

        class Violator(_FakeBase):
            def __init__(self, foo: str, bar: int = 0) -> None:
                self.foo = foo
                self.bar = bar

    message = str(excinfo.value)
    assert "_FakeBase contract" in message
    assert "foo" in message
    assert "bar" in message


def test_positional_or_keyword_default_still_raises() -> None:
    """A param with a default is still positional-or-keyword by default."""
    with pytest.raises(TypeError):

        class StillPositional(_FakeBase):
            def __init__(self, foo: str = "x") -> None:
                self.foo = foo


def test_starargs_without_star_marker_raises() -> None:
    """``*args`` after positional params doesn't fix the positional params."""
    with pytest.raises(TypeError) as excinfo:

        class StarArgsSandwich(_FakeBase):
            def __init__(self, foo: str = "", *args: object) -> None:
                self.foo = foo

    assert "foo" in str(excinfo.value)


def test_starargs_first_passes() -> None:
    """``*args`` immediately after ``self`` makes subsequent params kw-only."""

    class StarArgsFirst(_FakeBase):
        def __init__(self, *args: object, bar: int = 0) -> None:
            self.args = args
            self.bar = bar

    assert StarArgsFirst(bar=1).bar == 1


def test_error_message_lists_only_positional_offenders() -> None:
    """The error message should only list positional offenders, not kw-only ones."""
    with pytest.raises(TypeError) as excinfo:

        class Mixed(_FakeBase):
            def __init__(self, positional_one: str, *, keyword_only: int = 0) -> None:
                self.positional_one = positional_one
                self.keyword_only = keyword_only

    message = str(excinfo.value)
    assert "positional_one" in message
    assert "keyword_only" not in message
