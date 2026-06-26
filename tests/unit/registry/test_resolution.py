# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the shared registry constructor-argument resolution primitive.
"""

from typing import Literal

import pytest

from pyrit.common import REQUIRED_VALUE
from pyrit.common.apply_defaults import _RequiredValueSentinel
from pyrit.models import Message, MessagePiece
from pyrit.models.identifiers import ConverterIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.prompt_target import PromptTarget
from pyrit.registry.object_registries import TargetRegistry
from pyrit.registry.resolution import (
    derive_parameters,
    display_choices,
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


class _Plain:
    def __init__(
        self, *, count: int, ratio: float = 0.5, mode: Literal["a", "b"] = "a", note: str | None = None
    ) -> None:
        """Plain converter-like helper.

        Args:
            count (int): A required count.
            ratio (float): A ratio with a default.
            mode (Literal): A constrained mode.
            note (str): An optional note.
        """
        self.count = count
        self.ratio = ratio
        self.mode = mode
        self.note = note


class _SentinelDefault:
    def __init__(self, *, value: int = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
        self.value = value


class _VarArgs:
    def __init__(self, *args: object, name: str = "n", **kwargs: object) -> None:
        self.name = name


class _StrTargetArg:
    """A constructor arg named like the identifier reference but annotated as a plain type."""

    def __init__(self, *, converter_target: str = "x") -> None:
        self.converter_target = converter_target


def _resolve(cls: type, raw_args: dict[str, object], *, identifier_type: type | None = None) -> dict[str, object]:
    """Resolve ``raw_args`` against the derived parameter contract for ``cls``."""
    return resolve_constructor_args(cls=cls, raw_args=raw_args, identifier_type=identifier_type)


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


class TestDisplayChoices:
    """Tests for the allowed-value presentation projection."""

    def test_literal(self) -> None:
        assert display_choices(Literal["a", "b"]) == ("a", "b")

    def test_optional_literal_unwrapped(self) -> None:
        assert display_choices(Literal["a", "b"] | None) == ("a", "b")

    def test_unconstrained_returns_none(self) -> None:
        assert display_choices(int) is None


@pytest.mark.usefixtures("patch_central_database")
class TestResolveConstructorArgs:
    """Tests for the end-to-end resolve_constructor_args over a derived contract."""

    def test_coerces_simple_params(self) -> None:
        resolved = _resolve(_SimpleOnly, {"count": "3", "ratio": "0.75", "flag": "true"})
        assert resolved == {"count": 3, "ratio": 0.75, "flag": True}

    def test_literal_passthrough(self) -> None:
        resolved = _resolve(_SimpleOnly, {"mode": "b"})
        assert resolved == {"mode": "b"}

    def test_literal_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            _resolve(_SimpleOnly, {"mode": "z"})

    def test_unknown_param_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown parameter 'nope'"):
            _resolve(_SimpleOnly, {"nope": "1"})

    def test_unknown_param_lists_valid_params(self) -> None:
        with pytest.raises(ValueError, match="count"):
            _resolve(_SimpleOnly, {"nope": "1"})

    def test_invalid_coercion_raises(self) -> None:
        with pytest.raises(ValueError, match="count"):
            _resolve(_SimpleOnly, {"count": "not-an-int"})

    def test_resolves_registry_reference_by_name(self, target_registry: TargetRegistry) -> None:
        resolved = _resolve(
            _NeedsTarget, {"converter_target": "my_target", "offset": "5"}, identifier_type=ConverterIdentifier
        )
        assert resolved["converter_target"] is target_registry.get_instance_by_name("my_target")
        assert resolved["offset"] == 5

    def test_registry_reference_instance_passthrough(self, target_registry: TargetRegistry) -> None:
        instance = MockPromptTarget()
        resolved = _resolve(_NeedsTarget, {"converter_target": instance}, identifier_type=ConverterIdentifier)
        assert resolved["converter_target"] is instance

    def test_unknown_registry_reference_raises_with_names(self, target_registry: TargetRegistry) -> None:
        with pytest.raises(ValueError, match="my_target"):
            _resolve(_NeedsTarget, {"converter_target": "missing"}, identifier_type=ConverterIdentifier)

    def test_unknown_registry_reference_empty_registry_hint(self, empty_target_registry: TargetRegistry) -> None:
        with pytest.raises(ValueError, match="is empty"):
            _resolve(_NeedsTarget, {"converter_target": "missing"}, identifier_type=ConverterIdentifier)


class TestDeriveParameters:
    """Tests for deriving the Parameter contract from a constructor signature."""

    def test_required_and_defaults(self) -> None:
        params = {p.name: p for p in derive_parameters(cls=_Plain)}
        assert params["count"].default is REQUIRED_VALUE
        assert params["ratio"].default == 0.5
        assert params["count"].param_type is int

    def test_optional_unwrapped(self) -> None:
        params = {p.name: p for p in derive_parameters(cls=_Plain)}
        assert params["note"].param_type is str

    def test_descriptions_parsed(self) -> None:
        params = {p.name: p for p in derive_parameters(cls=_Plain)}
        assert params["count"].description == "A required count."

    def test_order_follows_signature(self) -> None:
        names = [p.name for p in derive_parameters(cls=_Plain)]
        assert names == ["count", "ratio", "mode", "note"]

    def test_sentinel_default_is_required(self) -> None:
        param = derive_parameters(cls=_SentinelDefault)[0]
        assert param.default is REQUIRED_VALUE
        assert isinstance(REQUIRED_VALUE, _RequiredValueSentinel)

    def test_var_args_skipped(self) -> None:
        names = [p.name for p in derive_parameters(cls=_VarArgs)]
        assert names == ["name"]

    def test_identifier_marker_overrides_plain_annotation(self) -> None:
        # The identifier marks ``converter_target`` as a TARGET reference, so even a
        # plainly-annotated arg of that name becomes a reference (the marker wins).
        param = derive_parameters(cls=_StrTargetArg, identifier_type=ConverterIdentifier)[0]
        assert param.reference is not None
        assert param.reference.component_type is ComponentType.TARGET

    def test_no_identifier_yields_no_references(self) -> None:
        # Without an identifier, no parameter is treated as a reference.
        param = derive_parameters(cls=_StrTargetArg)[0]
        assert param.reference is None
        assert param.param_type is str


def test_signature_inspection_failure_raises() -> None:
    class _NoInit:
        __init__ = None  # type: ignore[assignment]

    with pytest.raises(ValueError, match="Failed to inspect"):
        derive_parameters(cls=_NoInit)


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
