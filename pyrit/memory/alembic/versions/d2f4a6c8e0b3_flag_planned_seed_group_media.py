# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Flag planned seed groups that reference media.

Dataset media cleanup reads only flagged rows, so existing rows are classified from
their stored seed group before the flag becomes required.

Revision ID: d2f4a6c8e0b3
Revises: c7d1e9a3b5f2
Create Date: 2026-11-17 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID, planned_media_values

# revision identifiers, used by Alembic.
revision: str = "d2f4a6c8e0b3"
down_revision: str | None = "c7d1e9a3b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ScenarioPlanSeedGroupEntries"
_INDEX = "ix_ScenarioPlanSeedGroupEntries_has_media"
_BATCH_SIZE = 500


def _backfill_has_media(*, connection: sa.Connection) -> None:
    """
    Classify every unflagged row from its stored seed group, a bounded page at a time.

    Args:
        connection (sa.Connection): The Alembic transaction's existing connection.
    """
    entries = sa.table(
        _TABLE,
        sa.column("id", CustomUUID()),
        sa.column("seed_group", sa.JSON()),
        sa.column("has_media", sa.Boolean()),
    )
    page = sa.select(entries.c.id, entries.c.seed_group).where(entries.c.has_media.is_(None)).limit(_BATCH_SIZE)
    update = (
        sa.update(entries)
        .where(entries.c.id == sa.bindparam("row_id", type_=CustomUUID()))
        .values(has_media=sa.bindparam("flag", type_=sa.Boolean()))
    )
    while rows := connection.execute(page).all():
        connection.execute(
            update, [{"row_id": row_id, "flag": bool(planned_media_values(seed_group))} for row_id, seed_group in rows]
        )


def upgrade() -> None:
    """Add the media flag, classify existing rows, then require and index it."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column("has_media", sa.Boolean(), nullable=True))
    _backfill_has_media(connection=op.get_bind())
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.alter_column("has_media", existing_type=sa.Boolean(), nullable=False)
    op.create_index(_INDEX, _TABLE, ["has_media"], unique=False)


def downgrade() -> None:
    """Remove the media flag; stored seed groups are kept."""
    op.drop_index(_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("has_media")
