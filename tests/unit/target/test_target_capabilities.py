# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.prompt_target.common.target_capabilities import TargetCapabilities

# Env vars that may leak from .env files loaded by other tests in parallel workers.
# Clear them so that targets use _DEFAULT_CAPABILITIES instead of _KNOWN_CAPABILITIES.
_CLEAN_UNDERLYING_MODEL_ENV = {
    "OPENAI_VIDEO_UNDERLYING_MODEL": "",
    "OPENAI_REALTIME_UNDERLYING_MODEL": "",
    "OPENAI_CHAT_UNDERLYING_MODEL": "",
    "OPENAI_IMAGE_UNDERLYING_MODEL": "",
    "OPENAI_TTS_UNDERLYING_MODEL": "",
    "OPENAI_COMPLETION_UNDERLYING_MODEL": "",
    "OPENAI_RESPONSES_UNDERLYING_MODEL": "",
}


class TestDefaultCapabilitiesDefined:
    """Verify that every concrete PromptTarget subclass defines _DEFAULT_CAPABILITIES."""

    def _all_concrete_target_classes(self):
        from pyrit.prompt_target import (
            AzureBlobStorageTarget,
            AzureMLChatTarget,
            CrucibleTarget,
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
            CrucibleTarget,
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

    def test_all_targets_have_default_capabilities(self):
        """Every concrete target must have _DEFAULT_CAPABILITIES as a TargetCapabilities instance."""
        for cls in self._all_concrete_target_classes():
            assert hasattr(cls, "_DEFAULT_CAPABILITIES"), (
                f"{cls.__name__} is missing _DEFAULT_CAPABILITIES class attribute"
            )
            assert isinstance(cls._DEFAULT_CAPABILITIES, TargetCapabilities), (
                f"{cls.__name__}._DEFAULT_CAPABILITIES must be a TargetCapabilities instance, "
                f"got {type(cls._DEFAULT_CAPABILITIES)}"
            )


@pytest.mark.usefixtures("patch_central_database")
class TestTargetCapabilitiesModalities:
    """Test that each target declares the correct input/output modalities via _DEFAULT_CAPABILITIES."""

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

        caps = HuggingFaceChatTarget._DEFAULT_CAPABILITIES
        assert caps.supports_editable_history is True
        assert caps.supports_multi_turn is True

    def test_azure_ml_chat_target_capabilities(self):
        from pyrit.prompt_target import AzureMLChatTarget

        target = AzureMLChatTarget(
            endpoint="https://mock.azure.com/score",
            api_key="mock-api-key",
        )
        assert target.capabilities.supports_editable_history is True
        assert target.capabilities.supports_multi_message_pieces is True

    def test_custom_capabilities_override_modalities(self):
        from pyrit.prompt_target import OpenAIChatTarget, TargetCapabilities

        custom = TargetCapabilities(
            supports_multi_turn=True,
            input_modalities=frozenset({frozenset(["text"])}),
            output_modalities=frozenset({frozenset(["text"])}),
        )
        target = OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
            custom_capabilities=custom,
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
class TestGetDefaultCapabilities:
    """Test PromptTarget.get_default_capabilities classmethod."""

    def _make_target_class(self, *, default_caps: "TargetCapabilities"):
        """Create a minimal concrete PromptTarget subclass with the given _DEFAULT_CAPABILITIES."""
        from pyrit.models import Message
        from pyrit.prompt_target.common.prompt_target import PromptTarget

        class _ConcreteTarget(PromptTarget):
            _DEFAULT_CAPABILITIES = default_caps

            async def send_prompt_async(self, *, message: Message) -> list[Message]:
                return []

        return _ConcreteTarget

    def test_returns_class_default_when_underlying_model_is_none(self):
        custom_caps = TargetCapabilities(supports_editable_history=True)
        cls = self._make_target_class(default_caps=custom_caps)
        result = cls.get_default_capabilities(None)
        assert result is custom_caps

    def test_returns_known_caps_when_model_is_recognized(self):
        custom_caps = TargetCapabilities()
        cls = self._make_target_class(default_caps=custom_caps)
        result = cls.get_default_capabilities("gpt-4o")
        expected = TargetCapabilities.get_known_capabilities("gpt-4o")
        assert result == expected

    def test_returns_class_default_and_warns_when_model_is_unrecognized(self):
        custom_caps = TargetCapabilities(supports_multi_turn=True)
        cls = self._make_target_class(default_caps=custom_caps)
        with patch("pyrit.prompt_target.common.prompt_target.logger") as mock_logger:
            result = cls.get_default_capabilities("totally-unknown-model")
            mock_logger.info.assert_called_once()
            warning_args = mock_logger.info.call_args[0]
            assert "totally-unknown-model" in warning_args[1]
        assert result is custom_caps

    def test_subclass_default_caps_not_overridden_by_parent_default(self):
        custom_caps = TargetCapabilities(supports_json_output=True, supports_multi_turn=True)
        cls = self._make_target_class(default_caps=custom_caps)
        result = cls.get_default_capabilities(None)
        assert result.supports_json_output is True
        assert result.supports_multi_turn is True

    def test_recognized_model_overrides_class_default(self):
        # Class has a minimal default; recognized model should override it
        minimal_caps = TargetCapabilities()
        cls = self._make_target_class(default_caps=minimal_caps)
        result = cls.get_default_capabilities("tts")
        assert result.output_modalities == frozenset({frozenset(["audio_path"])})
