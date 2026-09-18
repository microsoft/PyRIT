# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the preparation conversation and scorer observation heads.

Revision ID: 7a9c1e3f5b2d
Revises: 2c4e6a8b0d14, 2c4e6a8b0d1f
Create Date: 2026-09-15 12:08:57.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "7a9c1e3f5b2d"
down_revision: str | Sequence[str] | None = ("2c4e6a8b0d14", "2c4e6a8b0d1f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
