# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persist dataset upload ownership, cleanup, and a cross-process reference lock."""

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

revision: str = "f8a2c6e0b419"
down_revision: str | None = "e7c4a2d9f013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the bounded-key lifecycle tables on SQLite and SQL Server."""
    lock = op.create_table("DatasetLifecycleLock", sa.Column("id", sa.Integer(), primary_key=True))
    op.bulk_insert(lock, [{"id": 1}])
    op.create_table(
        "DatasetLifecycleEntries",
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("dataset_name", sa.Unicode(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deleted_seeds", sa.Integer(), nullable=False),
        sa.Column("error", sa.Unicode(256), nullable=True),
    )
    op.create_table(
        "DatasetMediaEntries",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("path", sa.Unicode(), nullable=False),
        sa.Column("upload_id", CustomUUID(), sa.ForeignKey("DatasetLifecycleEntries.id"), nullable=False),
        sa.Column("cleanup_id", CustomUUID(), sa.ForeignKey("DatasetLifecycleEntries.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
    )
    op.create_index("ix_DatasetMediaEntries_upload_id", "DatasetMediaEntries", ["upload_id"])
    op.create_index("ix_DatasetMediaEntries_cleanup_id", "DatasetMediaEntries", ["cleanup_id"])


def downgrade() -> None:
    """
    Do not discard ownership or outstanding cleanup work.

    Raises:
        RuntimeError: If durable ownership or cleanup records still exist.
    """
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM DatasetLifecycleEntries")):
        raise RuntimeError("Dataset lifecycle records exist; refusing to discard media ownership and cleanup state.")
    op.drop_table("DatasetMediaEntries")
    op.drop_table("DatasetLifecycleEntries")
    op.drop_table("DatasetLifecycleLock")
