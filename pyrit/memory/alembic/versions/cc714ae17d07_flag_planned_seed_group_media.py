# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Flag planned seed groups that reference media.

Dataset media cleanup reads only flagged rows, so existing rows are classified from
their stored seed group before the flag becomes required.

Revision ID: cc714ae17d07
Revises: 3e55dd099e38
Create Date: 2026-11-20 14:00:00.000000
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID, planned_media_values

# revision identifiers, used by Alembic.
revision: str = "cc714ae17d07"
down_revision: str | None = "3e55dd099e38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ScenarioPlanSeedGroupEntries"
_INDEX = "ix_ScenarioPlanSeedGroupEntries_has_media"
_BATCH_SIZE = 500
# Created by be3d7027ac91 on SQLite.
_DELETE_TRIGGER = "trg_delete_scenario_plan_seed_groups"


@contextmanager
def _without_sqlite_delete_trigger() -> Generator[None, None, None]:
    """
    Drop the SQLite planned-group delete trigger while batch mode rebuilds the table, then restore it.

    SQLite checks every trigger when batch mode renames the rebuilt table into place, and this
    trigger names the table being replaced, so the rename fails while it exists.

    Yields:
        None: Control while the table is rebuilt.
    """
    sqlite = op.get_context().dialect.name == "sqlite"
    if sqlite:
        op.execute(f'DROP TRIGGER "{_DELETE_TRIGGER}"')
    yield
    if sqlite:
        op.execute(
            f"""
            CREATE TRIGGER "{_DELETE_TRIGGER}"
            AFTER DELETE ON "ScenarioResultEntries"
            BEGIN
                DELETE FROM "{_TABLE}" WHERE scenario_result_id = OLD.id;
            END
            """
        )


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
    with _without_sqlite_delete_trigger(), op.batch_alter_table(_TABLE) as batch_op:
        batch_op.alter_column("has_media", existing_type=sa.Boolean(), nullable=False)
    op.create_index(_INDEX, _TABLE, ["has_media"], unique=False)


def downgrade() -> None:
    """Remove the media flag; stored seed groups are kept."""
    op.drop_index(_INDEX, table_name=_TABLE)
    with _without_sqlite_delete_trigger(), op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("has_media")
