# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store each finding as a severity plus a JSON object of field values.

Drops the three classification CHECK constraints on ``FindingEntries``, makes ``severity`` unbounded Unicode
(binary-collated on SQL Server), replaces an Other severity with its custom text without surrounding whitespace, and
moves the harm type (its custom text when it is Other) and any description that isn't empty or only spaces into the
new ``field_values`` column. Finding IDs, Operation links, creation times, and evidence links do not change.
Downgrading moves the default fields' values back into the old columns, and refuses while any finding has a value
that PyRIT 1.2 has no column for.

Revision ID: d43b1bcf9c56
Revises: 0d3592c62ad8
Create Date: 2026-10-27 12:00:00.000000
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

# revision identifiers, used by Alembic.
revision: str = "d43b1bcf9c56"
down_revision: str | None = "0d3592c62ad8"
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
_OLD_SEVERITIES = frozenset({"critical", "important", "moderate", "low", "informational"})
_OLD_FIELD_KEYS = frozenset({"harm_type", "description"})
# The harm categories PyRIT 1.2 reads, frozen so a category added later downgrades as Other text.
_OLD_HARM_CATEGORIES = frozenset(
    {
        "Hate Speech", "Harassment", "Sexualization", "Grooming", "Child Leakage", "Dangerous Situations",
        "Graphic Violence and Gore", "Pornography & Sexual Content", "Profanity", "Quality of Service (QoS)",
        "Allocation of Resources & Opportunities",
        "Representational Harms (Stereotyping, Demeaning & Erasing Outputs)", "Suicide", "Self-Harm",
        "Eating Disorders", "Drug Use", "Violent Threats, Incitement & Glorification",
        "Terrorism & Violent Extremism", "Coordination of Harm", "Sale of Regulated Goods", "Sexual Solicitation",
        "Scams", "Spam", "Impersonation", "Fake Accounts", "Inaccurate, Incomplete, False & Misleading Information",
        "Misinformation for current events", "Biased or revisionist retelling of controversial historical events",
        "Inaccurate, Incomplete, False & Misleading Info in Election Context", "Deceptive Inducement",
        "Covert Targeted Persuasion", "Reputational Damage", "Copyright & Piracy", "Trademark", "Upload IP Images",
        "Plagiarism & Academic Dishonesty", "Proprietary, Confidential & Classified Information",
        "Private Personal Information", "Images of Public Figures", "Upload Images of People without Consent",
        "Insecure Code", "Malware", "Weapons Development & Military",
        "Chemical, Biological, Radiological, & Nuclear", "High-Risk Government Decision-Making",
        "Management or Operation of Critical Infrastructure in Energy, Transportation & Water", "Financial Advice",
        "Multi-Level Marketing", "Gambling", "Lending", "Financial Service Eligibility", "Health Diagnosis",
        "Pseudo-Pharmaceuticals", "Public & Personal Health", "Political Campaigning & Lobbying", "Legal Advice",
        "Romantic", "Self-Validation", "Mental Health", "Emotional", "Legally-Protected Attributes", "Emotion",
    }
)  # fmt: skip


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
    Move each finding's default field values back into the classification columns, and restore the checks.

    A severity that is not a PyRIT 1.2 option key becomes Other with its text, as does a harm type that is not a
    PyRIT 1.2 category.

    Raises:
        ValueError: If the downgrade is offline, or a finding has a value that PyRIT 1.2 has no column for.
    """
    if op.get_context().as_sql:
        raise ValueError("Finding downgrade requires online data validation.")
    bind = op.get_bind()
    findings = sa.table(
        "FindingEntries",
        sa.column("id", CustomUUID()),
        sa.column("severity", sa.Unicode()),
        sa.column("field_values", sa.JSON()),
        *(sa.column(column, sa.Unicode()) for column in _OLD_COLUMNS),
    )
    rows = bind.execute(sa.select(findings.c.id, findings.c.severity, findings.c.field_values)).all()
    blocked = [keys for keys in (set(field_values) - _OLD_FIELD_KEYS for _, _, field_values in rows) if keys]
    if blocked:
        noun = "finding uses" if len(blocked) == 1 else "findings use"
        raise ValueError(
            f"{len(blocked)} {noun} keys PyRIT 1.2 has no column for: {', '.join(sorted(set().union(*blocked)))}. "
            "Remove those values before downgrading."
        )
    with op.batch_alter_table("FindingEntries") as batch_op:
        for column in _OLD_COLUMNS:
            batch_op.add_column(sa.Column(column, sa.Unicode(), nullable=True))
    for finding_id, severity, field_values in rows:
        values = _old_values(severity=severity, field_values=field_values)
        bind.execute(findings.update().where(findings.c.id == finding_id).values(**values))
    # SQL Server rejects ALTER COLUMN while a CHECK references the column, so the checks come last.
    with op.batch_alter_table("FindingEntries") as batch_op:
        batch_op.alter_column("description", existing_type=sa.Unicode(), nullable=False)
        batch_op.alter_column("severity", existing_type=_SEVERITY_TYPE, type_=sa.String(32), existing_nullable=False)
        batch_op.drop_column("field_values")
        for name, condition in _CHECKS.items():
            batch_op.create_check_constraint(name, condition)


def _old_values(*, severity: str, field_values: dict[str, str]) -> dict[str, str | None]:
    """
    Map a finding's severity and default field values to the PyRIT 1.2 columns.

    Returns:
        dict[str, str | None]: The severity, its Other text, the harm type, its Other text, and the description.
    """
    harm_type = field_values.get("harm_type")
    is_category = harm_type is None or harm_type in _OLD_HARM_CATEGORIES
    is_option = severity in _OLD_SEVERITIES
    return {
        "severity": severity if is_option else "other",
        "severity_other": None if is_option else severity,
        "harm_type": harm_type if is_category else "Other",
        "harm_type_other": None if is_category else harm_type,
        "description": field_values.get("description", ""),
    }
