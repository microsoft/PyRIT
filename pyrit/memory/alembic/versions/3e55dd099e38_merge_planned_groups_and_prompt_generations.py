# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the planned seed group and prompt generation migration heads.

Revision ID: 3e55dd099e38
Revises: be3d7027ac91, ccf97e771f04
Create Date: 2026-11-20 09:00:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3e55dd099e38"
down_revision: str | Sequence[str] | None = ("be3d7027ac91", "ccf97e771f04")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
