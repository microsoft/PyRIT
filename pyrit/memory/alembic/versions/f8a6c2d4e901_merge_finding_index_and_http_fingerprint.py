# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the finding-evidence index and HTTP request fingerprint heads.

Revision ID: f8a6c2d4e901
Revises: b5d7f9a1c3e6, d6de1045cca4
Create Date: 2026-11-07 08:40:00.000000
"""

from collections.abc import Sequence

revision: str = "f8a6c2d4e901"
down_revision: str | Sequence[str] | None = ("b5d7f9a1c3e6", "d6de1045cca4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
