# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add first-class attack attribution fields and history query indexes.

Revision ID: a4c6e8f0b2d1
Revises: 8d1e3f5a7b9c
Create Date: 2026-09-04 18:48:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a4c6e8f0b2d1"
down_revision: str | Sequence[str] | None = "8d1e3f5a7b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTRIBUTION_FIELDS = ("operator", "operation")
_ATTRIBUTION_MAX_LENGTH = 128


def upgrade() -> None:
    """Add attribution columns, migrate legacy labels, and replace history indexes."""
    op.add_column("AttackResultEntries", sa.Column("operator", sa.Unicode(_ATTRIBUTION_MAX_LENGTH), nullable=True))
    op.add_column("AttackResultEntries", sa.Column("operation", sa.Unicode(_ATTRIBUTION_MAX_LENGTH), nullable=True))
    _move_attribution_from_labels()
    _bound_indexed_text_columns()

    op.drop_index("ix_AttackResultEntries_conversation_id", table_name="AttackResultEntries")
    op.create_index(
        "ix_AttackResultEntries_conversation_timestamp_id",
        "AttackResultEntries",
        ["conversation_id", "timestamp", "id"],
    )
    op.create_index(
        "ix_AttackResultEntries_operator_conversation_timestamp_id",
        "AttackResultEntries",
        ["operator", "conversation_id", "timestamp", "id"],
    )
    op.create_index(
        "ix_AttackResultEntries_operation_conversation_timestamp_id",
        "AttackResultEntries",
        ["operation", "conversation_id", "timestamp", "id"],
    )

    _drop_index_if_exists(name="idx_conversation_id", table_name="PromptMemoryEntries")
    op.create_index(
        "ix_PromptMemoryEntries_conversation_sequence_id",
        "PromptMemoryEntries",
        ["conversation_id", "sequence", "id"],
        mssql_include=["timestamp", "converted_value_data_type"],
    )

    op.create_index(
        "ix_ScenarioResultEntries_scenario_name_timestamp_id",
        "ScenarioResultEntries",
        ["scenario_name", "timestamp", "id"],
    )
    op.create_index(
        "ix_ScenarioResultEntries_scenario_run_state_timestamp_id",
        "ScenarioResultEntries",
        ["scenario_run_state", "timestamp", "id"],
    )


def downgrade() -> None:
    """Restore legacy labels and indexes, then remove attribution columns."""
    _restore_attribution_to_labels()

    op.drop_index(
        "ix_ScenarioResultEntries_scenario_run_state_timestamp_id",
        table_name="ScenarioResultEntries",
    )
    op.drop_index(
        "ix_ScenarioResultEntries_scenario_name_timestamp_id",
        table_name="ScenarioResultEntries",
    )

    op.drop_index(
        "ix_PromptMemoryEntries_conversation_sequence_id",
        table_name="PromptMemoryEntries",
    )

    op.drop_index(
        "ix_AttackResultEntries_operation_conversation_timestamp_id",
        table_name="AttackResultEntries",
    )
    op.drop_index(
        "ix_AttackResultEntries_operator_conversation_timestamp_id",
        table_name="AttackResultEntries",
    )
    op.drop_index(
        "ix_AttackResultEntries_conversation_timestamp_id",
        table_name="AttackResultEntries",
    )
    op.create_index(
        "ix_AttackResultEntries_conversation_id",
        "AttackResultEntries",
        ["conversation_id"],
    )

    _restore_unbounded_text_columns()
    op.drop_column("AttackResultEntries", "operation")
    op.drop_column("AttackResultEntries", "operator")


def _attack_results_table(*, include_attribution: bool) -> sa.Table:
    """
    Build a typed table for portable JSON migration reads and writes.

    Returns:
        The lightweight attack-results table.
    """
    columns = [
        sa.Column("id", CustomUUID(), primary_key=True),
        sa.Column("labels", sa.JSON(), nullable=True),
    ]
    if include_attribution:
        columns.extend(
            [
                sa.Column("operator", sa.Unicode(_ATTRIBUTION_MAX_LENGTH), nullable=True),
                sa.Column("operation", sa.Unicode(_ATTRIBUTION_MAX_LENGTH), nullable=True),
            ]
        )
    return sa.Table("AttackResultEntries", sa.MetaData(), *columns)


def _drop_index_if_exists(*, name: str, table_name: str) -> None:
    """Drop an index only when it exists in the source schema."""
    bind = op.get_bind()
    existing_names = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
    if name in existing_names:
        op.drop_index(name, table_name=table_name)


def _bound_indexed_text_columns() -> None:
    """Bound existing text keys before creating indexes that SQL Server accepts."""
    _validate_column_length(table_name="PromptMemoryEntries", column_name="conversation_id", max_length=36)
    _validate_column_length(table_name="ScenarioResultEntries", column_name="scenario_name", max_length=256)
    _validate_column_length(table_name="ScenarioResultEntries", column_name="scenario_run_state", max_length=32)
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.alter_column(
            "conversation_id",
            existing_type=sa.String(),
            type_=sa.String(36),
            existing_nullable=False,
        )
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "scenario_name",
            existing_type=sa.String(),
            type_=sa.String(256),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "scenario_run_state",
            existing_type=sa.String(),
            type_=sa.String(32),
            existing_nullable=False,
        )


def _restore_unbounded_text_columns() -> None:
    """Restore the pre-migration unbounded text column types."""
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "scenario_name",
            existing_type=sa.String(256),
            type_=sa.String(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "scenario_run_state",
            existing_type=sa.String(32),
            type_=sa.String(),
            existing_nullable=False,
        )
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.alter_column(
            "conversation_id",
            existing_type=sa.String(36),
            type_=sa.String(),
            existing_nullable=False,
        )


def _validate_column_length(*, table_name: str, column_name: str, max_length: int) -> None:
    """
    Fail before a bounded type conversion could truncate existing data.

    Raises:
        ValueError: If an existing value exceeds the new bound.
    """
    table = sa.Table(
        table_name,
        sa.MetaData(),
        sa.Column(column_name, sa.String(), nullable=False),
    )
    oversized_value = (
        op.get_bind()
        .execute(
            sa.select(table.c[column_name])
            .where(sa.func.length(table.c[column_name]) > max_length)
            .limit(1)
        )
        .scalar_one_or_none()
    )
    if oversized_value is not None:
        raise ValueError(
            f"{table_name}.{column_name} contains a value longer than {max_length} characters; "
            "migration will not truncate it."
        )


def _move_attribution_from_labels() -> None:
    """
    Move exact legacy attribution label keys into bounded scalar columns.

    Raises:
        ValueError: If a legacy attribution value is invalid or too long.
    """
    bind = op.get_bind()
    table = _attack_results_table(include_attribution=True)
    rows = bind.execute(sa.select(table.c.id, table.c.labels)).all()
    for row in rows:
        labels = row.labels
        if not isinstance(labels, dict):
            continue
        remaining_labels = dict(labels)
        values: dict[str, Any] = {}
        for field_name in _ATTRIBUTION_FIELDS:
            if field_name not in remaining_labels:
                continue
            value = remaining_labels.pop(field_name)
            if not isinstance(value, str):
                raise ValueError(
                    f"AttackResultEntries row {row.id} has non-string labels.{field_name}; "
                    "cannot migrate it to a first-class string column."
                )
            if len(value) > _ATTRIBUTION_MAX_LENGTH:
                raise ValueError(
                    f"AttackResultEntries row {row.id} has labels.{field_name} longer than "
                    f"{_ATTRIBUTION_MAX_LENGTH} characters; migration will not truncate it."
                )
            values[field_name] = value
        if values:
            values["labels"] = remaining_labels
            bind.execute(sa.update(table).where(table.c.id == row.id).values(**values))


def _restore_attribution_to_labels() -> None:
    """
    Restore populated attribution columns to exact legacy JSON label keys.

    Raises:
        ValueError: If a legacy label conflicts with its dedicated value.
    """
    bind = op.get_bind()
    table = _attack_results_table(include_attribution=True)
    rows = bind.execute(sa.select(table.c.id, table.c.labels, table.c.operator, table.c.operation)).all()
    for row in rows:
        labels = dict(row.labels) if isinstance(row.labels, dict) else {}
        changed = False
        for field_name in _ATTRIBUTION_FIELDS:
            value = getattr(row, field_name)
            if value is None:
                continue
            existing = labels.get(field_name)
            if existing is not None and existing != value:
                raise ValueError(
                    f"AttackResultEntries row {row.id} has conflicting labels.{field_name} "
                    f"while downgrading: {existing!r} != {value!r}."
                )
            labels[field_name] = value
            changed = True
        if changed:
            bind.execute(sa.update(table).where(table.c.id == row.id).values(labels=labels))
