# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for prepended-conversation policy owned by ``AttackStrategy``."""

import inspect
import uuid
from typing import Any

import pytest

from pyrit.executor.attack.component import PrependedConversationConfig
from pyrit.executor.attack.component.prepended_history_send_context import (
    PrependedHistorySendContext,
)
from pyrit.executor.attack.core import AttackStrategy
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.executor.attack.multi_turn.chunked_request import ChunkedRequestAttack
from pyrit.executor.attack.multi_turn.crescendo import CrescendoAttack
from pyrit.executor.attack.multi_turn.multi_prompt_sending import MultiPromptSendingAttack
from pyrit.executor.attack.multi_turn.pair import PAIRAttack
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack
from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack
from pyrit.message_normalizer import HistorySquashNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import CapabilityName, PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


class _NonEditableHistoryTarget(PromptTarget):
    _DEFAULT_CONFIGURATION = TargetConfiguration(capabilities=TargetCapabilities(supports_editable_history=False))

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1]
        return [
            MessagePiece(
                role="assistant",
                original_value="response",
                conversation_id=request.get_piece().conversation_id,
            ).to_message()
        ]


@pytest.mark.usefixtures("patch_central_database")
def test_attack_strategy_owns_prepended_policy_and_identifier() -> None:
    config = PrependedConversationConfig(apply_converters_to_roles=["user", "assistant"])
    attack = PromptSendingAttack(
        objective_target=_NonEditableHistoryTarget(),
        prepended_conversation_config=config,
    )

    identifier = attack.get_identifier()
    assert attack._prepended_conversation_config is config
    assert identifier.params["prepended_conversation_converter_roles"] == ["user", "assistant"]
    assert "prepended_conversation_formatter" in identifier.children


@pytest.mark.usefixtures("patch_central_database")
def test_attack_strategy_default_prepended_policy_preserves_identifier() -> None:
    attack = PromptSendingAttack(objective_target=_NonEditableHistoryTarget())

    identifier = attack.get_identifier()

    assert "prepended_conversation_converter_roles" not in identifier.params
    assert "prepended_conversation_formatter" not in identifier.children


@pytest.mark.usefixtures("patch_central_database")
def test_attack_strategy_resolves_per_send_history_override() -> None:
    attack = PromptSendingAttack(objective_target=_NonEditableHistoryTarget())
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(uuid.uuid4(),),
        replay_seed_each_send=False,
    )

    overrides = attack._get_prepended_normalizer_overrides(prepended_history_send_context=context)

    assert isinstance(overrides[CapabilityName.EDITABLE_HISTORY], HistorySquashNormalizer)


@pytest.mark.parametrize(
    "attack_class",
    [
        ChunkedRequestAttack,
        CrescendoAttack,
        MultiPromptSendingAttack,
        PAIRAttack,
        PromptSendingAttack,
        RedTeamingAttack,
        SkeletonKeyAttack,
        TreeOfAttacksWithPruningAttack,
    ],
)
def test_techniques_can_specify_prepended_policy(attack_class: type[AttackStrategy[Any, Any]]) -> None:
    """Each attack that creates or accepts prepended history exposes the policy."""
    parameter = inspect.signature(attack_class.__init__).parameters.get("prepended_conversation_config")

    assert parameter is not None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.usefixtures("patch_central_database")
def test_technique_factory_forwards_prepended_policy() -> None:
    config = PrependedConversationConfig(apply_converters_to_roles=["user", "assistant"])
    factory = AttackTechniqueFactory(
        name="policy_test",
        attack_class=PromptSendingAttack,
        attack_kwargs={"prepended_conversation_config": config},
    )

    attack = factory.create(
        objective_target=_NonEditableHistoryTarget(),
        attack_scoring_config=AttackScoringConfig(),
    ).attack

    assert attack._prepended_conversation_config is config
