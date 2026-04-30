# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings
from unittest.mock import MagicMock

import pytest
from unit.mocks import MockPromptTarget

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


@pytest.mark.usefixtures("patch_central_database")
def test_init_default_capabilities():
    target = MockPromptTarget()
    caps = target.capabilities
    assert caps.supports_multi_turn is True
    assert caps.supports_multi_message_pieces is True
    assert caps.supports_system_prompt is True


@pytest.mark.usefixtures("patch_central_database")
def test_is_response_format_json_false_when_no_metadata():
    target = MockPromptTarget()
    piece = MagicMock(spec=MessagePiece)
    piece.prompt_metadata = None
    # MockPromptTarget doesn't have is_response_format_json, use the base class method
    result = PromptChatTarget.is_response_format_json(target, message_piece=piece)
    assert result is False


@pytest.mark.usefixtures("patch_central_database")
def test_is_response_format_json_true_when_json_format():
    target = MockPromptTarget()
    piece = MagicMock(spec=MessagePiece)
    piece.prompt_metadata = {"response_format": "json"}
    # PromptChatTarget default capabilities don't support json_output, so this should raise
    with pytest.raises(ValueError, match="does not support JSON response format"):
        PromptChatTarget.is_response_format_json(target, message_piece=piece)


@pytest.mark.usefixtures("patch_central_database")
def test_is_response_format_json_true_with_json_capable_target():
    custom_conf = TargetConfiguration(capabilities=TargetCapabilities(supports_json_output=True))
    target = MockPromptTarget()
    target._configuration = custom_conf
    piece = MagicMock(spec=MessagePiece)
    piece.prompt_metadata = {"response_format": "json"}
    result = PromptChatTarget.is_response_format_json(target, message_piece=piece)
    assert result is True


@pytest.mark.usefixtures("patch_central_database")
def test_default_configuration_class_attribute():
    assert PromptChatTarget._DEFAULT_CONFIGURATION.capabilities.supports_multi_turn is True
    assert PromptChatTarget._DEFAULT_CONFIGURATION.capabilities.supports_system_prompt is True


@pytest.mark.usefixtures("patch_central_database")
def test_configuration_property_returns_configuration():
    target = MockPromptTarget()
    config = target.configuration
    assert isinstance(config, TargetConfiguration)
    assert config is target._configuration


@pytest.mark.usefixtures("patch_central_database")
def test_init_subclass_promotes_default_capabilities_with_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        class _LegacyTarget(PromptTarget):
            _DEFAULT_CAPABILITIES = TargetCapabilities(supports_multi_turn=True)

            async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
                return []

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) == 1
    assert "_DEFAULT_CAPABILITIES is deprecated" in str(deprecation_warnings[0].message)
    assert isinstance(_LegacyTarget._DEFAULT_CONFIGURATION, TargetConfiguration)
    assert _LegacyTarget._DEFAULT_CONFIGURATION.capabilities.supports_multi_turn is True


@pytest.mark.usefixtures("patch_central_database")
def test_get_default_capabilities_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = PromptChatTarget.get_default_capabilities(None)

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) == 1
    assert "get_default_capabilities() is deprecated" in str(deprecation_warnings[0].message)
    assert isinstance(result, TargetCapabilities)
    assert result.supports_multi_turn is True
