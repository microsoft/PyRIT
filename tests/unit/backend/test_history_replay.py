# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from pyrit.backend.models.attacks import CreateAttackRequest
from pyrit.backend.routes.attacks import create_attack
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.history_replay import HistoryReplayError, validate_history_replay
from pyrit.models import Message, MessagePiece
from pyrit.models.target.target_capabilities import TargetCapabilities
from pyrit.prompt_target import PromptTarget, TargetConfiguration
from unit.mocks import MockPromptTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _mock_target(capabilities: TargetCapabilities) -> MagicMock:
    real_target = MockPromptTarget(custom_configuration=TargetConfiguration(capabilities=capabilities))
    target = MagicMock(spec=PromptTarget)
    target.capabilities = capabilities
    target.validate_history_replay.side_effect = real_target.validate_history_replay
    return target


@pytest.mark.parametrize(
    "error,status_code",
    [(HistoryReplayError("Cannot replay this history"), 400), (ValueError("Target not found"), 404)],
)
async def test_creation_distinguishes_unsupported_history_from_missing_targets_async(
    *, error: ValueError, status_code: int
) -> None:
    service = MagicMock(spec=AttackService)
    service.create_attack_async = AsyncMock(side_effect=error)
    with patch("pyrit.backend.routes.attacks.get_attack_service", return_value=service):
        with pytest.raises(HTTPException) as raised:
            await create_attack(request=CreateAttackRequest(target_registry_name="target"))
    assert raised.value.status_code == status_code
    assert raised.value.detail == str(error)


@pytest.mark.parametrize("multi_turn,editable", [(False, False), (True, False), (True, True)])
def test_empty_history_needs_no_replay_capability(*, multi_turn: bool, editable: bool) -> None:
    target = _mock_target(TargetCapabilities(supports_multi_turn=multi_turn, supports_editable_history=editable))
    validate_history_replay(target=target, messages=[])
    target.validate_history.assert_called_once_with([])


@pytest.mark.parametrize("role", ["user", "assistant", "simulated_assistant", "tool", "simulated_tool"])
def test_history_requires_target_replay_support(role: str) -> None:
    messages = [Message(message_pieces=[MessagePiece(role=role, original_value="History")])]
    for multi_turn in (False, True):
        target = _mock_target(TargetCapabilities(supports_multi_turn=multi_turn))
        with pytest.raises(ValueError, match="does not support editable history"):
            validate_history_replay(target=target, messages=messages)
        target.validate_history.assert_not_called()
    target = _mock_target(TargetCapabilities(supports_multi_turn=True, supports_editable_history=True))
    validate_history_replay(target=target, messages=messages)
    target.validate_history.assert_called_once_with(messages)


def test_initial_system_setup_is_not_assistant_history() -> None:
    messages = [
        Message(message_pieces=[MessagePiece(role="system", original_value="System setup")]),
        Message(message_pieces=[MessagePiece(role="developer", original_value="Developer setup")]),
    ]
    target = _mock_target(
        TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True, supports_editable_history=False)
    )
    validate_history_replay(target=target, messages=messages)
    target.validate_history.assert_called_once_with(messages)
    messages.append(Message(message_pieces=[MessagePiece(role="simulated_assistant", original_value="Reply")]))
    with pytest.raises(ValueError, match="editable history"):
        validate_history_replay(target=target, messages=messages)


def test_replay_still_validates_native_input_and_tool_history() -> None:
    target = _mock_target(TargetCapabilities(supports_multi_turn=True, supports_editable_history=True))
    target.validate_history.side_effect = ValueError("Unsupported history piece")
    with pytest.raises(ValueError, match="Unsupported history piece"):
        validate_history_replay(
            target=target,
            messages=[Message(message_pieces=[MessagePiece(role="user", original_value="History")])],
        )
