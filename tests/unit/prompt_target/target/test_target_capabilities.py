# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pyrit.models.catalog import TargetInstance
from pyrit.models.identifiers import TargetIdentifier
from pyrit.prompt_target.common.conversation_normalization_pipeline import NORMALIZABLE_CAPABILITIES
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
    get_known_capabilities,
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
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.ADAPT,
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
        expected = {
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.ADAPT,
        }
        for cap in policy.behaviors:
            assert policy.get_behavior(capability=cap) is expected[cap]

    def test_capability_handling_policy_rejects_capability_without_policy(self):
        policy = CapabilityHandlingPolicy()

        with pytest.raises(KeyError, match="No policy for capability 'supports_multi_message_pieces'"):
            policy.get_behavior(capability=CapabilityName.MULTI_MESSAGE_PIECES)

        with pytest.raises(AttributeError, match="supports_multi_message_pieces"):
            _ = policy.supports_multi_message_pieces

    def test_capability_handling_policy_rejects_unknown_attribute(self):
        policy = CapabilityHandlingPolicy()

        with pytest.raises(AttributeError, match="totally_unknown_attribute"):
            _ = policy.totally_unknown_attribute

    def test_normalizable_capabilities(self):
        assert (
            frozenset(
                {
                    CapabilityName.MULTI_TURN,
                    CapabilityName.EDITABLE_HISTORY,
                    CapabilityName.SYSTEM_PROMPT,
                    CapabilityName.JSON_SCHEMA,
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
    """Test get_known_capabilities for every recognized model."""

    def test_gpt_4o_supports_multi_turn_and_json_output(self):
        caps = get_known_capabilities("gpt-4o")
        assert caps is not None
        assert caps.supports_multi_turn is True
        assert caps.supports_multi_message_pieces is True
        assert caps.supports_json_output is True

    def test_gpt_4o_does_not_set_json_schema_or_editable_history(self):
        caps = get_known_capabilities("gpt-4o")
        assert caps is not None
        assert caps.supports_json_schema is False
        assert caps.supports_editable_history is True

    def test_gpt_4o_input_modalities_include_text_image_and_combined(self):
        caps = get_known_capabilities("gpt-4o")
        assert caps is not None
        assert frozenset({"text"}) in caps.input_modalities
        assert frozenset({"image_path"}) in caps.input_modalities
        assert frozenset({"text", "image_path"}) in caps.input_modalities

    def test_gpt_4o_output_modalities_are_text_only(self):
        caps = get_known_capabilities("gpt-4o")
        assert caps is not None
        assert caps.output_modalities == frozenset({frozenset({"text"})})

    def test_gpt_5_returns_json_schema_and_json_output(self):
        for model in ["gpt-5", "gpt-5.1", "gpt-5.4"]:
            caps = get_known_capabilities(model)
            assert caps is not None, f"Expected caps for {model}"
            assert caps.supports_multi_turn is True
            assert caps.supports_multi_message_pieces is True
            assert caps.supports_json_schema is True
            assert caps.supports_json_output is True

    def test_gpt_5_input_modalities_include_text_image_path_and_combined(self):
        for model in ["gpt-5", "gpt-5.1", "gpt-5.4"]:
            caps = get_known_capabilities(model)
            assert caps is not None
            assert frozenset({"text"}) in caps.input_modalities
            assert frozenset({"image_path"}) in caps.input_modalities
            assert frozenset({"text", "image_path"}) in caps.input_modalities

    def test_gpt_5_output_modalities_are_text_only(self):
        for model in ["gpt-5", "gpt-5.1", "gpt-5.4"]:
            caps = get_known_capabilities(model)
            assert caps is not None
            assert caps.output_modalities == frozenset({frozenset({"text"})})

    def test_gpt_realtime_1_5_returns_multi_turn_text_defaults(self):
        caps = get_known_capabilities("gpt-realtime-1.5")
        assert caps is not None
        assert caps.supports_multi_turn is True
        assert caps.supports_multi_message_pieces is True
        assert frozenset({"text"}) in caps.input_modalities
        assert frozenset({"audio_path"}) in caps.input_modalities
        assert frozenset({"image_path"}) in caps.input_modalities
        assert frozenset({"text"}) in caps.output_modalities
        assert frozenset({"audio_path"}) in caps.output_modalities

    def test_tts_returns_text_input_audio_output(self):
        caps = get_known_capabilities("tts")
        assert caps is not None
        assert caps.input_modalities == frozenset({frozenset(["text"])})
        assert caps.output_modalities == frozenset({frozenset({"audio_path"})})

    def test_sora_2_input_modalities_include_text_image_path_and_combined(self):
        caps = get_known_capabilities("sora-2")
        assert caps is not None
        assert caps.supports_multi_turn is True
        assert caps.supports_multi_message_pieces is True
        assert frozenset({"text"}) in caps.input_modalities
        assert frozenset({"image_path"}) in caps.input_modalities
        assert frozenset({"text", "image_path"}) in caps.input_modalities

    def test_sora_2_output_modalities_include_video_and_audio(self):
        caps = get_known_capabilities("sora-2")
        assert caps is not None
        assert frozenset({"video_path"}) in caps.output_modalities
        assert frozenset({"audio_path", "video_path"}) in caps.output_modalities

    def test_unknown_model_returns_none(self):
        assert get_known_capabilities("unknown-model-xyz") is None

    def test_empty_string_returns_none(self):
        assert get_known_capabilities("") is None


@pytest.mark.usefixtures("patch_central_database")
class TestGetDefaultConfiguration:
    """Test PromptTarget.get_default_configuration classmethod."""

    def _make_target_class(self, *, default_config: "TargetConfiguration"):
        """Create a minimal concrete PromptTarget subclass with the given _DEFAULT_CONFIGURATION."""
        from pyrit.models import Message
        from pyrit.prompt_target.common.prompt_target import PromptTarget

        class _ConcreteTarget(PromptTarget):
            _DEFAULT_CONFIGURATION = default_config

            async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
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
        expected = get_known_capabilities("gpt-4o")
        assert expected is not None
        assert result.capabilities == expected.model_copy(
            update={
                "input_modalities": frozenset(
                    combo
                    for combo in expected.input_modalities
                    if not combo & {"function_call", "function_call_output"}
                )
            }
        )

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

    def test_prompt_target_preserves_system_prompt_for_recognized_model(self):
        from pyrit.prompt_target.common.prompt_target import PromptTarget

        result = PromptTarget.get_default_configuration("gpt-4o")

        assert result.capabilities.supports_multi_turn is True
        assert result.capabilities.supports_multi_message_pieces is True
        assert result.capabilities.supports_system_prompt is True


class TestTargetCapabilitiesWireRoundTrip:
    """Test that the REST wire form of TargetCapabilities reads back without losing data.

    ``TargetCapabilities`` is embedded in the ``TargetInstance`` REST response, so
    ``model_dump_json()`` / ``model_validate_json()`` is a real round trip: the CLI does
    exactly this on every ``GET /api/targets`` payload. Serialization emits the modality
    *combinations* as a sorted list of sorted lists (the ``frozenset[frozenset]`` fields
    have no order of their own), so reading the wire form back has to rebuild those
    combinations -- otherwise a non-text target silently reads back as text-only and the
    object contradicts the payload it came from. The flattened ``supported_*_modalities``
    projections stay on the wire for the UI.
    """

    def test_multi_combination_profile_survives_the_wire_round_trip(self):
        # More than one combination per field: reconstructing a single combination from
        # the flattened projection would still be readable but would not be equal.
        caps = TargetCapabilities(
            input_modalities=frozenset(
                {frozenset({"text"}), frozenset({"text", "image_path"}), frozenset({"audio_path", "text", "url"})}
            ),
            output_modalities=frozenset({frozenset({"text"}), frozenset({"audio_path", "text"})}),
        )

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps
        assert restored.input_modalities == caps.input_modalities
        assert restored.output_modalities == caps.output_modalities

    def test_wire_form_carries_combinations_and_flattened_projections(self):
        caps = TargetCapabilities(
            input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path", "text"})}),
        )

        payload = caps.model_dump(mode="json")

        # Combinations, sorted within and across, so equal capabilities serialize equally.
        assert payload["input_modalities"] == [["image_path", "text"], ["text"]]
        # The flattened projection the UI reads stays on the wire.
        assert payload["supported_input_modalities"] == ["image_path", "text"]
        assert payload["supported_output_modalities"] == ["text"]

    def test_serialized_ordering_is_stable_across_equivalent_objects(self):
        # frozenset iteration order varies with the strings' hashes, so two objects built
        # from differently-ordered inputs must still serialize identically.
        first = TargetCapabilities(
            input_modalities=frozenset(
                {frozenset({"url"}), frozenset({"audio_path", "text", "url"}), frozenset({"text"})}
            )
        )
        second = TargetCapabilities(
            input_modalities=frozenset(
                {frozenset({"text"}), frozenset({"audio_path", "text", "url"}), frozenset({"url"})}
            )
        )

        assert first.model_dump_json() == second.model_dump_json()

    def test_non_text_output_target_does_not_read_back_as_text_only(self):
        caps = TargetCapabilities(output_modalities=frozenset({frozenset({"image_path"})}))

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps
        assert restored.output_modalities == caps.output_modalities
        assert restored.supported_output_modalities == ["image_path"]

    def test_non_text_input_target_does_not_read_back_as_text_only(self):
        caps = TargetCapabilities(input_modalities=frozenset({frozenset({"audio_path"})}))

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps
        assert restored.input_modalities == caps.input_modalities
        assert restored.supported_input_modalities == ["audio_path"]

    def test_known_model_capabilities_survive_the_wire_round_trip(self):
        caps = get_known_capabilities("gpt-4o")
        assert caps is not None

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps
        assert restored.supported_input_modalities == caps.supported_input_modalities
        assert restored.supported_output_modalities == caps.supported_output_modalities
        for field in ("supports_multi_turn", "supports_system_prompt", "supports_json_output"):
            assert getattr(restored, field) == getattr(caps, field)

    def test_capability_helpers_agree_after_the_wire_round_trip(self):
        caps = TargetCapabilities(
            supports_multi_turn=True,
            output_modalities=frozenset({frozenset({"audio_path"})}),
        )

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps
        assert restored.includes(capability=CapabilityName.MULTI_TURN) is True
        assert "audio_path" in restored.supported_output_modalities

    def test_empty_modalities_survive_the_wire_round_trip(self):
        caps = TargetCapabilities(input_modalities=frozenset())

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps
        assert restored.supported_input_modalities == []
        assert restored.input_modalities == frozenset()

    def test_default_capabilities_are_unchanged_by_the_round_trip(self):
        caps = TargetCapabilities()

        restored = TargetCapabilities.model_validate_json(caps.model_dump_json())

        assert restored == caps

    def test_flattened_projection_in_the_payload_is_not_read_back_as_state(self):
        # ``supported_*_modalities`` is a derived projection, so a payload carrying a stale
        # one must not be able to override the combinations it is derived from.
        caps = TargetCapabilities.model_validate(
            {
                "input_modalities": [["image_path"]],
                "supported_input_modalities": ["text"],
            }
        )

        assert caps.input_modalities == frozenset({frozenset({"image_path"})})
        assert caps.supported_input_modalities == ["image_path"]

    def test_nested_target_instance_round_trip_preserves_capabilities(self):
        # ``TargetInstance`` is what the REST layer actually serves and what the CLI
        # validates, so the capabilities have to survive that nesting -- including the
        # inner targets of a composite target.
        inner = TargetInstance(
            target_registry_name="openai_chat",
            identifier=TargetIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target.openai"),
            capabilities=TargetCapabilities(
                input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path", "text"})}),
                output_modalities=frozenset({frozenset({"text"})}),
            ),
        )
        composite = TargetInstance(
            target_registry_name="round_robin",
            identifier=TargetIdentifier(class_name="RoundRobinTarget", class_module="pyrit.prompt_target.round_robin"),
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                input_modalities=frozenset({frozenset({"audio_path", "text"})}),
                output_modalities=frozenset({frozenset({"audio_path"}), frozenset({"text"})}),
            ),
            inner_targets=[inner],
        )

        restored = TargetInstance.model_validate_json(composite.model_dump_json())

        assert restored == composite
        assert restored.capabilities.input_modalities == composite.capabilities.input_modalities
        assert restored.capabilities.output_modalities == composite.capabilities.output_modalities
        assert restored.capabilities.supported_input_modalities == ["audio_path", "text"]
        assert restored.inner_targets is not None
        assert restored.inner_targets[0] == inner
        assert restored.inner_targets[0].capabilities.input_modalities == inner.capabilities.input_modalities

    def test_non_mapping_payload_is_left_to_pydantic(self):
        with pytest.raises(ValidationError):
            TargetCapabilities.model_validate_json('"not an object"')
