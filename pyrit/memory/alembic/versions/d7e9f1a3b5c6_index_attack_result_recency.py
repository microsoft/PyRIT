# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Index AttackResultEntries for the History recency query and make the ``timestamp``
column the single source of truth for "last updated".

Adds an index on ``conversation_id`` (serves the per-conversation dedup window) and a
composite ``(timestamp, id)`` index (serves the recency ORDER BY + keyset seek). Backfills
``timestamp`` from the legacy ``attack_metadata.updated_at``/``created_at`` JSON keys so
manually-edited conversations keep their current History order, and drops the now-redundant
``updated_at`` key from the JSON metadata. Narrows ``conversation_id`` to ``VARCHAR(36)`` on
all backends so the persisted schema matches the ORM model; this is required on SQL Server
because ``VARCHAR(MAX)`` columns cannot be index keys.

Revision ID: d7e9f1a3b5c6
Revises: 3f6e8a0c2d4b
Create Date: 2026-07-20 12:00:00.000000
"""

from __future__ import annotations

import logging
from collections.abc import Sequence  # noqa: TC003
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "d7e9f1a3b5c6"
down_revision: str | Sequence[str] | None = "3f6e8a0c2d4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger(__name__)

# Bounded batch size for the recency backfill updates. Grouping the row updates into a single
# executemany per batch keeps the number of statements (and, on SQL Server, database roundtrips)
# proportional to the row count / batch size instead of one statement per row.
_BACKFILL_UPDATE_BATCH_SIZE = 400
_CONVERSATION_ID_LENGTH = 36


def _attack_results_table() -> sa.Table:
    """
    Build a lightweight typed table clause for reading/writing the columns this migration
    touches, so ``UTCDateTime``/``JSON`` bind and result processors run on both backends.

    Returns:
        sa.Table: A minimal ``AttackResultEntries`` table with the id, attack_metadata, and
        timestamp columns.
    """
    metadata_obj = sa.MetaData()
    return sa.Table(
        "AttackResultEntries",
        metadata_obj,
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("attack_metadata", sa.JSON()),
        sa.Column("timestamp", UTCDateTime()),
        extend_existing=True,
    )


def _parse_iso(value: object) -> datetime | None:
    """
    Parse an ISO-8601 string (tolerating a trailing ``Z``) into a datetime, or ``None``.

    Returns:
        datetime | None: The parsed datetime, or ``None`` when ``value`` is not a parseable
        ISO string.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def upgrade() -> None:
    """Apply this schema upgrade."""
    _set_conversation_id_type(length=_CONVERSATION_ID_LENGTH)
    op.create_index(
        "ix_AttackResultEntries_conversation_id",
        "AttackResultEntries",
        ["conversation_id"],
    )
    op.create_index(
        "ix_AttackResultEntries_timestamp_id",
        "AttackResultEntries",
        ["timestamp", "id"],
    )
    _backfill_timestamp_from_metadata()


def downgrade() -> None:
    """Revert this schema upgrade."""
    # Restore metadata.updated_at from the timestamp column so the legacy JSON recency sort
    # reflects each row's last-updated time again. The original created/updated split cannot be
    # perfectly recovered (it was collapsed into the single timestamp), so updated_at is set to
    # the current timestamp value.
    _restore_updated_at_to_metadata()
    op.drop_index("ix_AttackResultEntries_timestamp_id", table_name="AttackResultEntries")
    op.drop_index("ix_AttackResultEntries_conversation_id", table_name="AttackResultEntries")
    _set_conversation_id_type(length=None)


def _set_conversation_id_type(*, length: int | None) -> None:
    """
    Set ``conversation_id`` to bounded or unbounded ``VARCHAR``.

    Batch alteration recreates the table on SQLite, which does not support ``ALTER COLUMN``
    directly, and emits an in-place alteration on SQL Server. The bounded type keeps the
    persisted schema aligned with the ORM model and makes the column indexable on SQL Server.

    Args:
        length (int | None): The target ``VARCHAR`` length, or ``None`` for ``VARCHAR(MAX)``.
    """
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.alter_column(
            "conversation_id",
            existing_type=sa.String(),
            type_=sa.String(length=length),
            existing_nullable=False,
        )


def _backfill_timestamp_from_metadata() -> None:
    """
    Set ``timestamp = COALESCE(parse(metadata.updated_at), parse(metadata.created_at),
    timestamp)`` for existing rows and drop the now-redundant ``updated_at`` JSON key.

    Only rows that carried a JSON recency key change; programmatic rows already have
    ``timestamp`` equal to their creation time, and rows with no ``attack_metadata`` at all are
    skipped by the query filter since they can never carry a recency key. The row updates are
    applied in bounded ``executemany`` batches rather than one statement per row. Idempotent:
    rerunning is a no-op once the ``updated_at`` key is gone and ``timestamp`` already reflects it.
    """
    bind = op.get_bind()
    table = _attack_results_table()

    rows = bind.execute(
        sa.select(table.c.id, table.c.attack_metadata, table.c.timestamp).where(table.c.attack_metadata.isnot(None))
    ).fetchall()

    updates: list[dict[str, object]] = []
    for row in rows:
        metadata = dict(row.attack_metadata) if isinstance(row.attack_metadata, dict) else {}
        new_timestamp = _parse_iso(metadata.get("updated_at")) or _parse_iso(metadata.get("created_at"))

        had_updated_at = "updated_at" in metadata
        new_metadata = {k: v for k, v in metadata.items() if k != "updated_at"}

        timestamp_changes = new_timestamp is not None and new_timestamp != row.timestamp
        if not timestamp_changes and not had_updated_at:
            continue

        # Always carry a timestamp so a single uniform executemany can update both columns;
        # rows that only strip the JSON key rewrite their existing timestamp unchanged.
        updates.append(
            {
                "b_id": row.id,
                "b_metadata": new_metadata or None,
                "b_timestamp": new_timestamp if timestamp_changes else row.timestamp,
            }
        )

    _apply_backfill_updates(bind=bind, table=table, updates=updates)

    if updates:
        logger.info(f"Attack recency backfill: updated {len(updates)} AttackResultEntries row(s).")


def _restore_updated_at_to_metadata() -> None:
    """
    Downgrade inverse: write ``metadata.updated_at = timestamp.isoformat()`` for every row so
    the legacy JSON recency expression orders rows by their last-updated time again. The row
    updates are applied in bounded ``executemany`` batches rather than one statement per row.
    """
    bind = op.get_bind()
    table = _attack_results_table()

    rows = bind.execute(
        sa.select(table.c.id, table.c.attack_metadata, table.c.timestamp).where(table.c.timestamp.isnot(None))
    ).fetchall()

    updates: list[dict[str, object]] = []
    for row in rows:
        metadata = dict(row.attack_metadata) if isinstance(row.attack_metadata, dict) else {}
        metadata["updated_at"] = row.timestamp.isoformat()
        updates.append({"b_id": row.id, "b_metadata": metadata, "b_timestamp": row.timestamp})

    _apply_backfill_updates(bind=bind, table=table, updates=updates)


def _apply_backfill_updates(*, bind: sa.engine.Connection, table: sa.Table, updates: list[dict[str, object]]) -> None:
    """
    Apply keyed ``(b_id, b_metadata, b_timestamp)`` updates to ``AttackResultEntries`` in bounded
    ``executemany`` batches. Each batch is a single compiled UPDATE executed against a slice of the
    parameter list, so the statement count scales with ``len(updates) / _BACKFILL_UPDATE_BATCH_SIZE``.
    """
    if not updates:
        return

    update_stmt = (
        sa.update(table)
        .where(table.c.id == sa.bindparam("b_id"))
        .values(attack_metadata=sa.bindparam("b_metadata"), timestamp=sa.bindparam("b_timestamp"))
    )
    for start in range(0, len(updates), _BACKFILL_UPDATE_BATCH_SIZE):
        bind.execute(update_stmt, updates[start : start + _BACKFILL_UPDATE_BATCH_SIZE])
