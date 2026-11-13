# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Keep an operation's findings a valid forest of decomposition trees.

Every change to a finding's parent or paths, and every deletion, runs these checks in the transaction that writes
it, after ``lock_finding_tree_async`` has serialized changes to the operation's findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from pyrit.memory.memory_models import FindingEntry, FindingEvidenceEntry, OperationEntry
from pyrit.memory.memory_session import MemorySession, _begin_sqlite_write
from pyrit.models import validate_own_decomposition

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute

    from pyrit.models import DecompositionPath

# The operation filter takes one of each statement's binds.
_BATCH_SIZE = MemorySession._MAX_BIND_VARS - 1


class FindingHierarchyError(ValueError):
    """A finding change would break its operation's tree of findings, or silently change a parent's path."""


async def lock_finding_tree_async(session: AsyncSession, *, operation_id: uuid.UUID) -> bool:
    """
    Serialize changes to an operation's findings until the transaction ends.

    SQLite takes its writer lock before any read, and SQL Server locks the operation's row.

    Args:
        session (AsyncSession): A session inside a transaction.
        operation_id (uuid.UUID): The operation whose findings change.

    Returns:
        bool: Whether the operation exists.
    """
    await session.run_sync(_begin_sqlite_write)
    statement = (
        select(OperationEntry.id)
        .where(OperationEntry.id == operation_id)
        .with_hint(OperationEntry, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
    )
    return await session.scalar(statement) is not None


async def require_parent_async(session: AsyncSession, *, operation_id: uuid.UUID, parent_id: uuid.UUID) -> None:
    """
    Require a sub-finding's parent to be in the same operation.

    Args:
        session (AsyncSession): A session holding the operation's finding lock.
        operation_id (uuid.UUID): The sub-finding's operation.
        parent_id (uuid.UUID): The parent finding.

    Raises:
        FindingHierarchyError: If the parent is missing or in another operation.
    """
    owner = await session.scalar(select(FindingEntry.operation_id).where(FindingEntry.id == parent_id))
    if owner != operation_id:
        raise FindingHierarchyError(f"Parent finding '{parent_id}' is not in operation '{operation_id}'.")


async def validate_move_async(session: AsyncSession, *, entry: FindingEntry, parent_id: uuid.UUID | None) -> None:
    """
    Check that a finding can leave its parent for another one, or for the top level, with its sub-findings.

    Args:
        session (AsyncSession): A session holding the operation's finding lock.
        entry (FindingEntry): The finding before the move.
        parent_id (uuid.UUID | None): The new parent, or None for the top level.

    Raises:
        FindingHierarchyError: If the finding is a step of its parent's paths, or the new parent is missing, in
            another operation, the finding itself, or one of its sub-findings.
    """
    if entry.parent_finding_id is not None:
        await reject_path_step_async(
            session, finding_id=entry.id, parent_id=entry.parent_finding_id, change="moved to another parent"
        )
    if parent_id is None:
        return
    await require_parent_async(session, operation_id=entry.operation_id, parent_id=parent_id)
    ancestor: uuid.UUID | None = parent_id
    visited: set[uuid.UUID] = set()
    while ancestor is not None and ancestor not in visited:
        if ancestor == entry.id:
            raise FindingHierarchyError(f"Finding '{entry.id}' cannot move under itself or one of its sub-findings.")
        visited.add(ancestor)
        ancestor = await session.scalar(select(FindingEntry.parent_finding_id).where(FindingEntry.id == ancestor))


async def reject_path_step_async(
    session: AsyncSession, *, finding_id: uuid.UUID, parent_id: uuid.UUID, change: str
) -> None:
    """
    Refuse to move or delete a finding that its parent's paths use, so no path changes silently.

    Args:
        session (AsyncSession): A session holding the operation's finding lock.
        finding_id (uuid.UUID): The finding to change.
        parent_id (uuid.UUID): Its parent.
        change (str): How the finding would change, for the error message.

    Raises:
        FindingHierarchyError: If any of the parent's paths has the finding as a step.
    """
    parent = await session.get(FindingEntry, parent_id)
    using = [path for path in parent.get_paths() if finding_id in path.steps] if parent is not None else []
    if using:
        names = ", ".join(f"'{path.name or path.id}'" for path in using)
        raise FindingHierarchyError(
            f"Finding '{finding_id}' is a step of its parent's paths {names}, so it cannot be {change}. "
            "Remove it from those paths first."
        )


async def validate_path_steps_async(
    session: AsyncSession, *, entry: FindingEntry, paths: tuple[DecompositionPath, ...]
) -> None:
    """
    Require each step of a finding's new paths to be one of its direct sub-findings.

    Args:
        session (AsyncSession): A session holding the operation's finding lock.
        entry (FindingEntry): The finding that owns the paths.
        paths (tuple[DecompositionPath, ...]): The paths to store.

    Raises:
        FindingHierarchyError: If path IDs repeat, or a step is not a direct sub-finding.
    """
    try:
        validate_own_decomposition(node_id=entry.id, parent_id=entry.parent_finding_id, paths=paths)
    except ValueError as exc:
        raise FindingHierarchyError(str(exc)) from exc
    children = set(await session.scalars(select(FindingEntry.id).where(FindingEntry.parent_finding_id == entry.id)))
    outside = list(dict.fromkeys(step for path in paths for step in path.steps if step not in children))
    if outside:
        raise FindingHierarchyError(
            f"Path steps must be direct sub-findings of finding '{entry.id}', and these are not: "
            f"{', '.join(str(step) for step in outside)}."
        )


async def get_subtree_levels_async(
    session: AsyncSession, *, operation_id: uuid.UUID, root_ids: Sequence[uuid.UUID]
) -> list[list[uuid.UUID]]:
    """
    Find findings and all of their sub-findings, one level at a time.

    Args:
        session (AsyncSession): An open session.
        operation_id (uuid.UUID): The operation to read.
        root_ids (Sequence[uuid.UUID]): The findings to start from. IDs outside the operation are ignored.

    Returns:
        list[list[uuid.UUID]]: The starting findings found in the operation, then each deeper level.
    """
    frontier = await _select_ids_async(
        session, column=FindingEntry.id, values=list(dict.fromkeys(root_ids)), operation_id=operation_id
    )
    seen = set(frontier)
    levels: list[list[uuid.UUID]] = []
    while frontier:
        levels.append(frontier)
        children = await _select_ids_async(
            session, column=FindingEntry.parent_finding_id, values=frontier, operation_id=operation_id
        )
        # A finding below two starting points is visited once.
        frontier = [child for child in children if child not in seen]
        seen.update(frontier)
    return levels


async def delete_subtree_async(session: AsyncSession, *, levels: Sequence[Sequence[uuid.UUID]]) -> None:
    """
    Delete findings level by level, deepest first so no parent goes before its sub-findings, with their evidence.

    Args:
        session (AsyncSession): A session holding the operation's finding lock.
        levels (Sequence[Sequence[uuid.UUID]]): The findings, from ``get_subtree_levels_async``.
    """
    for level in levels:
        for start in range(0, len(level), _BATCH_SIZE):
            batch = list(level[start : start + _BATCH_SIZE])
            await session.execute(delete(FindingEvidenceEntry).where(FindingEvidenceEntry.finding_id.in_(batch)))
    for level in reversed(levels):
        for start in range(0, len(level), _BATCH_SIZE):
            batch = list(level[start : start + _BATCH_SIZE])
            await session.execute(delete(FindingEntry).where(FindingEntry.id.in_(batch)))


async def _select_ids_async(
    session: AsyncSession,
    *,
    column: InstrumentedAttribute[uuid.UUID] | InstrumentedAttribute[uuid.UUID | None],
    values: Sequence[uuid.UUID],
    operation_id: uuid.UUID,
) -> list[uuid.UUID]:
    """
    Read the IDs of an operation's findings whose column matches any value, in batches.

    Returns:
        list[uuid.UUID]: The matching IDs.
    """
    found: dict[uuid.UUID, None] = {}
    for start in range(0, len(values), _BATCH_SIZE):
        statement = select(FindingEntry.id).where(
            column.in_(values[start : start + _BATCH_SIZE]), FindingEntry.operation_id == operation_id
        )
        found.update(dict.fromkeys(await session.scalars(statement)))
    return list(found)
