# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.converter import ConverterResult, SuffixAppendConverter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackExecutor,
    PromptSendingAttack,
    generate_simulated_conversation_async,
)
from pyrit.executor.attack.component import PrependedConversationConfig
from pyrit.models import (
    AttackSeedGroup,
    Message,
    PromptDataType,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.score import SubStringScorer


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("convert_assistant_history", [False, True])
@pytest.mark.parametrize("use_seed_group", [False, True])
async def test_simulated_history_uses_replay_attack_converters_async(
    *, convert_assistant_history: bool, use_seed_group: bool
) -> None:
    model = MockPromptTarget()
    objective_target = MockPromptTarget()
    converter = SuffixAppendConverter(suffix="test translation")
    attack = PromptSendingAttack(
        objective_target=objective_target,
        attack_converter_config=AttackConverterConfig(
            request_converters=[ConverterConfiguration(converters=[converter])]
        ),
        prepended_conversation_config=(
            PrependedConversationConfig(apply_converters_to_roles=["user", "assistant"])
            if convert_assistant_history
            else None
        ),
    )
    config = SeedSimulatedConversation(
        num_turns=1,
        adversarial_chat_system_prompt=SeedPrompt(value="Adversarial: {{ objective }}", parameters=["objective"]),
        simulated_target_system_prompt=SeedPrompt(
            value="Simulated: {{ objective }}; {{ num_turns }}", parameters=["objective", "num_turns"]
        ),
        next_message_system_prompt=SeedPrompt(
            value="Next: {{ objective }}; {{ conversation_context }}", parameters=["objective", "conversation_context"]
        ),
    )
    replies = iter(
        [
            json.dumps({"next_message": "Hello", "rationale": "test", "last_response_summary": "test"}),
            "Simulated reply",
            json.dumps({"next_message": "Next message", "rationale": "test", "last_response_summary": "test"}),
        ]
    )
    simulation_requests: list[list[Message]] = []
    target_requests: list[list[Message]] = []

    async def simulate_async(*, normalized_conversation: list[Message]) -> list[Message]:
        simulation_requests.append(normalized_conversation)
        return [Message.from_prompt(role="assistant", prompt=next(replies))]

    async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
        target_requests.append(normalized_conversation)
        return [Message.from_prompt(role="assistant", prompt="Target reply")]

    async def translate_async(*, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text=f"es({prompt})", output_type=input_type)

    with (
        patch.object(model, "_send_prompt_to_target_async", new=AsyncMock(side_effect=simulate_async)),
        patch.object(objective_target, "_send_prompt_to_target_async", new=AsyncMock(side_effect=respond_async)),
        patch.object(converter, "convert_async", new=AsyncMock(side_effect=translate_async)) as convert,
    ):
        scorer = SubStringScorer(substring="Simulated reply")
        if use_seed_group:
            result = await AttackExecutor().execute_attack_from_seed_groups_async(
                attack=attack,
                seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="Test objective"), config])],
                adversarial_chat=model,
                objective_scorer=scorer,
            )
            assert result.incomplete_objectives == []
            assert len(result.completed_results) == 1
        else:
            simulated = await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=model,
                objective_scorer=scorer,
                num_turns=config.num_turns,
                adversarial_chat_system_prompt=config.adversarial_chat_system_prompt,
                simulated_target_system_prompt=config.simulated_target_system_prompt,
                next_message_system_prompt=config.next_message_system_prompt,
            )
            group = SeedGroup(seeds=simulated.seed_prompts)
            await attack.execute_async(
                objective="Test objective",
                prepended_conversation=group.prepended_conversation,
                next_message=group.next_message,
                source_conversations=simulated.related_conversations,
            )

    assert len(simulation_requests) == 3
    assert simulation_requests[0][0].get_value() == "Adversarial: Test objective"
    assert simulation_requests[1][0].get_value() == "Simulated: Test objective; 1"
    assert simulation_requests[1][-1].get_value() == "Hello"
    assert "Hello" in simulation_requests[2][0].get_value()
    assert "Simulated reply" in simulation_requests[2][0].get_value()
    assert "es(" not in simulation_requests[2][0].get_value()
    assert len(target_requests) == 1
    history = target_requests[0]
    assert [message.api_role for message in history] == ["user", "assistant", "user"]
    assert [message.get_value() for message in history] == [
        "es(Hello)",
        "es(Simulated reply)" if convert_assistant_history else "Simulated reply",
        "es(Next message)",
    ]
    expected_calls = (
        ["Hello", "Simulated reply", "Next message"] if convert_assistant_history else ["Hello", "Next message"]
    )
    assert [call.kwargs["prompt"] for call in convert.await_args_list] == expected_calls
