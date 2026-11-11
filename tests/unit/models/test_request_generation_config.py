# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any, ClassVar

import pytest

from pyrit.models import (
    CapabilityName,
    Message,
    MessagePiece,
    ParameterResolution,
    RequestGenerationConfig,
    RequestGenerationInfo,
)


@pytest.mark.parametrize("temperature", [None, 0, 0.7, 2])
def test_request_temperature_round_trip(temperature: float | None) -> None:
    config = RequestGenerationConfig(temperature=temperature)
    assert config.to_metadata() == (
        {} if temperature is None else {"request_generation_config": {"temperature": temperature}}
    )
    assert RequestGenerationConfig.from_metadata(metadata=config.to_metadata()) == config


@pytest.mark.parametrize("temperature", [True, False, "0.2", float("nan"), float("inf"), -float("inf"), [], {}])
def test_request_temperature_rejects_invalid_values(temperature: Any) -> None:
    metadata = {"request_generation_config": {"temperature": temperature}}
    with pytest.raises(ValueError):
        RequestGenerationConfig.from_metadata(metadata=metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
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
        RequestGenerationConfig.from_metadata(metadata={"request_generation_config": value})


def test_draft_only_metadata_is_not_interpreted() -> None:
    metadata = {"request_temperature": 0, "_pyrit_temperature": {"resolved_temperature": 0}}
    assert RequestGenerationConfig.from_metadata(metadata=metadata) == RequestGenerationConfig()
    assert RequestGenerationInfo.from_metadata(metadata=metadata) is None


def test_each_generation_field_has_a_capability() -> None:
    assert RequestGenerationConfig.FIELD_CAPABILITIES.keys() == RequestGenerationConfig.model_fields.keys()


@pytest.mark.parametrize("temperature", [None, 0, 1])
def test_only_non_null_options_require_capabilities(temperature: float | None) -> None:
    config = RequestGenerationConfig(temperature=temperature)
    expected = frozenset() if temperature is None else frozenset({CapabilityName.TEMPERATURE})
    assert config.required_capabilities == expected


@pytest.mark.parametrize("values", [[None, 0], [0, None], [0, 0.0], [None, None]])
def test_request_temperature_reads_all_pieces(values: list[float | None]) -> None:
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="hello",
                prompt_metadata=RequestGenerationConfig(temperature=value).to_metadata(),
            )
            for value in values
        ]
    )
    assert RequestGenerationConfig.from_message(message).temperature == (0 if 0 in values else None)


def test_request_temperature_rejects_conflicting_pieces() -> None:
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="hello",
                prompt_metadata=RequestGenerationConfig(temperature=value).to_metadata(),
            )
            for value in (0, 1)
        ]
    )
    with pytest.raises(ValueError, match="Conflicting"):
        RequestGenerationConfig.from_message(message)


def test_additional_field_uses_shared_aggregation_and_capability_mapping() -> None:
    class ExtendedConfig(RequestGenerationConfig):
        # An existing flag stands in for a future parameter capability in this plumbing-only test.
        FIELD_CAPABILITIES: ClassVar[dict[str, CapabilityName]] = {
            **RequestGenerationConfig.FIELD_CAPABILITIES,
            "additional_parameter": CapabilityName.JSON_OUTPUT,
        }
        additional_parameter: float | None = None

    message = Message(
        message_pieces=[
            MessagePiece(
                role="user", original_value="one", prompt_metadata=ExtendedConfig(temperature=0).to_metadata()
            ),
            MessagePiece(
                role="user",
                original_value="two",
                prompt_metadata=ExtendedConfig(additional_parameter=0.5).to_metadata(),
            ),
        ]
    )
    config = ExtendedConfig.from_message(message)
    assert config == ExtendedConfig(temperature=0, additional_parameter=0.5)
    assert config.required_capabilities == frozenset({CapabilityName.TEMPERATURE, CapabilityName.JSON_OUTPUT})
    message.get_piece().prompt_metadata.update(ExtendedConfig(additional_parameter=0).to_metadata())
    with pytest.raises(ValueError, match="Conflicting request additional_parameter"):
        ExtendedConfig.from_message(message)


@pytest.fixture
def info() -> RequestGenerationInfo:
    return RequestGenerationInfo(
        temperature=ParameterResolution(requested=0, resolved=0, source="request"),
        request_piece_ids=("request-id",),
    )


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


@pytest.mark.parametrize("field", ["requested", "resolved"])
@pytest.mark.parametrize("value", [True, "0", float("inf"), float("nan")])
def test_resolution_rejects_invalid_numbers(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        ParameterResolution.model_validate({"requested": 0, "resolved": 0, "source": "request", field: value})


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
        {
            "_pyrit_generation": {
                "temperature": {"requested": 0, "resolved": 0, "source": "request"},
                "extra": True,
            }
        },
        {"_pyrit_generation": []},
    ],
)
def test_malformed_evidence_is_rejected(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RequestGenerationInfo.from_metadata(metadata=metadata)


def test_unknown_provider_default_has_a_resolution_record() -> None:
    info = RequestGenerationInfo(
        temperature=ParameterResolution(requested=None, resolved=None, source="provider_default")
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
        "_pyrit_generation": {},
        "web_search_config": {"mode": "disabled"},
    }
    RequestGenerationConfig.clear_metadata(metadata)
    assert "_pyrit_generation" in metadata
    RequestGenerationInfo.clear_metadata(metadata)
    assert metadata == {"web_search_config": {"mode": "disabled"}}
