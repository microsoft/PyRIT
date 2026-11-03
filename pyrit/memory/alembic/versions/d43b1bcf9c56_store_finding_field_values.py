# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store each finding as a severity plus a JSON object of field values.

Drops the three classification CHECK constraints on ``FindingEntries``, makes ``severity`` unbounded Unicode
(binary-collated on SQL Server), replaces an Other severity with its custom text without surrounding whitespace, and
moves the harm type (its custom text when it is Other) and any description that isn't empty or only spaces into the
new ``field_values`` column. Finding IDs, Operation links, creation times, and evidence links do not change.
Downgrading restores the old columns only while no findings exist.

Revision ID: d43b1bcf9c56
Revises: e7c4a2d9f013
Create Date: 2026-10-27 12:00:00.000000
"""

import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

# revision identifiers, used by Alembic.
revision: str = "d43b1bcf9c56"
down_revision: str | None = "e7c4a2d9f013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_SEVERITY_TYPE = sa.Unicode().with_variant(sa.Unicode(collation="Latin1_General_100_BIN2"), "mssql")
_HARM_TYPE_SQL = "CASE WHEN harm_type = 'Other' THEN harm_type_other ELSE harm_type END"
_DESCRIPTION_SQL = "CASE WHEN LTRIM(RTRIM(description)) = '' THEN NULL ELSE description END"
_OLD_COLUMNS = ("description", "severity_other", "harm_type", "harm_type_other")
_CHECKS = {
    "ck_findings_severity": "severity IN ('critical', 'important', 'moderate', 'low', 'informational', 'other')",
    "ck_findings_severity_other": (
        "(severity = 'other' AND severity_other IS NOT NULL AND LTRIM(RTRIM(severity_other)) <> '') "
        "OR (severity <> 'other' AND severity_other IS NULL)"
    ),
    "ck_findings_harm_type_other": (
        "(harm_type IS NOT NULL AND harm_type = 'Other' AND harm_type_other IS NOT NULL "
        "AND LTRIM(RTRIM(harm_type_other)) <> '') "
        "OR ((harm_type IS NULL OR harm_type <> 'Other') AND harm_type_other IS NULL)"
    ),
}


def upgrade() -> None:
    """Drop the classification checks and move each finding's values into ``severity`` and ``field_values``."""
    # SQL Server rejects ALTER COLUMN while a CHECK references the column, so the checks go first.
    with op.batch_alter_table("FindingEntries") as batch_op:
        for name in _CHECKS:
            batch_op.drop_constraint(name, type_="check")
        batch_op.add_column(sa.Column("field_values", sa.JSON(), nullable=True))
        batch_op.alter_column("severity", existing_type=sa.String(32), type_=_SEVERITY_TYPE, existing_nullable=False)
    op.execute(sa.text("UPDATE FindingEntries SET severity = severity_other WHERE LOWER(severity) = 'other'"))
    _strip_custom_severities()
    op.execute(sa.text(f"UPDATE FindingEntries SET field_values = {_field_values_sql()}"))
    with op.batch_alter_table("FindingEntries") as batch_op:
        batch_op.alter_column("field_values", existing_type=sa.JSON(), nullable=False)
        for column in _OLD_COLUMNS:
            batch_op.drop_column(column)


def _strip_custom_severities() -> None:
    """
    Remove the surrounding whitespace from each severity copied from Other text, as new findings have it removed.

    SQL trimming keeps tabs, line breaks and non-breaking spaces, so Python strips the text instead.
    """
    if op.get_context().as_sql:
        logger.warning("Skipping custom severity trimming: offline SQL generation cannot read existing findings.")
        return
    bind = op.get_bind()
    findings = sa.table(
        "FindingEntries",
        sa.column("id", CustomUUID()),
        sa.column("severity", sa.Unicode()),
        sa.column("severity_other", sa.Unicode()),
    )
    custom = sa.select(findings.c.id, findings.c.severity).where(findings.c.severity_other.is_not(None))
    for finding_id, severity in bind.execute(custom).all():
        if severity != severity.strip():
            bind.execute(findings.update().where(findings.c.id == finding_id).values(severity=severity.strip()))


def _field_values_sql() -> str:
    """
    Build the dialect's SQL expression for a JSON object of harm type and description, leaving out NULL values.

    Returns:
        str: The SQL expression.
    """
    if op.get_context().dialect.name == "mssql":
        return (
            f"JSON_MODIFY(JSON_MODIFY(N'{{}}', '$.harm_type', {_HARM_TYPE_SQL}), '$.description', {_DESCRIPTION_SQL})"
        )
    return f"json_patch('{{}}', json_object('harm_type', {_HARM_TYPE_SQL}, 'description', {_DESCRIPTION_SQL}))"


def downgrade() -> None:
    """
    Restore the classification columns and checks only when no findings exist.

    Raises:
        ValueError: If the downgrade is offline or findings would be lost.
    """
    if op.get_context().as_sql:
        raise ValueError("Finding downgrade requires online data validation.")
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM FindingEntries")).scalar():
        raise ValueError("Remove retained findings explicitly before downgrading.")
    with op.batch_alter_table("FindingEntries") as batch_op:
        batch_op.add_column(sa.Column("description", sa.Unicode(), nullable=False))
        for column in _OLD_COLUMNS[1:]:
            batch_op.add_column(sa.Column(column, sa.Unicode(), nullable=True))
        batch_op.alter_column("severity", existing_type=_SEVERITY_TYPE, type_=sa.String(32), existing_nullable=False)
        batch_op.drop_column("field_values")
        for name, condition in _CHECKS.items():
            batch_op.create_check_constraint(name, condition)
