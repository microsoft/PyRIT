# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add the HTTP request fingerprint to TargetIdentifiers.

Revision ID: d6de1045cca4
Revises: e7c4a2d9f013
Create Date: 2026-11-03 21:13:47.703952
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6de1045cca4"
down_revision: str | None = "e7c4a2d9f013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable storage for the HTTP request fingerprint."""
    with op.batch_alter_table("TargetIdentifiers") as batch_op:
        batch_op.add_column(sa.Column("http_request_fingerprint", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove the HTTP request fingerprint storage."""
    with op.batch_alter_table("TargetIdentifiers") as batch_op:
        batch_op.drop_column("http_request_fingerprint")
