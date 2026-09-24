# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Recent runtime activity shown before PyRIT reinitialization."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any


@dataclass(frozen=True, slots=True)
class RecentChat:
    """A chat conversation with recent message activity."""

    conversation_id: str
    operator: str | None
    operation: str | None
    last_activity: datetime


class RuntimeActivityTracker:
    """Track recent chat activity without querying persistent history."""

    CHAT_ACTIVITY_WINDOW = timedelta(minutes=10)

    def __init__(self) -> None:
        """Initialize an empty process-local activity tracker."""
        self._lock = Lock()
        self._chats: dict[str, RecentChat] = {}

    def record_chat(
        self,
        *,
        conversation_id: str,
        operator: str | None,
        operation: str | None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record the latest activity for one conversation."""
        activity = RecentChat(
            conversation_id=conversation_id,
            operator=operator,
            operation=operation,
            last_activity=occurred_at or datetime.now(UTC),
        )
        with self._lock:
            self._chats[conversation_id] = activity

    def recent_chats(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Return chat activity from the last ten minutes, newest first."""
        current_time = now or datetime.now(UTC)
        cutoff = current_time - self.CHAT_ACTIVITY_WINDOW
        with self._lock:
            self._chats = {
                conversation_id: activity
                for conversation_id, activity in self._chats.items()
                if activity.last_activity >= cutoff
            }
            activities = sorted(self._chats.values(), key=lambda activity: activity.last_activity, reverse=True)
        return [asdict(activity) for activity in activities]
