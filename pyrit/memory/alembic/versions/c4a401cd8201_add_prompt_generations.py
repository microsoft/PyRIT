# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store prompt generation jobs in memory.

Revision ID: c4a401cd8201
Revises: 7aa74745cc9b
Create Date: 2026-11-18 09:31:41.049773
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID, UTCDateTime

revision: str = "c4a401cd8201"
down_revision: str | None = "7aa74745cc9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the prompt generation table with bounded index keys on SQLite and SQL Server."""
    op.create_table(
        "PromptGenerationEntries",
        sa.Column("id", CustomUUID(), nullable=False),
        sa.Column("generator", sa.String(length=64), nullable=False),
        sa.Column("dataset_name", sa.Unicode(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("execution_id", CustomUUID(), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("pause_for_review", sa.Boolean(), nullable=False),
        sa.Column("review_completed", sa.Boolean(), nullable=False),
        sa.Column("group_id", CustomUUID(), nullable=True),
        sa.Column("group_label", sa.Unicode(), nullable=True),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error", sa.Unicode(), nullable=True),
        sa.Column("created_by", sa.Unicode(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_PromptGenerationEntries_created_at_id", "PromptGenerationEntries", ["created_at", "id"], unique=False
    )
    op.create_index(
        "ix_PromptGenerationEntries_dataset_name", "PromptGenerationEntries", ["dataset_name"], unique=False
    )


def downgrade() -> None:
    """
    Drop the prompt generation table only when it is empty.

    Raises:
        RuntimeError: If generation records exist, since dropping them would also release their dataset names.
    """
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM PromptGenerationEntries")):
        raise RuntimeError("Prompt generation records exist; refusing to discard them and their dataset names.")
    op.drop_index("ix_PromptGenerationEntries_dataset_name", table_name="PromptGenerationEntries")
    op.drop_index("ix_PromptGenerationEntries_created_at_id", table_name="PromptGenerationEntries")
    op.drop_table("PromptGenerationEntries")
