# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the dataset lifecycle and HTTP request fingerprint heads.

Revision ID: b7d9f1a3c5e8
Revises: a9c3e7f1b205, d6de1045cca4
Create Date: 2026-11-09 11:00:00.000000
"""

from collections.abc import Sequence

revision: str = "b7d9f1a3c5e8"
down_revision: str | Sequence[str] | None = ("a9c3e7f1b205", "d6de1045cca4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
