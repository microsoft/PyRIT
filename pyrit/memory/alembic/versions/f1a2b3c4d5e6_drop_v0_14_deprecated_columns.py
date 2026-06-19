# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Drop deprecated columns scheduled for removal in v0.15.0.

* ``AttackResultEntries.attack_identifier`` (superseded by
  ``atomic_attack_identifier``).
* ``PromptMemoryEntries.targeted_harm_categories`` (callers should use the
  attack-level ``labels`` column with ``{"harm_category": [...]}`` instead).

Revision ID: f1a2b3c4d5e6
Revises: 9c8b7a6d5e4f
Create Date: 2026-06-05 14:39:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "9c8b7a6d5e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    # SQLite does not support DROP COLUMN on a table with constraints in older
    # versions; use batch_alter_table so the operation is portable across both
    # SQLite and Azure SQL.
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.drop_column("attack_identifier")
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.drop_column("targeted_harm_categories")


def downgrade() -> None:
    """Revert this schema upgrade."""
    # Re-add the columns as nullable so legacy code can still write to them
    # (the not-null default on attack_identifier is intentionally relaxed on
    # downgrade since we have no way to backfill the original value).
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.add_column(sa.Column("targeted_harm_categories", sa.JSON(), nullable=True))
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.add_column(sa.Column("attack_identifier", sa.JSON(), nullable=True))
