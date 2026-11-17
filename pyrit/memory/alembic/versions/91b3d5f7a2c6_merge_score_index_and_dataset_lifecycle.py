# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the score message-piece index and dataset lifecycle heads.

Revision ID: 91b3d5f7a2c6
Revises: 7f2c8a4d6e91, c4e6a8b0d2f1
Create Date: 2026-11-11 04:05:00.000000
"""

from collections.abc import Sequence

revision: str = "91b3d5f7a2c6"
down_revision: str | Sequence[str] | None = ("7f2c8a4d6e91", "c4e6a8b0d2f1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
