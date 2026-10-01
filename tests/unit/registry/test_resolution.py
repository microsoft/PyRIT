# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the shared registry constructor-argument resolution primitive.
"""

import contextlib
import json
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import pytest

from pyrit.common import REQUIRED_VALUE, forward_init_parameters
from pyrit.common.apply_defaults import _RequiredValueSentinel
from pyrit.models import Message, MessagePiece
from pyrit.models.identifiers import ConverterIdentifier, ScorerIdentifier, TargetIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components import ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.resolution import (
    _registry_getter_for_component_type,
    derive_parameters,
    display_choices,
    resolve_constructor_args,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget as _TypeCheckingOnlyTarget


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


class _Speed(Enum):
    FAST = "fast"
    SLOW = "slow"


class _EnumOnly:
    """Helper whose constructor takes an enum parameter."""

    def __init__(self, *, speed: _Speed) -> None:
        self.speed = speed


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


class _ForwardedParent:
    def __init__(self, *, count: int = 1, speed: _Speed = _Speed.FAST) -> None:
        self.count = count
        self.speed = speed


class _ForwardingChild(_ForwardedParent):
    @forward_init_parameters
    def __init__(self, *, label: str = "child", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.label = label


class _OpaqueParent:
    def __init__(self, *, count: int = 1) -> None:
        self.count = count


class _OpenBagChild(_OpaqueParent):
    def __init__(self, *, label: str = "child", **options: object) -> None:
        super().__init__()
        self.label = label
        self.options = options


class _StrTargetArg:
    """A constructor arg named like the identifier reference but annotated as a plain type."""

    def __init__(self, *, converter_target: str = "x") -> None:
        self.converter_target = converter_target


class _NeedsTargets:
    """Helper whose constructor takes a list-typed registry reference."""

    def __init__(self, *, targets: list[PromptTarget]) -> None:
        self.targets = targets


@dataclass
class _Settings:
    level: int = 0


class _Provider(Protocol):
    def provide(self) -> str: ...


class _Sized(Protocol):
    def __len__(self) -> int: ...


class _Unresolved:
    """Helper whose annotation names a type-checking-only import, as many components do."""

    def __init__(self, *, target: "_TypeCheckingOnlyTarget | None" = None) -> None:
        self.target = target


class _Handle:
    """A live object type that no JSON value can represent."""


class _Bag(list[int]):
    """A live list subclass that callers pass as an existing object."""


class _JsonShaped:
    """Helper whose constructor takes container and object parameters that JSON callers supply."""

    def __init__(
        self,
        *,
        color: tuple[int, int, int] = (0, 0, 0),
        weights: list[int] | None = None,
        extra: dict[str, int] | None = None,
        speed: _Speed | None = None,
        speeds: list[_Speed] | None = None,
        modes: list[Literal["a", "b"] | None] | None = None,
        note: str | None = None,
        words: Collection[str] | None = None,
        settings: _Settings | None = None,
        location: _Speed | Path | None = None,
        provider: _Provider | None = None,
        sized: _Sized | None = None,
        options: dict[str, Any] | None = None,
        handle: _Handle | None = None,
        groups: dict[str, Collection[str]] | None = None,
        choices: Collection[str] | _Handle | None = None,
        bag: _Bag | None = None,
        anything: Collection | None = None,
    ) -> None:
        self.color = color
        self.weights = weights
        self.extra = extra
        self.speed = speed
        self.speeds = speeds
        self.modes = modes
        self.note = note
        self.words = words
        self.settings = settings
        self.location = location
        self.provider = provider
        self.sized = sized
        self.options = options
        self.handle = handle
        self.groups = groups
        self.choices = choices
        self.bag = bag
        self.anything = anything


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

    def test_enum_string_coerces_to_member(self) -> None:
        resolved = _resolve(_EnumOnly, {"speed": "fast"})
        assert resolved == {"speed": _Speed.FAST}

    def test_forwarded_parent_params_are_coerced(self) -> None:
        resolved = _resolve(_ForwardingChild, {"label": "configured", "count": "3", "speed": "slow"})
        assert resolved == {"label": "configured", "count": 3, "speed": _Speed.SLOW}

    def test_open_bag_does_not_disable_unknown_param_rejection(self) -> None:
        with pytest.raises(ValueError, match="Unknown parameter 'count'"):
            _resolve(_OpenBagChild, {"count": "3"})

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

    @pytest.mark.parametrize(
        ("cls", "raw_args"),
        [
            (_SimpleOnly, {"count": {"value": 1}}),
            (_SimpleOnly, {"count": [5]}),
            (_SimpleOnly, {"count": True}),
            (_SimpleOnly, {"count": 1.5}),
            (_SimpleOnly, {"count": None}),
            (_SimpleOnly, {"ratio": {"value": 1}}),
            (_SimpleOnly, {"flag": 1}),
            (_JsonShaped, {"note": 5}),
            (_JsonShaped, {"weights": [1, "2"]}),
            (_JsonShaped, {"extra": "not-an-object"}),
            (_JsonShaped, {"color": [1, 2]}),
            (_JsonShaped, {"color": None}),
            (_JsonShaped, {"words": [1, "a"]}),
            (_JsonShaped, {"words": "the"}),
            (_JsonShaped, {"groups": {"group": [1]}}),
            (_JsonShaped, {"choices": {}}),
            (_JsonShaped, {"anything": 7}),
            (_JsonShaped, {"modes": ["c"]}),
        ],
    )
    def test_rejects_json_value_of_wrong_type(self, cls: type, raw_args: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="expects"):
            _resolve(cls, raw_args)

    @pytest.mark.parametrize(
        ("cls", "raw_args"),
        [
            (_SimpleOnly, {"ratio": 1}),
            (_SimpleOnly, {"count": 3, "flag": False}),
            (_JsonShaped, {"weights": [1, 2], "extra": {"a": 1}}),
            (_JsonShaped, {"note": None, "settings": None}),
            (_JsonShaped, {"words": ["the", "a"]}),
            (_JsonShaped, {"groups": {"group": ["one", "two"]}}),
            (_JsonShaped, {"choices": ["the", "a"]}),
            (_JsonShaped, {"anything": [1, "a"]}),
            (_JsonShaped, {"modes": []}),
            (_JsonShaped, {"modes": ["a", None]}),
        ],
    )
    def test_accepts_matching_json_value_unchanged(self, cls: type, raw_args: dict[str, object]) -> None:
        assert _resolve(cls, raw_args) == raw_args

    def test_json_array_becomes_tuple_for_tuple_parameter(self) -> None:
        assert _resolve(_JsonShaped, {"color": [10, 20, 30]})["color"] == (10, 20, 30)

    @pytest.mark.parametrize("raw_args", [{"settings": {"level": 1}}, {"location": "fast"}, {"location": "/tmp/x"}])
    def test_rejects_json_value_that_needs_an_object(self, raw_args: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="cannot be built from JSON"):
            _resolve(_JsonShaped, raw_args)

    def test_live_objects_pass_through_unchecked(self) -> None:
        color = object()
        weights = [object()]
        options = {"timeout": (5.0, 10.0)}
        extra: dict[str, object] = {}
        extra["self"] = extra
        bag = _Bag([1, 2])
        live = {"color": color, "weights": weights, "options": options, "extra": extra, "bag": bag}

        resolved = _resolve(_JsonShaped, live)

        assert all(resolved[name] is value for name, value in live.items())

    def test_deeply_nested_json_value_is_rejected(self) -> None:
        nested: object = 1
        for _ in range(500):
            nested = [nested]

        with pytest.raises(ValueError, match="expects"):
            _resolve(_SimpleOnly, {"count": nested})

    def test_protocol_parameter_requires_protocol_members(self) -> None:
        with pytest.raises(ValueError, match="provider"):
            _resolve(_JsonShaped, {"provider": "anything"})
        with pytest.raises(ValueError, match="sized"):
            _resolve(_JsonShaped, {"sized": 7})

        assert _resolve(_JsonShaped, {"provider": None, "sized": [1, 2]}) == {"provider": None, "sized": [1, 2]}

    def test_unresolved_annotation_is_left_to_constructor(self) -> None:
        assert _resolve(_Unresolved, {"target": {"a": 1}}) == {"target": {"a": 1}}

    def test_json_value_for_object_parameter_is_rejected(self) -> None:
        handle = _Handle()

        with pytest.raises(ValueError, match="handle"):
            _resolve(_JsonShaped, {"handle": {}})
        assert _resolve(_JsonShaped, {"handle": None}) == {"handle": None}
        assert _resolve(_JsonShaped, {"handle": handle})["handle"] is handle

    @pytest.mark.parametrize(
        ("registry_type", "identifier_type", "type_name", "raw_args"),
        [
            (TargetRegistry, TargetIdentifier, "TextTarget", {"custom_configuration": {}}),
            (TargetRegistry, TargetIdentifier, "A2ATarget", {"auth_token": {"token": "x"}}),
            (TargetRegistry, TargetIdentifier, "OpenAIResponseTarget", {"tool_providers": [{"name": "x"}]}),
            (ConverterRegistry, ConverterIdentifier, "TokenBijectionConverter", {"tokenizer": "name"}),
        ],
    )
    def test_registered_component_rejects_json_for_object_parameter(
        self, registry_type: type, identifier_type: type, type_name: str, raw_args: dict[str, object]
    ) -> None:
        cls = registry_type.get_registry_singleton().get_class(type_name)

        with pytest.raises(ValueError, match="expects"):
            _resolve(cls, raw_args, identifier_type=identifier_type)

    @pytest.mark.parametrize(
        ("registry_type", "identifier_type", "type_name", "raw_args"),
        [
            (ConverterRegistry, ConverterIdentifier, "FlipConverter", {"converter_target": {"name": "x"}}),
            (TargetRegistry, TargetIdentifier, "RoundRobinTarget", {"targets": [{"name": "x"}]}),
        ],
    )
    def test_registry_reference_rejects_json_object(
        self, registry_type: type, identifier_type: type, type_name: str, raw_args: dict[str, object]
    ) -> None:
        cls = registry_type.get_registry_singleton().get_class(type_name)

        with pytest.raises(ValueError, match="registry name or instance"):
            _resolve(cls, raw_args, identifier_type=identifier_type)

    def test_registered_target_accepts_string_token(self) -> None:
        cls = TargetRegistry.get_registry_singleton().get_class("A2ATarget")

        assert _resolve(cls, {"auth_token": "token"}, identifier_type=TargetIdentifier) == {"auth_token": "token"}

    @pytest.mark.parametrize(
        ("type_name", "raw_args", "expected"),
        [
            ("ImageCompressionConverter", {"background_color": [10, 20, 30]}, {"background_color": (10, 20, 30)}),
            ("SATAMaskingConverter", {"stopwords": ["the", "a"]}, {"stopwords": ["the", "a"]}),
        ],
    )
    def test_registered_converter_json_values(
        self, type_name: str, raw_args: dict[str, object], expected: dict[str, object]
    ) -> None:
        cls = ConverterRegistry.get_registry_singleton().get_class(type_name)

        assert _resolve(cls, raw_args, identifier_type=ConverterIdentifier) == expected

    @pytest.mark.parametrize(
        ("registry_type", "identifier_type"),
        [(ConverterRegistry, ConverterIdentifier), (TargetRegistry, TargetIdentifier)],
    )
    def test_registered_parameters_only_raise_value_error(self, registry_type: type, identifier_type: type) -> None:
        registry = registry_type.get_registry_singleton()
        for type_name in registry.get_class_names():
            cls = registry.get_class(type_name)
            for parameter in derive_parameters(cls=cls, identifier_type=identifier_type):
                for value in (None, 1, 1.5, True, "text", [], [1, "a"], {"a": [1]}):
                    with contextlib.suppress(ValueError):
                        _resolve(cls, {parameter.name: value}, identifier_type=identifier_type)

    @pytest.mark.parametrize(
        ("registry_type", "identifier_type"),
        [(ConverterRegistry, ConverterIdentifier), (TargetRegistry, TargetIdentifier)],
    )
    def test_registered_json_defaults_are_accepted(self, registry_type: type, identifier_type: type) -> None:
        registry = registry_type.get_registry_singleton()
        for type_name in registry.get_class_names():
            cls = registry.get_class(type_name)
            for parameter in derive_parameters(cls=cls, identifier_type=identifier_type):
                if parameter.reference is not None or parameter.default is None:
                    continue
                try:
                    value = json.loads(json.dumps(parameter.default))
                except (TypeError, ValueError):
                    continue
                _resolve(cls, {parameter.name: value}, identifier_type=identifier_type)

    @pytest.mark.parametrize(
        ("registry_type", "identifier_type"),
        [
            (ConverterRegistry, ConverterIdentifier),
            (TargetRegistry, TargetIdentifier),
            (ScorerRegistry, ScorerIdentifier),
        ],
    )
    def test_registered_choices_are_accepted(self, registry_type: type, identifier_type: type) -> None:
        registry = registry_type.get_registry_singleton()
        for type_name in registry.get_class_names():
            cls = registry.get_class(type_name)
            for parameter in derive_parameters(cls=cls, identifier_type=identifier_type):
                if parameter.reference is not None or not parameter.choices:
                    continue
                value = [parameter.choices[0]] if parameter.is_list else parameter.choices[0]
                _resolve(cls, {parameter.name: value}, identifier_type=identifier_type)

    def test_enum_list_is_coerced_from_json_choices(self) -> None:
        live = [_Speed.SLOW]

        assert _resolve(_JsonShaped, {"speeds": ["fast"]}) == {"speeds": [_Speed.FAST]}
        assert _resolve(_JsonShaped, {"speeds": live})["speeds"] is live
        with pytest.raises(ValueError, match="speeds"):
            _resolve(_JsonShaped, {"speeds": ["bogus"]})

    def test_collection_parameter_rejects_wrong_member_type(self) -> None:
        cls = ConverterRegistry.get_registry_singleton().get_class("SATAMaskingConverter")

        with pytest.raises(ValueError, match="stopwords"):
            _resolve(cls, {"stopwords": [1, "a"]}, identifier_type=ConverterIdentifier)


class TestDeriveParameters:
    """Tests for deriving the Parameter contract from a constructor signature."""

    def test_required_and_defaults(self) -> None:
        params = {p.name: p for p in derive_parameters(cls=_Plain)}
        assert params["count"].default is REQUIRED_VALUE
        assert params["ratio"].default == 0.5
        assert params["count"].param_type is int

    def test_optional_annotation_preserved(self) -> None:
        params = {p.name: p for p in derive_parameters(cls=_Plain)}
        assert params["note"].param_type == str | None
        assert params["note"].type_name == "str"

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

    def test_forwarded_parent_parameters_follow_child_in_mro_order(self) -> None:
        names = [p.name for p in derive_parameters(cls=_ForwardingChild)]
        assert names == ["label", "count", "speed"]

    def test_unexposed_parent_parameters_are_not_inferred_from_open_bag(self) -> None:
        names = [p.name for p in derive_parameters(cls=_OpenBagChild)]
        assert names == ["label"]

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
