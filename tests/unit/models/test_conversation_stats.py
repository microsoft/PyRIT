# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timezone

import pytest

from pyrit.models.conversation_stats import ConversationStats


def test_conversation_stats_defaults():
    stats = ConversationStats()
    assert stats.message_count == 0
    assert stats.last_message_preview is None
    assert stats.labels == {}
    assert stats.created_at is None


def test_conversation_stats_with_values():
    now = datetime.now(timezone.utc)
    stats = ConversationStats(
        message_count=5,
        last_message_preview="Hello world",
        labels={"env": "test"},
        created_at=now,
    )
    assert stats.message_count == 5
    assert stats.last_message_preview == "Hello world"
    assert stats.labels == {"env": "test"}
    assert stats.created_at == now


def test_conversation_stats_is_frozen():
    stats = ConversationStats(message_count=3)
    with pytest.raises(AttributeError):
        stats.message_count = 10


def test_conversation_stats_preview_max_len_class_var():
    assert ConversationStats.PREVIEW_MAX_LEN == 100


def test_conversation_stats_labels_default_factory():
    stats1 = ConversationStats()
    stats2 = ConversationStats()
    assert stats1.labels is not stats2.labels
