# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Atomic attack identity builder functions.

Builds a composite ComponentIdentifier that uniquely identifies an attack run
by combining the attack strategy's identity with the seed identifiers from
the dataset.

The composite identifier has this shape::

    AtomicAttack
      ├── attack_technique  (class_name="AttackTechnique")
      │   ├── attack            (attack strategy's ComponentIdentifier)
      │   └── technique_seeds   (optional, list of seed ComponentIdentifiers)
      └── seed_identifiers      (list of ALL seed ComponentIdentifiers, for traceability)
"""

import logging
from typing import TYPE_CHECKING, Any

from pyrit.identifiers.component_identifier import ComponentIdentifier

if TYPE_CHECKING:
    from pyrit.models.seeds.seed import Seed
    from pyrit.models.seeds.seed_group import SeedGroup

logger = logging.getLogger(__name__)

# Class metadata for the composite identifier
_ATOMIC_ATTACK_CLASS_NAME = "AtomicAttack"
_ATOMIC_ATTACK_CLASS_MODULE = "pyrit.scenario.core.atomic_attack"

_ATTACK_TECHNIQUE_CLASS_NAME = "AttackTechnique"
_ATTACK_TECHNIQUE_CLASS_MODULE = "pyrit.scenario.core.attack_technique"


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
    technique_identifier: ComponentIdentifier | None = None,
    attack_identifier: ComponentIdentifier | None = None,
    seed_group: "SeedGroup | None" = None,
) -> ComponentIdentifier:
    """
    Build a composite ComponentIdentifier for an atomic attack.

    The identifier places the attack technique in ``children["attack_technique"]``
    and all seeds from the seed group in ``children["seed_identifiers"]`` for traceability.

    Callers that have an ``AttackTechnique`` object should pass
    ``technique_identifier=attack_technique.get_identifier()``.
    Callers that only have a raw attack strategy identifier (e.g. legacy
    backward-compat paths) can pass ``attack_identifier`` instead, which is
    wrapped in a minimal technique node automatically.

    Args:
        technique_identifier: Pre-built technique identifier from
            ``AttackTechnique.get_identifier()``. Mutually exclusive with
            ``attack_identifier``.
        attack_identifier: Raw attack strategy identifier. Used when no
            ``AttackTechnique`` instance is available. Mutually exclusive
            with ``technique_identifier``.
        seed_group: The seed group to extract all seeds from.

    Returns:
        A composite ComponentIdentifier with class_name="AtomicAttack".

    Raises:
        ValueError: If both or neither of ``technique_identifier`` and
            ``attack_identifier`` are provided.
    """
    if technique_identifier is not None and attack_identifier is not None:
        raise ValueError("Provide technique_identifier or attack_identifier, not both")

    if technique_identifier is None:
        if attack_identifier is None:
            raise ValueError("Either technique_identifier or attack_identifier must be provided")
        technique_identifier = ComponentIdentifier(
            class_name=_ATTACK_TECHNIQUE_CLASS_NAME,
            class_module=_ATTACK_TECHNIQUE_CLASS_MODULE,
            children={"attack": attack_identifier},
        )

    seed_identifiers: list[ComponentIdentifier] = []
    if seed_group is not None:
        seed_identifiers.extend(build_seed_identifier(seed) for seed in seed_group.seeds)

    children: dict[str, Any] = {
        "attack_technique": technique_identifier,
        "seed_identifiers": seed_identifiers,
    }

    return ComponentIdentifier(
        class_name=_ATOMIC_ATTACK_CLASS_NAME,
        class_module=_ATOMIC_ATTACK_CLASS_MODULE,
        children=children,
    )
