# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import mssql

from pyrit.memory import SQLiteMemory
from pyrit.memory._finding_decomposition import lock_finding_tree_async
from pyrit.memory.memory_interface import FindingHierarchyError
from pyrit.memory.memory_models import FindingEntry, FindingEvidenceEntry
from pyrit.models import DecompositionPath, Finding, FindingCreate, FindingEvidence, Operation

pytestmark = pytest.mark.usefixtures("patch_central_database")

STAMP = datetime(2026, 11, 6, tzinfo=UTC)


async def _operation(memory: SQLiteMemory, name: str = "Decomposed") -> Operation:
    return await memory.add_operation_async(Operation(name=name))


async def _finding(
    memory: SQLiteMemory,
    *,
    operation: Operation,
    title: str,
    parent: Finding | None = None,
    number: int | None = None,
    severity: str = "moderate",
    demonstrated: bool = True,
    minutes: int = 0,
) -> Finding:
    return await memory.add_finding_async(
        finding=Finding(
            id=UUID(int=number) if number is not None else uuid4(),
            created_at=STAMP + timedelta(minutes=minutes),
            operation_id=operation.id,
            title=title,
            severity=severity,
            parent_finding_id=parent.id if parent else None,
            demonstrated=demonstrated,
        )
    )


def _edit(finding: Finding, **changes: object) -> FindingCreate:
    return FindingCreate.model_validate(finding.model_dump(include=set(FindingCreate.model_fields)) | changes)


async def test_sub_finding_round_trips_its_parent_and_status(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    parent = await _finding(sqlite_instance, operation=operation, title="Exfiltration")
    child = await _finding(sqlite_instance, operation=operation, title="Recon", parent=parent, demonstrated=False)
    read = await sqlite_instance.get_finding_async(operation_id=operation.id, finding_id=child.id)
    assert read == child
    assert read.parent_finding_id == parent.id
    assert read.demonstrated is False


async def test_sub_finding_parent_must_be_in_the_same_operation(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    other = await _operation(sqlite_instance, name="Other")
    foreign = await _finding(sqlite_instance, operation=other, title="Foreign")
    for parent_id in (foreign.id, uuid4()):
        with pytest.raises(FindingHierarchyError, match="not in operation"):
            await sqlite_instance.add_finding_async(
                finding=Finding(operation_id=operation.id, title="Orphan", severity="low", parent_finding_id=parent_id)
            )
    assert await sqlite_instance.get_findings_async(operation_id=operation.id) == []


async def test_new_finding_cannot_have_paths(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    finding = Finding(
        operation_id=operation.id, title="Early", severity="low", paths=(DecompositionPath(steps=(uuid4(),)),)
    )
    with pytest.raises(FindingHierarchyError, match="cannot have paths"):
        await sqlite_instance.add_finding_async(finding=finding)


async def test_findings_filter_by_parent_and_top_level(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    root = await _finding(sqlite_instance, operation=operation, title="Root", number=1, severity="critical")
    first = await _finding(sqlite_instance, operation=operation, title="First", parent=root, number=2, minutes=1)
    second = await _finding(
        sqlite_instance, operation=operation, title="Second", parent=root, number=3, severity="critical", minutes=2
    )
    nested = await _finding(sqlite_instance, operation=operation, title="Nested", parent=first, number=4)
    standalone = await _finding(sqlite_instance, operation=operation, title="Standalone", number=5, severity="low")

    everything = await sqlite_instance.get_findings_async(operation_id=operation.id)
    assert {finding.id for finding in everything} == {root.id, first.id, second.id, nested.id, standalone.id}
    top_level = await sqlite_instance.get_findings_async(operation_id=operation.id, top_level_only=True)
    assert [finding.title for finding in top_level] == ["Root", "Standalone"]
    # Sub-findings read in the order they were written, not by severity.
    children = await sqlite_instance.get_findings_async(operation_id=operation.id, parent_finding_id=root.id)
    assert [finding.title for finding in children] == ["First", "Second"]
    later_page = await sqlite_instance.get_findings_async(parent_finding_id=root.id, limit=1, offset=1)
    assert [finding.title for finding in later_page] == ["Second"]
    assert await sqlite_instance.get_finding_child_counts_async(
        finding_ids=[root.id, first.id, second.id, standalone.id]
    ) == {root.id: 2, first.id: 1}
    with pytest.raises(ValueError, match="not both"):
        await sqlite_instance.get_findings_async(parent_finding_id=root.id, top_level_only=True)
    assert nested.parent_finding_id == first.id


async def test_replace_paths_keeps_order_and_requires_direct_sub_findings(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    root = await _finding(sqlite_instance, operation=operation, title="Root")
    recon, creds, pivot = [
        await _finding(sqlite_instance, operation=operation, title=title, parent=root)
        for title in ("Recon", "Creds", "Pivot")
    ]
    grandchild = await _finding(sqlite_instance, operation=operation, title="Deep", parent=recon)
    paths = (
        DecompositionPath(name="Credential theft", steps=(recon.id, creds.id)),
        DecompositionPath(name="Lateral", steps=(recon.id, pivot.id)),
    )

    saved = await sqlite_instance.replace_finding_paths_async(
        operation_id=operation.id, finding_id=root.id, paths=paths
    )

    assert saved.paths == paths
    assert (await sqlite_instance.get_finding_async(operation_id=operation.id, finding_id=root.id)).paths == paths
    for invalid, message in [
        ((DecompositionPath(steps=(recon.id, grandchild.id)),), "direct sub-findings"),
        ((DecompositionPath(steps=(uuid4(),)),), "direct sub-findings"),
        ((DecompositionPath(steps=(root.id,)),), "own path"),
        ((paths[0], paths[0]), "distinct ID"),
    ]:
        with pytest.raises(FindingHierarchyError, match=message):
            await sqlite_instance.replace_finding_paths_async(
                operation_id=operation.id, finding_id=root.id, paths=invalid
            )
    assert (await sqlite_instance.get_finding_async(operation_id=operation.id, finding_id=root.id)).paths == paths
    cleared = await sqlite_instance.replace_finding_paths_async(operation_id=operation.id, finding_id=root.id, paths=())
    assert cleared.paths == ()
    other = await _operation(sqlite_instance, name="Other")
    with pytest.raises(LookupError, match="not found"):
        await sqlite_instance.replace_finding_paths_async(operation_id=other.id, finding_id=root.id, paths=())


async def test_update_finding_never_changes_paths(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    root = await _finding(sqlite_instance, operation=operation, title="Root")
    step = await _finding(sqlite_instance, operation=operation, title="Step", parent=root)
    paths = (DecompositionPath(steps=(step.id,)),)
    await sqlite_instance.replace_finding_paths_async(operation_id=operation.id, finding_id=root.id, paths=paths)

    updated = await sqlite_instance.update_finding_async(
        operation_id=operation.id, finding_id=root.id, request=_edit(root, title="Renamed", demonstrated=False)
    )
    marked = await sqlite_instance.update_finding_async(
        operation_id=operation.id, finding_id=step.id, request=_edit(step, demonstrated=False)
    )

    assert (updated.title, updated.demonstrated, updated.paths) == ("Renamed", False, paths)
    assert (marked.demonstrated, marked.parent_finding_id) == (False, root.id)


async def test_move_keeps_sub_findings_and_rejects_cycles(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    other = await _operation(sqlite_instance, name="Other")
    first = await _finding(sqlite_instance, operation=operation, title="First")
    second = await _finding(sqlite_instance, operation=operation, title="Second")
    child = await _finding(sqlite_instance, operation=operation, title="Child", parent=first)
    grandchild = await _finding(sqlite_instance, operation=operation, title="Grandchild", parent=child)
    foreign = await _finding(sqlite_instance, operation=other, title="Foreign")

    for parent_id, message in [
        (grandchild.id, "itself or one of its sub-findings"),
        (child.id, "itself or one of its sub-findings"),
        (foreign.id, "not in operation"),
    ]:
        with pytest.raises(FindingHierarchyError, match=message):
            await sqlite_instance.update_finding_async(
                operation_id=operation.id, finding_id=child.id, request=_edit(child, parent_finding_id=parent_id)
            )

    moved = await sqlite_instance.update_finding_async(
        operation_id=operation.id, finding_id=child.id, request=_edit(child, parent_finding_id=second.id)
    )
    promoted = await sqlite_instance.update_finding_async(
        operation_id=operation.id, finding_id=second.id, request=_edit(second, parent_finding_id=None)
    )

    assert moved.parent_finding_id == second.id
    assert promoted.parent_finding_id is None
    assert [f.id for f in await sqlite_instance.get_findings_async(parent_finding_id=child.id)] == [grandchild.id]
    assert await sqlite_instance.get_finding_child_counts_async(finding_ids=[first.id, second.id]) == {second.id: 1}


async def test_a_path_step_cannot_move_or_be_deleted_until_the_paths_change(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    root = await _finding(sqlite_instance, operation=operation, title="Root")
    other_root = await _finding(sqlite_instance, operation=operation, title="Other root")
    step = await _finding(sqlite_instance, operation=operation, title="Step", parent=root)
    spare = await _finding(sqlite_instance, operation=operation, title="Spare", parent=root)
    path = DecompositionPath(name="Only way", steps=(step.id,))
    await sqlite_instance.replace_finding_paths_async(operation_id=operation.id, finding_id=root.id, paths=(path,))

    with pytest.raises(FindingHierarchyError, match="'Only way'.*moved"):
        await sqlite_instance.update_finding_async(
            operation_id=operation.id, finding_id=step.id, request=_edit(step, parent_finding_id=other_root.id)
        )
    with pytest.raises(FindingHierarchyError, match="'Only way'.*deleted"):
        await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=step.id)

    await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=spare.id)
    assert (await sqlite_instance.get_finding_async(operation_id=operation.id, finding_id=root.id)).paths == (path,)
    await sqlite_instance.replace_finding_paths_async(operation_id=operation.id, finding_id=root.id, paths=())
    await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=step.id)
    assert await sqlite_instance.get_findings_async(parent_finding_id=root.id) == []


async def test_delete_removes_the_whole_subtree_with_its_evidence(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    root = await _finding(sqlite_instance, operation=operation, title="Root")
    kept = await _finding(sqlite_instance, operation=operation, title="Kept")
    child = await _finding(sqlite_instance, operation=operation, title="Child", parent=root)
    grandchild = await _finding(sqlite_instance, operation=operation, title="Grandchild", parent=child)
    await sqlite_instance.replace_finding_paths_async(
        operation_id=operation.id, finding_id=child.id, paths=(DecompositionPath(steps=(grandchild.id,)),)
    )
    for finding in (root, child, grandchild, kept):
        await sqlite_instance.add_finding_evidence_async(
            evidence=FindingEvidence(finding_id=finding.id, attack_result_id=uuid4())
        )

    await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=root.id)

    assert await sqlite_instance.get_findings_async(operation_id=operation.id) == [kept]
    async with await sqlite_instance.get_session_async() as session:
        assert await session.scalar(select(func.count()).select_from(FindingEntry)) == 1
        assert list(await session.scalars(select(FindingEvidenceEntry.finding_id))) == [kept.id]


async def test_get_finding_subtrees_includes_every_descendant_once(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    other = await _operation(sqlite_instance, name="Other")
    root = await _finding(sqlite_instance, operation=operation, title="Root", number=1, severity="low")
    child = await _finding(sqlite_instance, operation=operation, title="Child", parent=root, number=2)
    grandchild = await _finding(
        sqlite_instance, operation=operation, title="Grandchild", parent=child, number=3, severity="critical"
    )
    await _finding(sqlite_instance, operation=operation, title="Unselected", number=4)
    foreign = await _finding(sqlite_instance, operation=other, title="Foreign", number=5)

    subtree = await sqlite_instance.get_finding_subtrees_async(
        operation_id=operation.id, finding_ids=[child.id, root.id, foreign.id, uuid4()]
    )

    assert [finding.id for finding in subtree] == [grandchild.id, child.id, root.id]
    assert await sqlite_instance.get_finding_subtrees_async(operation_id=operation.id, finding_ids=[]) == []


async def test_tree_changes_take_the_sqlite_writer_lock_before_reading(sqlite_instance: SQLiteMemory) -> None:
    operation = await _operation(sqlite_instance)
    root = await _finding(sqlite_instance, operation=operation, title="Root")
    statements: list[str] = []
    engine = sqlite_instance._get_async_engine().sync_engine

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        await sqlite_instance.replace_finding_paths_async(operation_id=operation.id, finding_id=root.id, paths=())
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements[0] == "BEGIN IMMEDIATE"


async def test_tree_lock_holds_the_operation_row_on_sql_server() -> None:
    session = MagicMock()
    session.run_sync = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    assert await lock_finding_tree_async(session, operation_id=uuid4()) is False

    statement = session.scalar.call_args.args[0]
    assert "WITH (UPDLOCK, HOLDLOCK)" in str(statement.compile(dialect=mssql.dialect()))
