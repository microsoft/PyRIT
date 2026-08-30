# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import get_type_hints
from unittest.mock import MagicMock

from pyrit.executor.attack.component.prepended_conversation_config import PrependedConversationConfig
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import ChatMessageRole


def test_default_init_apply_converters_to_user_role():
    config = PrependedConversationConfig()
    assert config.apply_converters_to_roles == ["user"]


def test_simulated_assistant_converter_role_normalizes_to_assistant():
    config = PrependedConversationConfig(apply_converters_to_roles=["simulated_assistant"])
    assert config.apply_converters_to_roles == ["assistant"]


def test_public_type_hints_resolve_at_runtime():
    assert get_type_hints(PrependedConversationConfig)["apply_converters_to_roles"] == list[ChatMessageRole]


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
