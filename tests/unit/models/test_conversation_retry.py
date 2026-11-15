# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from enum import StrEnum

from pyrit.models import ConversationRetry, ConversationRetryReason


def test_conversation_retry_reason_is_str_enum():
    assert issubclass(ConversationRetryReason, StrEnum)
    assert isinstance(ConversationRetryReason.JSON_PARSING, str)


def test_conversation_retry_reason_str_and_format_return_value():
    member = ConversationRetryReason.JSON_PARSING
    assert str(member) == "json_parsing"
    assert f"{member}" == "json_parsing"
    assert format(member) == "json_parsing"
    assert member == "json_parsing"


def test_conversation_retry_reason_value_round_trip():
    assert ConversationRetryReason("json_parsing") is ConversationRetryReason.JSON_PARSING
    assert json.dumps(ConversationRetryReason.JSON_PARSING) == '"json_parsing"'


def test_conversation_retry_json_round_trip():
    record = ConversationRetry(sequence=3, reason=ConversationRetryReason.JSON_PARSING)
    dumped = record.model_dump(mode="json")
    assert dumped["reason"] == "json_parsing"
    restored = ConversationRetry.model_validate(dumped)
    assert restored.reason is ConversationRetryReason.JSON_PARSING
