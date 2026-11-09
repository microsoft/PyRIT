# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared send-boundary requirements for adapters opting into runtime generation."""

from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.models import (
    CapabilityName,
    Message,
    ParameterResolution,
    RequestGenerationConfig,
    RequestGenerationInfo,
    TargetCapabilities,
)
from pyrit.prompt_target import TargetConfiguration


class _GenerationTarget(MockPromptTarget):
    _SUPPORTED_GENERATION_CAPABILITIES: ClassVar[frozenset[CapabilityName]] = frozenset({CapabilityName.TEMPERATURE})
    _DEFAULT_CONFIGURATION = TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=True))


@pytest.fixture
def target(patch_central_database: Any) -> _GenerationTarget:
    return _GenerationTarget()


def request() -> Message:
    message = Message.from_prompt(prompt="hello", role="user")
    message.get_piece().prompt_metadata.update(RequestGenerationConfig(temperature=0).to_metadata())
    return message


@pytest.mark.parametrize(
    "resolution",
    [
        None,
        ParameterResolution(requested=None, resolved=0, source="target_default"),
        ParameterResolution(requested=0, resolved=0.8, source="request"),
        ParameterResolution(requested=0, resolved=0, source="target_default"),
    ],
)
async def test_adapter_cannot_silently_drop_or_misreport_an_override_async(
    target: _GenerationTarget, resolution: ParameterResolution | None
) -> None:
    async def send_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if resolution is not None:
            normalized_conversation[-1].get_piece().prompt_metadata.update(
                RequestGenerationInfo(temperature=resolution).to_metadata()
            )
        return [Message.from_prompt(prompt="reply", role="assistant")]

    with patch.object(target, "_send_prompt_to_target_async", side_effect=send_async):
        with pytest.raises(RuntimeError, match="generation evidence.*temperature"):
            await target.send_prompt_async(message=request())


async def test_base_rechecks_generation_support_after_normalization_async(target: _GenerationTarget) -> None:
    async def normalize_async(**kwargs: Any) -> list[Message]:
        target.apply_capabilities(capabilities=TargetCapabilities(supports_temperature=False))
        return [kwargs["message"]]

    with (
        patch.object(target, "_get_normalized_conversation_async", side_effect=normalize_async),
        patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send,
    ):
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            await target.send_prompt_async(message=request())
    send.assert_not_called()


async def test_base_associates_adapter_evidence_with_request_and_responses_async(target: _GenerationTarget) -> None:
    message = request()

    async def send_async(*, normalized_conversation: list[Message]) -> list[Message]:
        normalized_conversation[-1].get_piece().prompt_metadata.update(
            RequestGenerationInfo(
                temperature=ParameterResolution(requested=0, resolved=0, source="request")
            ).to_metadata()
        )
        return [Message.from_prompt(prompt="reply", role="assistant")]

    with patch.object(target, "_send_prompt_to_target_async", side_effect=send_async):
        [response] = await target.send_prompt_async(message=message)
    info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
    assert info is not None
    assert info.request_piece_ids == (str(message.get_piece().id),)
    assert RequestGenerationInfo.from_metadata(metadata=response.get_piece().prompt_metadata) == info
    assert RequestGenerationConfig.METADATA_KEY not in response.get_piece().prompt_metadata


async def test_missing_evidence_does_not_mask_adapter_failure_async(target: _GenerationTarget) -> None:
    with patch.object(
        target, "_send_prompt_to_target_async", new_callable=AsyncMock, side_effect=ValueError("Preparation failed")
    ):
        with pytest.raises(ValueError, match="Preparation failed"):
            await target.send_prompt_async(message=request())
