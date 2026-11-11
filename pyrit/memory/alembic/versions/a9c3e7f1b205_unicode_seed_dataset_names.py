# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Preserve dataset identities independently of SQL Server's VARCHAR code page."""

import sqlalchemy as sa
from alembic import op

revision: str = "a9c3e7f1b205"
down_revision: str | None = "f8a2c6e0b419"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Widen seed names without truncating legacy names or changing their collation."""
    if op.get_bind().dialect.name == "mssql":
        op.execute(
            sa.text(
                "DECLARE @collation sysname = (SELECT collation_name FROM sys.columns "
                "WHERE object_id = OBJECT_ID(N'SeedPromptEntries') AND name = N'dataset_name'); "
                "IF @collation IS NULL THROW 50000, 'Seed dataset-name collation is missing.', 1; "
                "DECLARE @sql nvarchar(max) = N'ALTER TABLE [SeedPromptEntries] "
                "ALTER COLUMN [dataset_name] NVARCHAR(MAX) COLLATE ' + QUOTENAME(@collation) + N' NULL'; "
                "EXEC sp_executesql @sql;"
                "SET @sql = N'ALTER TABLE [DatasetLifecycleEntries] "
                "ALTER COLUMN [dataset_name] NVARCHAR(256) COLLATE ' + QUOTENAME(@collation) + N' NOT NULL'; "
                "EXEC sp_executesql @sql;"
            )
        )


def downgrade() -> None:
    """
    Refuse a conversion that could corrupt existing Unicode dataset identities.

    Raises:
        RuntimeError: If SQL Server would convert dataset names back to VARCHAR.
    """
    if op.get_bind().dialect.name == "mssql":
        raise RuntimeError("Refusing a lossy downgrade of Unicode dataset names to VARCHAR.")
