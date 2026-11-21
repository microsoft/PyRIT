# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store conversation ids up to 128 characters in every table.

``Conversations.conversation_id`` and ``AttackResultEntries.conversation_id`` were
``VARCHAR(36)``, sized for a bare UUID, while ``PromptMemoryEntries`` already stores
128. Conversation ids are free-form, so SQL Server rejected longer ids such as the
capability-discovery probes' ``capability-probe-<uuid>``. SQLite does not enforce
string lengths; its declared types are updated so the schema matches the models.

Revision ID: 0d3592c62ad8
Revises: 3e55dd099e38
Create Date: 2026-11-20 18:00:00.000000
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d3592c62ad8"
down_revision: str | Sequence[str] | None = "3e55dd099e38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("Conversations", "AttackResultEntries")

# SQL Server cannot alter a primary-key column in place, so the key is dropped and
# re-created with its original name and clustering. Widening a VARCHAR that only
# participates in ordinary indexes is allowed in place.
_WIDEN_CONVERSATION_IDS_SQL = """
DECLARE @pk sysname, @pk_type nvarchar(60), @sql nvarchar(max);
SELECT @pk = key_constraint.name, @pk_type = idx.type_desc
FROM sys.key_constraints AS key_constraint
JOIN sys.indexes AS idx
    ON idx.object_id = key_constraint.parent_object_id AND idx.index_id = key_constraint.unique_index_id
WHERE key_constraint.parent_object_id = OBJECT_ID(N'Conversations') AND key_constraint.type = 'PK';
IF @pk IS NULL THROW 50000, 'Conversations primary key is missing.', 1;
SET @sql = N'ALTER TABLE [Conversations] DROP CONSTRAINT ' + QUOTENAME(@pk);
EXEC sp_executesql @sql;
ALTER TABLE [Conversations] ALTER COLUMN [conversation_id] VARCHAR(128) NOT NULL;
SET @sql = N'ALTER TABLE [Conversations] ADD CONSTRAINT ' + QUOTENAME(@pk) + N' PRIMARY KEY '
    + @pk_type + N' ([conversation_id])';
EXEC sp_executesql @sql;
ALTER TABLE [AttackResultEntries] ALTER COLUMN [conversation_id] VARCHAR(128) NOT NULL;
"""


def upgrade() -> None:
    """Widen conversation ids to match ``PromptMemoryEntries``."""
    if op.get_bind().dialect.name == "mssql":
        op.execute(sa.text(_WIDEN_CONVERSATION_IDS_SQL))
        return
    _set_sqlite_declared_length(old_length=36, new_length=128)


def downgrade() -> None:
    """
    Keep SQL Server's wider columns and restore SQLite's declared types.

    Earlier revisions read and write ids of up to 36 characters in a 128-character
    SQL Server column unchanged, while narrowing would truncate or reject ids stored
    since the upgrade. SQLite never enforced the length, so its declaration is
    restored without touching stored ids.
    """
    if op.get_bind().dialect.name == "sqlite":
        _set_sqlite_declared_length(old_length=128, new_length=36)


def _set_sqlite_declared_length(*, old_length: int, new_length: int) -> None:
    """
    Change the declared ``conversation_id`` length without rebuilding either table.

    SQLite ignores the length and stores the same TEXT values either way, so this is
    the schema-only edit SQLite documents for changes that leave stored content valid
    (https://www.sqlite.org/lang_altertable.html#otheralter). A rebuild would have to
    re-create ``AttackResultEntries``' generated analytics column and would rewrite
    its column-level foreign keys, which earlier downgrades drop in place.

    Raises:
        RuntimeError: If a table does not declare the expected column exactly once,
            or the database fails its integrity check afterwards.
    """
    bind = op.get_bind()
    pattern = re.compile(rf"(\bconversation_id\s+)VARCHAR\({old_length}\)", re.IGNORECASE)
    replacements: dict[str, str] = {}
    for table_name in _TABLES:
        sql = bind.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"), {"name": table_name}
        ).scalar_one()
        updated, count = pattern.subn(rf"\g<1>VARCHAR({new_length})", sql)
        if count != 1:
            raise RuntimeError(f"{table_name} does not declare conversation_id as VARCHAR({old_length}).")
        replacements[table_name] = updated
    schema_version = bind.execute(sa.text("PRAGMA schema_version")).scalar_one()
    bind.execute(sa.text("PRAGMA writable_schema = ON"))
    try:
        for table_name, sql in replacements.items():
            bind.execute(
                sa.text("UPDATE sqlite_master SET sql = :sql WHERE type = 'table' AND name = :name"),
                {"sql": sql, "name": table_name},
            )
        # A new schema version makes every connection reload the edited definitions.
        bind.execute(sa.text(f"PRAGMA schema_version = {int(schema_version) + 1}"))
    finally:
        bind.execute(sa.text("PRAGMA writable_schema = OFF"))
    result = bind.execute(sa.text("PRAGMA integrity_check")).scalar_one()
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed after widening conversation ids: {result}")
