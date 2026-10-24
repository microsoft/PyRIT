# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

import pytest

from pyrit.models import Message, MessagePiece, RequestGenerationConfig, TemperatureRequestInfo


@pytest.mark.parametrize("temperature", [None, 0, 0.7, 2])
def test_request_temperature_round_trip(temperature: float | None) -> None:
    config = RequestGenerationConfig(temperature=temperature)
    assert RequestGenerationConfig.from_metadata(metadata=config.to_metadata()) == config


@pytest.mark.parametrize("temperature", [True, False, "0.2", float("nan"), float("inf"), -float("inf"), [], {}])
def test_request_temperature_rejects_invalid_values(temperature: Any) -> None:
    with pytest.raises(ValueError):
        RequestGenerationConfig.from_metadata(metadata={"request_temperature": temperature})


@pytest.mark.parametrize("metadata", [None, {}, {"request_temperature": None}])
def test_missing_temperature_is_not_an_override(metadata: dict[str, Any] | None) -> None:
    assert RequestGenerationConfig.from_metadata(metadata=metadata).temperature is None


@pytest.mark.parametrize("values", [[None, 0], [0, None], [0, 0.0], [None, None]])
def test_request_temperature_reads_all_pieces(values: list[float | None]) -> None:
    message = Message(
        message_pieces=[
            MessagePiece(role="user", original_value="hello", prompt_metadata={"request_temperature": value})
            for value in values
        ]
    )
    assert RequestGenerationConfig.from_message(message).temperature == (0 if 0 in values else None)


def test_request_temperature_rejects_conflicting_pieces() -> None:
    message = Message(
        message_pieces=[
            MessagePiece(role="user", original_value="hello", prompt_metadata={"request_temperature": value})
            for value in (0, 1)
        ]
    )
    with pytest.raises(ValueError, match="Conflicting"):
        RequestGenerationConfig.from_message(message)


def test_temperature_evidence_json_round_trip() -> None:
    info = TemperatureRequestInfo(
        requested_temperature=0, resolved_temperature=0, source="request", request_piece_ids=("request-id",)
    )
    assert TemperatureRequestInfo.from_metadata(metadata=json.loads(json.dumps(info.to_metadata()))) == info
    assert RequestGenerationConfig.from_metadata(metadata=info.to_metadata()).temperature is None
    assert TemperatureRequestInfo.from_metadata(metadata={}) is None
