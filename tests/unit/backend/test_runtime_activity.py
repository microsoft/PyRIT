# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for recent runtime activity tracking."""

from datetime import UTC, datetime, timedelta

from pyrit.backend.services.runtime_activity import RuntimeActivityTracker


def test_recent_chats_returns_only_last_ten_minutes_newest_first() -> None:
    tracker = RuntimeActivityTracker()
    now = datetime(2026, 9, 24, 23, tzinfo=UTC)
    tracker.record_chat(
        conversation_id="recent",
        operator="alice",
        operation="nightly",
        occurred_at=now - timedelta(minutes=2),
    )
    tracker.record_chat(
        conversation_id="newest",
        operator="bob",
        operation=None,
        occurred_at=now - timedelta(seconds=30),
    )
    tracker.record_chat(
        conversation_id="expired",
        operator="carol",
        operation="old",
        occurred_at=now - timedelta(minutes=11),
    )

    chats = tracker.recent_chats(now=now)

    assert [chat["conversation_id"] for chat in chats] == ["newest", "recent"]
    assert chats[1]["operator"] == "alice"
    assert chats[1]["operation"] == "nightly"
