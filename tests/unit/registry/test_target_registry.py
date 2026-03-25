# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry.instance_registries.target_registry import TargetRegistry


class MockPromptTarget(PromptTarget):
    """Mock PromptTarget for testing."""

    def __init__(self, *, model_name: str = "mock_model") -> None:
        super().__init__(model_name=model_name)

    async def send_prompt_async(
        self,
        *,
        message: Message,
    ) -> list[Message]:
        return [
            MessagePiece(
                role="assistant",
                original_value="mock response",
            ).to_message()
        ]

    def _validate_request(self, *, message: Message) -> None:
        pass


class MockPromptChatTarget(PromptChatTarget):
    """Mock PromptChatTarget for testing conversation history support."""

    def __init__(self, *, model_name: str = "mock_chat_model", endpoint: str = "http://chat-test") -> None:
        super().__init__(model_name=model_name, endpoint=endpoint)

    async def send_prompt_async(
        self,
        *,
        message: Message,
    ) -> list[Message]:
        return [
            MessagePiece(
                role="assistant",
                original_value="chat response",
            ).to_message()
        ]

    def _validate_request(self, *, message: Message) -> None:
        pass


class TestTargetRegistrySingleton:
    """Tests for the singleton pattern in TargetRegistry."""

    def setup_method(self):
        """Reset the singleton before each test."""
        TargetRegistry.reset_instance()

    def teardown_method(self):
        """Reset the singleton after each test."""
        TargetRegistry.reset_instance()

    def test_get_registry_singleton_returns_same_instance(self):
        """Test that get_registry_singleton returns the same singleton each time."""
        instance1 = TargetRegistry.get_registry_singleton()
        instance2 = TargetRegistry.get_registry_singleton()

        assert instance1 is instance2

    def test_get_registry_singleton_returns_target_registry_type(self):
        """Test that get_registry_singleton returns a TargetRegistry instance."""
        instance = TargetRegistry.get_registry_singleton()
        assert isinstance(instance, TargetRegistry)

    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        instance1 = TargetRegistry.get_registry_singleton()
        TargetRegistry.reset_instance()
        instance2 = TargetRegistry.get_registry_singleton()

        assert instance1 is not instance2


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryRegisterInstance:
    """Tests for register_instance functionality in TargetRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        TargetRegistry.reset_instance()
        self.registry = TargetRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        TargetRegistry.reset_instance()

    def test_register_instance_with_custom_name(self):
        """Test registering a target with a custom name."""
        target = MockPromptTarget()
        self.registry.register_instance(target, name="custom_target")

        assert "custom_target" in self.registry
        assert self.registry.get("custom_target") is target

    def test_register_instance_generates_name_from_class(self):
        """Test that register_instance generates a name from class name when not provided."""
        target = MockPromptTarget()
        self.registry.register_instance(target)

        # Name should be derived from class name with hash suffix
        names = self.registry.get_names()
        assert len(names) == 1
        assert names[0].startswith("MockPromptTarget::")

    def test_register_instance_multiple_targets_unique_names(self):
        """Test registering multiple targets generates unique names."""
        target1 = MockPromptTarget()
        target2 = MockPromptChatTarget()

        self.registry.register_instance(target1)
        self.registry.register_instance(target2)

        assert len(self.registry) == 2

    def test_register_instance_same_target_type_different_config(self):
        """Test that same target class with different configs can be registered."""
        target1 = MockPromptTarget(model_name="model_a")
        target2 = MockPromptTarget(model_name="model_b")

        # Register with explicit names
        self.registry.register_instance(target1, name="target_1")
        self.registry.register_instance(target2, name="target_2")

        assert len(self.registry) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryGetInstanceByName:
    """Tests for get_instance_by_name functionality in TargetRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        TargetRegistry.reset_instance()
        self.registry = TargetRegistry.get_registry_singleton()
        self.target = MockPromptTarget()
        self.registry.register_instance(self.target, name="test_target")

    def teardown_method(self):
        """Reset the singleton after each test."""
        TargetRegistry.reset_instance()

    def test_get_instance_by_name_returns_target(self):
        """Test getting a registered target by name."""
        result = self.registry.get_instance_by_name("test_target")
        assert result is self.target

    def test_get_instance_by_name_nonexistent_returns_none(self):
        """Test that getting a non-existent target returns None."""
        result = self.registry.get_instance_by_name("nonexistent")
        assert result is None


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryBuildMetadata:
    """Tests for _build_metadata functionality in TargetRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        TargetRegistry.reset_instance()
        self.registry = TargetRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        TargetRegistry.reset_instance()

    def test_build_metadata_includes_class_name(self):
        """Test that metadata (ComponentIdentifier) includes the class name."""
        target = MockPromptTarget()
        self.registry.register_instance(target, name="mock_target")

        metadata = self.registry.list_metadata()
        assert len(metadata) == 1
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0].class_name == "MockPromptTarget"

    def test_build_metadata_includes_model_name(self):
        """Test that metadata includes the model_name."""
        target = MockPromptTarget(model_name="test_model")
        self.registry.register_instance(target, name="mock_target")

        metadata = self.registry.list_metadata()
        assert metadata[0].params["model_name"] == "test_model"


@pytest.mark.usefixtures("patch_central_database")
class TestTargetRegistryListMetadata:
    """Tests for list_metadata in TargetRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry with multiple targets."""
        TargetRegistry.reset_instance()
        self.registry = TargetRegistry.get_registry_singleton()

        self.target1 = MockPromptTarget(model_name="model_a")
        self.target2 = MockPromptTarget(model_name="model_b")
        self.chat_target = MockPromptChatTarget()

        self.registry.register_instance(self.target1, name="target_1")
        self.registry.register_instance(self.target2, name="target_2")
        self.registry.register_instance(self.chat_target, name="chat_target")

    def teardown_method(self):
        """Reset the singleton after each test."""
        TargetRegistry.reset_instance()

    def test_list_metadata_returns_all_registered(self):
        """Test that list_metadata returns metadata for all registered targets."""
        metadata = self.registry.list_metadata()
        assert len(metadata) == 3

    def test_list_metadata_filter_by_class_name(self):
        """Test filtering metadata by class_name."""
        mock_metadata = self.registry.list_metadata(include_filters={"class_name": "MockPromptTarget"})

        assert len(mock_metadata) == 2
        for m in mock_metadata:
            assert m.class_name == "MockPromptTarget"
