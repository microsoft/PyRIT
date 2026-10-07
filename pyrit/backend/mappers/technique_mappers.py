# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Safe catalog projection, independent of factory identity serialization."""

from pyrit.backend.models.techniques import TechniqueInstance
from pyrit.scenario.core import AttackTechniqueFactory


def technique_to_instance(*, name: str, factory: AttackTechniqueFactory) -> TechniqueInstance:
    """
    Map a real factory without constructing an attack or resolving default targets.

    Returns:
        TechniqueInstance: Safe settings for the registered factory.
    """
    return TechniqueInstance(
        name=name,
        description=factory.description,
        attack_type=factory.attack_class.__name__,
        tags=factory.technique_tags,
        uses_adversarial=factory.uses_adversarial,
        uses_default_adversarial_target=factory.uses_default_adversarial_target,
        configuration=factory.get_configuration(),
    )
