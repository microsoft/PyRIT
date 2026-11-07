# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Parent-child decomposition shared by objectives and findings.

A parent is achieved through its direct children. Each ``DecompositionPath`` is one ordered way to
get there, so alternatives such as ``A -> B -> C`` and ``A -> D -> C`` are separate paths. Any child
can be decomposed further, which makes the relationships a forest of trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class DecompositionPath(BaseModel):
    """One ordered chain of a parent's direct children that together achieve the parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    name: str | None = None
    description: str = ""
    steps: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def _reject_repeated_steps(cls, steps: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """
        Require each step to appear once in a path.

        Returns:
            tuple[UUID, ...]: The steps.

        Raises:
            ValueError: If a step repeats.
        """
        if len(set(steps)) != len(steps):
            raise ValueError("A decomposition path cannot repeat a step.")
        return steps


def validate_own_decomposition(
    *, node_id: UUID | None, parent_id: UUID | None, paths: tuple[DecompositionPath, ...]
) -> None:
    """
    Check the relationship fields one node can validate without its relatives.

    Args:
        node_id (UUID | None): The node's ID.
        parent_id (UUID | None): The node's parent, if any.
        paths (tuple[DecompositionPath, ...]): The node's paths.

    Raises:
        ValueError: If the node has relationships but no ID, is its own parent, is a step of its own
            path, or repeats a path ID.
    """
    if node_id is None:
        if parent_id is not None or paths:
            raise ValueError("A node with a parent or paths must have an ID.")
        return
    if parent_id == node_id:
        raise ValueError("A node cannot be its own parent.")
    if len({path.id for path in paths}) != len(paths):
        raise ValueError("Each decomposition path of a node must have a distinct ID.")
    if any(node_id in path.steps for path in paths):
        raise ValueError("A node cannot be a step of its own path.")


@dataclass(frozen=True)
class DecompositionNode:
    """The decomposition relationships of one objective or finding."""

    id: UUID
    parent_id: UUID | None = None
    paths: tuple[DecompositionPath, ...] = field(default=())

    @property
    def is_member(self) -> bool:
        """Whether the node itself declares a parent or paths."""
        return self.parent_id is not None or bool(self.paths)


class DecompositionTree:
    """
    A validated forest of decomposition nodes.

    Children keep the order in which their nodes were supplied.
    """

    def __init__(self, nodes: Iterable[DecompositionNode]) -> None:
        """
        Index and validate the nodes.

        Args:
            nodes (Iterable[DecompositionNode]): Every node of each tree.

        Raises:
            ValueError: If an ID repeats, a parent is missing, the parents form a cycle, or a path
                step is not a direct child of the path's owner.
        """
        self._nodes: dict[UUID, DecompositionNode] = {}
        for node in nodes:
            if node.id in self._nodes:
                raise ValueError(f"Decomposition node {node.id} appears more than once.")
            validate_own_decomposition(node_id=node.id, parent_id=node.parent_id, paths=node.paths)
            self._nodes[node.id] = node
        self._children: dict[UUID, list[UUID]] = {node_id: [] for node_id in self._nodes}
        for node in self._nodes.values():
            if node.parent_id is None:
                continue
            if node.parent_id not in self._nodes:
                raise ValueError(f"Decomposition parent {node.parent_id} of {node.id} is missing.")
            self._children[node.parent_id].append(node.id)
        self._reject_cycles()
        self._validate_path_steps()

    @property
    def node_ids(self) -> tuple[UUID, ...]:
        """Every node ID, in the order supplied."""
        return tuple(self._nodes)

    @property
    def roots(self) -> tuple[UUID, ...]:
        """The IDs of nodes without a parent, in the order supplied."""
        return tuple(node_id for node_id, node in self._nodes.items() if node.parent_id is None)

    def __contains__(self, node_id: object) -> bool:
        """
        Whether the tree has a node with this ID.

        Returns:
            bool: True if the node is present.
        """
        return node_id in self._nodes

    def get_node(self, node_id: UUID) -> DecompositionNode:
        """
        Get a node by ID.

        Args:
            node_id (UUID): The node's ID.

        Returns:
            DecompositionNode: The node.
        """
        return self._nodes[node_id]

    def get_children(self, node_id: UUID) -> tuple[UUID, ...]:
        """
        Get a node's direct children.

        Args:
            node_id (UUID): The parent's ID.

        Returns:
            tuple[UUID, ...]: The children's IDs, in the order supplied.
        """
        return tuple(self._children[node_id])

    def walk(self, root_id: UUID) -> Iterator[tuple[UUID, int]]:
        """
        Visit a node and its descendants depth-first, parents before children.

        Args:
            root_id (UUID): The node to start from.

        Yields:
            tuple[UUID, int]: Each node's ID and its depth below ``root_id``.
        """
        stack = [(root_id, 0)]
        while stack:
            node_id, depth = stack.pop()
            yield node_id, depth
            stack.extend((child_id, depth + 1) for child_id in reversed(self._children[node_id]))

    def get_subtree(self, root_id: UUID) -> tuple[UUID, ...]:
        """
        Get a node and all of its descendants.

        Args:
            root_id (UUID): The node to start from.

        Returns:
            tuple[UUID, ...]: The IDs, depth-first with parents before children.
        """
        return tuple(node_id for node_id, _ in self.walk(root_id))

    def _reject_cycles(self) -> None:
        """
        Require every chain of parents to reach a root.

        Raises:
            ValueError: If the parents form a cycle.
        """
        reaches_root: set[UUID] = set()
        for start in self._nodes:
            chain: list[UUID] = []
            on_chain: set[UUID] = set()
            current: UUID | None = start
            while current is not None and current not in reaches_root:
                if current in on_chain:
                    raise ValueError(f"Decomposition parents of {start} form a cycle.")
                chain.append(current)
                on_chain.add(current)
                current = self._nodes[current].parent_id
            reaches_root.update(chain)

    def _validate_path_steps(self) -> None:
        """
        Require each path step to be a direct child of the path's owner.

        Raises:
            ValueError: If a step is not a direct child.
        """
        for node in self._nodes.values():
            children = set(self._children[node.id])
            for path in node.paths:
                outside = [step for step in path.steps if step not in children]
                if outside:
                    raise ValueError(
                        f"Path {path.id} of {node.id} has steps that are not its direct children: "
                        f"{', '.join(str(step) for step in outside)}."
                    )
