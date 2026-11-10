# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest
from pydantic import ValidationError

from pyrit.models import DecompositionNode, DecompositionPath, DecompositionTree, validate_own_decomposition

ROOT, A, B, C, D, D1 = (uuid.uuid4() for _ in range(6))


def _tree() -> DecompositionTree:
    return DecompositionTree(
        [
            DecompositionNode(
                id=ROOT,
                paths=(DecompositionPath(name="first", steps=(A, B, C)), DecompositionPath(steps=(A, D, C))),
            ),
            DecompositionNode(id=A, parent_id=ROOT),
            DecompositionNode(id=B, parent_id=ROOT),
            DecompositionNode(id=D, parent_id=ROOT, paths=(DecompositionPath(steps=(D1,)),)),
            DecompositionNode(id=D1, parent_id=D),
            DecompositionNode(id=C, parent_id=ROOT),
        ]
    )


def test_path_requires_a_step() -> None:
    with pytest.raises(ValidationError):
        DecompositionPath(steps=())


def test_path_rejects_repeated_steps() -> None:
    with pytest.raises(ValidationError, match="cannot repeat a step"):
        DecompositionPath(steps=(A, A))


def test_path_round_trips_through_json() -> None:
    path = DecompositionPath(name="phishing", description="via email", steps=(A, D, C))

    assert DecompositionPath.model_validate(path.model_dump(mode="json")) == path


@pytest.mark.parametrize(
    ("node_id", "parent_id", "paths", "message"),
    [
        (None, A, (), "must have an ID"),
        (A, A, (), "own parent"),
        (A, None, (DecompositionPath(steps=(A,)),), "step of its own path"),
    ],
)
def test_validate_own_decomposition_rejects_inconsistent_node(
    node_id: uuid.UUID | None, parent_id: uuid.UUID | None, paths: tuple[DecompositionPath, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_own_decomposition(node_id=node_id, parent_id=parent_id, paths=paths)


def test_validate_own_decomposition_rejects_repeated_path_ids() -> None:
    path = DecompositionPath(steps=(B,))

    with pytest.raises(ValueError, match="distinct ID"):
        validate_own_decomposition(node_id=A, parent_id=None, paths=(path, path))


def test_validate_own_decomposition_accepts_standalone_node_without_id() -> None:
    validate_own_decomposition(node_id=None, parent_id=None, paths=())


def test_tree_indexes_roots_and_children_in_supplied_order() -> None:
    tree = _tree()

    assert tree.roots == (ROOT,)
    assert tree.get_children(ROOT) == (A, B, D, C)
    assert tree.get_children(D) == (D1,)
    assert tree.get_node(D).parent_id == ROOT
    assert D1 in tree
    assert uuid.uuid4() not in tree


def test_tree_walk_visits_parents_before_children_with_depth() -> None:
    assert list(_tree().walk(ROOT)) == [(ROOT, 0), (A, 1), (B, 1), (D, 1), (D1, 2), (C, 1)]


def test_tree_subtree_starts_at_any_level() -> None:
    tree = _tree()

    assert tree.get_subtree(D) == (D, D1)
    assert tree.get_subtree(A) == (A,)


@pytest.mark.parametrize("method", ["get_node", "get_children", "walk", "get_subtree"])
@pytest.mark.parametrize("empty", [False, True])
def test_tree_rejects_unknown_node_id(*, method: str, empty: bool) -> None:
    tree = DecompositionTree([]) if empty else _tree()
    missing = uuid.uuid4()

    with pytest.raises(ValueError, match=f"Decomposition node {missing} is missing"):
        if method == "walk":
            next(tree.walk(missing))
        else:
            getattr(tree, method)(missing)


def test_tree_rejects_duplicate_nodes() -> None:
    with pytest.raises(ValueError, match="more than once"):
        DecompositionTree([DecompositionNode(id=A), DecompositionNode(id=A)])


def test_tree_rejects_missing_parent() -> None:
    with pytest.raises(ValueError, match="is missing"):
        DecompositionTree([DecompositionNode(id=A, parent_id=B)])


def test_tree_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        DecompositionTree([DecompositionNode(id=A, parent_id=B), DecompositionNode(id=B, parent_id=A)])


def test_tree_rejects_steps_that_are_not_direct_children() -> None:
    with pytest.raises(ValueError, match="not its direct children"):
        DecompositionTree(
            [
                DecompositionNode(id=ROOT, paths=(DecompositionPath(steps=(A, D1)),)),
                DecompositionNode(id=A, parent_id=ROOT),
                DecompositionNode(id=D, parent_id=ROOT),
                DecompositionNode(id=D1, parent_id=D),
            ]
        )


def test_node_membership_reflects_its_own_relationships() -> None:
    assert not DecompositionNode(id=A).is_member
    assert DecompositionNode(id=A, parent_id=B).is_member
    assert DecompositionNode(id=A, paths=(DecompositionPath(steps=(B,)),)).is_member
