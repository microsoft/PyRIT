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


def test_get_message_normalizer_returns_default_when_none():
    config = PrependedConversationConfig()
    normalizer = config.get_message_normalizer()
    assert isinstance(normalizer, ConversationContextNormalizer)


def test_get_message_normalizer_returns_custom():
    mock_normalizer = MagicMock()
    config = PrependedConversationConfig(message_normalizer=mock_normalizer)
    assert config.get_message_normalizer() is mock_normalizer
