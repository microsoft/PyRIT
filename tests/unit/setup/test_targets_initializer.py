# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import patch

import pytest

from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import TargetRegistry
from pyrit.setup.initializers import TargetInitializer
from pyrit.setup.initializers.components.targets import TARGET_CONFIGS, generate_rr_name, get_behavioral_key


class TestTargetInitializerBasic:
    """Tests for TargetInitializer class - basic functionality."""

    def test_can_be_created(self):
        """Test that TargetInitializer can be instantiated."""
        init = TargetInitializer()
        assert init is not None

    def test_required_env_vars_is_empty(self):
        """Test that no env vars are required (initializer is optional)."""
        init = TargetInitializer()
        assert init.required_env_vars == []


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerInitialize:
    """Tests for TargetInitializer.initialize_async method."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        TargetRegistry.reset_registry_singleton()
        # Clear all target-related env vars
        self._clear_env_vars()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_registry_singleton()
        self._clear_env_vars()

    def _clear_env_vars(self) -> None:
        """Clear all environment variables used by TARGET_CONFIGS."""
        for config in TARGET_CONFIGS:
            for var in [config.endpoint_var, config.key_var, config.model_var, config.underlying_model_var]:
                if var and var in os.environ:
                    del os.environ[var]

    async def test_initialize_runs_without_error_no_env_vars(self):
        """Test that initialize runs without errors when no env vars are set."""
        init = TargetInitializer()
        await init.initialize_async()

        # No targets should be registered
        registry = TargetRegistry.get_registry_singleton()
        assert len(registry.instances) == 0

    async def test_registers_target_when_env_vars_set(self):
        """Test that a target is registered when its env vars are set."""
        os.environ["PLATFORM_OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["PLATFORM_OPENAI_CHAT_KEY"] = "test_key"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "platform_openai_chat" in registry.instances
        target = registry.instances.get("platform_openai_chat")
        assert target is not None
        assert target._model_name == "gpt-4o"

    async def test_does_not_register_target_without_endpoint(self):
        """Test that target is not registered if endpoint is missing."""
        # Only set key, not endpoint
        os.environ["PLATFORM_OPENAI_CHAT_KEY"] = "test_key"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "platform_openai_chat" not in registry.instances

    async def test_does_not_register_target_without_api_key(self):
        """Test that target is not registered if api_key env var is missing."""
        # Only set endpoint, not key
        os.environ["PLATFORM_OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "platform_openai_chat" not in registry.instances

    async def test_registers_multiple_targets(self):
        """Test that multiple targets are registered when their env vars are set."""
        # Set up platform_openai_chat
        os.environ["PLATFORM_OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["PLATFORM_OPENAI_CHAT_KEY"] = "test_key"
        os.environ["PLATFORM_OPENAI_CHAT_GPT4O_MODEL"] = "gpt-4o"

        # Set up openai_image_platform (uses ENDPOINT2/KEY2/MODEL2)
        os.environ["OPENAI_IMAGE_ENDPOINT2"] = "https://api.openai.com/v1"
        os.environ["OPENAI_IMAGE_API_KEY2"] = "test_image_key"
        os.environ["OPENAI_IMAGE_MODEL2"] = "dall-e-3"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert len(registry.instances) == 2
        assert "platform_openai_chat" in registry.instances
        assert "openai_image_platform" in registry.instances

    async def test_registers_azure_content_safety_without_model(self):
        """Test that PromptShieldTarget is registered without model_name (it doesn't use one)."""
        os.environ["AZURE_CONTENT_SAFETY_API_ENDPOINT"] = "https://test.cognitiveservices.azure.com"

        with patch(
            "pyrit.setup.initializers.components.targets.get_azure_token_provider", return_value=lambda: "mock-token"
        ):
            init = TargetInitializer()
            await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "azure_content_safety" in registry.instances

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
        target = registry.instances.get("azure_openai_gpt4o")
        assert target is not None
        assert target._model_name == "my-deployment-name"
        assert target._underlying_model == "gpt-4o"

    async def test_registers_ollama_without_api_key(self):
        """Test that Ollama target is registered without requiring an API key."""
        os.environ["OLLAMA_CHAT_ENDPOINT"] = "http://127.0.0.1:11434/v1"
        os.environ["OLLAMA_MODEL"] = "llama2"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "ollama" in registry.instances
        target = registry.instances.get("ollama")
        assert target is not None
        assert target._model_name == "llama2"

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
        target = registry.instances.get("azure_openai_gpt4o")
        assert target is not None
        # The token provider gets wrapped by _ensure_async_token_provider, so just verify it's callable
        assert callable(target._api_key)  # type: ignore[ty:unresolved-attribute]


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

    def test_target_configs_have_unique_registry_names(self):
        """Guard against typos: every ``registry_name`` in ``ENV_TARGET_CONFIGS`` must be unique.

        Duplicate names would silently overwrite each other when
        ``TargetInitializer`` registers them (per ``BaseInstanceRegistry.register``
        semantics, characterized in ``test_target_registry.py``). Only the
        second entry would survive in the registry, which breaks downstream
        scenarios that resolve targets by name (e.g. ``AdversarialBenchmark``'s
        ``adversarial_targets`` parameter) and is hard to diagnose. Tracked
        as ``duplicate-registry-name`` in failure_mode_followups.
        """
        registry_names = [config.registry_name for config in TARGET_CONFIGS]
        seen: dict[str, int] = {}
        for name in registry_names:
            seen[name] = seen.get(name, 0) + 1
        duplicates = {name: count for name, count in seen.items() if count > 1}
        assert not duplicates, f"Duplicate registry_name(s) in TARGET_CONFIGS: {duplicates}"


class TestTargetInitializerGetInfo:
    """Tests for TargetInitializer.get_info_async method."""

    async def test_get_info_returns_expected_structure(self):
        """Test that get_info_async returns expected structure."""
        info = await TargetInitializer.get_info_async()

        assert isinstance(info, dict)
        assert info["class"] == "TargetInitializer"
        assert "description" in info
        assert isinstance(info["description"], str)

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
        TargetRegistry.reset_registry_singleton()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_registry_singleton()

    async def test_no_tags_registers_default_only(self) -> None:
        """Test that no tags registers only default targets (not scorer variants)."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()  # No tags = default only
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        # Default targets should be registered (including temp9), scorer-only should not
        assert registry.instances.get("azure_openai_gpt4o") is not None
        assert registry.instances.get("azure_openai_gpt4o_temp9") is not None
        assert registry.instances.get("azure_openai_gpt4o_temp0") is None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    async def test_default_tag_excludes_scorer_targets(self) -> None:
        """Test that tags=['default'] registers default-tagged targets including temp9."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        init.params = {"tags": ["default"]}
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.instances.get("azure_openai_gpt4o") is not None
        assert registry.instances.get("azure_openai_gpt4o_temp9") is not None
        assert registry.instances.get("azure_openai_gpt4o_temp0") is None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    async def test_scorer_tag_only_registers_scorer_targets(self) -> None:
        """Test that tags=['scorer'] only registers scorer-tagged targets (temp0)."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        init.params = {"tags": ["scorer"]}
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.instances.get("azure_openai_gpt4o") is None
        assert registry.instances.get("azure_openai_gpt4o_temp9") is None
        assert registry.instances.get("azure_openai_gpt4o_temp0") is not None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    async def test_multiple_tags_registers_matching(self) -> None:
        """Test that multiple tags register targets matching any tag."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        init.params = {"tags": ["default", "scorer"]}
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.instances.get("azure_openai_gpt4o") is not None
        assert registry.instances.get("azure_openai_gpt4o_temp9") is not None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    async def test_all_tag_registers_all_targets(self) -> None:
        """Test that tags=['all'] registers both default and scorer targets."""
        os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_KEY"] = "test_key"
        os.environ["AZURE_OPENAI_GPT4O_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        init.params = {"tags": ["all"]}
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.instances.get("azure_openai_gpt4o") is not None
        assert registry.instances.get("azure_openai_gpt4o_temp9") is not None

        # Clean up
        del os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_KEY"]
        del os.environ["AZURE_OPENAI_GPT4O_MODEL"]


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerDefaultObjectiveTarget:
    """Tests for DEFAULT_OBJECTIVE_TARGET tagging in TargetInitializer."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        TargetRegistry.reset_registry_singleton()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_registry_singleton()
        for var in ["OPENAI_CHAT_ENDPOINT", "OPENAI_CHAT_KEY", "OPENAI_CHAT_MODEL"]:
            os.environ.pop(var, None)

    async def test_openai_chat_registered_with_default_tag(self) -> None:
        """Test that openai_chat target is tagged as DEFAULT_OBJECTIVE_TARGET."""
        from pyrit.setup.initializers.components.targets import TargetInitializerTags

        os.environ["OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["OPENAI_CHAT_KEY"] = "test_key"
        os.environ["OPENAI_CHAT_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "openai_chat" in registry.instances

        entries = registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT_OBJECTIVE_TARGET)
        assert len(entries) == 1
        assert entries[0].name == "openai_chat"

    async def test_no_default_tag_when_env_vars_missing(self) -> None:
        """Test that no DEFAULT_OBJECTIVE_TARGET is tagged when openai_chat env vars missing."""
        from pyrit.setup.initializers.components.targets import TargetInitializerTags

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        entries = registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT_OBJECTIVE_TARGET)
        assert len(entries) == 0

    async def test_openai_chat_config_has_default_objective_target_flag(self) -> None:
        """Test that the openai_chat TargetConfig has default_objective_target=True."""
        openai_chat_configs = [c for c in TARGET_CONFIGS if c.registry_name == "openai_chat"]
        assert len(openai_chat_configs) == 1
        assert openai_chat_configs[0].default_objective_target is True

    async def test_other_targets_not_tagged_as_default(self) -> None:
        """Test that non-default targets are not tagged as DEFAULT_OBJECTIVE_TARGET."""
        other_configs = [c for c in TARGET_CONFIGS if c.registry_name != "openai_chat"]
        for config in other_configs:
            assert config.default_objective_target is False, (
                f"Target {config.registry_name} should not have default_objective_target=True"
            )


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerConfigTagPropagation:
    """Tests for TargetInitializer propagating ``TargetConfig.tags`` to the registry (F1c)."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        TargetRegistry.reset_registry_singleton()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_registry_singleton()
        for var in [
            "OBJECTIVE_SCORER_CHAT_ENDPOINT",
            "OBJECTIVE_SCORER_CHAT_KEY",
            "OBJECTIVE_SCORER_CHAT_MODEL",
            "OPENAI_CHAT_ENDPOINT",
            "OPENAI_CHAT_KEY",
            "OPENAI_CHAT_MODEL",
        ]:
            os.environ.pop(var, None)

    async def test_register_target_propagates_config_tags(self) -> None:
        """
        ``TargetConfig.tags`` should be added to the registry entry so the entire
        ``TargetInitializerTags`` enum is queryable post-registration.
        """
        from pyrit.setup.initializers.components.targets import TargetInitializerTags

        os.environ["OBJECTIVE_SCORER_CHAT_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["OBJECTIVE_SCORER_CHAT_KEY"] = "test_key"
        os.environ["OBJECTIVE_SCORER_CHAT_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert "objective_scorer_chat" in registry.instances

        scorer_entries = registry.instances.get_by_tag(tag=TargetInitializerTags.SCORER)
        assert any(entry.name == "objective_scorer_chat" for entry in scorer_entries), (
            "objective_scorer_chat should be discoverable by the SCORER tag after F1c"
        )

        default_entries = registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT)
        assert any(entry.name == "objective_scorer_chat" for entry in default_entries), (
            "objective_scorer_chat declares both DEFAULT and SCORER tags; both must propagate"
        )

    async def test_register_target_no_tags_in_config_no_extra_add_tags(self) -> None:
        """An empty ``config.tags`` list must not trigger an ``add_tags`` call (no spurious empty-list passes)."""
        from unittest.mock import MagicMock, patch

        from pyrit.setup.initializers.components.targets import TargetConfig, TargetInitializer

        config = TargetConfig(
            registry_name="empty_tags_target",
            target_class=MagicMock(return_value=MagicMock()),
            endpoint_var="EMPTY_TAGS_ENDPOINT",
            key_var="",
            tags=[],
        )

        os.environ["EMPTY_TAGS_ENDPOINT"] = "https://example.com"

        try:
            mock_registry = MagicMock()
            with patch.object(TargetRegistry, "get_registry_singleton", return_value=mock_registry):
                init = TargetInitializer()
                init._register_target(config)

            mock_registry.instances.register.assert_called_once()
            mock_registry.instances.add_tags.assert_not_called()
        finally:
            os.environ.pop("EMPTY_TAGS_ENDPOINT", None)

    async def test_register_target_default_objective_tag_still_applied(self) -> None:
        """
        Regression: ``default_objective_target=True`` must still add the ``DEFAULT_OBJECTIVE_TARGET``
        tag alongside any ``config.tags``.
        """
        from pyrit.setup.initializers.components.targets import TargetInitializerTags

        os.environ["OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        os.environ["OPENAI_CHAT_KEY"] = "test_key"
        os.environ["OPENAI_CHAT_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        default_objective_entries = registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT_OBJECTIVE_TARGET)
        assert len(default_objective_entries) == 1
        assert default_objective_entries[0].name == "openai_chat"

        default_entries = registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT)
        assert any(entry.name == "openai_chat" for entry in default_entries), (
            "openai_chat's config.tags=[DEFAULT] must propagate even when default_objective_target=True"
        )


ADVERSARIAL_CHAT_VARIANTS: list[tuple[str, str]] = [
    ("adversarial_chat_singleturn", "ADVERSARIAL_CHAT_SINGLETURN"),
    ("adversarial_chat_multiturn", "ADVERSARIAL_CHAT_MULTITURN"),
    ("adversarial_chat_reasoning", "ADVERSARIAL_CHAT_REASONING"),
]


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerAdversarialChatVariants:
    """Tests for the ``ADVERSARIAL_CHAT_{SINGLETURN,MULTITURN,REASONING}_*`` env-driven variants."""

    def setup_method(self) -> None:
        """Reset registry and clear variant env vars."""
        TargetRegistry.reset_registry_singleton()
        self._clear_variant_env_vars()

    def teardown_method(self) -> None:
        """Reset registry and clear variant env vars."""
        TargetRegistry.reset_registry_singleton()
        self._clear_variant_env_vars()

    @staticmethod
    def _clear_variant_env_vars() -> None:
        for _, prefix in ADVERSARIAL_CHAT_VARIANTS:
            for suffix in ("ENDPOINT", "KEY", "MODEL"):
                os.environ.pop(f"{prefix}_{suffix}", None)

    @staticmethod
    def _set_variant_env_vars(prefix: str) -> None:
        os.environ[f"{prefix}_ENDPOINT"] = "https://variant.openai.azure.com/openai/v1"
        os.environ[f"{prefix}_KEY"] = "test_key"
        os.environ[f"{prefix}_MODEL"] = "deployment-name"

    @pytest.mark.parametrize(("registry_name", "env_prefix"), ADVERSARIAL_CHAT_VARIANTS)
    async def test_variant_registers_with_default_tag(self, registry_name: str, env_prefix: str) -> None:
        """Each variant registers with the ``DEFAULT`` tag when its env vars are set."""
        from pyrit.setup.initializers.components.targets import TargetInitializerTags

        self._set_variant_env_vars(env_prefix)

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry_name in registry.instances

        default_entries = registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT)
        assert any(entry.name == registry_name for entry in default_entries)

    @pytest.mark.parametrize(("registry_name", "env_prefix"), ADVERSARIAL_CHAT_VARIANTS)
    async def test_variant_skips_when_env_vars_missing(self, registry_name: str, env_prefix: str) -> None:
        """Variants skip gracefully when their env vars are missing (matches existing adversarial_chat behavior)."""
        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry_name not in registry.instances

    @pytest.mark.parametrize(("registry_name", "env_prefix"), ADVERSARIAL_CHAT_VARIANTS)
    async def test_variant_skips_when_model_env_var_missing(
        self, registry_name: str, env_prefix: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Endpoint+key set but _MODEL unset must skip with a warning, not silently fall back to OPENAI_CHAT_MODEL."""
        import logging

        os.environ[f"{env_prefix}_ENDPOINT"] = "https://variant.openai.azure.com/openai/v1"
        os.environ[f"{env_prefix}_KEY"] = "test_key"

        try:
            with caplog.at_level(logging.WARNING, logger="pyrit.setup.initializers.components.targets"):
                init = TargetInitializer()
                await init.initialize_async()

            registry = TargetRegistry.get_registry_singleton()
            assert registry_name not in registry.instances

            captured_messages = [r.message for r in caplog.records]
            assert any(f"{env_prefix}_MODEL" in m for m in captured_messages), (
                f"Expected a warning naming the missing {env_prefix}_MODEL env var; got: {captured_messages}"
            )
        finally:
            os.environ.pop(f"{env_prefix}_ENDPOINT", None)
            os.environ.pop(f"{env_prefix}_KEY", None)

    async def test_double_initialize_async_is_idempotent(self) -> None:
        """Re-running ``initialize_async`` with the same env state produces the same registry contents.

        Regression guard for the duplicate-registration silent-overwrite path:
        because env vars haven't changed between calls, the rebuilt entries
        carry identical configuration. If anyone introduces non-idempotent
        side-effects (e.g. tag accumulation, instance leaks) into
        ``_register_target``, this test will catch it. Tracked as
        ``duplicate-registry-name`` in failure_mode_followups.
        """
        from pyrit.setup.initializers.components.targets import TargetInitializerTags

        for _, prefix in ADVERSARIAL_CHAT_VARIANTS:
            self._set_variant_env_vars(prefix)

        init = TargetInitializer()
        await init.initialize_async()
        registry = TargetRegistry.get_registry_singleton()
        first_names = sorted(registry.instances.get_names())
        first_default_count = len(registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT))

        await init.initialize_async()
        second_names = sorted(registry.instances.get_names())
        second_default_count = len(registry.instances.get_by_tag(tag=TargetInitializerTags.DEFAULT))

        assert first_names == second_names
        assert first_default_count == second_default_count


@pytest.mark.usefixtures("patch_central_database")
class TestTargetInitializerAutoGroup:
    """Tests for automatic round-robin grouping in TargetInitializer."""

    # Env vars for the two unsafe-chat endpoints that share the same underlying model.
    UNSAFE1_ENV: dict[str, str] = {
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://unsafe1.openai.azure.com",
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "key1",
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4o",
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL": "gpt-4o",
    }
    UNSAFE2_ENV: dict[str, str] = {
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2": "https://unsafe2.openai.azure.com",
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY2": "key2",
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2": "gpt-4o",
        "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL2": "gpt-4o",
    }

    # Env vars for the two regular gpt4o endpoints.
    GPT4O_1_ENV: dict[str, str] = {
        "AZURE_OPENAI_GPT4O_ENDPOINT": "https://gpt4o-1.openai.azure.com",
        "AZURE_OPENAI_GPT4O_KEY": "key1",
        "AZURE_OPENAI_GPT4O_MODEL": "gpt-4o-deploy1",
        "AZURE_OPENAI_GPT4O_UNDERLYING_MODEL": "gpt-4o",
    }
    GPT4O_2_ENV: dict[str, str] = {
        "AZURE_OPENAI_GPT4O_ENDPOINT2": "https://gpt4o-2.openai.azure.com",
        "AZURE_OPENAI_GPT4O_KEY2": "key2",
        "AZURE_OPENAI_GPT4O_MODEL2": "gpt-4o-deploy2",
        "AZURE_OPENAI_GPT4O_UNDERLYING_MODEL2": "gpt-4o",
    }

    def setup_method(self) -> None:
        """Reset registry and clear env vars before each test."""
        TargetRegistry.reset_registry_singleton()
        self._clear_env_vars()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        TargetRegistry.reset_registry_singleton()
        self._clear_env_vars()

    def _clear_env_vars(self) -> None:
        all_vars = list(self.UNSAFE1_ENV) + list(self.UNSAFE2_ENV) + list(self.GPT4O_1_ENV) + list(self.GPT4O_2_ENV)
        for var in all_vars:
            os.environ.pop(var, None)

    async def test_auto_groups_targets_with_same_underlying_model(self) -> None:
        """Test that targets with the same underlying model are auto-grouped into a round-robin."""
        from pyrit.prompt_target import RoundRobinTarget

        os.environ.update(self.GPT4O_1_ENV)
        os.environ.update(self.GPT4O_2_ENV)

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()

        # Find the auto-generated round-robin by checking for RoundRobinTarget instances
        rr_names = [
            name
            for name in registry.instances.get_names()
            if isinstance(registry.instances.get(name), RoundRobinTarget)
        ]
        assert len(rr_names) >= 1, "Expected at least one auto-grouped round-robin target"

        # The gpt-4o round-robin should contain both gpt4o targets
        rr = registry.instances.get("OpenAIChatTarget_gpt-4o_rr")
        assert rr is not None
        assert isinstance(rr, RoundRobinTarget)

    async def test_individual_targets_still_accessible_after_auto_group(self) -> None:
        """Test that individual member targets remain accessible after auto-grouping."""
        os.environ.update(self.GPT4O_1_ENV)
        os.environ.update(self.GPT4O_2_ENV)

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.instances.get("azure_openai_gpt4o") is not None
        assert registry.instances.get("azure_openai_gpt4o2") is not None

    async def test_no_round_robin_when_single_target(self) -> None:
        """Test that no round-robin is created when only one target has a given model."""
        os.environ.update(self.GPT4O_1_ENV)

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert registry.instances.get("azure_openai_gpt4o") is not None
        # No round-robin should exist
        rr = registry.instances.get("OpenAIChatTarget_gpt-4o_rr")
        assert rr is None

    async def test_no_round_robin_when_no_targets(self) -> None:
        """Test that no round-robin targets are created when no env vars are set."""
        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        assert len(registry.instances) == 0

    async def test_different_temperatures_not_grouped(self) -> None:
        """Test that targets with different temperatures are NOT grouped together."""
        # Set up gpt4o endpoints — the base target (no temp) and temp9 variant share the same
        # underlying model but have different temperatures, so they should be in separate groups.
        os.environ.update(self.GPT4O_1_ENV)

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        base = registry.instances.get("azure_openai_gpt4o")
        temp9 = registry.instances.get("azure_openai_gpt4o_temp9")

        # Both should exist but have different behavioral keys
        assert base is not None
        assert temp9 is not None
        assert get_behavioral_key(base) != get_behavioral_key(temp9)

    async def test_different_target_classes_not_grouped(self) -> None:
        """Test that targets of different classes are NOT grouped even with same model."""
        os.environ.update(self.GPT4O_1_ENV)
        os.environ["AZURE_OPENAI_RESPONSES_ENDPOINT"] = "https://responses.openai.azure.com"
        os.environ["AZURE_OPENAI_RESPONSES_KEY"] = "key"
        os.environ["AZURE_OPENAI_RESPONSES_MODEL"] = "gpt-4o-deploy"
        os.environ["AZURE_OPENAI_RESPONSES_UNDERLYING_MODEL"] = "gpt-4o"

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        chat_target = registry.instances.get("azure_openai_gpt4o")
        response_target = registry.instances.get("azure_openai_responses")

        assert chat_target is not None
        assert response_target is not None
        assert get_behavioral_key(chat_target) != get_behavioral_key(response_target)

        # Clean up
        for var in [
            "AZURE_OPENAI_RESPONSES_ENDPOINT",
            "AZURE_OPENAI_RESPONSES_KEY",
            "AZURE_OPENAI_RESPONSES_MODEL",
            "AZURE_OPENAI_RESPONSES_UNDERLYING_MODEL",
        ]:
            os.environ.pop(var, None)

    async def test_auto_group_disabled_when_false(self) -> None:
        """Test that auto_group=False disables round-robin auto-grouping."""
        from pyrit.prompt_target import RoundRobinTarget

        os.environ.update(self.GPT4O_1_ENV)
        os.environ.update(self.GPT4O_2_ENV)

        init = TargetInitializer()
        init.params = {"tags": ["default"], "auto_group": False}
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        # Individual targets should exist
        assert registry.instances.get("azure_openai_gpt4o") is not None
        assert registry.instances.get("azure_openai_gpt4o2") is not None
        # But no round-robin should be created
        rr_targets = [
            name
            for name in registry.instances.get_names()
            if isinstance(registry.instances.get(name), RoundRobinTarget)
        ]
        assert len(rr_targets) == 0

    async def test_auto_group_three_targets_same_model(self) -> None:
        """Test that 3 targets with the same underlying model are grouped into one round-robin."""
        from pyrit.prompt_target import RoundRobinTarget

        os.environ.update(self.GPT4O_1_ENV)
        os.environ.update(self.GPT4O_2_ENV)
        os.environ.update(self.UNSAFE1_ENV)

        init = TargetInitializer()
        await init.initialize_async()

        registry = TargetRegistry.get_registry_singleton()
        rr = registry.instances.get("OpenAIChatTarget_gpt-4o_rr")
        assert rr is not None
        assert isinstance(rr, RoundRobinTarget)
        # Should have 3 inner targets (gpt4o, gpt4o2, unsafe_chat)
        assert len(rr._targets) == 3

    async def test_auto_group_deduplicates_identical_targets(self) -> None:
        """Test that targets with identical config (same endpoint+model+key) are deduplicated in auto-grouping."""
        from pyrit.prompt_target import RoundRobinTarget

        # Set GPT4O_1 and GPT4O_2, but also set a third env var block that points
        # to the SAME endpoint+key+model as GPT4O_1. This simulates the real-world
        # case where .env has overlapping entries.
        os.environ.update(self.GPT4O_1_ENV)
        os.environ.update(self.GPT4O_2_ENV)
        os.environ.update(self.UNSAFE1_ENV)

        # Register a duplicate: same endpoint, key, model as UNSAFE1 but under a different registry name.
        # We do this by directly registering a target with the same params.
        from pyrit.prompt_target import OpenAIChatTarget

        dup_target = OpenAIChatTarget(
            endpoint="https://unsafe1.openai.azure.com",
            api_key="key1",
            model_name="gpt-4o",
            underlying_model="gpt-4o",
        )
        registry = TargetRegistry.get_registry_singleton()

        init = TargetInitializer()
        await init.initialize_async()

        # Register the duplicate after init so it's in the registry with a distinct name.
        registry.instances.register(dup_target, name="unsafe1_duplicate")
        init._registered_names.append("unsafe1_duplicate")

        # Re-run auto-grouping (clear existing RR first)
        existing_rr = registry.instances.get("OpenAIChatTarget_gpt-4o_rr")
        if existing_rr:
            registry.instances._registry_items.pop("OpenAIChatTarget_gpt-4o_rr", None)
        init._auto_group_targets()

        rr = registry.instances.get("OpenAIChatTarget_gpt-4o_rr")
        assert rr is not None
        assert isinstance(rr, RoundRobinTarget)
        # Should have 3, not 4 — the duplicate should be deduplicated
        assert len(rr._targets) == 3


class TestGetBehavioralKey:
    """Tests for _get_behavioral_key helper function."""

    def test_key_includes_class_name(self) -> None:
        """Test that the behavioral key includes the target's class name."""
        from tests.unit.mocks import MockPromptTarget

        target = MockPromptTarget()
        key = get_behavioral_key(target)
        assert key[0] == "MockPromptTarget"

    def test_same_model_same_temp_same_key(self) -> None:
        """Test that targets with identical behavioral params produce the same key."""
        target1 = OpenAIChatTarget(
            endpoint="https://ep1.com", api_key="k1", model_name="d1", underlying_model="gpt-4o", temperature=0.5
        )
        target2 = OpenAIChatTarget(
            endpoint="https://ep2.com", api_key="k2", model_name="d2", underlying_model="gpt-4o", temperature=0.5
        )
        assert get_behavioral_key(target1) == get_behavioral_key(target2)

    def test_different_temp_different_key(self) -> None:
        """Test that targets with different temperatures produce different keys."""
        target1 = OpenAIChatTarget(
            endpoint="https://ep1.com", api_key="k1", model_name="m1", underlying_model="gpt-4o", temperature=0.0
        )
        target2 = OpenAIChatTarget(
            endpoint="https://ep2.com", api_key="k2", model_name="m2", underlying_model="gpt-4o", temperature=0.9
        )
        assert get_behavioral_key(target1) != get_behavioral_key(target2)

    def test_different_underlying_model_different_key(self) -> None:
        """Test that targets with different underlying models produce different keys."""
        target1 = OpenAIChatTarget(endpoint="https://ep1.com", api_key="k1", model_name="m1", underlying_model="gpt-4o")
        target2 = OpenAIChatTarget(
            endpoint="https://ep2.com", api_key="k2", model_name="m2", underlying_model="gpt-3.5-turbo"
        )
        assert get_behavioral_key(target1) != get_behavioral_key(target2)

    def test_falls_back_to_model_name_when_no_underlying_model(self) -> None:
        """Test that model_name is used as fallback when underlying_model is not set."""
        target1 = OpenAIChatTarget(endpoint="https://ep1.com", api_key="k1", model_name="gpt-4o")
        target2 = OpenAIChatTarget(endpoint="https://ep2.com", api_key="k2", model_name="gpt-4o")
        assert get_behavioral_key(target1) == get_behavioral_key(target2)

    def test_different_top_p_different_key(self) -> None:
        """Test that targets with different top_p produce different keys."""
        target1 = OpenAIChatTarget(
            endpoint="https://ep1.com", api_key="k1", model_name="m1", underlying_model="gpt-4o", top_p=0.9
        )
        target2 = OpenAIChatTarget(
            endpoint="https://ep2.com", api_key="k2", model_name="m2", underlying_model="gpt-4o", top_p=0.5
        )
        assert get_behavioral_key(target1) != get_behavioral_key(target2)


class TestGenerateRrName:
    """Tests for _generate_rr_name helper function."""

    def test_basic_name_generation(self) -> None:
        """Test basic round-robin name generation from a behavioral key."""
        key = ("OpenAIChatTarget", ("temperature", None), ("top_p", None), ("underlying_model_name", "gpt-4o"))
        assert generate_rr_name(key) == "OpenAIChatTarget_gpt-4o_rr"

    def test_name_includes_temperature(self) -> None:
        """Test that non-default temperature is included in the name."""
        key = ("OpenAIChatTarget", ("temperature", 0.0), ("top_p", None), ("underlying_model_name", "gpt-4o"))
        assert generate_rr_name(key) == "OpenAIChatTarget_gpt-4o_temperature0.0_rr"

    def test_name_includes_top_p(self) -> None:
        """Test that non-default top_p is included in the name."""
        key = ("OpenAIChatTarget", ("temperature", None), ("top_p", 0.9), ("underlying_model_name", "gpt-4o"))
        assert generate_rr_name(key) == "OpenAIChatTarget_gpt-4o_top_p0.9_rr"

    def test_name_includes_class_prefix_for_all_target_types(self) -> None:
        """Test that all target types get their class name in the prefix."""
        key = ("OpenAIResponseTarget", ("temperature", None), ("top_p", None), ("underlying_model_name", "gpt-4o"))
        assert generate_rr_name(key) == "OpenAIResponseTarget_gpt-4o_rr"

    def test_name_unknown_when_no_model(self) -> None:
        """Test that 'unknown' is used when no underlying model is available."""
        key = ("OpenAIChatTarget", ("temperature", None), ("top_p", None), ("underlying_model_name", None))
        assert generate_rr_name(key) == "OpenAIChatTarget_unknown_rr"
