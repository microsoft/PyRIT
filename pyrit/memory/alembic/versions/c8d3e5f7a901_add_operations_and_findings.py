# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add operations, their findings, finding evidence, and Operation references on activity.

Creates ``OperationEntries``, ``FindingEntries``, and ``FindingEvidenceEntries``,
registers an operation for each name already used by attacks and scenario runs,
and adds nullable ``operation_id`` references to ``AttackResultEntries`` and
``ScenarioResultEntries``. Existing activity keeps its stored names and gets no
reference. Downgrading removes all of it only while no operations exist.

Revision ID: c8d3e5f7a901
Revises: 3f6a9d2c8b1e
Create Date: 2026-10-09 12:00:00.000000
"""

import json
import logging
import uuid
from collections.abc import Sequence  # noqa: TC003
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "c8d3e5f7a901"
down_revision: str | None = "3f6a9d2c8b1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_OPERATION_NAME_MAX_LENGTH = 128
_ACTIVITY_TABLES = (
    ("AttackResultEntries", "fk_attack_results_operation"),
    ("ScenarioResultEntries", "fk_scenario_results_operation"),
)


def upgrade() -> None:
    """Create the operation, finding, and evidence tables, then reference operations from activity."""
    operations = op.create_table(
        "OperationEntries",
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("name", sa.Unicode(128), nullable=False),
        sa.Column(
            "name_key",
            sa.Unicode(384).with_variant(sa.Unicode(384, collation="Latin1_General_100_BIN2"), "mssql"),
            nullable=False,
        ),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("description", sa.Unicode(), nullable=False),
    )
    op.create_index("ix_OperationEntries_name_key", "OperationEntries", ["name_key"], unique=True)
    _register_legacy_operations(operations=operations)
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
        sa.CheckConstraint(
            "severity IN ('critical', 'important', 'moderate', 'low', 'informational', 'other')",
            name="ck_findings_severity",
        ),
        sa.CheckConstraint(
            "(severity = 'other' AND severity_other IS NOT NULL AND LTRIM(RTRIM(severity_other)) <> '') "
            "OR (severity <> 'other' AND severity_other IS NULL)",
            name="ck_findings_severity_other",
        ),
        sa.CheckConstraint(
            "(harm_type IS NOT NULL AND harm_type = 'Other' AND harm_type_other IS NOT NULL "
            "AND LTRIM(RTRIM(harm_type_other)) <> '') "
            "OR ((harm_type IS NULL OR harm_type <> 'Other') AND harm_type_other IS NULL)",
            name="ck_findings_harm_type_other",
        ),
    )
    op.create_index("ix_FindingEntries_operation_id", "FindingEntries", ["operation_id"])
    op.create_table(
        "FindingEvidenceEntries",
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("finding_id", CustomUUID(), nullable=False),
        sa.Column("attack_result_id", CustomUUID(), nullable=False),
        sa.Column("attached_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["finding_id"], ["FindingEntries.id"], name="fk_finding_evidence_finding"),
        sa.UniqueConstraint("finding_id", "attack_result_id", name="uq_finding_evidence_source"),
    )
    op.create_index("ix_FindingEvidenceEntries_finding_id", "FindingEvidenceEntries", ["finding_id"])
    for table, constraint in _ACTIVITY_TABLES:
        if op.get_bind().dialect.name == "sqlite":
            # Avoid reflecting/rebuilding the existing generated analytics column.
            op.execute(
                sa.text(
                    f'ALTER TABLE "{table}" ADD COLUMN operation_id CHAR(36) '
                    f'CONSTRAINT "{constraint}" REFERENCES "OperationEntries" (id)'
                )
            )
        else:
            op.add_column(table, sa.Column("operation_id", CustomUUID(), nullable=True))
            op.create_foreign_key(constraint, table, "OperationEntries", ["operation_id"], ["id"])
        op.create_index(f"ix_{table}_operation_id", table, ["operation_id"])


def _operation_name_key(name: str) -> str:
    """
    Return the operation identity key as defined at this revision.

    Returns:
        str: The trimmed, case-folded name.
    """
    return name.strip().casefold()


def _register_legacy_operations(*, operations: sa.Table) -> None:
    """
    Register one operation for each name that existing attacks and scenario runs use.

    Names that differ only in case or surrounding whitespace become one operation, named with
    the spelling used first. The activity rows themselves are not changed.
    """
    if op.get_context().as_sql:
        logger.warning("Skipping legacy operation registration: offline SQL generation cannot read existing names.")
        return
    bind = op.get_bind()
    attacks = sa.table(
        "AttackResultEntries", sa.column("operation", sa.Unicode()), sa.column("timestamp", UTCDateTime())
    )
    scenarios = sa.table("ScenarioResultEntries", sa.column("labels", sa.JSON()), sa.column("timestamp", UTCDateTime()))
    first_use: dict[str, tuple[datetime, str]] = {}
    skipped = 0

    def consider(name: object, used_at: datetime) -> None:
        nonlocal skipped
        if not isinstance(name, str) or not name.strip():
            return
        trimmed = name.strip()
        # Operation names are bounded in UTF-16 code units, as SQL Server NVARCHAR counts them.
        if len(trimmed.encode("utf-16-le")) // 2 > _OPERATION_NAME_MAX_LENGTH:
            skipped += 1
            return
        key = _operation_name_key(trimmed)
        if key not in first_use or used_at < first_use[key][0]:
            first_use[key] = (used_at, trimmed)

    attack_names = sa.select(attacks.c.operation, sa.func.min(attacks.c.timestamp)).group_by(attacks.c.operation)
    for name, used_at in bind.execute(attack_names.where(attacks.c.operation.is_not(None))):
        consider(name, used_at)
    for labels, used_at in bind.execute(sa.select(scenarios.c.labels, scenarios.c.timestamp)):
        if isinstance(labels, str):
            labels = json.loads(labels)
        if isinstance(labels, dict):
            consider(labels.get("operation"), used_at)

    if first_use:
        op.bulk_insert(
            operations,
            [
                {"id": uuid.uuid4(), "name": name, "name_key": key, "created_at": used_at, "description": ""}
                for key, (used_at, name) in sorted(first_use.items())
            ],
        )
    logger.info(f"Registered {len(first_use)} legacy operation(s); skipped {skipped} name(s) over 128 UTF-16 units.")


def downgrade() -> None:
    """
    Remove operations, findings, and activity references only when no operations exist.

    Raises:
        ValueError: If the downgrade is offline or operations would be lost.
    """
    if op.get_context().as_sql:
        raise ValueError("Operation downgrade requires online data validation.")
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM OperationEntries")).scalar():
        raise ValueError("Remove retained operations explicitly before downgrading.")
    for table, constraint in reversed(_ACTIVITY_TABLES):
        op.drop_index(f"ix_{table}_operation_id", table_name=table)
        if op.get_bind().dialect.name != "sqlite":
            op.drop_constraint(constraint, table, type_="foreignkey")
        op.drop_column(table, "operation_id")
    op.drop_index("ix_FindingEvidenceEntries_finding_id", table_name="FindingEvidenceEntries")
    op.drop_table("FindingEvidenceEntries")
    op.drop_index("ix_FindingEntries_operation_id", table_name="FindingEntries")
    op.drop_table("FindingEntries")
    op.drop_index("ix_OperationEntries_name_key", table_name="OperationEntries")
    op.drop_table("OperationEntries")
