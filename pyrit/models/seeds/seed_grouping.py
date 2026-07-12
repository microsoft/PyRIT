# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Helpers for grouping flat seeds into structured groups.

These mirror the ``group_message_pieces_into_conversations`` helpers for
``MessagePiece`` (``pyrit.models.messages.conversations``): a flat list of
seeds is regrouped by ``prompt_group_id`` -- the seed analog of
``conversation_id`` -- back into validated group objects. Construction of the
group object *is* the validation: ``SeedAttackGroup`` enforces exactly one
objective, consistent group id, and role/sequence rules on init, so a malformed
grouping raises there rather than via a separate hand-rolled check.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, cast

from pyrit.models.seeds.seed_attack_group import SeedAttackGroup

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.seeds.seed import Seed
    from pyrit.models.seeds.seed_group import SeedUnion


def group_seeds_into_attack_groups(seeds: Sequence[Seed]) -> list[SeedAttackGroup]:
    """
    Group flat seeds by ``prompt_group_id`` into ``SeedAttackGroup`` instances.

    Seeds sharing a ``prompt_group_id`` are collapsed into a single
    ``SeedAttackGroup``; seeds without one (``prompt_group_id is None``) each
    become their own group. Within a group, seeds are ordered by ``sequence``
    when available before construction.

    Construction validates the grouping: ``SeedAttackGroup`` requires exactly one
    objective per group (plus the inherited ``SeedGroup`` invariants), so a group
    that lacks an objective -- or otherwise violates the invariants -- raises a
    ``ValueError`` here. This is intentional: callers that want stored groupings
    turned into attack groups get a fail-fast error on malformed data.

    Args:
        seeds (Sequence[Seed]): The flat seeds to group.

    Returns:
        list[SeedAttackGroup]: One attack group per ``prompt_group_id`` (and one
            per ungrouped seed), each self-validated on construction.

    Raises:
        ValueError: If any resulting group does not satisfy ``SeedAttackGroup``'s
            invariants (e.g. it has no objective or more than one).
    """
    grouped_seeds: dict[uuid.UUID, list[Seed]] = defaultdict(list)
    for seed in seeds:
        group_id = seed.prompt_group_id if seed.prompt_group_id is not None else uuid.uuid4()
        grouped_seeds[group_id].append(seed)

    attack_groups: list[SeedAttackGroup] = []
    for group_seeds in grouped_seeds.values():
        if len(group_seeds) > 1:
            group_seeds.sort(key=lambda s: getattr(s, "sequence", None) or 0)
        attack_groups.append(SeedAttackGroup(seeds=cast("list[SeedUnion]", group_seeds)))

    return attack_groups
