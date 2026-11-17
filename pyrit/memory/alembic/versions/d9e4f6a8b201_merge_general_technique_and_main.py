# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge general technique storage and main's schema migrations.

Revision ID: d9e4f6a8b201
Revises: 91b3d5f7a2c6, a8f3c1d9e620
Create Date: 2026-11-11 18:35:00.000000
"""

from collections.abc import Sequence

revision: str = "d9e4f6a8b201"
down_revision: str | Sequence[str] | None = ("91b3d5f7a2c6", "a8f3c1d9e620")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
