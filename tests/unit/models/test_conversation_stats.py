# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from pyrit.models.conversation_stats import ConversationStats


def test_conversation_stats_defaults():
    stats = ConversationStats()
    assert stats.message_count == 0
    assert stats.last_message_preview is None
    assert stats.last_message_data_type is None
    assert stats.labels == {}
    assert stats.created_at is None


def test_conversation_stats_with_values():
    now = datetime.now(timezone.utc)
    stats = ConversationStats(
        message_count=5,
        last_message_preview="Hello world",
        last_message_data_type="text",
        labels={"env": "test"},
        created_at=now,
    )
    assert stats.message_count == 5
    assert stats.last_message_preview == "Hello world"
    assert stats.last_message_data_type == "text"
    assert stats.labels == {"env": "test"}
    assert stats.created_at == now


def test_conversation_stats_is_frozen():
    stats = ConversationStats(message_count=3)
    with pytest.raises(ValidationError):
        stats.message_count = 10


def test_conversation_stats_preview_max_len_class_var():
    assert ConversationStats.PREVIEW_MAX_LEN == 100


def test_conversation_stats_preview_fetch_max_len_class_var():
    # Storage-fetch cap must be strictly larger than the display-truncation
    # cap so downstream formatters can extract a basename / preview from a
    # long media path before further truncation.
    assert ConversationStats.PREVIEW_FETCH_MAX_LEN > ConversationStats.PREVIEW_MAX_LEN


def test_conversation_stats_accepts_media_data_type():
    stats = ConversationStats(
        message_count=1,
        last_message_preview=r"C:\foo\bar.png",
        last_message_data_type="image_path",
    )
    assert stats.last_message_data_type == "image_path"


def test_conversation_stats_rejects_unknown_data_type():
    with pytest.raises(ValidationError):
        ConversationStats(last_message_data_type="not_a_real_type")  # type: ignore[arg-type]


def test_conversation_stats_labels_default_factory():
    stats1 = ConversationStats()
    stats2 = ConversationStats()
    assert stats1.labels is not stats2.labels
