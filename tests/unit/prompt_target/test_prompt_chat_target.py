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
    assert target.is_response_format_json(message_piece=piece) is False


@pytest.mark.usefixtures("patch_central_database")
def test_is_response_format_json_true_when_json_format():
    target = MockPromptTarget()
    piece = MagicMock(spec=MessagePiece)
    piece.prompt_metadata = {"response_format": "json"}
    # Default MockPromptTarget capabilities don't support json_output, so this should raise
    with pytest.raises(ValueError, match="does not support JSON response format"):
        target.is_response_format_json(message_piece=piece)


@pytest.mark.usefixtures("patch_central_database")
def test_is_response_format_json_true_with_json_capable_target():
    custom_conf = TargetConfiguration(capabilities=TargetCapabilities(supports_json_output=True))
    target = MockPromptTarget()
    target._configuration = custom_conf
    piece = MagicMock(spec=MessagePiece)
    piece.prompt_metadata = {"response_format": "json"}
    assert target.is_response_format_json(message_piece=piece) is True


@pytest.mark.usefixtures("patch_central_database")
def test_configuration_property_returns_configuration():
    target = MockPromptTarget()
    config = target.configuration
    assert isinstance(config, TargetConfiguration)
    assert config is target._configuration


@pytest.mark.usefixtures("patch_central_database")
def test_subclassing_prompt_chat_target_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        class _LegacyChatSubclass(PromptChatTarget):
            async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
                return []

    deprecation_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "PromptChatTarget" in str(w.message)
        and "deprecated" in str(w.message)
    ]
    assert len(deprecation_warnings) >= 1


@pytest.mark.usefixtures("patch_central_database")
def test_instantiating_prompt_chat_target_subclass_emits_deprecation_warning():
    """``PromptChatTarget.__init__`` is deprecated and must emit a warning when called."""

    class _LegacyChatSubclassForInit(PromptChatTarget):
        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            return []

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _LegacyChatSubclassForInit()

    deprecation_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "PromptChatTarget" in str(w.message)
        and "0.16.0" in str(w.message)
    ]
    assert len(deprecation_warnings) >= 1


@pytest.mark.usefixtures("patch_central_database")
def test_set_system_prompt_available_on_prompt_target():
    """The set_system_prompt API now lives on PromptTarget directly."""
    assert hasattr(PromptTarget, "set_system_prompt")
    assert hasattr(PromptTarget, "is_response_format_json")


class _BarePromptTarget(PromptTarget):
    """Minimal PromptTarget subclass that does not override set_system_prompt."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "supports_multi_turn,supports_editable_history",
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
def test_set_system_prompt_raises_when_capabilities_missing(supports_multi_turn: bool, supports_editable_history: bool):
    """set_system_prompt must require both multi-turn and editable-history capabilities."""
    config = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=supports_multi_turn,
            supports_editable_history=supports_editable_history,
        )
    )
    target = _BarePromptTarget(custom_configuration=config)

    with pytest.raises(ValueError, match="does not support setting a system prompt"):
        target.set_system_prompt(
            system_prompt="you are a helpful assistant",
            conversation_id="conv-1",
        )


@pytest.mark.usefixtures("patch_central_database")
def test_set_system_prompt_writes_system_message_when_capabilities_present():
    """set_system_prompt writes a system-role message to memory on a capable target."""
    config = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
        )
    )
    target = _BarePromptTarget(custom_configuration=config)
    conversation_id = "conv-success"

    target.set_system_prompt(
        system_prompt="you are a helpful assistant",
        conversation_id=conversation_id,
    )

    messages = target._memory.get_conversation(conversation_id=conversation_id)
    assert len(messages) == 1
    pieces = messages[0].message_pieces
    assert len(pieces) == 1
    assert pieces[0].get_role_for_storage() == "system"
    assert pieces[0].original_value == "you are a helpful assistant"


@pytest.mark.usefixtures("patch_central_database")
def test_set_system_prompt_raises_when_conversation_already_exists():
    """set_system_prompt must refuse to overwrite an existing conversation."""
    config = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
        )
    )
    target = _BarePromptTarget(custom_configuration=config)
    conversation_id = "conv-existing"

    target.set_system_prompt(system_prompt="first", conversation_id=conversation_id)

    with pytest.raises(RuntimeError, match="Conversation already exists"):
        target.set_system_prompt(system_prompt="second", conversation_id=conversation_id)
