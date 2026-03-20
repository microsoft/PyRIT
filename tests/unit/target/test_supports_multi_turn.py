# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from tests.unit.mocks import MockPromptTarget


@pytest.mark.usefixtures("patch_central_database")
class TestSupportsMultiTurn:
    """Test supports_multi_turn capability flag across the target hierarchy."""

    def test_prompt_target_defaults_to_false(self):
        # PromptTarget is abstract, so we verify via the class default capabilities
        from pyrit.prompt_target import PromptTarget, TargetCapabilities

        assert TargetCapabilities() == PromptTarget._DEFAULT_CAPABILITIES
        assert PromptTarget._DEFAULT_CAPABILITIES.supports_multi_turn is False

    def test_prompt_chat_target_returns_true(self):
        target = MockPromptTarget()
        assert target.capabilities.supports_multi_turn is True

    def test_openai_chat_target_returns_true(self):
        from pyrit.prompt_target import OpenAIChatTarget

        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is True

    def test_openai_image_target_returns_false(self):
        from pyrit.prompt_target import OpenAIImageTarget

        target = OpenAIImageTarget(
            model_name="dall-e-3",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is False

    def test_openai_video_target_returns_false(self):
        from pyrit.prompt_target import OpenAIVideoTarget

        target = OpenAIVideoTarget(
            model_name="sora-2",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is False

    def test_openai_tts_target_returns_false(self):
        from pyrit.prompt_target import OpenAITTSTarget

        target = OpenAITTSTarget(
            model_name="tts-1",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is False

    def test_openai_completion_target_returns_false(self):
        from pyrit.prompt_target import OpenAICompletionTarget

        target = OpenAICompletionTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is False

    def test_text_target_returns_false(self):
        from pyrit.prompt_target import TextTarget

        target = TextTarget()
        assert target.capabilities.supports_multi_turn is False

    def test_constructor_override_supports_multi_turn(self):
        """Test that capabilities can be overridden via the constructor."""
        from pyrit.prompt_target import OpenAIChatTarget, TargetCapabilities

        # By default, chat targets support multi-turn
        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is True

        # Override via constructor
        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
            custom_capabilities=TargetCapabilities(supports_multi_turn=False),
        )
        assert target.capabilities.supports_multi_turn is False

    def test_constructor_override_single_turn_to_multi(self):
        """Test that a single-turn target can be overridden to multi-turn."""
        from pyrit.prompt_target import OpenAIImageTarget, TargetCapabilities

        target = OpenAIImageTarget(
            model_name="dall-e-3",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is False

        target = OpenAIImageTarget(
            model_name="dall-e-3",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
            custom_capabilities=TargetCapabilities(supports_multi_turn=True),
        )
        assert target.capabilities.supports_multi_turn is True

    def test_capabilities_property_returns_target_capabilities(self):
        """Test that the capabilities property returns a TargetCapabilities instance."""
        from pyrit.prompt_target import OpenAIChatTarget, TargetCapabilities

        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        caps = target.capabilities
        assert isinstance(caps, TargetCapabilities)
        assert caps.supports_multi_turn is True

    def test_capabilities_override_via_constructor(self):
        """Test that capabilities are correctly overridden via the constructor."""
        from pyrit.prompt_target import OpenAIChatTarget, TargetCapabilities

        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
            custom_capabilities=TargetCapabilities(supports_multi_turn=False),
        )
        caps = target.capabilities
        assert isinstance(caps, TargetCapabilities)
        assert caps.supports_multi_turn is False

    def test_prompt_shield_target_returns_false(self):
        from pyrit.prompt_target import PromptShieldTarget

        target = PromptShieldTarget(
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_multi_turn is False
