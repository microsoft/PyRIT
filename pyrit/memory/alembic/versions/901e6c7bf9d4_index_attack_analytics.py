# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Index attack analytics and backfill the frozen v1 objective-target identity.

Revision ID: 901e6c7bf9d4
Revises: 34a18645c7e9
Create Date: 2026-10-03 04:46:09.282494
"""

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection, RowMapping

from pyrit.memory.analytics_identity_v1 import ObjectiveTargetAnalyticsIdentityV1
from pyrit.memory.memory_models import CustomUUID
from pyrit.models import ComponentIdentifier

revision: str = "901e6c7bf9d4"
down_revision: str | None = "34a18645c7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVAL_INDEX = "ix_AttackResultEntries_objective_target_eval_v1"
_BATCH_SIZE = 500
_LOGGER = logging.getLogger(__name__)


def _historical_hash(*, row: RowMapping) -> str | None:
    """
    Resolve the saved target snapshot first, then its normalized identifier.

    Args:
        row (RowMapping): One result ID and its embedded/normalized identifiers.

    Returns:
        str | None: A frozen v1 evaluation hash, or None for missing/unsupported
            historical metadata. Such results remain in the missing bucket.
    """
    for name, document in (
        ("embedded", row["embedded_identifier"]),
        ("normalized", row["target_identifier"]),
    ):
        if document is None:
            continue
        if not isinstance(document, dict):
            _LOGGER.warning("Attack result %s has a non-object %s target identifier.", row["id"], name)
            continue
        try:
            value = (
                ObjectiveTargetAnalyticsIdentityV1.from_atomic_document(document=document)
                if name == "embedded"
                else ObjectiveTargetAnalyticsIdentityV1.hash(identifier=ComponentIdentifier.model_validate(document))
            )
        except (TypeError, ValueError) as error:
            _LOGGER.warning(
                "Attack result %s has an unsupported %s target identifier (%s).",
                row["id"],
                name,
                type(error).__name__,
            )
            continue
        if value is not None:
            return value
    return None


def _backfill(*, connection: Connection) -> None:
    """
    Update historical results in bounded pages, preserving every stored result ID.

    Args:
        connection (Connection): The Alembic transaction's existing connection.
    """
    result = sa.table(
        "AttackResultEntries",
        sa.column("id", CustomUUID()),
        sa.column("atomic_attack_identifier", sa.JSON()),
        sa.column("resolved_atomic_attack_identifier_hash", sa.String(64)),
        sa.column("objective_target_eval_hash_v1", sa.String(64)),
    )
    atomic = sa.table(
        "AtomicAttackIdentifiers",
        sa.column("hash", sa.String(64)),
        sa.column("attack_technique_identifier_hash", sa.String(64)),
    )
    technique = sa.table(
        "AttackTechniqueIdentifiers",
        sa.column("hash", sa.String(64)),
        sa.column("attack_identifier_hash", sa.String(64)),
    )
    attack = sa.table(
        "AttackIdentifiers", sa.column("hash", sa.String(64)), sa.column("objective_target_hash", sa.String(64))
    )
    target = sa.table("TargetIdentifiers", sa.column("hash", sa.String(64)), sa.column("identifier_json", sa.JSON()))
    source = (
        result.outerjoin(atomic, atomic.c.hash == result.c.resolved_atomic_attack_identifier_hash)
        .outerjoin(technique, technique.c.hash == atomic.c.attack_technique_identifier_hash)
        .outerjoin(attack, attack.c.hash == technique.c.attack_identifier_hash)
        .outerjoin(target, target.c.hash == attack.c.objective_target_hash)
    )
    page = (
        sa.select(
            result.c.id,
            result.c.atomic_attack_identifier.label("embedded_identifier"),
            target.c.identifier_json.label("target_identifier"),
        )
        .select_from(source)
        .order_by(result.c.id)
        .limit(_BATCH_SIZE)
    )
    update = (
        sa.update(result)
        .where(result.c.id == sa.bindparam("result_id", type_=CustomUUID()))
        .values(objective_target_eval_hash_v1=sa.bindparam("eval_hash", type_=sa.String(64)))
    )
    last_id: Any | None = None
    unsupported = 0
    while True:
        rows = connection.execute(page.where(result.c.id > last_id) if last_id is not None else page).mappings().all()
        if not rows:
            break
        values = []
        for row in rows:
            eval_hash = _historical_hash(row=row)
            if eval_hash is not None:
                values.append({"result_id": row["id"], "eval_hash": eval_hash})
            elif row["embedded_identifier"] is not None or row["target_identifier"] is not None:
                unsupported += 1
        if values:
            connection.execute(update, values)
        last_id = rows[-1]["id"]
    if unsupported:
        _LOGGER.warning(
            "Kept %d attack results with unavailable v1 target identity in the missing bucket.", unsupported
        )


def upgrade() -> None:
    """
    Add the analytics lookup, indexes, and historical evaluation identity.

    Raises:
        ValueError: If an existing outcome cannot fit the bounded index key.
    """
    connection = op.get_bind()
    dialect = connection.dialect.name
    length = "DATALENGTH(outcome)" if dialect == "mssql" else "length(outcome)"
    count = connection.execute(sa.text(f'SELECT COUNT(*) FROM "AttackResultEntries" WHERE {length} > 16')).scalar_one()
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
    op.add_column("AttackResultEntries", sa.Column("objective_target_eval_hash_v1", sa.String(64), nullable=True))
    _backfill(connection=connection)
    op.create_index(_EVAL_INDEX, "AttackResultEntries", ["objective_target_eval_hash_v1", "outcome"])


def downgrade() -> None:
    """Remove analytics projections and indexes without deleting stored results."""
    op.drop_index(_EVAL_INDEX, table_name="AttackResultEntries")
    op.drop_column("AttackResultEntries", "objective_target_eval_hash_v1")
    suffix = "sqlite" if op.get_bind().dialect.name == "sqlite" else "mssql"
    op.drop_index(f"ix_AttackResultEntries_analytics_facts_{suffix}", table_name="AttackResultEntries")
    op.drop_index(f"ix_AttackResultEntries_analytics_labels_{suffix}", table_name="AttackResultEntries")
    op.drop_column("AttackResultEntries", "resolved_atomic_attack_identifier_hash")
    with op.batch_alter_table("AttackResultEntries") as batch:
        batch.alter_column("outcome", existing_type=sa.String(16), type_=sa.String(), existing_nullable=False)
