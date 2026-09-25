# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private scenario-history page query component."""

from __future__ import annotations

import uuid
from contextlib import closing
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_, select

from pyrit.memory.memory_models import ScenarioResultEntry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.memory.memory_interface import (
        MemoryInterface,
        ScenarioHistoryKeysetCursor,
        ScenarioHistoryRunRecord,
    )


class _ScenarioHistoryQueries:
    """
    Private query builder for scenario-history page retrieval.

    Owns filter normalization, cursor handling, compact SQL construction,
    and row-to-record conversion. Reaches backend-specific expressions
    through MemoryInterface hooks. Not exported from pyrit.memory.
    """

    def __init__(self, *, memory: MemoryInterface) -> None:
        """Inject MemoryInterface for hook access and session management."""
        self._memory = memory

    def get_page(
        self,
        *,
        scenario_names: Sequence[str] | None,
        statuses: Sequence[str] | None,
        labels: Mapping[str, str | Sequence[str]] | None,
        cursor: ScenarioHistoryKeysetCursor | None,
        limit: int,
    ) -> tuple[list[ScenarioHistoryRunRecord], bool]:
        """
        Return one descending scenario-history page.

        Args:
            scenario_names: Filter by scenario name or registry name (exact match after strip).
            statuses: Filter by scenario run state (case-insensitive, normalized to upper).
            labels: Filter by label key-value pairs (exact match; keys restricted to [A-Za-z0-9_.-]+).
            cursor: Keyset cursor for pagination (timestamp DESC, id DESC).
            limit: Page size (1-100).

        Returns:
            tuple[list[ScenarioHistoryRunRecord], bool]:
                Page records and whether another page exists.

        Raises:
            ValueError: If the limit, cursor ID, or label keys are invalid.
        """
        # Import here to avoid circular dependency
        from pyrit.memory.memory_interface import ScenarioHistoryRunRecord

        if limit < 1 or limit > 100:
            raise ValueError("Scenario history limit must be between 1 and 100.")

        conditions: list[Any] = []
        effective_names = sorted({name.strip() for name in scenario_names or [] if name.strip()})
        if effective_names:
            conditions.append(
                or_(
                    ScenarioResultEntry.scenario_name.in_(effective_names),
                    self._memory._get_scenario_registry_name_condition(scenario_names=effective_names),
                )
            )
        effective_statuses = sorted({status.strip().upper() for status in statuses or [] if status.strip()})
        if effective_statuses:
            conditions.append(ScenarioResultEntry.scenario_run_state.in_(effective_statuses))
        effective_labels = {
            key: value
            for key, value in (labels or {}).items()
            if (isinstance(value, str) and value) or (not isinstance(value, str) and len(value) > 0)
        }
        invalid_keys = sorted(key for key in effective_labels if not self._memory._LABEL_KEY_PATTERN.fullmatch(key))
        if invalid_keys:
            raise ValueError(
                f"Invalid label key(s) {invalid_keys!r}: keys must match {self._memory._LABEL_KEY_PATTERN.pattern}."
            )
        if effective_labels:
            conditions.append(self._memory._get_scenario_result_labels_condition(labels=effective_labels))
        if cursor is not None:
            cursor_id = uuid.UUID(cursor.scenario_result_id)
            conditions.append(
                or_(
                    ScenarioResultEntry.timestamp < cursor.timestamp,
                    and_(
                        ScenarioResultEntry.timestamp == cursor.timestamp,
                        ScenarioResultEntry.id < cursor_id,
                    ),
                )
            )

        statement = select(
            ScenarioResultEntry.id,
            ScenarioResultEntry.scenario_name,
            ScenarioResultEntry.scenario_version,
            ScenarioResultEntry.pyrit_version,
            ScenarioResultEntry.scenario_identifier,
            ScenarioResultEntry.objective_target_identifier,
            ScenarioResultEntry.scenario_run_state,
            ScenarioResultEntry.labels,
            ScenarioResultEntry.timestamp,
            self._memory._get_scenario_started_at_expression().label("started_at"),
            ScenarioResultEntry.completion_time,
            ScenarioResultEntry.error_message,
            ScenarioResultEntry.error_type,
            *(
                expression.label(label)
                for expression, label in zip(
                    self._memory._get_scenario_history_plan_expressions(),
                    ("scenario_registry_name", "plan_atomic_groups", "plan_seed_id_map"),
                    strict=True,
                )
            ),
        )
        if conditions:
            statement = statement.where(and_(*conditions))
        statement = statement.order_by(
            ScenarioResultEntry.timestamp.desc(),
            ScenarioResultEntry.id.desc(),
        ).limit(limit + 1)
        with closing(self._memory.get_session()) as session:
            rows = session.execute(statement).all()
        page_rows = rows[:limit]

        records = [
            ScenarioHistoryRunRecord(
                scenario_result_id=str(row.id),
                scenario_name=row.scenario_name,
                scenario_version=row.scenario_version,
                pyrit_version=row.pyrit_version,
                scenario_identifier=row.scenario_identifier or {},
                objective_target_identifier=row.objective_target_identifier or {},
                status=row.scenario_run_state,
                labels=row.labels or {},
                created_at=row.timestamp,
                started_at=_parse_scenario_started_at(raw_value=row.started_at),
                completed_at=row.completion_time,
                error_message=row.error_message,
                error_type=row.error_type,
                scenario_registry_name=row.scenario_registry_name,
                plan_atomic_groups=row.plan_atomic_groups,
                plan_seed_id_map=row.plan_seed_id_map,
            )
            for row in page_rows
        ]
        return records, len(rows) > limit


def _parse_scenario_started_at(*, raw_value: Any) -> datetime | None:
    """
    Parse a persisted aware scenario start timestamp.

    Args:
        raw_value: Raw value from the database (typically a string or None).

    Returns:
        datetime | None: Aware start timestamp, or None for legacy or malformed values.
    """
    if not isinstance(raw_value, str):
        return None
    try:
        value = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    return value if value.tzinfo is not None else None
