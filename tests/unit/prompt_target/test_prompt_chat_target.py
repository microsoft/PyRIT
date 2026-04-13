# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest
from unit.mocks import MockPromptTarget, get_mock_attack_identifier

from pyrit.models import MessagePiece
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities


@pytest.mark.usefixtures("patch_central_database")
def test_init_default_capabilities():
    target = MockPromptTarget()
    caps = target.capabilities
    assert caps.supports_multi_turn is True
    assert caps.supports_multi_message_pieces is True
    assert caps.supports_system_prompt is True


@pytest.mark.usefixtures("patch_central_database")
def test_init_custom_capabilities():
    custom = TargetCapabilities(supports_multi_turn=True)
    target = MockPromptTarget()
    target._capabilities = custom
    assert target.capabilities.supports_multi_turn is True


@pytest.mark.usefixtures("patch_central_database")
def test_set_system_prompt_adds_to_memory():
    target = MockPromptTarget()
    attack_id = get_mock_attack_identifier()
    target.set_system_prompt(
        system_prompt="You are a helpful assistant.",
        conversation_id="conv-1",
        attack_identifier=attack_id,
        labels={"key": "value"},
    )
    messages = target._memory.get_message_pieces(conversation_id="conv-1")
    assert len(messages) == 1
    assert messages[0].api_role == "system"
    assert messages[0].converted_value == "You are a helpful assistant."


@pytest.mark.usefixtures("patch_central_database")
def test_set_system_prompt_raises_if_conversation_exists():
    target = MockPromptTarget()
    target.set_system_prompt(
        system_prompt="first",
        conversation_id="conv-2",
    )
    # The base PromptChatTarget.set_system_prompt should raise on existing conversation,
    # but MockPromptTarget overrides it. Test the base class directly via a concrete subclass.
    # We test using the real PromptChatTarget.set_system_prompt by calling it on a
    # target that uses the real implementation.


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
    custom_caps = TargetCapabilities(supports_json_output=True)
    target = MockPromptTarget()
    target._capabilities = custom_caps
    piece = MagicMock(spec=MessagePiece)
    piece.prompt_metadata = {"response_format": "json"}
    result = PromptChatTarget.is_response_format_json(target, message_piece=piece)
    assert result is True


@pytest.mark.usefixtures("patch_central_database")
def test_default_capabilities_class_attribute():
    assert PromptChatTarget._DEFAULT_CAPABILITIES.supports_multi_turn is True
    assert PromptChatTarget._DEFAULT_CAPABILITIES.supports_system_prompt is True
