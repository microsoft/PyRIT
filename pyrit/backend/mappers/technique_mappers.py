# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Safe settings and the evaluation identity of registered technique factories."""

from pyrit.backend.models.techniques import TechniqueInstance
from pyrit.models import EvaluationIdentifier
from pyrit.scenario.core import AttackTechniqueFactory


def technique_to_instance(*, name: str, factory: AttackTechniqueFactory) -> TechniqueInstance:
    """
    Map a real factory without constructing an attack or resolving default targets.

    Returns:
        TechniqueInstance: Safe settings and factory identity, without deferred execution inputs.
    """
    identifier = factory.get_identifier()
    return TechniqueInstance(
        name=name,
        description=factory.description,
        attack_type=factory.attack_class.__name__,
        tags=factory.technique_tags,
        uses_adversarial=factory.uses_adversarial,
        uses_default_adversarial_target=factory.uses_default_adversarial_target,
        configuration=factory.get_configuration(),
        evaluation_identifier=identifier.with_eval_hash(EvaluationIdentifier(identifier).eval_hash),
    )
