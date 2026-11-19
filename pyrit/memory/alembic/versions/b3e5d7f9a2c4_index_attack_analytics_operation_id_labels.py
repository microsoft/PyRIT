# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Cover saved Operation ID analytics label reads.

Label facets and label axes filtered by a saved Operation ID read ``labels`` for
every matching result. Covering them avoids one wide table-row lookup per result.

Revision ID: b3e5d7f9a2c4
Revises: 7aa74745cc9b
Create Date: 2026-11-19 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b3e5d7f9a2c4"
down_revision: str | Sequence[str] | None = "7aa74745cc9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the dialect-specific Operation ID label index."""
    if op.get_bind().dialect.name == "sqlite":
        op.create_index(
            "ix_AttackResultEntries_analytics_operation_id_labels_sqlite",
            "AttackResultEntries",
            ["operation_id", "labels"],
        )
    else:
        op.create_index(
            "ix_AttackResultEntries_analytics_operation_id_labels_mssql",
            "AttackResultEntries",
            ["operation_id"],
            mssql_include=["labels"],
        )


def downgrade() -> None:
    """Remove the Operation ID label index without changing stored results."""
    suffix = "sqlite" if op.get_bind().dialect.name == "sqlite" else "mssql"
    op.drop_index(f"ix_AttackResultEntries_analytics_operation_id_labels_{suffix}", table_name="AttackResultEntries")
