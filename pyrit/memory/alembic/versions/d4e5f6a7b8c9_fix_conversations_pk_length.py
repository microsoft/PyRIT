# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Fix Conversations.conversation_id column length for SQL Server compatibility.

SQL Server cannot use VARCHAR(max) as a primary key. The original migration
created conversation_id as sa.String() (no length), which maps to VARCHAR(max)
on MSSQL. This migration alters the column to String(36) to match UUID string
length (36 characters).

Revision ID: d4e5f6a7b8c9
Revises: b2f4c6a8d1e3
Create Date: 2026-06-16 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str = "c3d5e7f9a1b2"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Alter conversation_id to String(36) for SQL Server PK compatibility."""
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.alter_column(
            "conversation_id",
            existing_type=sa.String(),
            type_=sa.String(36),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Revert conversation_id back to unbounded String."""
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.alter_column(
            "conversation_id",
            existing_type=sa.String(36),
            type_=sa.String(),
            existing_nullable=False,
        )
