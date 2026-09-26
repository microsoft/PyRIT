# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Add narrow analytics indexes without rewriting stored result identities."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d8f0a2c4e1"
down_revision: str = "7a9c1e3f5b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Add an indexed, computed identifier lookup and covering metadata indexes.

    Raises:
        ValueError: If an existing outcome cannot fit the bounded index key.
    """
    dialect = op.get_bind().dialect.name
    length = "DATALENGTH(outcome)" if dialect == "mssql" else "length(outcome)"
    count = (
        op.get_bind().execute(sa.text(f'SELECT COUNT(*) FROM "AttackResultEntries" WHERE {length} > 16')).scalar_one()
    )
    if count:
        raise ValueError("Attack analytics migration requires outcome values of at most 16 characters.")
    with op.batch_alter_table("AttackResultEntries") as batch:
        batch.alter_column("outcome", existing_type=sa.String(), type_=sa.String(16), existing_nullable=False)
    expression = (
        "CONVERT(varchar(64), coalesce(atomic_attack_identifier_hash, JSON_VALUE(atomic_attack_identifier, '$.hash')))"
        if dialect == "mssql"
        else "coalesce(atomic_attack_identifier_hash, json_extract(atomic_attack_identifier, '$.hash'))"
    )
    op.add_column(
        "AttackResultEntries",
        sa.Column(
            "resolved_atomic_attack_identifier_hash",
            sa.String(64),
            sa.Computed(expression, persisted=False),
            nullable=True,
        ),
    )
    if dialect == "sqlite":
        op.create_index(
            "ix_AttackResultEntries_analytics_facts_sqlite",
            "AttackResultEntries",
            ["resolved_atomic_attack_identifier_hash", "outcome", "targeted_harm_categories", "operation", "operator"],
        )
        op.create_index(
            "ix_AttackResultEntries_analytics_labels_sqlite", "AttackResultEntries", ["operation", "labels"]
        )
    else:
        op.create_index(
            "ix_AttackResultEntries_analytics_facts_mssql",
            "AttackResultEntries",
            ["resolved_atomic_attack_identifier_hash", "outcome", "operation", "operator"],
            mssql_include=["targeted_harm_categories"],
        )
        op.create_index(
            "ix_AttackResultEntries_analytics_labels_mssql",
            "AttackResultEntries",
            ["operation"],
            mssql_include=["labels"],
        )


def downgrade() -> None:
    """Remove query projections and restore the original outcome column type."""
    suffix = "sqlite" if op.get_bind().dialect.name == "sqlite" else "mssql"
    op.drop_index(f"ix_AttackResultEntries_analytics_facts_{suffix}", table_name="AttackResultEntries")
    op.drop_index(f"ix_AttackResultEntries_analytics_labels_{suffix}", table_name="AttackResultEntries")
    op.drop_column("AttackResultEntries", "resolved_atomic_attack_identifier_hash")
    with op.batch_alter_table("AttackResultEntries") as batch:
        batch.alter_column("outcome", existing_type=sa.String(16), type_=sa.String(), existing_nullable=False)
