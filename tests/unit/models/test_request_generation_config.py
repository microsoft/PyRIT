# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from copy import deepcopy
from typing import Any

import pytest

from pyrit.models import (
    Message,
    MessagePiece,
    RequestGenerationConfig,
    RequestGenerationInfo,
    TemperatureResolution,
)


@pytest.mark.parametrize("temperature", [None, 0, 0.7, 2])
def test_request_temperature_round_trip(temperature: float | None) -> None:
    config = RequestGenerationConfig(temperature=temperature)
    assert config.to_metadata() == (
        {} if temperature is None else {"request_generation_config": {"temperature": temperature}}
    )
    assert RequestGenerationConfig.from_metadata(metadata=config.to_metadata()) == config


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("temperature", [True, False, "0.2", float("nan"), float("inf"), -float("inf"), [], {}])
def test_request_temperature_rejects_invalid_values(temperature: Any, legacy: bool) -> None:
    metadata = (
        {"request_temperature": temperature} if legacy else {"request_generation_config": {"temperature": temperature}}
    )
    with pytest.raises(ValueError):
        RequestGenerationConfig.from_metadata(metadata=metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"request_temperature": None},
        {"request_generation_config": None},
        {"request_generation_config": {}},
        {"request_generation_config": {"temperature": None}},
    ],
)
def test_missing_temperature_is_not_an_override(metadata: dict[str, Any] | None) -> None:
    assert RequestGenerationConfig.from_metadata(metadata=metadata).temperature is None


@pytest.mark.parametrize("value", [0, False, "", [], {"top_p": 0.9}])
def test_request_generation_rejects_invalid_envelopes(value: Any) -> None:
    with pytest.raises(ValueError):
        RequestGenerationConfig.from_metadata(metadata={"request_generation_config": value, "request_temperature": 0})


@pytest.mark.parametrize(("current", "legacy", "expected"), [(0, 0.0, 0), (None, 0, 0), (0, None, 0)])
def test_current_and_legacy_request_options_agree(current: float | None, legacy: float | None, expected: float) -> None:
    metadata = {"request_generation_config": {"temperature": current}, "request_temperature": legacy}
    original = deepcopy(metadata)
    config = RequestGenerationConfig.from_metadata(metadata=metadata)
    assert config.temperature == expected
    assert config.to_metadata() == {"request_generation_config": {"temperature": expected}}
    assert metadata == original


@pytest.mark.parametrize("legacy", [1, True, "0", float("nan")])
def test_current_request_does_not_hide_conflicting_or_invalid_legacy_value(legacy: Any) -> None:
    with pytest.raises(ValueError):
        RequestGenerationConfig.from_metadata(
            metadata={"request_generation_config": {"temperature": 0}, "request_temperature": legacy}
        )


def test_legacy_request_is_read_but_not_written() -> None:
    config = RequestGenerationConfig.from_metadata(metadata={"request_temperature": 0})
    assert config.to_metadata() == {"request_generation_config": {"temperature": 0}}


@pytest.mark.parametrize("values", [[None, 0], [0, None], [0, 0.0], [None, None]])
@pytest.mark.parametrize("legacy_first", [False, True])
def test_request_temperature_reads_all_pieces(values: list[float | None], legacy_first: bool) -> None:
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="hello",
                prompt_metadata=(
                    {"request_temperature": value}
                    if legacy_first and index == 0
                    else RequestGenerationConfig(temperature=value).to_metadata()
                ),
            )
            for index, value in enumerate(values)
        ]
    )
    assert RequestGenerationConfig.from_message(message).temperature == (0 if 0 in values else None)


@pytest.mark.parametrize("legacy_first", [False, True])
def test_request_temperature_rejects_conflicting_pieces(legacy_first: bool) -> None:
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="hello",
                prompt_metadata=(
                    {"request_temperature": value}
                    if legacy_first and value == 0
                    else RequestGenerationConfig(temperature=value).to_metadata()
                ),
            )
            for value in (0, 1)
        ]
    )
    with pytest.raises(ValueError, match="Conflicting"):
        RequestGenerationConfig.from_message(message)


@pytest.fixture
def info() -> RequestGenerationInfo:
    return RequestGenerationInfo(
        temperature=TemperatureResolution(requested=0, resolved=0, source="request"),
        request_piece_ids=("request-id",),
    )


@pytest.fixture
def legacy_record() -> dict[str, Any]:
    return {
        "requested_temperature": 0,
        "resolved_temperature": 0,
        "source": "request",
        "request_piece_ids": ["request-id"],
    }


def test_generation_evidence_json_round_trip(info: RequestGenerationInfo) -> None:
    assert info.to_metadata() == {
        "_pyrit_generation": {
            "temperature": {"requested": 0, "resolved": 0, "source": "request"},
            "request_piece_ids": ["request-id"],
        }
    }
    assert RequestGenerationInfo.from_metadata(metadata=json.loads(json.dumps(info.to_metadata()))) == info
    assert RequestGenerationConfig.from_metadata(metadata=info.to_metadata()).temperature is None
    assert RequestGenerationInfo.from_metadata(metadata={}) is None


def test_legacy_evidence_is_read_without_rewriting_history(
    info: RequestGenerationInfo, legacy_record: dict[str, Any]
) -> None:
    original = deepcopy(legacy_record)
    read = RequestGenerationInfo.from_metadata(metadata={"_pyrit_temperature": legacy_record})
    assert read == info
    assert read.to_metadata() == info.to_metadata()
    assert legacy_record == original
    assert (
        RequestGenerationInfo.from_metadata(metadata={**info.to_metadata(), "_pyrit_temperature": legacy_record})
        == info
    )


@pytest.mark.parametrize(
    "change",
    [
        {"requested_temperature": 0.5},
        {"resolved_temperature": 1},
        {"source": "target_default"},
        {"request_piece_ids": ["different-id"]},
    ],
)
def test_conflicting_evidence_is_rejected(
    info: RequestGenerationInfo, legacy_record: dict[str, Any], change: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="Conflicting"):
        RequestGenerationInfo.from_metadata(
            metadata={**info.to_metadata(), "_pyrit_temperature": {**legacy_record, **change}}
        )


@pytest.mark.parametrize("field", ["requested", "resolved"])
@pytest.mark.parametrize("value", [True, "0", float("inf"), float("nan")])
def test_resolution_rejects_invalid_numbers(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        TemperatureResolution.model_validate({"requested": 0, "resolved": 0, "source": "request", field: value})


@pytest.mark.parametrize(
    "metadata",
    [
        {"_pyrit_generation": {}},
        {"_pyrit_generation": {"temperature": {"resolved": 0, "source": "request"}}},
        {"_pyrit_generation": {"temperature": {"requested": 0, "resolved": 0, "source": "unknown"}}},
        {
            "_pyrit_generation": {
                "temperature": {"requested": 0, "resolved": 0, "source": "request"},
                "request_piece_ids": [1],
            }
        },
        {"_pyrit_temperature": {"resolved_temperature": 0, "source": "request"}},
        {
            "_pyrit_temperature": {
                "requested_temperature": 0,
                "resolved_temperature": 0,
                "source": "request",
                "extra": True,
            }
        },
        {"_pyrit_temperature": []},
    ],
)
def test_malformed_evidence_is_rejected(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RequestGenerationInfo.from_metadata(metadata=metadata)


def test_valid_evidence_does_not_hide_malformed_other_format(
    info: RequestGenerationInfo, legacy_record: dict[str, Any]
) -> None:
    for metadata in (
        {**info.to_metadata(), "_pyrit_temperature": {}},
        {"_pyrit_generation": {}, "_pyrit_temperature": legacy_record},
    ):
        with pytest.raises(ValueError):
            RequestGenerationInfo.from_metadata(metadata=metadata)


def test_unknown_provider_default_has_a_resolution_record() -> None:
    info = RequestGenerationInfo(
        temperature=TemperatureResolution(requested=None, resolved=None, source="provider_default")
    )
    assert RequestGenerationInfo.from_metadata(metadata=info.to_metadata()) == info


def test_generation_models_are_frozen(info: RequestGenerationInfo) -> None:
    for model, field, value in (
        (RequestGenerationConfig(), "temperature", 1),
        (info, "request_piece_ids", ()),
        (info.temperature, "resolved", 1),
    ):
        with pytest.raises(ValueError, match="frozen"):
            setattr(model, field, value)


def test_clearing_owned_metadata_preserves_unrelated_options() -> None:
    metadata: dict[str, Any] = {
        "request_generation_config": {"temperature": 0},
        "request_temperature": 1,
        "_pyrit_generation": {},
        "_pyrit_temperature": {},
        "web_search_config": {"mode": "disabled"},
    }
    RequestGenerationConfig.clear_metadata(metadata)
    assert "_pyrit_generation" in metadata
    RequestGenerationInfo.clear_metadata(metadata)
    assert metadata == {"web_search_config": {"mode": "disabled"}}
