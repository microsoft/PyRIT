# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist the full scoring expectation instead of a bare objective.

``ScoreEntries.objective`` held only the objective string a score was judged against.
``ScoreEntries.scored_expectation`` records the complete versioned expectation (objective
plus any typed conditions), so a persisted score keeps the whole of what it was scored
for. On upgrade the legacy objective is folded into an objective-only expectation; on
downgrade only the objective survives and typed conditions are dropped.

Revision ID: 1b3d5f7a9c2e
Revises: 8d1e3f5a7b9c
Create Date: 2026-09-03 10:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1b3d5f7a9c2e"
down_revision: str | None = "8d1e3f5a7b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

#: Version stamped onto every backfilled expectation.
_SCHEMA_VERSION = 1

#: Rows per page so a large score table migrates in bounded keyset batches, not one statement.
_BACKFILL_BATCH_SIZE = 500

_MSSQL_BACKFILL_SCORED_EXPECTATION_QUERY = """
UPDATE score_entry
SET [scored_expectation] = JSON_MODIFY(
    N'{"schema_version":1,"objective":null,"conditions":[]}',
    N'$.objective',
    score_entry.[objective]
)
FROM [ScoreEntries] AS score_entry
WHERE score_entry.[objective] IS NOT NULL
  AND score_entry.[scored_expectation] IS NULL
"""
_MSSQL_RESTORE_OBJECTIVE_QUERY = """
UPDATE score_entry
SET [objective] = objective_attribute.[value]
FROM [ScoreEntries] AS score_entry
CROSS APPLY (
    SELECT TOP (1) attribute.[value]
    FROM OPENJSON(
        CASE
            WHEN ISJSON(score_entry.[scored_expectation]) = 1
                THEN score_entry.[scored_expectation]
            ELSE N'{}'
        END
    ) AS attribute
    WHERE attribute.[key] COLLATE Latin1_General_100_BIN2 = N'objective'
      AND attribute.[type] = 1
) AS objective_attribute
WHERE score_entry.[scored_expectation] IS NOT NULL
  AND score_entry.[objective] IS NULL
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
    """Add ``scored_expectation``, fold the legacy objective into it, then drop ``objective``."""
    _report_progress("Scored expectation migration: adding scored_expectation column.")
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("scored_expectation", sa.JSON(), nullable=True))

    _backfill_scored_expectation()

    _report_progress("Scored expectation migration: dropping legacy objective column.")
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("objective")
    _report_progress("Scored expectation migration: upgrade completed.")


def downgrade() -> None:
    """
    Re-add ``objective``, recover it from ``scored_expectation``, then drop the expectation.

    This is lossy by design: only the expectation's objective survives. Typed conditions
    have no column in the old schema and are dropped.
    """
    _report_progress("Scored expectation migration: restoring legacy objective column.")
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("objective", sa.String(), nullable=True))

    _backfill_objective()

    _report_progress("Scored expectation migration: dropping scored_expectation column.")
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("scored_expectation")
    _report_progress("Scored expectation migration: downgrade completed.")


def _backfill_scored_expectation() -> None:
    """
    Fold every non-null legacy objective into an objective-only versioned expectation.

    Rows are read a page at a time, keyed on ``id``, so a large score table is never pulled
    into memory at once. Scores with no objective keep a NULL expectation.
    """
    connection = op.get_bind()
    if connection.dialect.name == "mssql":
        _report_progress("Scored expectation backfill: applying set-based SQL Server update.")
        result = connection.exec_driver_sql(_MSSQL_BACKFILL_SCORED_EXPECTATION_QUERY)
        if isinstance(result.rowcount, int) and result.rowcount >= 0:
            _report_progress(f"Scored expectation backfill: updated {result.rowcount} row(s).")
        else:
            _report_progress("Scored expectation backfill: SQL Server update completed.")
        return

    score_entries = sa.table(
        "ScoreEntries",
        sa.column("id"),
        sa.column("objective"),
        sa.column("scored_expectation"),
    )
    statement = sa.text('UPDATE "ScoreEntries" SET scored_expectation = :scored_expectation WHERE id = :score_id')

    last_id = None
    batch_number = 0
    updated_count = 0
    _report_progress(f"Scored expectation backfill: processing rows in batches of {_BACKFILL_BATCH_SIZE}.")
    while True:
        conditions = [
            score_entries.c.objective.isnot(None),
            score_entries.c.scored_expectation.is_(None),
        ]
        if last_id is not None:
            conditions.append(score_entries.c.id > last_id)
        rows = connection.execute(
            sa.select(score_entries.c.id, score_entries.c.objective)
            .where(*conditions)
            .order_by(score_entries.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        ).fetchall()
        if not rows:
            _report_progress(f"Scored expectation backfill: updated {updated_count} row(s).")
            return
        last_id = rows[-1][0]
        batch_number += 1

        updates = [
            {
                "score_id": score_id,
                "scored_expectation": json.dumps(
                    {"schema_version": _SCHEMA_VERSION, "objective": objective, "conditions": []}
                ),
            }
            for score_id, objective in rows
        ]
        connection.execute(statement, updates)
        updated_count += len(updates)
        _report_progress(
            f"Scored expectation backfill: completed batch {batch_number}; updated {updated_count} row(s)."
        )


def _backfill_objective() -> None:
    """
    Recover the objective string from every stored expectation.

    Typed conditions cannot be represented by the old ``objective`` column and are dropped.
    Rows are read a page at a time, keyed on ``id``.
    """
    connection = op.get_bind()
    if connection.dialect.name == "mssql":
        _report_progress("Objective restore: applying set-based SQL Server update.")
        result = connection.exec_driver_sql(_MSSQL_RESTORE_OBJECTIVE_QUERY)
        if isinstance(result.rowcount, int) and result.rowcount >= 0:
            _report_progress(f"Objective restore: updated {result.rowcount} row(s).")
        else:
            _report_progress("Objective restore: SQL Server update completed.")
        return

    score_entries = sa.table(
        "ScoreEntries",
        sa.column("id"),
        sa.column("objective"),
        sa.column("scored_expectation"),
    )
    statement = sa.text('UPDATE "ScoreEntries" SET objective = :objective WHERE id = :score_id')

    last_id = None
    batch_number = 0
    processed_count = 0
    updated_count = 0
    _report_progress(f"Objective restore: processing rows in batches of {_BACKFILL_BATCH_SIZE}.")
    while True:
        conditions = [
            score_entries.c.scored_expectation.isnot(None),
            score_entries.c.objective.is_(None),
        ]
        if last_id is not None:
            conditions.append(score_entries.c.id > last_id)
        rows = connection.execute(
            sa.select(score_entries.c.id, score_entries.c.scored_expectation)
            .where(*conditions)
            .order_by(score_entries.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        ).fetchall()
        if not rows:
            _report_progress(f"Objective restore: processed {processed_count} row(s); updated {updated_count} row(s).")
            return
        last_id = rows[-1][0]
        batch_number += 1
        processed_count += len(rows)

        updates = []
        for score_id, scored_expectation in rows:
            objective = _extract_objective(scored_expectation)
            if objective is None:
                continue
            updates.append({"score_id": score_id, "objective": objective})
        if updates:
            connection.execute(statement, updates)
            updated_count += len(updates)
        _report_progress(
            f"Objective restore: completed batch {batch_number}; processed {processed_count} row(s), "
            f"updated {updated_count} row(s)."
        )


def _extract_objective(scored_expectation: object) -> str | None:
    """
    Read the objective out of a stored expectation, tolerating either a dict or JSON text.

    Args:
        scored_expectation (object): The stored ``scored_expectation`` value.

    Returns:
        str | None: The objective string, or ``None`` when absent or unparsable.
    """
    if isinstance(scored_expectation, str):
        try:
            scored_expectation = json.loads(scored_expectation)
        except (ValueError, TypeError):
            return None
    if isinstance(scored_expectation, dict):
        objective = scored_expectation.get("objective")
        return objective if isinstance(objective, str) else None
    return None
