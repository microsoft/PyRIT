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
from pyrit.models.identifiers import ConverterIdentifier, TargetIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components import ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.resolution import (
    _registry_getter_for_component_type,
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


class _NeedsTargets:
    """Helper whose constructor takes a list-typed registry reference."""

    def __init__(self, *, targets: list[PromptTarget]) -> None:
        self.targets = targets


def _resolve(cls: type, raw_args: dict[str, object], *, identifier_type: type | None = None) -> dict[str, object]:
    """Resolve ``raw_args`` against the derived parameter contract for ``cls``."""
    return resolve_constructor_args(cls=cls, raw_args=raw_args, identifier_type=identifier_type)


@pytest.fixture
def target_registry():
    """Provide a fresh TargetRegistry singleton with one registered target."""
    TargetRegistry.reset_registry_singleton()
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(MockPromptTarget(), name="my_target")
    yield registry
    TargetRegistry.reset_registry_singleton()


@pytest.fixture
def empty_target_registry():
    """Provide a fresh, empty TargetRegistry singleton."""
    TargetRegistry.reset_registry_singleton()
    registry = TargetRegistry.get_registry_singleton()
    yield registry
    TargetRegistry.reset_registry_singleton()


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
        assert resolved["converter_target"] is target_registry.instances.get("my_target")
        assert resolved["offset"] == 5

    def test_resolves_list_registry_reference_by_name(self, target_registry: TargetRegistry) -> None:
        # A ``list[...]`` reference resolves each element by name (the list-aware path
        # used by RoundRobinTarget and composite scorers).
        target_registry.instances.register(MockPromptTarget(), name="second_target")
        resolved = _resolve(
            _NeedsTargets, {"targets": ["my_target", "second_target"]}, identifier_type=TargetIdentifier
        )
        assert resolved["targets"] == [
            target_registry.instances.get("my_target"),
            target_registry.instances.get("second_target"),
        ]

    def test_list_registry_reference_instance_passthrough(self, target_registry: TargetRegistry) -> None:
        # Non-string elements (already-built instances) pass through unchanged,
        # interleaved with names that are looked up.
        instance = MockPromptTarget()
        resolved = _resolve(_NeedsTargets, {"targets": ["my_target", instance]}, identifier_type=TargetIdentifier)
        assert resolved["targets"][0] is target_registry.instances.get("my_target")
        assert resolved["targets"][1] is instance

    def test_list_registry_reference_unknown_name_raises(self, target_registry: TargetRegistry) -> None:
        with pytest.raises(ValueError, match="missing"):
            _resolve(_NeedsTargets, {"targets": ["my_target", "missing"]}, identifier_type=TargetIdentifier)

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


# Component families whose references resolve by name, and the registry each maps to.
# Kept in the test (not imported from resolution.py) so it is an independent spec:
# the test fails if the production mapping drifts from this expectation.
_RESOLVABLE_COMPONENT_REGISTRIES = {
    ComponentType.TARGET: TargetRegistry,
    ComponentType.CONVERTER: ConverterRegistry,
    ComponentType.SCORER: ScorerRegistry,
}
# Scenarios are created by name, never referenced by name inside another component,
# so they are deliberately not wired for reference resolution.
_NON_RESOLVABLE_COMPONENT_TYPES = {ComponentType.SCENARIO}


def test_every_component_type_is_classified() -> None:
    # Guard against silently adding a ComponentType without deciding whether its
    # references resolve by name. A new member forces an update here (and to the
    # resolution map), rather than failing only at build time.
    classified = set(_RESOLVABLE_COMPONENT_REGISTRIES) | _NON_RESOLVABLE_COMPONENT_TYPES
    assert set(ComponentType) == classified


@pytest.mark.parametrize("component_type", list(_RESOLVABLE_COMPONENT_REGISTRIES))
def test_resolvable_component_type_maps_to_its_registry(component_type: ComponentType) -> None:
    getter = _registry_getter_for_component_type(component_type)
    assert getter is not None
    expected_registry = _RESOLVABLE_COMPONENT_REGISTRIES[component_type]
    assert getter() is expected_registry.get_registry_singleton().instances


@pytest.mark.parametrize("component_type", sorted(_NON_RESOLVABLE_COMPONENT_TYPES))
def test_non_resolvable_component_type_has_no_registry(component_type: ComponentType) -> None:
    assert _registry_getter_for_component_type(component_type) is None
