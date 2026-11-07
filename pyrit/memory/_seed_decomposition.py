# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store decomposed objectives as complete trees.

Each tree, with the seeds that share a prompt group with any of its objectives, is a unit. Units
are compared by content and structure, never member by member, so a reloaded tree is recognized
while the same objective text in a different tree or standalone stays separate.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.models import SeedObjective, build_objective_decomposition_tree

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.models import DecompositionTree, Seed


@dataclass(frozen=True)
class DecompositionUnit:
    """One decomposition tree and the seeds grouped with its objectives."""

    root_id: uuid.UUID
    seeds: tuple[Seed[Any], ...]

    @property
    def root(self) -> SeedObjective:
        """The tree's root objective."""
        return next(seed for seed in self.seeds if isinstance(seed, SeedObjective) and seed.id == self.root_id)


@dataclass(frozen=True)
class DecompositionIdentity:
    """New identifiers for the seeds of one unit: seed IDs, prompt group IDs, and path IDs."""

    seed_ids: dict[uuid.UUID, uuid.UUID]
    group_ids: dict[uuid.UUID, uuid.UUID]
    path_ids: dict[uuid.UUID, uuid.UUID]


def split_decomposition_units(seeds: Sequence[Seed[Any]]) -> tuple[list[DecompositionUnit], list[Seed[Any]]]:
    """
    Separate decomposition trees from independent seeds.

    Args:
        seeds (Sequence[Seed]): Seeds about to be stored.

    Returns:
        tuple[list[DecompositionUnit], list[Seed]]: One unit per tree, and every other seed.

    Raises:
        ValueError: If a tree is incomplete or invalid, or a prompt group spans two trees.
    """
    tree = build_objective_decomposition_tree(seed for seed in seeds if isinstance(seed, SeedObjective))
    if not tree.node_ids:
        return [], list(seeds)
    root_of = {node_id: root_id for root_id in tree.roots for node_id in tree.get_subtree(root_id)}
    group_root: dict[uuid.UUID, uuid.UUID] = {}
    for seed in seeds:
        if not isinstance(seed, SeedObjective) or seed.id is None or seed.prompt_group_id is None:
            continue
        if seed.id not in root_of:
            continue
        root_id = root_of[seed.id]
        if group_root.setdefault(seed.prompt_group_id, root_id) != root_id:
            raise ValueError(f"Prompt group {seed.prompt_group_id} spans two decomposition trees.")
    members: dict[uuid.UUID, list[Seed[Any]]] = {root_id: [] for root_id in tree.roots}
    independent: list[Seed[Any]] = []
    for seed in seeds:
        if isinstance(seed, SeedObjective) and seed.id is not None and seed.id in root_of:
            members[root_of[seed.id]].append(seed)
        elif seed.prompt_group_id is not None and seed.prompt_group_id in group_root:
            members[group_root[seed.prompt_group_id]].append(seed)
        else:
            independent.append(seed)
    return [DecompositionUnit(root_id=root_id, seeds=tuple(unit)) for root_id, unit in members.items()], independent


def canonicalize_decomposition(
    *, unit: DecompositionUnit, member_key: Callable[[Seed[Any]], str]
) -> tuple[str, list[Seed[Any]]]:
    """
    Describe a unit by content and structure, independent of its identifiers.

    Children are ordered by their own description, and path steps refer to children by that order.

    Args:
        unit (DecompositionUnit): The unit to describe.
        member_key (Callable[[Seed], str]): The persisted content identity of one seed.

    Returns:
        tuple[str, list[Seed]]: The description, and the unit's seeds in the matching order, so two
            units with equal descriptions can be paired seed by seed.

    Raises:
        ValueError: If the unit is not one valid tree.
    """
    objectives = {seed.id: seed for seed in unit.seeds if isinstance(seed, SeedObjective) and seed.id is not None}
    tree = build_objective_decomposition_tree(objectives.values())
    if tree.roots != (unit.root_id,):
        raise ValueError(f"Decomposition unit {unit.root_id} is not one tree.")
    mates: dict[uuid.UUID, list[Seed[Any]]] = {}
    for seed in unit.seeds:
        if not isinstance(seed, SeedObjective) and seed.prompt_group_id is not None:
            mates.setdefault(seed.prompt_group_id, []).append(seed)
    form, ordered = _canonicalize_node(
        node_id=unit.root_id, tree=tree, objectives=objectives, mates=mates, member_key=member_key
    )
    if len(ordered) != len(unit.seeds):
        raise ValueError(f"Decomposition unit {unit.root_id} has seeds outside its tree and prompt groups.")
    return _dumps(form), ordered


def pair_decomposition_identities(*, source: Sequence[Seed[Any]], target: Sequence[Seed[Any]]) -> DecompositionIdentity:
    """
    Map each identifier of one unit to the matching identifier of an equivalent unit.

    Args:
        source (Sequence[Seed]): A unit's seeds in canonical order.
        target (Sequence[Seed]): An equivalent unit's seeds in the same canonical order.

    Returns:
        DecompositionIdentity: The target identifiers for each source identifier.
    """
    identity = DecompositionIdentity(seed_ids={}, group_ids={}, path_ids={})
    for source_seed, target_seed in zip(source, target, strict=True):
        if source_seed.id is not None and target_seed.id is not None:
            identity.seed_ids[source_seed.id] = target_seed.id
        if source_seed.prompt_group_id is not None and target_seed.prompt_group_id is not None:
            identity.group_ids[source_seed.prompt_group_id] = target_seed.prompt_group_id
        if isinstance(source_seed, SeedObjective) and isinstance(target_seed, SeedObjective):
            for source_path, target_path in zip(source_seed.paths, target_seed.paths, strict=True):
                identity.path_ids[source_path.id] = target_path.id
    return identity


def fresh_decomposition_identities(seeds: Sequence[Seed[Any]]) -> DecompositionIdentity:
    """
    Give every seed and prompt group of a unit a new identifier, keeping path IDs.

    Args:
        seeds (Sequence[Seed]): The unit's seeds.

    Returns:
        DecompositionIdentity: New identifiers for each seed and prompt group.
    """
    return DecompositionIdentity(
        seed_ids={seed.id: uuid.uuid4() for seed in seeds if seed.id is not None},
        group_ids={group_id: uuid.uuid4() for group_id in {seed.prompt_group_id for seed in seeds} if group_id},
        path_ids={},
    )


def apply_decomposition_identity(*, seeds: Sequence[Seed[Any]], identity: DecompositionIdentity) -> list[Seed[Any]]:
    """
    Copy a unit's seeds with their identifiers and references replaced.

    Args:
        seeds (Sequence[Seed]): The unit's seeds.
        identity (DecompositionIdentity): Replacement identifiers; unmapped ones are kept.

    Returns:
        list[Seed]: The copies, in the same order.
    """
    return [_apply_identity(seed=seed, identity=identity) for seed in seeds]


def assign_decomposition_identity(*, target: Seed[Any], source: Seed[Any]) -> None:
    """
    Give a caller's seed the identifiers of its stored copy.

    Args:
        target (Seed): The caller's seed, updated in place.
        source (Seed): The stored copy.
    """
    target.id = source.id
    target.prompt_group_id = source.prompt_group_id
    if isinstance(target, SeedObjective) and isinstance(source, SeedObjective):
        target.parent_objective_id = source.parent_objective_id
        target.paths = source.paths


def _apply_identity(*, seed: Seed[Any], identity: DecompositionIdentity) -> Seed[Any]:
    def mapped(value: uuid.UUID | None, mapping: dict[uuid.UUID, uuid.UUID]) -> uuid.UUID | None:
        return mapping.get(value, value) if value is not None else None

    update: dict[str, Any] = {
        "id": mapped(seed.id, identity.seed_ids),
        "prompt_group_id": mapped(seed.prompt_group_id, identity.group_ids),
    }
    if isinstance(seed, SeedObjective):
        update["parent_objective_id"] = mapped(seed.parent_objective_id, identity.seed_ids)
        update["paths"] = tuple(
            path.model_copy(
                update={
                    "id": identity.path_ids.get(path.id, path.id),
                    "steps": tuple(identity.seed_ids.get(step, step) for step in path.steps),
                }
            )
            for path in seed.paths
        )
    return seed.model_copy(update=update)


def _canonicalize_node(
    *,
    node_id: uuid.UUID,
    tree: DecompositionTree,
    objectives: dict[uuid.UUID, SeedObjective],
    mates: dict[uuid.UUID, list[Seed[Any]]],
    member_key: Callable[[Seed[Any]], str],
) -> tuple[dict[str, Any], list[Seed[Any]]]:
    objective = objectives[node_id]
    group = sorted(mates.get(objective.prompt_group_id, []) if objective.prompt_group_id else [], key=member_key)
    children = sorted(
        (
            _canonicalize_node(node_id=child_id, tree=tree, objectives=objectives, mates=mates, member_key=member_key)
            for child_id in tree.get_children(node_id)
        ),
        key=lambda child: _dumps(child[0]),
    )
    position = {child_seeds[0].id: index for index, (_, child_seeds) in enumerate(children)}
    form = {
        "objective": member_key(objective),
        "group": [member_key(mate) for mate in group],
        "children": [child_form for child_form, _ in children],
        "paths": [
            {"name": path.name, "description": path.description, "steps": [position[step] for step in path.steps]}
            for path in objective.paths
        ],
    }
    return form, [objective, *group, *(seed for _, child_seeds in children for seed in child_seeds)]


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
