# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the shared registry constructor-argument resolution primitive.
"""

from typing import Literal

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import PromptTarget
from pyrit.registry.object_registries import TargetRegistry
from pyrit.registry.resolution import (
    coerce_string_to_annotation,
    get_resolvable_registry_getter,
    get_union_non_none_args,
    is_coercible_from_string,
    is_registry_reference,
    resolve_constructor_args,
)


class MockPromptTarget(PromptTarget):
    """Minimal PromptTarget for registry-resolution tests."""

    def __init__(self, *, model_name: str = "mock_model") -> None:
        super().__init__(model_name=model_name)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [MessagePiece(role="assistant", original_value="mock response").to_message()]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass


class _NeedsTarget:
    """Helper whose constructor takes a registry-reference target plus simple params."""

    def __init__(self, *, converter_target: PromptTarget, offset: int = 0, label: str = "x") -> None:
        self.converter_target = converter_target
        self.offset = offset
        self.label = label


class _SimpleOnly:
    """Helper whose constructor takes only simple/coercible params."""

    def __init__(
        self, *, count: int = 1, ratio: float = 0.5, flag: bool = False, mode: Literal["a", "b"] = "a"
    ) -> None:
        self.count = count
        self.ratio = ratio
        self.flag = flag
        self.mode = mode


class _AcceptsKwargs:
    """Helper whose constructor accepts arbitrary keyword arguments."""

    def __init__(self, *, name: str = "n", **kwargs: object) -> None:
        self.name = name
        self.kwargs = kwargs


@pytest.fixture
def target_registry():
    """Provide a fresh TargetRegistry singleton with one registered target."""
    TargetRegistry.reset_instance()
    registry = TargetRegistry.get_registry_singleton()
    registry.register_instance(MockPromptTarget(), name="my_target")
    yield registry
    TargetRegistry.reset_instance()


@pytest.fixture
def empty_target_registry():
    """Provide a fresh, empty TargetRegistry singleton."""
    TargetRegistry.reset_instance()
    registry = TargetRegistry.get_registry_singleton()
    yield registry
    TargetRegistry.reset_instance()


class TestTypeHelpers:
    """Tests for the type-introspection helpers."""

    def test_get_union_non_none_args_pep604(self) -> None:
        assert get_union_non_none_args(int | None) == [int]

    def test_get_union_non_none_args_not_a_union(self) -> None:
        assert get_union_non_none_args(int) is None

    def test_is_coercible_from_string(self) -> None:
        assert is_coercible_from_string(str) is True
        assert is_coercible_from_string(int | None) is True
        assert is_coercible_from_string(Literal["a", "b"]) is True
        assert is_coercible_from_string(PromptTarget) is False

    def test_is_registry_reference(self) -> None:
        assert is_registry_reference(PromptTarget) is True
        assert is_registry_reference(PromptTarget | None) is True
        assert is_registry_reference(int) is False

    def test_get_resolvable_registry_getter_returns_target_registry(self) -> None:
        getter = get_resolvable_registry_getter(PromptTarget)
        assert getter is not None
        assert isinstance(getter(), TargetRegistry)

    def test_get_resolvable_registry_getter_none_for_simple(self) -> None:
        assert get_resolvable_registry_getter(int) is None


class TestCoerceStringToAnnotation:
    """Tests for scalar string coercion."""

    def test_int(self) -> None:
        assert coerce_string_to_annotation(value="42", annotation=int) == 42

    def test_float(self) -> None:
        assert coerce_string_to_annotation(value="0.25", annotation=float) == 0.25

    def test_bool_true(self) -> None:
        assert coerce_string_to_annotation(value="yes", annotation=bool) is True

    def test_bool_false(self) -> None:
        assert coerce_string_to_annotation(value="0", annotation=bool) is False

    def test_bool_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="boolean"):
            coerce_string_to_annotation(value="maybe", annotation=bool)

    def test_optional_unwrapped(self) -> None:
        assert coerce_string_to_annotation(value="7", annotation=int | None) == 7

    def test_str_passthrough(self) -> None:
        assert coerce_string_to_annotation(value="hello", annotation=str) == "hello"

    def test_literal_valid(self) -> None:
        assert coerce_string_to_annotation(value="b", annotation=Literal["a", "b"]) == "b"

    def test_literal_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="one of"):
            coerce_string_to_annotation(value="c", annotation=Literal["a", "b"])

    def test_literal_coerces_to_member_type(self) -> None:
        result = coerce_string_to_annotation(value="2", annotation=Literal[1, 2])
        assert result == 2
        assert isinstance(result, int)


@pytest.mark.usefixtures("patch_central_database")
class TestResolveConstructorArgs:
    """Tests for the end-to-end resolve_constructor_args."""

    def test_coerces_simple_params(self) -> None:
        resolved = resolve_constructor_args(cls=_SimpleOnly, raw_args={"count": "3", "ratio": "0.75", "flag": "true"})
        assert resolved == {"count": 3, "ratio": 0.75, "flag": True}

    def test_literal_passthrough(self) -> None:
        resolved = resolve_constructor_args(cls=_SimpleOnly, raw_args={"mode": "b"})
        assert resolved == {"mode": "b"}

    def test_literal_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            resolve_constructor_args(cls=_SimpleOnly, raw_args={"mode": "z"})

    def test_unknown_param_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown parameter 'nope'"):
            resolve_constructor_args(cls=_SimpleOnly, raw_args={"nope": "1"})

    def test_unknown_param_lists_valid_params(self) -> None:
        with pytest.raises(ValueError, match="count"):
            resolve_constructor_args(cls=_SimpleOnly, raw_args={"nope": "1"})

    def test_var_kwargs_accepts_unknown(self) -> None:
        resolved = resolve_constructor_args(cls=_AcceptsKwargs, raw_args={"anything": "value"})
        assert resolved == {"anything": "value"}

    def test_invalid_coercion_raises(self) -> None:
        with pytest.raises(ValueError, match="count"):
            resolve_constructor_args(cls=_SimpleOnly, raw_args={"count": "not-an-int"})

    def test_resolves_registry_reference_by_name(self, target_registry: TargetRegistry) -> None:
        resolved = resolve_constructor_args(cls=_NeedsTarget, raw_args={"converter_target": "my_target", "offset": "5"})
        assert resolved["converter_target"] is target_registry.get_instance_by_name("my_target")
        assert resolved["offset"] == 5

    def test_registry_reference_instance_passthrough(self, target_registry: TargetRegistry) -> None:
        instance = MockPromptTarget()
        resolved = resolve_constructor_args(cls=_NeedsTarget, raw_args={"converter_target": instance})
        assert resolved["converter_target"] is instance

    def test_unknown_registry_reference_raises_with_names(self, target_registry: TargetRegistry) -> None:
        with pytest.raises(ValueError, match="my_target"):
            resolve_constructor_args(cls=_NeedsTarget, raw_args={"converter_target": "missing"})

    def test_unknown_registry_reference_empty_registry_hint(self, empty_target_registry: TargetRegistry) -> None:
        with pytest.raises(ValueError, match="is empty"):
            resolve_constructor_args(cls=_NeedsTarget, raw_args={"converter_target": "missing"})


def test_module_has_no_backend_dependency() -> None:
    # The resolution primitive must be reusable without depending on pyrit.backend.
    import ast
    import inspect

    import pyrit.registry.resolution as module

    tree = ast.parse(inspect.getsource(module))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    assert not any(name.startswith("pyrit.backend") for name in imported_modules)
