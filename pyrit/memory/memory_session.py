# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Transaction boundaries and last-reference cleanup for memory sessions."""

import uuid
from collections.abc import Sequence
from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from pyrit.memory.memory_models import (
    ObservationEntry,
    ObservationMessagePieceEntry,
    ScoreEntry,
    ScoreObservationEntry,
)


def _begin_sqlite_write(session: Session) -> None:
    """
    Start a physical SQLite transaction before validation reads.

    Raises:
        TypeError: If the SQLite session is not backed by a sqlite3 connection.
    """
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        driver_connection = connection.connection.driver_connection
        if not isinstance(driver_connection, SQLiteConnection):
            raise TypeError("SQLite memory requires a sqlite3.Connection.")
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_observations(*, session: Session, observation_ids: Sequence[uuid.UUID | str]) -> set[str]:
    """
    Serialize reference changes on parent rows, including SQL Server FK inserts.

    Returns:
        set[str]: Existing observation IDs, locked until the transaction ends.
    """
    found: set[str] = set()
    requested = sorted({str(value) for value in observation_ids})
    is_mssql = session.get_bind().dialect.name == "mssql"
    # SQL Server needs deterministic parent-lock ordering; ORDER BY on a batched
    # SELECT does not guarantee lock acquisition order. SQLite already holds its writer lock.
    batch_size = 1 if is_mssql else MemorySession._MAX_BIND_VARS
    for start in range(0, len(requested), batch_size):
        statement = select(ObservationEntry.id).where(ObservationEntry.id.in_(requested[start : start + batch_size]))
        if is_mssql:
            statement = statement.with_hint(ObservationEntry, "WITH (XLOCK, HOLDLOCK)", dialect_name="mssql")
        found.update(str(value) for value in session.scalars(statement))
    return found


class MemorySession(Session):
    """A session that releases evidence only after its last score is removed."""

    _MAX_BIND_VARS = 500

    def flush(self, objects: Sequence[Any] | None = None) -> None:
        """
        Flush ORM changes and clean up unreferenced observations transactionally.

        Raises:
            ValueError: If a new score references an observation that no longer exists.
        """
        if self._flushing:
            super().flush(objects)
            return
        with self.no_autoflush:
            removed = [entry for entry in self.deleted if isinstance(entry, ScoreEntry)]
            removed_links = [entry for entry in self.deleted if isinstance(entry, ScoreObservationEntry)]
            for entry in self.dirty | self.deleted:
                if isinstance(entry, ScoreEntry):
                    removed_links.extend(inspect(entry).attrs.observation_links.history.deleted)
            links = [entry for entry in self.new if isinstance(entry, ScoreObservationEntry)]
            if not removed and not removed_links and not links:
                super().flush(objects)
                return
            _begin_sqlite_write(self)
            candidates = {link.observation_id for link in removed_links if link.observation_id is not None}
            affected_score_ids = {entry.id for entry in removed} | {
                link.score_id for link in removed_links if link.score_id is not None
            }
            candidates.update(self._get_persisted_score_observation_ids(sorted(affected_score_ids, key=str)))
            new_ids = {
                link.observation_id if link.observation_id is not None else link.observation.id for link in links
            }
            found = _lock_observations(
                session=self,
                observation_ids=list(candidates | new_ids),
            )
            pending = {str(entry.id) for entry in self.new if isinstance(entry, ObservationEntry)}
            if missing := {str(value) for value in new_ids} - found - pending:
                raise ValueError(f"Score references observations not found in memory: {sorted(missing)}.")
            super().flush(objects)
            self._delete_remaining_score_links([entry for entry in removed if inspect(entry).deleted])
            self._delete_unreferenced_observations(candidates)

    def _get_persisted_score_observation_ids(self, score_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        """
        Find affected scores' observations without relying on cached relationships.

        Returns:
            set[uuid.UUID]: Persisted observation IDs to check after deleting the links.
        """
        candidates: set[uuid.UUID] = set()
        for start in range(0, len(score_ids), self._MAX_BIND_VARS):
            statement = select(ScoreObservationEntry.observation_id).where(
                ScoreObservationEntry.score_id.in_(score_ids[start : start + self._MAX_BIND_VARS])
            )
            if self.get_bind().dialect.name == "mssql":
                statement = statement.with_hint(ScoreObservationEntry, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
            candidates.update(self.scalars(statement))
        return candidates

    def _delete_remaining_score_links(self, scores: Sequence[ScoreEntry]) -> None:
        """Remove links missed by ORM cascades when database FK cascades are disabled."""
        score_ids = [entry.id for entry in scores]
        for start in range(0, len(score_ids), self._MAX_BIND_VARS):
            self.execute(
                delete(ScoreObservationEntry).where(
                    ScoreObservationEntry.score_id.in_(score_ids[start : start + self._MAX_BIND_VARS])
                )
            )

    def _delete_unreferenced_observations(self, observation_ids: set[uuid.UUID]) -> None:
        # Parent X locks (or SQLite's writer lock) survive until commit. An FK insert
        # cannot race the last-reference check and cascade-delete a new score's link.
        for observation_id in sorted(observation_ids, key=str):
            referenced = (
                select(ScoreObservationEntry.score_id)
                .where(ScoreObservationEntry.observation_id == observation_id)
                .limit(1)
            )
            if self.scalar(referenced) is not None:
                continue
            self.execute(
                delete(ObservationMessagePieceEntry).where(
                    ObservationMessagePieceEntry.observation_id == observation_id
                )
            )
            self.execute(delete(ObservationEntry).where(ObservationEntry.id == observation_id))
