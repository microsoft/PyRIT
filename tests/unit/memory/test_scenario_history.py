# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the private scenario-history page component.

Behavior that the public method already exercises through delegation is covered by
tests/unit/memory/memory_interface/test_interface_scenario_history.py; these tests pin only the
delegation boundary and the component behaviors that suite does not reach directly.
"""

import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface
from pyrit.memory._scenario_history import _ScenarioHistoryQueries
from pyrit.memory.memory_interface import ScenarioHistoryKeysetCursor, ScenarioHistoryRunRecord
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import ScenarioRunState
from unit.mocks import get_mock_target_identifier, make_scenario_result


def _make_history_scenario(
    *,
    result_id: str,
    timestamp: datetime,
    name: str,
    state: ScenarioRunState = ScenarioRunState.COMPLETED,
    labels: dict[str, str] | None = None,
):
    return make_scenario_result(
        id=result_id,
        scenario_name=name,
        scenario_run_state=state,
        labels=labels or {},
        creation_time=timestamp,
        completion_time=timestamp + timedelta(minutes=1),
        metadata={},
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )


def _get_page(component: _ScenarioHistoryQueries, **overrides: Any) -> tuple[list[ScenarioHistoryRunRecord], bool]:
    kwargs: dict[str, Any] = {"scenario_names": None, "statuses": None, "labels": None, "cursor": None, "limit": 10}
    kwargs.update(overrides)
    return component.get_page(**kwargs)


def _record(*, planned: bool) -> ScenarioHistoryRunRecord:
    return ScenarioHistoryRunRecord(
        scenario_result_id=str(uuid.uuid4()),
        scenario_name="Test",
        scenario_version=1,
        pyrit_version="1.0.0",
        scenario_identifier={},
        objective_target_identifier={},
        status="COMPLETED",
        labels={},
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        error_message=None,
        error_type=None,
        scenario_registry_name="registry" if planned else None,
        plan_atomic_groups=[{"id": "group-1"}] if planned else None,
        plan_seed_id_map=[{"seed": "id"}] if planned else None,
    )


def test_get_scenario_run_history_page_delegates_to_component(sqlite_instance: MemoryInterface) -> None:
    """Public method forwards the component's records and has_more, then derives aggregate IDs itself."""
    planned, unplanned = _record(planned=True), _record(planned=False)
    records = [planned, unplanned]

    with (
        patch.object(_ScenarioHistoryQueries, "get_page", return_value=(records, True)) as mock_get_page,
        patch.object(sqlite_instance, "get_scenario_history_aggregates", return_value={}) as mock_aggregates,
    ):
        result = sqlite_instance.get_scenario_run_history_page(scenario_names=["Test"], limit=50)

    mock_get_page.assert_called_once_with(scenario_names=["Test"], statuses=None, labels=None, cursor=None, limit=50)
    assert result[0] == records
    assert result[2] is True

    # All records feed scenario_result_ids; only records with a plan feed plan_scenario_ids.
    mock_aggregates.assert_called_once()
    assert mock_aggregates.call_args.kwargs["scenario_result_ids"] == [
        planned.scenario_result_id,
        unplanned.scenario_result_id,
    ]
    assert mock_aggregates.call_args.kwargs["plan_scenario_ids"] == [planned.scenario_result_id]
    assert isinstance(result[1], dict)


@pytest.mark.parametrize("limit", [0, 101])
def test_get_page_rejects_invalid_limit(sqlite_instance: MemoryInterface, limit: int) -> None:
    """Limit outside 1..100 raises before any session is opened."""
    component = _ScenarioHistoryQueries(memory=sqlite_instance)
    with (
        patch.object(sqlite_instance, "get_session", side_effect=AssertionError("session opened before validation")),
        pytest.raises(ValueError, match="between 1 and 100"),
    ):
        _get_page(component, limit=limit)


@pytest.mark.parametrize(
    "key,should_raise",
    [
        ("valid<bad>", True),  # shares the allowlist prefix but must fail fullmatch
        ("key;drop", True),
        ("key with space", True),
        ("valid_key", False),
        ("valid.key-0", False),
    ],
)
def test_get_page_rejects_invalid_label_keys(sqlite_instance: MemoryInterface, key: str, should_raise: bool) -> None:
    """Invalid label keys raise ValueError naming the key; valid keys pass through."""
    component = _ScenarioHistoryQueries(memory=sqlite_instance)
    if should_raise:
        with pytest.raises(ValueError, match=r"Invalid label key") as excinfo:
            _get_page(component, labels={key: "value"})
        assert key in str(excinfo.value)
    else:
        records, has_more = _get_page(component, labels={key: "value"})
        assert records == [] and has_more is False


def test_get_page_rejects_malformed_cursor(sqlite_instance: MemoryInterface) -> None:
    """A non-UUID cursor ID raises ValueError instead of degrading to an empty page."""
    component = _ScenarioHistoryQueries(memory=sqlite_instance)
    cursor = ScenarioHistoryKeysetCursor(timestamp=datetime.now(UTC), scenario_result_id="not-a-uuid")
    with pytest.raises(ValueError):
        _get_page(component, cursor=cursor)


@pytest.mark.parametrize(
    "filters,expected_names",
    [
        ({"scenario_names": ["  Alpha  ", "Alpha", "Beta"]}, {"Alpha", "Beta"}),  # strip + dedupe
        ({"scenario_names": ["alpha"]}, set()),  # case-sensitive
        ({"scenario_names": ["", "  "]}, {"Alpha", "Beta"}),  # all-empty → no filter
        ({"statuses": ["  completed  ", "COMPLETED"]}, {"Alpha"}),  # strip + upper + dedupe
        ({"statuses": [""]}, {"Alpha", "Beta"}),
        ({"labels": {"op": "alpha"}}, {"Alpha"}),
        ({"labels": {"op": ""}}, {"Alpha", "Beta"}),  # empty value dropped
        ({"labels": {"op": []}}, {"Alpha", "Beta"}),  # empty sequence dropped
        ({"labels": {}}, {"Alpha", "Beta"}),
    ],
)
def test_get_page_normalizes_filters(
    sqlite_instance: MemoryInterface, filters: dict[str, Any], expected_names: set[str]
) -> None:
    """Name, status, and label filters keep their current normalization against real rows."""
    now = datetime.now(UTC)
    sqlite_instance.add_scenario_results_to_memory(
        scenario_results=[
            _make_history_scenario(result_id=str(uuid.uuid4()), timestamp=now, name="Alpha", labels={"op": "alpha"}),
            _make_history_scenario(
                result_id=str(uuid.uuid4()),
                timestamp=now + timedelta(seconds=1),
                name="Beta",
                state=ScenarioRunState.IN_PROGRESS,
                labels={"op": "beta"},
            ),
        ]
    )
    records, _ = _get_page(_ScenarioHistoryQueries(memory=sqlite_instance), **filters)
    assert {r.scenario_name for r in records} == expected_names


def test_get_page_multi_page_walk_returns_every_row_once(sqlite_instance: MemoryInterface) -> None:
    """Walking tied timestamps with limit=2 yields every row exactly once, in timestamp DESC, id DESC order."""
    now = datetime.now(UTC)
    earlier = now - timedelta(seconds=10)
    ids_now = [uuid.UUID(int=i) for i in (1, 2, 3, 4)]
    ids_earlier = [uuid.UUID(int=i) for i in (5, 6, 7)]
    sqlite_instance.add_scenario_results_to_memory(
        scenario_results=[
            _make_history_scenario(result_id=str(i), timestamp=ts, name=str(i))
            for ts, ids in ((now, ids_now), (earlier, ids_earlier))
            for i in ids
        ]
    )
    component = _ScenarioHistoryQueries(memory=sqlite_instance)

    collected: list[str] = []
    cursor: ScenarioHistoryKeysetCursor | None = None
    for _ in range(10):  # cap so a non-advancing cursor fails instead of hanging
        page, has_more = _get_page(component, cursor=cursor, limit=2)
        collected.extend(r.scenario_result_id for r in page)
        if not has_more:
            break
        cursor = ScenarioHistoryKeysetCursor(
            timestamp=page[-1].created_at, scenario_result_id=page[-1].scenario_result_id
        )
    else:
        pytest.fail(f"walk did not terminate; collected {collected}")

    expected = [str(i) for i in reversed(ids_now)] + [str(i) for i in reversed(ids_earlier)]
    assert collected == expected


def test_get_page_empty_result(sqlite_instance: MemoryInterface) -> None:
    """No rows, or a cursor past the last row, returns an empty list and has_more=False."""
    component = _ScenarioHistoryQueries(memory=sqlite_instance)
    assert _get_page(component) == ([], False)

    sqlite_instance.add_scenario_results_to_memory(
        scenario_results=[_make_history_scenario(result_id=str(uuid.uuid4()), timestamp=datetime.now(UTC), name="Only")]
    )
    (only,), has_more = _get_page(component)
    assert has_more is False
    cursor = ScenarioHistoryKeysetCursor(timestamp=only.created_at, scenario_result_id=only.scenario_result_id)
    assert _get_page(component, cursor=cursor) == ([], False)


def test_get_page_null_json_columns_default_to_empty_dict(sqlite_instance: MemoryInterface) -> None:
    """NULL scenario_identifier, objective_target_identifier, and labels columns map to {} on the record."""
    row_id = str(uuid.uuid4())
    sqlite_instance.add_scenario_results_to_memory(
        scenario_results=[_make_history_scenario(result_id=row_id, timestamp=datetime.now(UTC), name="NullJson")]
    )
    with closing(sqlite_instance.get_session()) as session:
        entry = session.query(ScenarioResultEntry).filter_by(id=uuid.UUID(row_id)).one()
        entry.scenario_identifier = None
        entry.objective_target_identifier = None
        entry.labels = None
        session.commit()

    (record,), _ = _get_page(_ScenarioHistoryQueries(memory=sqlite_instance))
    assert record.scenario_identifier == {}
    assert record.objective_target_identifier == {}
    assert record.labels == {}


def test_get_page_single_query_execution(sqlite_instance: MemoryInterface) -> None:
    """One page costs exactly one Session.execute call: no per-row queries and no ORM hydration."""
    now = datetime.now(UTC)
    sqlite_instance.add_scenario_results_to_memory(
        scenario_results=[
            _make_history_scenario(result_id=str(uuid.uuid4()), timestamp=now + timedelta(seconds=i), name=f"Row{i}")
            for i in range(3)
        ]
    )
    component = _ScenarioHistoryQueries(memory=sqlite_instance)
    real_execute = Session.execute

    with patch.object(Session, "execute", autospec=True, side_effect=real_execute) as spy:
        records, has_more = _get_page(component, limit=2)

    assert spy.call_count == 1
    assert len(records) == 2 and has_more is True  # the query really ran and used limit + 1
