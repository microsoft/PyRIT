# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Coverage that the shared ``adversarial_chat`` schema reaches native-schema
targets as metadata and other targets as adapted text, without format rules
in the system prompt.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from unit.mocks import MockPromptTarget

from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackParameters,
    AttackScoringConfig,
    MultiTurnAttackContext,
    RedTeamingAttack,
    RTASystemPromptPaths,
)
from pyrit.executor.attack.component.modality_router import _ModalityFeedbackRouter
from pyrit.executor.attack.multi_turn.crescendo import (
    ConversationSession,
    CrescendoAttack,
    CrescendoAttackContext,
)
from pyrit.executor.attack.multi_turn.simulated_conversation import _generate_next_message_async
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TreeOfAttacksWithPruningAttack,
    _TreeOfAttacksNode,
)
from pyrit.models import (
    JSON_SCHEMA_METADATA_KEY,
    JsonResponseConfig,
    Message,
    MessagePiece,
    NextMessageSystemPromptPaths,
    ScoringExpectation,
    SeedPrompt,
    get_common_json_schema,
    load_next_message_prompt,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.score import TrueFalseScorer

# Text the JsonSchemaNormalizer appends when the target cannot enforce a schema
# natively, plus two properties unique to the shared adversarial_chat schema.
SCHEMA_MARKER = "conform to the following JSON schema"
SCHEMA_PROPERTIES = ('"next_message"', '"last_response_summary"')

# A schema-valid adversarial reply so the manager's send/parse cycle completes; the tests still assert
# on what actually reached the adversarial target (populated on send, before parsing).
_VALID_ADVERSARIAL_JSON = json.dumps(
    {"next_message": "crafted attack", "rationale": "because", "last_response_summary": "summary"}
)


class _JsonReturningTarget(MockPromptTarget):
    """A MockPromptTarget whose reply is schema-valid adversarial JSON.

    Crescendo and TAP now route the adversarial send through ``_AdversarialConversationManager``, which
    parses every reply against the ``adversarial_chat`` schema. The stock mock echoes a non-JSON
    ``"default"``, which the manager's json-retry would reject; returning valid JSON lets these tests
    drive the real send/parse path while still asserting the schema reached the target.
    """

    last_conversation: list[Message]

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.last_conversation = normalized_conversation
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        return [
            MessagePiece(
                role="assistant",
                original_value=_VALID_ADVERSARIAL_JSON,
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


def _adversarial_target(*, native_schema: bool) -> _JsonReturningTarget:
    target = _JsonReturningTarget()
    target.apply_capabilities(
        capabilities=target.capabilities.model_copy(
            update={"supports_json_schema": native_schema, "supports_json_output": native_schema}
        )
    )
    return target


def _assert_schema_delivery(target: _JsonReturningTarget) -> None:
    request = target.last_conversation[-1].message_pieces[0]
    schema = get_common_json_schema("adversarial_chat")
    if target.capabilities.supports_json_schema:
        assert JsonResponseConfig.from_metadata(metadata=request.prompt_metadata).json_schema == schema
        assert SCHEMA_MARKER not in request.converted_value
        assert all(prop not in request.converted_value for prop in SCHEMA_PROPERTIES)
    else:
        assert JSON_SCHEMA_METADATA_KEY not in request.prompt_metadata
        assert request.converted_value.count(SCHEMA_MARKER) == 1
        assert json.dumps(schema, indent=2) in request.converted_value

    system_text = "\n".join(message.get_value() for message in target.last_conversation if message.api_role == "system")
    assert system_text
    assert "JSON" not in system_text


@pytest.mark.parametrize("native_schema", [False, True])
async def test_crescendo_forwards_schema_to_adversarial_target(
    patch_central_database: MagicMock, native_schema: bool
) -> None:
    adversarial = _adversarial_target(native_schema=native_schema)
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

    manager = attack._build_adversarial_manager(context=context)
    await manager.set_adversarial_system_prompt_async(conversation_context="")
    await manager.generate_adversarial_reply_async(prompt_text="hello")

    _assert_schema_delivery(adversarial)


@pytest.mark.parametrize("native_schema", [False, True])
async def test_tap_forwards_schema_to_adversarial_target(
    patch_central_database: MagicMock, native_schema: bool
) -> None:
    adversarial = _adversarial_target(native_schema=native_schema)
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
        modality_router=_ModalityFeedbackRouter(adversarial_chat=adversarial, objective_target=objective),
        record_objective_conversation=lambda *, conversation_id: None,
        expectation=ScoringExpectation(objective="Test objective"),
    )

    await node._build_adversarial_manager().set_adversarial_system_prompt_async(
        desired_prefix="Sure, here is", conversation_context=""
    )
    await node._send_to_adversarial_chat_async(prompt_text="hello")

    _assert_schema_delivery(adversarial)


@pytest.mark.parametrize("native_schema", [False, True])
@pytest.mark.parametrize(
    "prompt_path",
    [
        RTASystemPromptPaths.TEXT_GENERATION.value,
        EXECUTOR_RED_TEAM_PATH.joinpath("role_play", "role_play_trivia_game.yaml"),
    ],
    ids=["text", "trivia"],
)
async def test_red_teaming_forwards_schema_to_adversarial_target(
    patch_central_database: MagicMock, native_schema: bool, prompt_path: Path
) -> None:
    adversarial = _adversarial_target(native_schema=native_schema)
    attack = RedTeamingAttack(
        objective_target=MockPromptTarget(),
        attack_scoring_config=AttackScoringConfig(objective_scorer=MagicMock(spec=TrueFalseScorer)),
        attack_adversarial_config=AttackAdversarialConfig(
            target=adversarial, system_prompt=SeedPrompt.from_yaml_file(prompt_path)
        ),
    )
    context = MultiTurnAttackContext(
        params=AttackParameters(objective="Test objective"),
        session=ConversationSession(),
    )
    manager = attack._build_adversarial_manager(context=context)
    await manager.set_adversarial_system_prompt_async()
    turn = await manager.get_next_message_async(turn_index=0)

    assert turn.objective_message.get_value() == "crafted attack"
    _assert_schema_delivery(adversarial)


@pytest.mark.parametrize("native_schema", [False, True])
@pytest.mark.parametrize("prompt_path", list(NextMessageSystemPromptPaths))
async def test_simulated_next_message_forwards_schema_to_adversarial_target(
    patch_central_database: MagicMock, native_schema: bool, prompt_path: NextMessageSystemPromptPaths
) -> None:
    adversarial = _adversarial_target(native_schema=native_schema)
    message = await _generate_next_message_async(
        objective="Test objective",
        conversation_messages=[Message.from_prompt(role="assistant", prompt="Previous response")],
        adversarial_chat=adversarial,
        conversation_id=str(uuid4()),
        next_message_system_prompt=load_next_message_prompt(prompt_path.value),
        prompt_normalizer=PromptNormalizer(),
    )

    assert message.get_value() == "crafted attack"
    assert "Previous response" in adversarial.system_prompt
    _assert_schema_delivery(adversarial)
