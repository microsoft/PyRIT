# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end coverage that the shared ``adversarial_chat`` JSON schema reaches the
adversarial chat target. Crescendo, TAP, and PAIR attach the schema to the
outgoing message via ``prompt_metadata`` and rely on the target's conversation
normalization pipeline to consume it. ``MockPromptTarget`` does not natively
support the JSON_SCHEMA capability, so the ``JsonSchemaNormalizer`` renders the
schema into the prompt text the target receives -- which is what these tests
assert.
"""

from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackAdversarialConfig, AttackParameters
from pyrit.executor.attack.multi_turn.crescendo import (
    ConversationSession,
    CrescendoAttack,
    CrescendoAttackContext,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TreeOfAttacksWithPruningAttack,
    _TreeOfAttacksNode,
)
from pyrit.prompt_normalizer import PromptNormalizer

# Text the JsonSchemaNormalizer appends when the target cannot enforce a schema
# natively, plus two properties unique to the shared adversarial_chat schema.
SCHEMA_MARKER = "conform to the following JSON schema"
SCHEMA_PROPERTIES = ('"next_message"', '"last_response_summary"')


async def test_crescendo_forwards_schema_to_adversarial_target(patch_central_database):
    adversarial = MockPromptTarget()
    objective = MockPromptTarget()

    attack = CrescendoAttack(
        objective_target=objective,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
        prompt_normalizer=PromptNormalizer(),
    )

    assert attack._adversarial_chat_system_prompt_template.response_json_schema is not None

    context = CrescendoAttackContext(
        params=AttackParameters(objective="Test objective"),
        session=ConversationSession(),
    )

    await attack._send_prompt_to_adversarial_chat_async(prompt_text="hello", context=context)

    assert adversarial.prompt_sent, "adversarial chat received nothing"
    sent = adversarial.prompt_sent[-1]
    assert SCHEMA_MARKER in sent
    assert all(prop in sent for prop in SCHEMA_PROPERTIES)


async def test_tap_forwards_schema_to_adversarial_target(patch_central_database):
    adversarial = MockPromptTarget()
    objective = MockPromptTarget()

    attack = TreeOfAttacksWithPruningAttack(
        objective_target=objective,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
    )

    system_seed = attack._adversarial_chat_system_seed_prompt
    assert system_seed.response_json_schema is not None

    node = _TreeOfAttacksNode(
        objective_target=objective,
        adversarial_chat=adversarial,
        adversarial_chat_seed_prompt=attack._adversarial_chat_seed_prompt,
        adversarial_chat_system_seed_prompt=system_seed,
        adversarial_chat_prompt_template=attack._adversarial_chat_prompt_template,
        objective_scorer=attack._objective_scorer,
        desired_response_prefix="Sure, here is",
        prompt_normalizer=PromptNormalizer(),
        on_topic_scorer=None,
        request_converters=[],
        response_converters=[],
        auxiliary_scorers=[],
        attack_id=attack.get_identifier(),
        attack_strategy_name="TreeOfAttacksWithPruningAttack",
    )

    await node._send_to_adversarial_chat_async(prompt_text="hello")

    assert adversarial.prompt_sent, "adversarial chat received nothing"
    sent = adversarial.prompt_sent[-1]
    assert SCHEMA_MARKER in sent
    assert all(prop in sent for prop in SCHEMA_PROPERTIES)
