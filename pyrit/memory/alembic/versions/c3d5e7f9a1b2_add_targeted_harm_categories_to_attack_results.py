# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add targeted_harm_categories to Attack Results.

Relocates the ``targeted_harm_categories`` field from the per-piece
``PromptMemoryEntries`` (dropped in ``f1a2b3c4d5e6``) to the attack-scoped
``AttackResultEntries`` table, where it belongs. No backfill: the source
per-piece column is already gone and was never populated by production code.

Revision ID: c3d5e7f9a1b2
Revises: b2f4c6a8d1e3
Create Date: 2026-06-11 17:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "b2f4c6a8d1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.add_column("AttackResultEntries", sa.Column("targeted_harm_categories", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.drop_column("AttackResultEntries", "targeted_harm_categories")
