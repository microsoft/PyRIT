# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.prompt_target.common.conversation_normalization_pipeline import NORMALIZABLE_CAPABILITIES
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


class TestCapabilityHandlingPolicy:
    """Test behavior and defaults of capability handling policy classes."""

    def test_capability_name_values(self):
        assert CapabilityName.MULTI_TURN.value == "supports_multi_turn"
        assert CapabilityName.MULTI_MESSAGE_PIECES.value == "supports_multi_message_pieces"
        assert CapabilityName.JSON_SCHEMA.value == "supports_json_schema"
        assert CapabilityName.JSON_OUTPUT.value == "supports_json_output"
        assert CapabilityName.EDITABLE_HISTORY.value == "supports_editable_history"
        assert CapabilityName.SYSTEM_PROMPT.value == "supports_system_prompt"

    def test_unsupported_capability_behavior_values(self):
        assert UnsupportedCapabilityBehavior.ADAPT.value == "adapt"
        assert UnsupportedCapabilityBehavior.RAISE.value == "raise"

    def test_capability_handling_policy_defaults(self):
        policy = CapabilityHandlingPolicy()
        assert policy.behaviors == {
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
        }

    def test_capability_handling_policy_custom_values(self):
        policy = CapabilityHandlingPolicy(
            behaviors={
                CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
                CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
            }
        )

        assert policy.behaviors[CapabilityName.MULTI_TURN] is UnsupportedCapabilityBehavior.ADAPT
        assert policy.behaviors[CapabilityName.SYSTEM_PROMPT] is UnsupportedCapabilityBehavior.RAISE

    def test_capability_handling_policy_get_behavior(self):
        policy = CapabilityHandlingPolicy()

        assert policy.get_behavior(capability=CapabilityName.MULTI_TURN) is UnsupportedCapabilityBehavior.RAISE
        assert policy.get_behavior(capability=CapabilityName.SYSTEM_PROMPT) is UnsupportedCapabilityBehavior.RAISE

    def test_capability_handling_policy_get_behavior_for_all_default_keys(self):
        policy = CapabilityHandlingPolicy()
        for cap in policy.behaviors:
            assert policy.get_behavior(capability=cap) is UnsupportedCapabilityBehavior.RAISE

    def test_capability_handling_policy_rejects_capability_without_policy(self):
        policy = CapabilityHandlingPolicy()

        with pytest.raises(KeyError, match="No policy for capability 'supports_editable_history'"):
            policy.get_behavior(capability=CapabilityName.EDITABLE_HISTORY)

        with pytest.raises(AttributeError, match="supports_editable_history"):
            _ = policy.supports_editable_history

    def test_capability_handling_policy_rejects_unknown_attribute(self):
        policy = CapabilityHandlingPolicy()

        with pytest.raises(AttributeError, match="totally_unknown_attribute"):
            _ = policy.totally_unknown_attribute

    def test_normalizable_capabilities(self):
        assert (
            frozenset(
                {
                    CapabilityName.MULTI_TURN,
                    CapabilityName.SYSTEM_PROMPT,
                }
            )
            == NORMALIZABLE_CAPABILITIES
        )

    def test_target_capabilities_includes_helper(self):
        capabilities = TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=False,
            supports_json_output=True,
        )

        assert capabilities.includes(capability=CapabilityName.MULTI_TURN) is True
        assert capabilities.includes(capability=CapabilityName.SYSTEM_PROMPT) is False
        assert capabilities.includes(capability=CapabilityName.JSON_OUTPUT) is True
        assert capabilities.includes(capability=CapabilityName.EDITABLE_HISTORY) is False


# Env vars that may leak from .env files loaded by other tests in parallel workers.
# Clear them so that targets use _DEFAULT_CONFIGURATION instead of _KNOWN_CAPABILITIES.
_CLEAN_UNDERLYING_MODEL_ENV = {
    "OPENAI_VIDEO_UNDERLYING_MODEL": "",
    "OPENAI_REALTIME_UNDERLYING_MODEL": "",
    "OPENAI_CHAT_UNDERLYING_MODEL": "",
    "OPENAI_IMAGE_UNDERLYING_MODEL": "",
    "OPENAI_TTS_UNDERLYING_MODEL": "",
    "OPENAI_COMPLETION_UNDERLYING_MODEL": "",
    "OPENAI_RESPONSES_UNDERLYING_MODEL": "",
}


class TestDefaultConfigurationDefined:
    """Verify that every concrete PromptTarget subclass defines _DEFAULT_CONFIGURATION."""

    def _all_concrete_target_classes(self):
        from pyrit.prompt_target import (
            AzureBlobStorageTarget,
            AzureMLChatTarget,
            GandalfTarget,
            HTTPTarget,
            HTTPXAPITarget,
            HuggingFaceChatTarget,
            HuggingFaceEndpointTarget,
            OpenAIChatTarget,
            OpenAICompletionTarget,
            OpenAIImageTarget,
            OpenAIResponseTarget,
            OpenAITTSTarget,
            OpenAIVideoTarget,
            PlaywrightCopilotTarget,
            PlaywrightTarget,
            PromptShieldTarget,
            RealtimeTarget,
            TextTarget,
            WebSocketCopilotTarget,
        )

        return [
            AzureBlobStorageTarget,
            AzureMLChatTarget,
            GandalfTarget,
            HTTPTarget,
            HTTPXAPITarget,
            HuggingFaceChatTarget,
            HuggingFaceEndpointTarget,
            OpenAIChatTarget,
            OpenAICompletionTarget,
            OpenAIImageTarget,
            OpenAIResponseTarget,
            OpenAITTSTarget,
            OpenAIVideoTarget,
            PlaywrightCopilotTarget,
            PlaywrightTarget,
            PromptShieldTarget,
            RealtimeTarget,
            TextTarget,
            WebSocketCopilotTarget,
        ]

    def test_all_targets_have_default_configuration(self):
        """Every concrete target must have _DEFAULT_CONFIGURATION as a TargetConfiguration instance."""
        for cls in self._all_concrete_target_classes():
            assert hasattr(cls, "_DEFAULT_CONFIGURATION"), (
                f"{cls.__name__} is missing _DEFAULT_CONFIGURATION class attribute"
            )
            assert isinstance(cls._DEFAULT_CONFIGURATION, TargetConfiguration), (
                f"{cls.__name__}._DEFAULT_CONFIGURATION must be a TargetConfiguration instance, "
                f"got {type(cls._DEFAULT_CONFIGURATION)}"
            )


@pytest.mark.usefixtures("patch_central_database")
class TestTargetCapabilitiesModalities:
    """Test that each target declares the correct input/output modalities via _DEFAULT_CONFIGURATION."""

    def test_default_capabilities_are_text_only(self):
        caps = TargetCapabilities()
        assert caps.input_modalities == frozenset({frozenset(["text"])})
        assert caps.output_modalities == frozenset({frozenset(["text"])})

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_chat_target_modalities(self):
        from pyrit.prompt_target import OpenAIChatTarget

        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("text" in combo for combo in target.capabilities.output_modalities)
        assert target.capabilities.supports_json_output is True
        assert target.capabilities.supports_multi_message_pieces is True

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_image_target_modalities(self):
        from pyrit.prompt_target import OpenAIImageTarget

        target = OpenAIImageTarget(
            model_name="dall-e-3",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert target.capabilities.output_modalities == frozenset({frozenset(["image_path"])})
        assert target.capabilities.supports_multi_message_pieces is True

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_tts_target_modalities(self):
        from pyrit.prompt_target import OpenAITTSTarget

        target = OpenAITTSTarget(
            model_name="tts-1",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.input_modalities == frozenset({frozenset(["text"])})
        assert target.capabilities.output_modalities == frozenset({frozenset(["audio_path"])})

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_video_target_modalities(self):
        from pyrit.prompt_target import OpenAIVideoTarget

        target = OpenAIVideoTarget(
            model_name="sora-2",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("image_path" in combo for combo in target.capabilities.input_modalities)
        assert target.capabilities.output_modalities == frozenset({frozenset(["video_path"])})
        assert target.capabilities.supports_multi_message_pieces is True

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_realtime_target_modalities(self):
        from pyrit.prompt_target import RealtimeTarget

        target = RealtimeTarget(
            model_name="gpt-4o-realtime",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("audio_path" in combo for combo in target.capabilities.input_modalities)
        assert any("text" in combo for combo in target.capabilities.output_modalities)
        assert any("audio_path" in combo for combo in target.capabilities.output_modalities)

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_response_target_modalities(self):
        from pyrit.prompt_target import OpenAIResponseTarget

        target = OpenAIResponseTarget(
            model_name="o1",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("image_path" in combo for combo in target.capabilities.input_modalities)
        assert target.capabilities.output_modalities == frozenset({frozenset(["text"])})
        assert target.capabilities.supports_json_output is True
        assert target.capabilities.supports_multi_message_pieces is True

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_openai_completion_target_modalities(self):
        from pyrit.prompt_target import OpenAICompletionTarget

        target = OpenAICompletionTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        assert target.capabilities.input_modalities == frozenset({frozenset(["text"])})
        assert target.capabilities.output_modalities == frozenset({frozenset(["text"])})

    def test_azure_blob_storage_target_modalities(self):
        from pyrit.prompt_target import AzureBlobStorageTarget

        target = AzureBlobStorageTarget(
            container_url="https://mock.blob.core.windows.net/container",
            sas_token="mock-sas-token",
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("url" in combo for combo in target.capabilities.input_modalities)
        assert target.capabilities.output_modalities == frozenset({frozenset(["url"])})

    def test_text_target_modalities(self):
        from pyrit.prompt_target import TextTarget

        target = TextTarget()
        assert target.capabilities.input_modalities == frozenset({frozenset(["text"])})
        assert target.capabilities.output_modalities == frozenset({frozenset(["text"])})

    def test_playwright_target_modalities(self):
        from unittest.mock import MagicMock

        from pyrit.prompt_target import PlaywrightTarget

        target = PlaywrightTarget(
            interaction_func=MagicMock(),
            page=MagicMock(),
        )
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("image_path" in combo for combo in target.capabilities.input_modalities)
        assert target.capabilities.output_modalities == frozenset({frozenset(["text"])})

    def test_playwright_copilot_target_modalities(self):
        from unittest.mock import MagicMock

        from pyrit.prompt_target import PlaywrightCopilotTarget

        target = PlaywrightCopilotTarget(page=MagicMock())
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("image_path" in combo for combo in target.capabilities.input_modalities)
        assert any("text" in combo for combo in target.capabilities.output_modalities)
        assert any("image_path" in combo for combo in target.capabilities.output_modalities)

    def test_websocket_copilot_target_modalities(self):
        from unittest.mock import MagicMock

        from pyrit.prompt_target import WebSocketCopilotTarget

        target = WebSocketCopilotTarget(authenticator=MagicMock())
        assert any("text" in combo for combo in target.capabilities.input_modalities)
        assert any("image_path" in combo for combo in target.capabilities.input_modalities)
        assert target.capabilities.output_modalities == frozenset({frozenset(["text"])})

    def test_hugging_face_chat_target_capabilities(self):
        from pyrit.prompt_target import HuggingFaceChatTarget

        caps = HuggingFaceChatTarget._DEFAULT_CONFIGURATION.capabilities
        assert caps.supports_editable_history is True
        assert caps.supports_multi_turn is True
        assert caps.supports_system_prompt is True

    def test_azure_ml_chat_target_capabilities(self):
        from pyrit.prompt_target import AzureMLChatTarget

        target = AzureMLChatTarget(
            endpoint="https://mock.azure.com/score",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_editable_history is True
        assert target.capabilities.supports_multi_message_pieces is True
        assert target.capabilities.supports_system_prompt is True

    @patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
    def test_prompt_chat_targets_support_system_prompt(self):
        from pyrit.prompt_target import OpenAIChatTarget, OpenAIResponseTarget, RealtimeTarget

        openai_chat_target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        openai_response_target = OpenAIResponseTarget(
            model_name="o1",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
        realtime_target = RealtimeTarget(
            model_name="gpt-4o-realtime",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )

        assert openai_chat_target.capabilities.supports_system_prompt is True
        assert openai_response_target.capabilities.supports_system_prompt is True
        assert realtime_target.capabilities.supports_system_prompt is True

    def test_custom_configuration_override_modalities(self):
        from pyrit.prompt_target import OpenAIChatTarget, TargetCapabilities, TargetConfiguration

        custom = TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                input_modalities=frozenset({frozenset(["text"])}),
                output_modalities=frozenset({frozenset(["text"])}),
            )
        )
        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
            custom_configuration=custom,
        )
        assert target.capabilities.input_modalities == frozenset({frozenset(["text"])})
        assert target.capabilities.output_modalities == frozenset({frozenset(["text"])})


class TestGetKnownCapabilities:
    """Test TargetCapabilities.get_known_capabilities for every recognized model."""

    def test_gpt_4o_supports_multi_turn_and_json_output(self):
        caps = TargetCapabilities.get_known_capabilities("gpt-4o")
        assert caps is not None
        assert caps.supports_multi_turn is True
        assert caps.supports_multi_message_pieces is True
        assert caps.supports_json_output is True

    def test_gpt_4o_does_not_set_json_schema_or_editable_history(self):
        caps = TargetCapabilities.get_known_capabilities("gpt-4o")
        assert caps is not None
        assert caps.supports_json_schema is False
        assert caps.supports_editable_history is False

    def test_gpt_4o_input_modalities_include_text_image_and_combined(self):
        caps = TargetCapabilities.get_known_capabilities("gpt-4o")
        assert caps is not None
        assert frozenset({"text"}) in caps.input_modalities
        assert frozenset({"image_path"}) in caps.input_modalities
        assert frozenset({"text", "image_path"}) in caps.input_modalities

    def test_gpt_4o_output_modalities_are_text_only(self):
        caps = TargetCapabilities.get_known_capabilities("gpt-4o")
        assert caps is not None
        assert caps.output_modalities == frozenset({frozenset({"text"})})

    def test_gpt_5_returns_json_schema_and_json_output(self):
        for model in ["gpt-5", "gpt-5.1", "gpt-5.4"]:
            caps = TargetCapabilities.get_known_capabilities(model)
            assert caps is not None, f"Expected caps for {model}"
            assert caps.supports_multi_turn is True
            assert caps.supports_multi_message_pieces is True
            assert caps.supports_json_schema is True
            assert caps.supports_json_output is True

    def test_gpt_5_input_modalities_include_text_image_path_and_combined(self):
        for model in ["gpt-5", "gpt-5.1", "gpt-5.4"]:
            caps = TargetCapabilities.get_known_capabilities(model)
            assert caps is not None
            assert frozenset({"text"}) in caps.input_modalities
            assert frozenset({"image_path"}) in caps.input_modalities
            assert frozenset({"text", "image_path"}) in caps.input_modalities

    def test_gpt_5_output_modalities_are_text_only(self):
        for model in ["gpt-5", "gpt-5.1", "gpt-5.4"]:
            caps = TargetCapabilities.get_known_capabilities(model)
            assert caps is not None
            assert caps.output_modalities == frozenset({frozenset({"text"})})

    def test_gpt_realtime_1_5_returns_multi_turn_text_defaults(self):
        caps = TargetCapabilities.get_known_capabilities("gpt-realtime-1.5")
        assert caps is not None
        assert caps.supports_multi_turn is True
        assert caps.supports_multi_message_pieces is True
        assert frozenset({"text"}) in caps.input_modalities
        assert frozenset({"audio_path"}) in caps.input_modalities
        assert frozenset({"image_path"}) in caps.input_modalities
        assert frozenset({"text"}) in caps.output_modalities
        assert frozenset({"audio_path"}) in caps.output_modalities

    def test_tts_returns_text_input_audio_output(self):
        caps = TargetCapabilities.get_known_capabilities("tts")
        assert caps is not None
        assert caps.input_modalities == frozenset({frozenset(["text"])})
        assert caps.output_modalities == frozenset({frozenset({"audio_path"})})

    def test_sora_2_input_modalities_include_text_image_path_and_combined(self):
        caps = TargetCapabilities.get_known_capabilities("sora-2")
        assert caps is not None
        assert caps.supports_multi_turn is True
        assert caps.supports_multi_message_pieces is True
        assert frozenset({"text"}) in caps.input_modalities
        assert frozenset({"image_path"}) in caps.input_modalities
        assert frozenset({"text", "image_path"}) in caps.input_modalities

    def test_sora_2_output_modalities_include_video_and_audio(self):
        caps = TargetCapabilities.get_known_capabilities("sora-2")
        assert caps is not None
        assert frozenset({"video_path"}) in caps.output_modalities
        assert frozenset({"audio_path", "video_path"}) in caps.output_modalities

    def test_unknown_model_returns_none(self):
        assert TargetCapabilities.get_known_capabilities("unknown-model-xyz") is None

    def test_empty_string_returns_none(self):
        assert TargetCapabilities.get_known_capabilities("") is None


@pytest.mark.usefixtures("patch_central_database")
class TestGetDefaultConfiguration:
    """Test PromptTarget.get_default_configuration classmethod."""

    def _make_target_class(self, *, default_config: "TargetConfiguration"):
        """Create a minimal concrete PromptTarget subclass with the given _DEFAULT_CONFIGURATION."""
        from pyrit.models import Message
        from pyrit.prompt_target.common.prompt_target import PromptTarget

        class _ConcreteTarget(PromptTarget):
            _DEFAULT_CONFIGURATION = default_config

            async def send_prompt_async(self, *, message: Message) -> list[Message]:
                return []

        return _ConcreteTarget

    def test_returns_class_default_when_underlying_model_is_none(self):
        custom_config = TargetConfiguration(capabilities=TargetCapabilities(supports_editable_history=True))
        cls = self._make_target_class(default_config=custom_config)
        result = cls.get_default_configuration(None)
        assert result is custom_config

    def test_returns_known_config_when_model_is_recognized(self):
        custom_config = TargetConfiguration(capabilities=TargetCapabilities())
        cls = self._make_target_class(default_config=custom_config)
        result = cls.get_default_configuration("gpt-4o")
        expected = TargetCapabilities.get_known_capabilities("gpt-4o")
        assert result.capabilities == expected

    def test_returns_class_default_and_warns_when_model_is_unrecognized(self):
        custom_config = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
        cls = self._make_target_class(default_config=custom_config)
        with patch("pyrit.prompt_target.common.prompt_target.logger") as mock_logger:
            result = cls.get_default_configuration("totally-unknown-model")
            mock_logger.info.assert_called_once()
            warning_args = mock_logger.info.call_args[0]
            assert "totally-unknown-model" in warning_args[1]
        assert result is custom_config

    def test_subclass_default_config_not_overridden_by_parent_default(self):
        custom_config = TargetConfiguration(
            capabilities=TargetCapabilities(supports_json_output=True, supports_multi_turn=True)
        )
        cls = self._make_target_class(default_config=custom_config)
        result = cls.get_default_configuration(None)
        assert result.capabilities.supports_json_output is True
        assert result.capabilities.supports_multi_turn is True

    def test_recognized_model_overrides_class_default(self):
        # Class has a minimal default; recognized model should override it
        minimal_config = TargetConfiguration(capabilities=TargetCapabilities())
        cls = self._make_target_class(default_config=minimal_config)
        result = cls.get_default_configuration("tts")
        assert result.capabilities.output_modalities == frozenset({frozenset(["audio_path"])})

    def test_prompt_chat_target_preserves_system_prompt_for_recognized_model(self):
        from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget

        result = PromptChatTarget.get_default_configuration("gpt-4o")

        assert result.capabilities.supports_multi_turn is True
        assert result.capabilities.supports_multi_message_pieces is True
        assert result.capabilities.supports_system_prompt is True
