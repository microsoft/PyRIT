# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the merged ``TargetRegistry`` (buildable catalog + instance container)
and its introspection helpers.
"""

import pytest

from pyrit.models import ComponentIdentifier, Message, MessagePiece
from pyrit.models.parameter import ComponentType
from pyrit.prompt_target import (
    PromptTarget,
    RoundRobinTarget,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.registry.components import TargetMetadata, TargetRegistry
from pyrit.registry.resolution import derive_parameters


class MockPromptTarget(PromptTarget):
    """Minimal PromptTarget (multi-turn capable) for registry tests."""

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )

    def __init__(self, *, model_name: str = "mock_model", endpoint: str | None = None) -> None:
        super().__init__(model_name=model_name, endpoint=endpoint)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [MessagePiece(role="assistant", original_value="mock response").to_message()]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass


class MockPromptChatTarget(PromptTarget):
    """A second mock target for multi-instance tests."""

    def __init__(self, *, model_name: str = "mock_chat_model", endpoint: str = "http://chat-test") -> None:
        super().__init__(model_name=model_name, endpoint=endpoint)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [MessagePiece(role="assistant", original_value="chat response").to_message()]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass


@pytest.fixture
def registry():
    """Provide a fresh ``TargetRegistry`` singleton, reset around each test."""
    TargetRegistry.reset_registry_singleton()
    instance = TargetRegistry.get_registry_singleton()
    yield instance
    TargetRegistry.reset_registry_singleton()


# ---------------------------------------------------------------------------
# Instance container (reached via the ``instances`` property)
# ---------------------------------------------------------------------------


class TestTargetRegistrySingleton:
    """Tests for the singleton pattern in TargetRegistry."""

    def setup_method(self):
        TargetRegistry.reset_registry_singleton()

    def teardown_method(self):
        TargetRegistry.reset_registry_singleton()

    def test_get_registry_singleton_returns_same_instance(self):
        assert TargetRegistry.get_registry_singleton() is TargetRegistry.get_registry_singleton()

    def test_get_registry_singleton_returns_target_registry_type(self):
        assert isinstance(TargetRegistry.get_registry_singleton(), TargetRegistry)

    def test_reset_registry_singleton_clears_singleton(self):
        instance1 = TargetRegistry.get_registry_singleton()
        TargetRegistry.reset_registry_singleton()
        assert TargetRegistry.get_registry_singleton() is not instance1


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryRegisterInstance:
    """Tests for instance registration via the ``instances`` property."""

    def test_register_instance_with_custom_name(self, registry: TargetRegistry):
        target = MockPromptTarget()
        registry.instances.register(target, name="custom_target")

        assert "custom_target" in registry.instances
        assert registry.instances.get("custom_target") is target

    def test_register_instance_generates_name_from_class(self, registry: TargetRegistry):
        target = MockPromptTarget()
        registry.instances.register(target)

        names = registry.instances.get_names()
        assert len(names) == 1
        assert names[0].startswith("MockPromptTarget::")

    def test_register_instance_multiple_targets_unique_names(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget())
        registry.instances.register(MockPromptChatTarget())

        assert len(registry.instances) == 2

    def test_register_instance_duplicate_name_overwrites(self, registry: TargetRegistry):
        first = MockPromptTarget(model_name="first")
        second = MockPromptTarget(model_name="second")

        registry.instances.register(first, name="same_name")
        registry.instances.register(second, name="same_name")

        assert len(registry.instances) == 1
        assert registry.instances.get("same_name") is second

    def test_register_instance_rejects_non_target(self, registry: TargetRegistry):
        class NotATarget:
            pass

        with pytest.raises(TypeError, match="PromptTarget"):
            registry.instances.register(NotATarget())  # type: ignore[arg-type]

        assert len(registry.instances) == 0


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryGetInstanceByName:
    """Tests for instance lookup via ``instances.get``."""

    def test_get_instance_by_name_returns_target(self, registry: TargetRegistry):
        target = MockPromptTarget()
        registry.instances.register(target, name="test_target")
        assert registry.instances.get("test_target") is target

    def test_get_instance_by_name_nonexistent_returns_none(self, registry: TargetRegistry):
        assert registry.instances.get("nonexistent") is None


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryInstanceMetadata:
    """Tests for instance-level metadata (``instances.list_metadata``)."""

    def test_instance_metadata_is_component_identifier(self, registry: TargetRegistry):
        target = MockPromptTarget(model_name="test_model")
        registry.instances.register(target, name="mock_target")

        metadata = registry.instances.list_metadata()
        assert len(metadata) == 1
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0].class_name == "MockPromptTarget"
        assert metadata[0].params["model_name"] == "test_model"

    def test_instance_metadata_filter_by_class_name(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(model_name="a"), name="t1")
        registry.instances.register(MockPromptTarget(model_name="b"), name="t2")
        registry.instances.register(MockPromptChatTarget(), name="chat")

        metadata = registry.instances.list_metadata(include_filters={"class_name": "MockPromptTarget"})
        assert len(metadata) == 2
        assert all(m.class_name == "MockPromptTarget" for m in metadata)


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryContainerProtocol:
    """Tests for the ``instances`` container protocol surface."""

    def test_contains_and_len_and_iter(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(), name="test_target")
        assert "test_target" in registry.instances
        assert "unknown_target" not in registry.instances
        assert len(registry.instances) == 1
        assert "test_target" in list(registry.instances)

    def test_get_names_returns_sorted_list(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(), name="zeta_target")
        registry.instances.register(MockPromptTarget(), name="alpha_target")
        assert registry.instances.get_names() == ["alpha_target", "zeta_target"]

    def test_get_all_instances_returns_all(self, registry: TargetRegistry):
        a = MockPromptTarget()
        b = MockPromptChatTarget()
        registry.instances.register(a, name="a")
        registry.instances.register(b, name="b")

        entry_map = {e.name: e for e in registry.instances.get_all_instances()}
        assert entry_map["a"].instance is a
        assert entry_map["b"].instance is b


# ---------------------------------------------------------------------------
# Buildable class catalog (discovery + introspection + build)
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Tests for target class discovery."""

    def test_discovers_known_targets(self, registry: TargetRegistry):
        names = registry.get_class_names()
        assert "OpenAIChatTarget" in names
        assert "RoundRobinTarget" in names

    def test_does_not_register_base_class(self, registry: TargetRegistry):
        assert "PromptTarget" not in registry.get_class_names()

    def test_keyed_by_exact_class_name(self, registry: TargetRegistry):
        names = registry.get_class_names()
        assert "OpenAIChatTarget" in names
        assert "openai_chat_target" not in names


class TestGetClass:
    """Tests for get_class (the inherited class-catalog accessor)."""

    def test_returns_class(self, registry: TargetRegistry):
        assert registry.get_class("RoundRobinTarget") is RoundRobinTarget

    def test_unknown_type_raises(self, registry: TargetRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.get_class("NotARealTarget")

    def test_is_subclass_relationship(self, registry: TargetRegistry):
        assert issubclass(registry.get_class("RoundRobinTarget"), PromptTarget)


@pytest.mark.usefixtures("patch_central_database")
class TestCreateInstance:
    """Tests for create_instance (build via the shared resolver)."""

    def test_build_round_robin_resolves_targets_by_name(self, registry: TargetRegistry):
        # The list-aware resolution path: a ``list[str]`` of registry names is
        # resolved element by element into the registered target instances.
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://a"), name="t1")
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://b"), name="t2")

        rr = registry.create_instance("RoundRobinTarget", targets=["t1", "t2"])
        assert isinstance(rr, RoundRobinTarget)

    def test_build_round_robin_unknown_target_raises(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://a"), name="t1")
        with pytest.raises(ValueError, match="not found"):
            registry.create_instance("RoundRobinTarget", targets=["t1", "missing"])

    def test_build_round_robin_resolves_prebuilt_instances_in_list(self, registry: TargetRegistry):
        # Passthrough path inside a list: already-built instances are passed through
        # unchanged rather than looked up by name.
        t1 = MockPromptTarget(model_name="m", endpoint="http://a")
        t2 = MockPromptTarget(model_name="m", endpoint="http://b")
        rr = registry.create_instance("RoundRobinTarget", targets=[t1, t2])
        assert isinstance(rr, RoundRobinTarget)

    def test_build_round_robin_mixes_names_and_instances(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://a"), name="t1")
        t2 = MockPromptTarget(model_name="m", endpoint="http://b")
        rr = registry.create_instance("RoundRobinTarget", targets=["t1", t2])
        assert isinstance(rr, RoundRobinTarget)

    def test_build_round_robin_scalar_for_list_reference_raises(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://a"), name="t1")
        with pytest.raises(ValueError, match="expected a list"):
            registry.create_instance("RoundRobinTarget", targets="t1")

    def test_unknown_type_raises(self, registry: TargetRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.create_instance("NotARealTarget")

    def test_build_does_not_register_instance(self, registry: TargetRegistry):
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://a"), name="t1")
        registry.instances.register(MockPromptTarget(model_name="m", endpoint="http://b"), name="t2")
        registry.create_instance("RoundRobinTarget", targets=["t1", "t2"])
        # The two pre-registered targets remain; the built RR is not auto-registered.
        assert len(registry.instances) == 2


class TestClassMetadata:
    """Tests for target class-catalog metadata building."""

    def _metadata_for(self, registry: TargetRegistry, name: str) -> TargetMetadata:
        return next(m for m in registry.get_all_registered_class_metadata() if m.class_name == name)

    def test_metadata_is_target_metadata(self, registry: TargetRegistry):
        meta = self._metadata_for(registry, "RoundRobinTarget")
        assert isinstance(meta, TargetMetadata)
        assert meta.class_name == "RoundRobinTarget"

    def test_round_robin_targets_param_is_reference(self, registry: TargetRegistry):
        meta = self._metadata_for(registry, "RoundRobinTarget")
        assert any(p.is_reference_to(ComponentType.TARGET) for p in meta.parameters)

    def test_metadata_supported_auth_modes_projects_class_attribute(self, registry: TargetRegistry):
        # Identity-capable targets expose both modes; api-key-only targets expose one.
        assert self._metadata_for(registry, "OpenAIChatTarget").supported_auth_modes == ("api_key", "identity")
        assert self._metadata_for(registry, "TextTarget").supported_auth_modes == ("api_key",)

    def test_metadata_supported_auth_modes_sourced_from_class_attributes(self, registry: TargetRegistry):
        # Auth modes are read off the class via Param.ClassAttr, not a fabricated instance.
        meta = self._metadata_for(registry, "OpenAIChatTarget")
        assert "supported_auth_modes" in meta.class_attributes
        assert meta.class_attributes["supported_auth_modes"] == ("api_key", "identity")


class TestRegistrationGate:
    """The identifier blueprint must line up with a resolvable contract for every target."""

    def test_discovery_validates_all_targets(self, registry: TargetRegistry) -> None:
        # Discovery registers every target through ``register_class``, which validates
        # each class; accessing the catalog therefore proves every target is buildable.
        names = registry.get_class_names()
        assert names
        assert "RoundRobinTarget" in names

    def test_every_target_reference_maps_to_a_wired_registry(self, registry: TargetRegistry) -> None:
        from pyrit.models.identifiers import TargetIdentifier

        for name in registry.get_class_names():
            parameters = derive_parameters(cls=registry.get_class(name), identifier_type=TargetIdentifier)
            for param in parameters:
                if param.reference is not None:
                    assert param.reference.component_type in (
                        ComponentType.TARGET,
                        ComponentType.CONVERTER,
                        ComponentType.SCORER,
                    )
