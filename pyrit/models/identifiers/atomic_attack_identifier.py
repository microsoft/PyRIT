# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Composite identifier for an atomic attack run.

Combines an attack technique with the seed identifiers from the dataset. The
composite identifier has this shape::

    AtomicAttack
      ├── attack_technique  (class_name="AttackTechnique")
      │   ├── attack            (attack strategy's ComponentIdentifier)
      │   └── technique_seeds   (optional, list of seed ComponentIdentifiers)
      └── seed_identifiers      (list of ALL seed ComponentIdentifiers, for traceability)

When the atomic attack generates simulated conversations with its own adversarial
chat, that chat's behavioral eval hash is stored in ``params`` under
``SIMULATED_CONVERSATION_ADVERSARIAL_CHAT_EVAL_HASH_KEY``.
"""

from typing import TYPE_CHECKING, Annotated, ClassVar, Self

from pydantic import Field

from pyrit.models.identifiers.attack_identifier import AttackIdentifier
from pyrit.models.identifiers.attack_technique_identifier import AttackTechniqueIdentifier
from pyrit.models.identifiers.component_identifier import ComponentIdentifier, JSONValue
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.seed_identifier import SeedIdentifier, compute_seed_group_hash

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedGroup

# Class metadata for the composite identifier
_ATOMIC_ATTACK_CLASS_NAME = "AtomicAttack"
_ATOMIC_ATTACK_CLASS_MODULE = "pyrit.scenario.core.atomic_attack"

_ATTACK_TECHNIQUE_CLASS_NAME = "AttackTechnique"
_ATTACK_TECHNIQUE_CLASS_MODULE = "pyrit.scenario.core.attack_technique"


class AtomicAttackIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of an atomic attack's ``ComponentIdentifier``.

    Promotes the attack technique (``attack_technique``) and all seed identifiers
    from the dataset (``seed_identifiers``). ``seed_identifiers`` is excluded from
    the eval hash — it is present for traceability only.

    The simulated-conversation adversarial chat hash is an unpromoted param: it
    feeds the identity and eval hashes without adding a persisted identifier column.
    """

    #: ``params`` key for the behavioral eval hash of the adversarial chat that
    #: generates the atomic attack's simulated conversations.
    SIMULATED_CONVERSATION_ADVERSARIAL_CHAT_EVAL_HASH_KEY: ClassVar[str] = (
        "simulated_conversation_adversarial_chat_eval_hash"
    )

    #: The attack technique executed.
    attack_technique: Annotated[AttackTechniqueIdentifier | None, Evaluate.Include()] = None
    #: All seed identifiers from the dataset, for traceability.
    seed_identifiers: Annotated[list[SeedIdentifier], Evaluate.Exclude()] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        *,
        technique_identifier: ComponentIdentifier | None = None,
        attack_identifier: ComponentIdentifier | None = None,
        seed_group: "SeedGroup | None" = None,
        simulated_conversation_adversarial_chat_eval_hash: str | None = None,
    ) -> Self:
        """
        Build a composite AtomicAttackIdentifier for an atomic attack.

        The identifier places the attack technique in ``children["attack_technique"]``
        and all seeds from the seed group in ``children["seed_identifiers"]`` for traceability.
        A simulated-conversation adversarial chat hash, when given, is stored in ``params``.

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
            simulated_conversation_adversarial_chat_eval_hash: Behavioral eval hash of the
                adversarial chat the atomic attack uses to generate simulated conversations.
                ``None`` when the atomic attack generates none.

        Returns:
            A composite AtomicAttackIdentifier with class_name="AtomicAttack".

        Raises:
            ValueError: If both or neither of ``technique_identifier`` and
                ``attack_identifier`` are provided.
        """
        if technique_identifier is not None and attack_identifier is not None:
            raise ValueError("Provide technique_identifier or attack_identifier, not both")

        if technique_identifier is None:
            if attack_identifier is None:
                raise ValueError("Either technique_identifier or attack_identifier must be provided")
            technique_identifier = AttackTechniqueIdentifier(
                class_name=_ATTACK_TECHNIQUE_CLASS_NAME,
                class_module=_ATTACK_TECHNIQUE_CLASS_MODULE,
                attack=AttackIdentifier.from_component_identifier(attack_identifier),
            )

        technique = AttackTechniqueIdentifier.from_component_identifier(technique_identifier)

        seed_identifiers: list[SeedIdentifier] = []
        if seed_group is not None:
            seed_identifiers.extend(SeedIdentifier.from_seed(seed) for seed in seed_group.seeds)

        params: dict[str, JSONValue] = {}
        if simulated_conversation_adversarial_chat_eval_hash is not None:
            params[cls.SIMULATED_CONVERSATION_ADVERSARIAL_CHAT_EVAL_HASH_KEY] = (
                simulated_conversation_adversarial_chat_eval_hash
            )

        return cls(
            class_name=_ATOMIC_ATTACK_CLASS_NAME,
            class_module=_ATOMIC_ATTACK_CLASS_MODULE,
            params=params,
            attack_technique=technique,
            seed_identifiers=seed_identifiers,
        )

    @property
    def logical_seed_group_id(self) -> str:
        """The logical seed-group ID represented by the ordered seed identifiers."""
        return compute_seed_group_hash(self.seed_identifiers)
