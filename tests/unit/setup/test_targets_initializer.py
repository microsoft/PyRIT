# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import patch

import pytest

from pyrit.registry import TargetRegistry
from pyrit.setup.initializers import TargetInitializer
from pyrit.setup.initializers.components.targets import TARGET_CONFIGS


class TestTargetInitializerBasic:
    """Tests for TargetInitializer class - basic functionality."""

    def test_can_be_created(self):
        """Test that TargetInitializer can be instantiated."""
        init = TargetInitializer()
        assert init is not None
        assert init.name == "Target Initializer"
        assert init.execution_order == 1

    def test_required_env_vars_is_empty(self):
        """Test that no env vars are required (initializer is optional)."""
        init = TargetInitializer()
        assert init.required_env_vars == []


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerInitialize:
    """Tests for TargetInitializer.initialize_async method."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        TargetRegistry.reset_instance()
        # Clear all target-related env vars
        self._clear_env_vars()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_instance()
        self._clear_env_vars()

    def _clear_env_vars(self) -> None:
        """Clear all environment variables used by TARGET_CONFIGS."""
        for config in TARGET_CONFIGS:
            for var in [config.endpoint_var, config.key_var, config.model_var, config.underlying_model_var]:
                if var and var in os.environ:
                    del os.environ[var]

    @pytest.mark.asyncio
    async def test_initialize_runs_without_error_no_env_vars(self):
        """Test that initialize runs without errors when no env vars are set."""
        init = TargetInitializer()
        await init.initialize_async()

        # No targets should be registered
        registry = TargetRegistry.get_registry_singleton()
        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_registers_target_when_env_vars_set(self):
        """Test that a target is registered when its env vars are set."""
        os.environ["PLATFORM_OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["PLATFORM_OPENAI_CHAT_API_KEY"] = "test_key"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "platform_openai_chat" in registry
        target = registry.get_instance_by_name("platform_openai_chat")
        assert target is not None
        assert target._model_name == "gpt-4o"

    @pytest.mark.asyncio
    async def test_does_not_register_target_without_endpoint(self):
        """Test that target is not registered if endpoint is missing."""
        # Only set key, not endpoint
        os.environ["PLATFORM_OPENAI_CHAT_API_KEY"] = "test_key"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "platform_openai_chat" not in registry

    @pytest.mark.asyncio
    async def test_does_not_register_target_without_api_key(self):
        """Test that target is not registered if api_key env var is missing."""
        # Only set endpoint, not key
        os.environ["PLATFORM_OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "platform_openai_chat" not in registry

    @pytest.mark.asyncio
    async def test_registers_multiple_targets(self):
        """Test that multiple targets are registered when their env vars are set."""
        # Set up platform_openai_chat
        os.environ["PLATFORM_OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["PLATFORM_OPENAI_CHAT_API_KEY"] = "test_key"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        # Set up openai_image_platform (uses ENDPOINT2/KEY2/MODEL2)
        os.environ["OPENAI_IMAGE_ENDPOINT2"] = "https://api.openai.com/v1"
        os.environ["OPENAI_IMAGE_API_KEY2"] = "test_image_key"
        os.environ["OPENAI_IMAGE_MODEL2"] = "dall-e-3"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert len(registry) == 2
        assert "platform_openai_chat" in registry
        assert "openai_image_platform" in registry

    @pytest.mark.asyncio
    async def test_registers_azure_content_safety_without_model(self):
        """Test that PromptShieldTarget is registered without model_name (it doesn't use one)."""
        os.environ["AZURE_CONTENT_SAFETY_API_ENDPOINT"] = "https://test.cognitiveservices.azure.com"

        with patch(
            "pyrit.setup.initializers.components.targets.get_azure_token_provider", return_value=lambda: "mock-token"
        ):
            init = TargetInitializer()
            await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "azure_content_safety" in registry

    @pytest.mark.asyncio
    async def test_underlying_model_passed_when_set(self):
        """Test that underlying_model is passed to target when env var is set."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://my-deployment.openai.azure.com/openai/v1"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "my-deployment-name"
        os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"] = "gpt-4o"

        with patch(
            "pyrit.setup.initializers.components.targets.get_azure_openai_auth", return_value=lambda: "mock-token"
        ):
            init = TargetInitializer()
            await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        target = registry.get_instance_by_name("azure_openai_gpt4o")
        assert target is not None
        assert target._model_name == "my-deployment-name"
        assert target._underlying_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_registers_ollama_without_api_key(self):
        """Test that Ollama target is registered without requiring an API key."""
        os.environ["OLLAMA_CHAT_ENDPOINT"] = "http://127.0.0.1:11434/v1"
        os.environ["OLLAMA_MODEL"] = "llama2"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "ollama" in registry
        target = registry.get_instance_by_name("ollama")
        assert target is not None
        assert target._model_name == "llama2"

    @pytest.mark.asyncio
    async def test_azure_target_uses_entra_auth(self):
        """Test that Azure targets use Entra auth (token provider) instead of API key."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://my-deployment.openai.azure.com/openai/v1"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        def mock_token_provider() -> str:
            return "mock-token"

        with patch(
            "pyrit.setup.initializers.components.targets.get_azure_openai_auth", return_value=mock_token_provider
        ):
            init = TargetInitializer()
            await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        target = registry.get_instance_by_name("azure_openai_gpt4o")
        assert target is not None
        # The token provider gets wrapped by _ensure_async_token_provider, so just verify it's callable
        assert callable(target._api_key)


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerTargetConfigs:
    """Tests verifying TARGET_CONFIGS covers expected targets."""

    def test_target_configs_not_empty(self):
        """Test that TARGET_CONFIGS has configurations defined."""
        assert len(TARGET_CONFIGS) > 0

    def test_all_configs_have_required_fields(self):
        """Test that all TARGET_CONFIGS have required fields (key_var is optional for some)."""
        for config in TARGET_CONFIGS:
            assert config.registry_name, f"Config missing registry_name"
            assert config.target_class, f"Config {config.registry_name} missing target_class"
            assert config.endpoint_var, f"Config {config.registry_name} missing endpoint_var"
            # key_var is optional for targets like Ollama that don't require auth

    def test_expected_targets_in_configs(self):
        """Test that expected target names are in TARGET_CONFIGS."""
        registry_names = [config.registry_name for config in TARGET_CONFIGS]

        # Verify key targets are configured (using new primary config names)
        assert "platform_openai_chat" in registry_names
        assert "azure_openai_gpt4o" in registry_names
        assert "openai_image_platform" in registry_names
        assert "openai_tts_platform" in registry_names
        assert "azure_content_safety" in registry_names
        assert "ollama" in registry_names
        assert "groq" in registry_names
        assert "google_gemini" in registry_names


class TestTargetInitializerGetInfo:
    """Tests for TargetInitializer.get_info_async method."""

    @pytest.mark.asyncio
    async def test_get_info_returns_expected_structure(self):
        """Test that get_info_async returns expected structure."""
        info = await TargetInitializer.get_info_async()

        assert isinstance(info, dict)
        assert info["name"] == "Target Initializer"
        assert info["class"] == "TargetInitializer"
        assert "description" in info
        assert isinstance(info["description"], str)

    @pytest.mark.asyncio
    async def test_get_info_required_env_vars_empty_or_not_present(self):
        """Test that get_info has empty or no required_env_vars (since none are required)."""
        info = await TargetInitializer.get_info_async()

        # required_env_vars may be omitted or empty since this initializer has no requirements
        if "required_env_vars" in info:
            assert info["required_env_vars"] == []


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerTags:
    """Tests for TargetInitializer tag filtering."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        TargetRegistry.reset_instance()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_instance()

    @pytest.mark.asyncio
    async def test_no_tags_registers_default_only(self) -> None:
        """Test that no tags registers only default targets (not scorer variants)."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()  # No tags = default only
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        # Default targets should be registered, scorer variants should not
        assert registry.get_instance_by_name("azure_openai_gpt4o") is not None
        assert registry.get_instance_by_name("azure_openai_gpt4o_temp9") is None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    @pytest.mark.asyncio
    async def test_default_tag_excludes_scorer_targets(self) -> None:
        """Test that tags=['default'] only registers default-tagged targets."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer(tags=["default"])
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("azure_openai_gpt4o") is not None
        assert registry.get_instance_by_name("azure_openai_gpt4o_temp9") is None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    @pytest.mark.asyncio
    async def test_scorer_tag_only_registers_scorer_targets(self) -> None:
        """Test that tags=['scorer'] only registers scorer-tagged targets."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer(tags=["scorer"])
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("azure_openai_gpt4o") is None
        assert registry.get_instance_by_name("azure_openai_gpt4o_temp9") is not None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    @pytest.mark.asyncio
    async def test_multiple_tags_registers_matching(self) -> None:
        """Test that multiple tags register targets matching any tag."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer(tags=["default", "scorer"])
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.get_instance_by_name("azure_openai_gpt4o") is not None
        assert registry.get_instance_by_name("azure_openai_gpt4o_temp9") is not None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]
