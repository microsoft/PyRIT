# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Coverage-preserving sampling for scenario populations."""

import random
from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

from pyrit.models import AttackSeedGroup
from pyrit.models.dataset_limit import ResolvedDatasetLimit
from pyrit.scenario.core.dataset_configuration import DatasetConstraintError

_Key = TypeVar("_Key", bound=Hashable)


def sample_with_coverage(
    *,
    groups_by_dataset: dict[str, list[AttackSeedGroup]],
    cap: ResolvedDatasetLimit,
    required_keys: Sequence[_Key],
    key: Callable[[str, AttackSeedGroup], _Key],
) -> dict[str, list[AttackSeedGroup]]:
    """
    Reserve one group per key, then sample the remaining budget without replacement.

    Returns:
        dict[str, list[AttackSeedGroup]]: Selected original groups in population order.

    Raises:
        DatasetConstraintError: If a coverage key is missing or the budget is too small.
    """
    if cap != "all" and cap < max(1, len(required_keys)):
        raise DatasetConstraintError(
            f"max_total ({cap}) must be at least the number of coverage groups ({len(required_keys)})."
        )
    pairs = [(name, group) for name, groups in groups_by_dataset.items() for group in groups]
    indices_by_key: dict[_Key, list[int]] = {value: [] for value in required_keys}
    for index, (name, group) in enumerate(pairs):
        indices_by_key[key(name, group)].append(index)
    missing = [value for value, indices in indices_by_key.items() if not indices]
    if missing:
        raise DatasetConstraintError(f"No attack groups for required coverage keys: {missing}.")
    if cap == "all" or len(pairs) <= cap:
        return groups_by_dataset
    selected = {random.choice(indices) for indices in indices_by_key.values()}
    remaining = [index for index in range(len(pairs)) if index not in selected]
    selected.update(random.sample(remaining, cap - len(selected)))
    result: dict[str, list[AttackSeedGroup]] = {name: [] for name in groups_by_dataset}
    for index in sorted(selected):
        name, group = pairs[index]
        result[name].append(group)
    return result
