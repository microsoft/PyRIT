# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add first-class attack attribution fields and history query indexes.

Revision ID: a4c6e8f0b2d1
Revises: 1b3d5f7a9c2e
Create Date: 2026-09-04 18:48:00.000000
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a4c6e8f0b2d1"
down_revision: str | Sequence[str] | None = "1b3d5f7a9c2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_ATTRIBUTION_FIELDS = ("operator", "operation")
_ATTRIBUTION_MAX_LENGTH = 128
_BATCH_SIZE = 1000
_MSSQL_INVALID_ATTRIBUTION_QUERY = f"""
SELECT TOP (1)
    attack_result.[id] AS row_id,
    attribute.[key] AS field_name,
    attribute.[type] AS value_type
FROM [AttackResultEntries] AS attack_result
CROSS APPLY OPENJSON(attack_result.[labels]) AS attribute
WHERE attribute.[key] COLLATE Latin1_General_100_BIN2 IN (N'operator', N'operation')
  AND (
      attribute.[type] <> 1
      OR DATALENGTH(attribute.[value]) > {_ATTRIBUTION_MAX_LENGTH * 2}
  )
"""
_MSSQL_MOVE_ATTRIBUTION_QUERY = """
UPDATE attack_result
SET
    [operator] = JSON_VALUE(attack_result.[labels], N'$.operator'),
    [operation] = JSON_VALUE(attack_result.[labels], N'$.operation'),
    [labels] = JSON_MODIFY(
        JSON_MODIFY(attack_result.[labels], N'$.operator', NULL),
        N'$.operation',
        NULL
    )
FROM [AttackResultEntries] AS attack_result
WHERE EXISTS (
    SELECT 1
    FROM OPENJSON(attack_result.[labels]) AS attribute
    WHERE attribute.[key] COLLATE Latin1_General_100_BIN2 IN (N'operator', N'operation')
)
"""


def _report_progress(message: str) -> None:
    """Write migration progress to Alembic stdout, or the logger outside a migration context."""
    try:
        context = op.get_context()
    except (AttributeError, NameError):
        logger.info(message)
        return
    config = context.config
    if config is not None:
        config.print_stdout(message)
    else:
        logger.info(message)


def upgrade() -> None:
    """Add attribution columns, migrate legacy labels, and replace history indexes."""
    _report_progress("Attack history migration: adding attribution columns.")
    op.add_column("AttackResultEntries", sa.Column("operator", sa.Unicode(_ATTRIBUTION_MAX_LENGTH), nullable=True))
    op.add_column("AttackResultEntries", sa.Column("operation", sa.Unicode(_ATTRIBUTION_MAX_LENGTH), nullable=True))

    _report_progress("Attack history migration: moving attribution values from labels.")
    _move_attribution_from_labels()

    _report_progress("Attack history migration: validating and bounding indexed text columns.")
    _bound_indexed_text_columns()

    _report_progress("Attack history migration: replacing AttackResultEntries indexes.")
    op.drop_index("ix_AttackResultEntries_conversation_id", table_name="AttackResultEntries")
    _report_progress("Attack history migration: creating ix_AttackResultEntries_conversation_timestamp_id.")
    op.create_index(
        "ix_AttackResultEntries_conversation_timestamp_id",
        "AttackResultEntries",
        ["conversation_id", "timestamp", "id"],
    )
    _report_progress("Attack history migration: creating ix_AttackResultEntries_operator_timestamp_id.")
    op.create_index(
        "ix_AttackResultEntries_operator_timestamp_id",
        "AttackResultEntries",
        ["operator", "timestamp", "id"],
        mssql_include=["conversation_id"],
    )
    _report_progress("Attack history migration: creating ix_AttackResultEntries_operation_timestamp_id.")
    op.create_index(
        "ix_AttackResultEntries_operation_timestamp_id",
        "AttackResultEntries",
        ["operation", "timestamp", "id"],
        mssql_include=["conversation_id"],
    )

    _report_progress("Attack history migration: replacing PromptMemoryEntries indexes.")
    _drop_index_if_exists(name="idx_conversation_id", table_name="PromptMemoryEntries")
    _report_progress("Attack history migration: creating ix_PromptMemoryEntries_conversation_sequence_id.")
    op.create_index(
        "ix_PromptMemoryEntries_conversation_sequence_id",
        "PromptMemoryEntries",
        ["conversation_id", "sequence", "id"],
        mssql_include=["timestamp", "converted_value_data_type"],
    )

    _report_progress("Attack history migration: creating ScenarioResultEntries indexes.")
    _report_progress("Attack history migration: creating ix_ScenarioResultEntries_scenario_name_timestamp_id.")
    op.create_index(
        "ix_ScenarioResultEntries_scenario_name_timestamp_id",
        "ScenarioResultEntries",
        ["scenario_name", "timestamp", "id"],
    )
    _report_progress("Attack history migration: creating ix_ScenarioResultEntries_scenario_run_state_timestamp_id.")
    op.create_index(
        "ix_ScenarioResultEntries_scenario_run_state_timestamp_id",
        "ScenarioResultEntries",
        ["scenario_run_state", "timestamp", "id"],
    )
    _report_progress("Attack history migration: upgrade completed.")


def downgrade() -> None:
    """Restore legacy labels and indexes, then remove attribution columns."""
    _report_progress("Attack history migration: restoring attribution values to labels.")
    _restore_attribution_to_labels()

    _report_progress("Attack history migration: restoring legacy indexes and text columns.")
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
        "ix_AttackResultEntries_operation_timestamp_id",
        table_name="AttackResultEntries",
    )
    op.drop_index(
        "ix_AttackResultEntries_operator_timestamp_id",
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
    _report_progress("Attack history migration: downgrade completed.")


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
    _validate_column_length(table_name="PromptMemoryEntries", column_name="conversation_id", max_length=128)
    _validate_column_length(table_name="ScenarioResultEntries", column_name="scenario_name", max_length=256)
    _validate_column_length(table_name="ScenarioResultEntries", column_name="scenario_run_state", max_length=32)
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.alter_column(
            "conversation_id",
            existing_type=sa.String(),
            type_=sa.String(128),
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
            existing_type=sa.String(128),
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
        .execute(sa.select(table.c[column_name]).where(sa.func.length(table.c[column_name]) > max_length).limit(1))
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
    if bind.dialect.name == "mssql":
        _move_attribution_from_labels_mssql(bind=bind)
        return

    _move_attribution_from_labels_portable(bind=bind)


def _move_attribution_from_labels_mssql(*, bind: Any) -> None:
    """
    Move attribution labels with set-based SQL Server JSON operations.

    Raises:
        ValueError: If a legacy attribution value is invalid or too long.
    """
    _report_progress("Attack attribution backfill: validating SQL Server JSON values.")
    invalid_value = bind.exec_driver_sql(_MSSQL_INVALID_ATTRIBUTION_QUERY).first()
    if invalid_value is not None:
        invalid = invalid_value._mapping
        if invalid["value_type"] != 1:
            raise ValueError(
                f"AttackResultEntries row {invalid['row_id']} has non-string labels.{invalid['field_name']}; "
                "cannot migrate it to a first-class string column."
            )
        raise ValueError(
            f"AttackResultEntries row {invalid['row_id']} has labels.{invalid['field_name']} longer than "
            f"{_ATTRIBUTION_MAX_LENGTH} characters; migration will not truncate it."
        )

    _report_progress("Attack attribution backfill: applying set-based SQL Server update.")
    result = bind.exec_driver_sql(_MSSQL_MOVE_ATTRIBUTION_QUERY)
    if isinstance(result.rowcount, int) and result.rowcount >= 0:
        _report_progress(f"Attack attribution backfill: updated {result.rowcount} row(s).")
    else:
        _report_progress("Attack attribution backfill: SQL Server update completed.")


def _move_attribution_from_labels_portable(*, bind: Any) -> None:
    """
    Move attribution labels using portable SQLAlchemy operations.

    Raises:
        ValueError: If a legacy attribution value is invalid or too long.
    """
    table = _attack_results_table(include_attribution=True)
    statement = (
        sa.update(table)
        .where(table.c.id == sa.bindparam("row_id"))
        .values(
            labels=sa.bindparam("new_labels"),
            operator=sa.bindparam("new_operator"),
            operation=sa.bindparam("new_operation"),
        )
    )
    rows = bind.execute(sa.select(table.c.id, table.c.labels)).all()
    batch_count = (len(rows) + _BATCH_SIZE - 1) // _BATCH_SIZE
    _report_progress(f"Attack attribution backfill: processing {len(rows)} row(s) in {batch_count} batch(es).")
    updated_count = 0
    for batch_number, start in enumerate(range(0, len(rows), _BATCH_SIZE), start=1):
        updates = []
        for row in rows[start : start + _BATCH_SIZE]:
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
                updates.append(
                    {
                        "row_id": row.id,
                        "new_labels": remaining_labels,
                        # Both columns were just added, so writing None leaves them NULL.
                        "new_operator": values.get("operator"),
                        "new_operation": values.get("operation"),
                    }
                )
        if updates:
            bind.execute(statement, updates)
            updated_count += len(updates)
        _report_progress(f"Attack attribution backfill: completed batch {batch_number}/{batch_count}.")
    _report_progress(f"Attack attribution backfill: updated {updated_count} row(s).")


def _restore_attribution_to_labels() -> None:
    """
    Restore populated attribution columns to exact legacy JSON label keys.

    Raises:
        ValueError: If a legacy label conflicts with its dedicated value.
    """
    bind = op.get_bind()
    table = _attack_results_table(include_attribution=True)
    statement = sa.update(table).where(table.c.id == sa.bindparam("row_id")).values(labels=sa.bindparam("new_labels"))
    rows = bind.execute(sa.select(table.c.id, table.c.labels, table.c.operator, table.c.operation)).all()
    batch_count = (len(rows) + _BATCH_SIZE - 1) // _BATCH_SIZE
    _report_progress(f"Attack attribution restore: processing {len(rows)} row(s) in {batch_count} batch(es).")
    updated_count = 0
    for batch_number, start in enumerate(range(0, len(rows), _BATCH_SIZE), start=1):
        updates = []
        for row in rows[start : start + _BATCH_SIZE]:
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
                updates.append({"row_id": row.id, "new_labels": labels})
        if updates:
            bind.execute(statement, updates)
            updated_count += len(updates)
        _report_progress(f"Attack attribution restore: completed batch {batch_number}/{batch_count}.")
    _report_progress(f"Attack attribution restore: updated {updated_count} row(s).")
