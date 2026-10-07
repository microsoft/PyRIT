# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Server-declared structured inputs for technique construction."""

from functools import cache

from pyrit.models import ComponentType, Seed, SeedPrompt, SeedSimulatedConversation
from pyrit.registry.resolution import register_structured_input


@cache
def declare_technique_inputs() -> None:
    """Declare configuration types without creating targets, scorers, or attacks."""
    from pyrit.executor.attack.component.prepended_conversation_config import PrependedConversationConfig
    from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
    from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
    from pyrit.prompt_normalizer import ConverterConfiguration

    register_structured_input(
        base_type=AttackConverterConfig, variants={"AttackConverterConfig": AttackConverterConfig}
    )
    register_structured_input(
        base_type=ConverterConfiguration,
        variants={"ConverterConfiguration": ConverterConfiguration},
        references={"converters": ComponentType.CONVERTER},
    )
    register_structured_input(
        base_type=AttackScoringConfig,
        variants={"AttackScoringConfig": AttackScoringConfig, "TAPAttackScoringConfig": TAPAttackScoringConfig},
        references={
            "objective_scorer": ComponentType.SCORER,
            "refusal_scorer": ComponentType.SCORER,
            "auxiliary_scorers": ComponentType.SCORER,
        },
    )
    register_structured_input(
        base_type=TAPAttackScoringConfig, variants={"TAPAttackScoringConfig": TAPAttackScoringConfig}
    )
    register_structured_input(
        base_type=PrependedConversationConfig, variants={"PrependedConversationConfig": PrependedConversationConfig}
    )
    register_structured_input(base_type=SeedPrompt, variants={"SeedPrompt": SeedPrompt})
    register_structured_input(
        base_type=SeedSimulatedConversation, variants={"SeedSimulatedConversation": SeedSimulatedConversation}
    )
    register_structured_input(
        base_type=Seed, variants={"SeedPrompt": SeedPrompt, "SeedSimulatedConversation": SeedSimulatedConversation}
    )
