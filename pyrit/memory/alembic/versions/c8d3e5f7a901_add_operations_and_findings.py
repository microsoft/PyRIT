# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add operations, their findings, and finding evidence.

Creates ``OperationEntries``, ``FindingEntries``, and ``FindingEvidenceEntries``
without changing existing tables. Downgrading drops them only while no
operations exist.

Revision ID: c8d3e5f7a901
Revises: 901e6c7bf9d4
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "c8d3e5f7a901"
down_revision: str | None = "901e6c7bf9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the operation, finding, and evidence tables."""
    op.create_table(
        "OperationEntries",
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("name", sa.Unicode(128), nullable=False),
        sa.Column("name_key", sa.Unicode(384), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_OperationEntries_name_key", "OperationEntries", ["name_key"], unique=True)
    op.create_table(
        "FindingEntries",
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("operation_id", CustomUUID(), nullable=False),
        sa.Column("title", sa.Unicode(), nullable=False),
        sa.Column("description", sa.Unicode(), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("severity_other", sa.Unicode(), nullable=True),
        sa.Column("harm_type", sa.Unicode(), nullable=True),
        sa.Column("harm_type_other", sa.Unicode(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["OperationEntries.id"], name="fk_findings_operation"),
    )
    op.create_index("ix_FindingEntries_operation_id", "FindingEntries", ["operation_id"])
    op.create_table(
        "FindingEvidenceEntries",
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("finding_id", CustomUUID(), nullable=False),
        sa.Column("conversation_id", sa.String(128), nullable=False),
        sa.Column("attack_result_id", CustomUUID(), nullable=False),
        sa.Column("attached_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["finding_id"], ["FindingEntries.id"], name="fk_finding_evidence_finding"),
        sa.UniqueConstraint("finding_id", "conversation_id", name="uq_finding_evidence_source"),
    )
    op.create_index("ix_FindingEvidenceEntries_finding_id", "FindingEvidenceEntries", ["finding_id"])


def downgrade() -> None:
    """
    Drop the tables only when no operations, and therefore no findings, exist.

    Raises:
        ValueError: If the downgrade is offline or operations would be lost.
    """
    if op.get_context().as_sql:
        raise ValueError("Operation downgrade requires online data validation.")
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM OperationEntries")).scalar():
        raise ValueError("Remove retained operations explicitly before downgrading.")
    op.drop_index("ix_FindingEvidenceEntries_finding_id", table_name="FindingEvidenceEntries")
    op.drop_table("FindingEvidenceEntries")
    op.drop_index("ix_FindingEntries_operation_id", table_name="FindingEntries")
    op.drop_table("FindingEntries")
    op.drop_index("ix_OperationEntries_name_key", table_name="OperationEntries")
    op.drop_table("OperationEntries")
