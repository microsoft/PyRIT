# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the dataset lifecycle and seed decomposition heads.

Revision ID: c4e6a8b0d2f1
Revises: b7d9f1a3c5e8, 5d7f9b1c3e2a
Create Date: 2026-11-10 15:10:00.000000
"""

from collections.abc import Sequence

revision: str = "c4e6a8b0d2f1"
down_revision: str | Sequence[str] | None = ("b7d9f1a3c5e8", "5d7f9b1c3e2a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
