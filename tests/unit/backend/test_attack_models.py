# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request contracts for exact converter preview results."""

from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pyrit.backend.models.attacks import (
    AddMessageRequest,
    CreateAttackRequest,
    MessagePieceRequest,
    SaveConversationRequest,
)
from pyrit.models import PromptDataType
from pyrit.models.catalog.scenario import RunScenarioRequest


def _validate_new_activity(
    kind: str, attribution: dict
) -> CreateAttackRequest | SaveConversationRequest | RunScenarioRequest:
    if kind == "create":
        return CreateAttackRequest.model_validate(attribution)
    if kind == "save":
        return SaveConversationRequest.model_validate(
            {"save_id": str(uuid4()), "destination": "new_attack", **attribution}
        )
    return RunScenarioRequest.model_validate({"scenario_name": "test", "target_name": "target", **attribution})


@pytest.mark.parametrize("kind", ["create", "save", "scan"])
def test_new_activity_rejects_malformed_operation_id(kind: str) -> None:
    with pytest.raises(ValidationError):
        _validate_new_activity(kind, {"operation_id": "not-a-uuid"})


@pytest.mark.parametrize("kind", ["create", "save"])
@pytest.mark.parametrize("attribution", [{"operation": "Name"}, {"labels": {"operation": "Name"}}])
def test_attack_requests_keep_deprecated_operation_name_for_resolution(kind: str, attribution: dict) -> None:
    request = _validate_new_activity(kind, attribution)
    assert isinstance(request, CreateAttackRequest | SaveConversationRequest)
    assert (request.operation, request.labels or {}) == ("Name", {})


@pytest.mark.parametrize("kind", ["create", "save", "scan"])
@pytest.mark.parametrize("assigned", [False, True])
def test_new_activity_has_typed_optional_operation_id(kind: str, assigned: bool) -> None:
    operation_id = uuid4() if assigned else None
    attribution = {"operation_id": str(operation_id)} if assigned else {}
    assert _validate_new_activity(kind, attribution).operation_id == operation_id


def test_add_message_rejects_save_only_source_reference() -> None:
    with pytest.raises(ValidationError, match="requires a complete conversation save"):
        AddMessageRequest(
            target_conversation_id="conversation",
            pieces=[MessagePieceRequest(original_value="Copy", source_piece_id="00000000-0000-0000-0000-000000000001")],
        )


@pytest.mark.parametrize("data_type", get_args(PromptDataType))
def test_message_piece_accepts_supported_converted_types(data_type: PromptDataType) -> None:
    piece = MessagePieceRequest(
        original_value="source",
        converted_value="preview",
        converted_value_data_type=data_type,
    )

    assert piece.converted_value_data_type == data_type
    assert piece.model_dump()["converted_value_data_type"] == data_type


def test_message_piece_rejects_converted_type_with_null_value() -> None:
    with pytest.raises(ValidationError, match="converted_value_data_type requires converted_value"):
        MessagePieceRequest(
            original_value="source",
            converted_value=None,
            converted_value_data_type="image_path",
        )


def test_message_piece_rejects_converted_type_without_value_field() -> None:
    with pytest.raises(ValidationError, match="converted_value_data_type requires converted_value"):
        MessagePieceRequest(original_value="source", converted_value_data_type="image_path")


def test_message_piece_accepts_empty_converted_value() -> None:
    piece = MessagePieceRequest(original_value="source", converted_value="", converted_value_data_type="text")

    assert piece.converted_value == ""


def test_message_piece_rejects_unknown_converted_type() -> None:
    with pytest.raises(ValidationError, match="converted_value_data_type"):
        MessagePieceRequest.model_validate(
            {"original_value": "source", "converted_value": "preview", "converted_value_data_type": "image"}
        )


def test_message_piece_ignores_client_converter_identifiers() -> None:
    piece = MessagePieceRequest.model_validate(
        {
            "original_value": "source",
            "converted_value": "preview",
            "converter_identifiers": [{"class_name": "UnregisteredConverter"}],
        }
    )

    assert "converter_identifiers" not in piece.model_dump()


@pytest.mark.parametrize("converter_ids", [[], ["first", "second", "first"]])
def test_message_piece_accepts_applied_converter_order(converter_ids: list[str]) -> None:
    piece = MessagePieceRequest(original_value="source", converted_value="", applied_converter_ids=converter_ids)

    assert piece.applied_converter_ids == converter_ids


@pytest.mark.parametrize("converter_ids", [[], ["first"]])
def test_message_piece_rejects_applied_converters_without_value(converter_ids: list[str]) -> None:
    with pytest.raises(ValidationError, match="applied_converter_ids requires converted_value"):
        MessagePieceRequest(original_value="source", applied_converter_ids=converter_ids)
