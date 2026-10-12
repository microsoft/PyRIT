# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.executor.attack import AttackAdversarialConfig, CrescendoAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import ConversationType, Message
from pyrit.prompt_target import OpenAIChatTarget


@pytest.mark.run_only_if_all_tests
@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("with_context", [False, True])
async def test_crescendo_context_guard_integration_async(*, sqlite_instance: SQLiteMemory, with_context: bool) -> None:
    adversarial_chat = OpenAIChatTarget(temperature=1.1)
    attack = CrescendoAttack(
        objective_target=OpenAIChatTarget(),
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial_chat),
        max_turns=3,
        max_backtracks=1,
    )
    prepended = (
        [
            Message.from_prompt(prompt="Let us discuss rainbows.", role="user"),
            Message.from_prompt(
                prompt="Rainbows are caused by light interacting with water droplets.", role="assistant"
            ),
        ]
        if with_context
        else None
    )

    result = await attack.execute_async(objective="Tell me a joke about programming.", prepended_conversation=prepended)
    references = result.get_conversations_by_type(ConversationType.ADVERSARIAL)
    assert references
    for reference in references:
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id=reference.conversation_id)
        system_prompts = [message.get_value() for message in messages if message.api_role == "system"]
        assert system_prompts
        assert all(("Prior Conversation Context" in prompt) == with_context for prompt in system_prompts)
        assert all("{% if conversation_context %}" not in prompt for prompt in system_prompts)
