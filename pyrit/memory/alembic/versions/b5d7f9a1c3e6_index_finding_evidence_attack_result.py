# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Index finding evidence by attack result so an attack's findings are a direct lookup.

``attack_result_id`` is a bounded UUID column on every backend, so SQL Server accepts it as an index key.

Revision ID: b5d7f9a1c3e6
Revises: e7c4a2d9f013
Create Date: 2026-11-06 17:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5d7f9a1c3e6"
down_revision: str | None = "e7c4a2d9f013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the attack-result lookup index."""
    op.create_index("ix_FindingEvidenceEntries_attack_result_id", "FindingEvidenceEntries", ["attack_result_id"])


def downgrade() -> None:
    """Remove the attack-result lookup index."""
    op.drop_index("ix_FindingEvidenceEntries_attack_result_id", table_name="FindingEvidenceEntries")
