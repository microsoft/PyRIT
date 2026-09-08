# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the score status and scenario progress delta index heads.

Revision ID: 8e2c4a6b0d13
Revises: 5a1d3c7e9f04, 6b8d0f2a4c1e
Create Date: 2026-08-21 09:15:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "8e2c4a6b0d13"
down_revision: str | Sequence[str] | None = ("5a1d3c7e9f04", "6b8d0f2a4c1e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
