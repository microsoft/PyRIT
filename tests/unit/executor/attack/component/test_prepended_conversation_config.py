# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import get_args
from unittest.mock import MagicMock

from pyrit.executor.attack.component.prepended_conversation_config import PrependedConversationConfig
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import ChatMessageRole


def test_default_init_apply_converters_to_all_roles():
    config = PrependedConversationConfig()
    assert config.apply_converters_to_roles == list(get_args(ChatMessageRole))


def test_default_init_message_normalizer_is_none():
    config = PrependedConversationConfig()
    assert config.message_normalizer is None


def test_default_init_non_chat_target_behavior():
    config = PrependedConversationConfig()
    assert config.non_chat_target_behavior == "normalize_first_turn"


def test_get_message_normalizer_returns_default_when_none():
    config = PrependedConversationConfig()
    normalizer = config.get_message_normalizer()
    assert isinstance(normalizer, ConversationContextNormalizer)


def test_get_message_normalizer_returns_custom():
    mock_normalizer = MagicMock()
    config = PrependedConversationConfig(message_normalizer=mock_normalizer)
    assert config.get_message_normalizer() is mock_normalizer


def test_default_class_method():
    config = PrependedConversationConfig.default()
    assert config.apply_converters_to_roles == list(get_args(ChatMessageRole))
    assert config.message_normalizer is None
    assert config.non_chat_target_behavior == "raise"


def test_for_non_chat_target_defaults():
    config = PrependedConversationConfig.for_non_chat_target()
    assert config.apply_converters_to_roles == list(get_args(ChatMessageRole))
    assert config.message_normalizer is None
    assert config.non_chat_target_behavior == "normalize_first_turn"


def test_for_non_chat_target_with_custom_normalizer():
    mock_normalizer = MagicMock()
    config = PrependedConversationConfig.for_non_chat_target(message_normalizer=mock_normalizer)
    assert config.message_normalizer is mock_normalizer
    assert config.non_chat_target_behavior == "normalize_first_turn"


def test_for_non_chat_target_with_specific_roles():
    config = PrependedConversationConfig.for_non_chat_target(apply_converters_to_roles=["user"])
    assert config.apply_converters_to_roles == ["user"]


def test_default_vs_init_differ_in_behavior():
    default_config = PrependedConversationConfig.default()
    init_config = PrependedConversationConfig()
    assert default_config.non_chat_target_behavior == "raise"
    assert init_config.non_chat_target_behavior == "normalize_first_turn"
