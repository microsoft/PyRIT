# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Atomic attack identity builder functions.

Builds a composite ComponentIdentifier that uniquely identifies an attack run
by combining the attack strategy's identity with the seed identifiers from
the dataset.

The composite identifier has this shape:
    class_name = "AtomicAttack"
    children["attack"] = attack strategy's ComponentIdentifier
    children["technique_seeds"] = list of technique-only seed ComponentIdentifiers (optional)
    children["seeds"] = list of ALL seed ComponentIdentifiers (for traceability)
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from pyrit.identifiers.component_identifier import ComponentIdentifier

if TYPE_CHECKING:
    from pyrit.models.seeds.seed import Seed
    from pyrit.models.seeds.seed_attack_technique_group import SeedAttackTechniqueGroup
    from pyrit.models.seeds.seed_group import SeedGroup

logger = logging.getLogger(__name__)

# Class metadata for the composite identifier
_ATOMIC_ATTACK_CLASS_NAME = "AtomicAttack"
_ATOMIC_ATTACK_CLASS_MODULE = "pyrit.scenario.core.atomic_attack"


def build_seed_identifier(seed: "Seed") -> ComponentIdentifier:
    """
    Build a ComponentIdentifier from a seed's behavioral properties.

    Captures the seed's content hash, dataset name, and class type so that
    different seeds produce different identifiers while the same seed content
    always produces the same identifier.

    Args:
        seed: The seed to build an identifier for.

    Returns:
        An identifier capturing the seed's behavioral properties.
    """
    params: dict[str, Any] = {
        "value": seed.value,
        "value_sha256": seed.value_sha256,
        "dataset_name": seed.dataset_name,
        "is_general_technique": seed.is_general_technique,
    }

    return ComponentIdentifier(
        class_name=seed.__class__.__name__,
        class_module=seed.__class__.__module__,
        params=params,
    )


def build_atomic_attack_identifier(
    *,
    attack_identifier: ComponentIdentifier,
    seed_group: Optional["SeedGroup"] = None,
    seed_technique: Optional["SeedAttackTechniqueGroup"] = None,
) -> ComponentIdentifier:
    """
    Build a composite ComponentIdentifier for an atomic attack.

    The identifier always includes the attack strategy as ``children["attack"]``
    and all seeds from the seed group in ``children["seeds"]`` for traceability.

    When ``seed_technique`` is provided, its seeds are also included as
    ``children["technique_seeds"]``. These represent the reusable "how to attack"
    portion and are included in eval-hash computation, while ``seeds`` is excluded
    from the eval hash.

    Args:
        attack_identifier: The attack strategy's identifier.
        seed_group: The seed group to extract all seeds from.
        seed_technique: Optional technique seed group whose seeds are added
            as a separate ``technique_seeds`` child.

    Returns:
        A composite ComponentIdentifier with class_name="AtomicAttack".
    """
    seed_identifiers: list[ComponentIdentifier] = []
    if seed_group is not None:
        seed_identifiers.extend(build_seed_identifier(seed) for seed in seed_group.seeds)

    children: dict[str, Any] = {
        "attack": attack_identifier,
        "seeds": seed_identifiers,
    }

    if seed_technique is not None:
        technique_seed_ids = [build_seed_identifier(seed) for seed in seed_technique.seeds]
        if technique_seed_ids:
            children["technique_seeds"] = technique_seed_ids

    return ComponentIdentifier(
        class_name=_ATOMIC_ATTACK_CLASS_NAME,
        class_module=_ATOMIC_ATTACK_CLASS_MODULE,
        children=children,
    )
