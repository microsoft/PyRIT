# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request contracts for exact converter preview results."""

from typing import get_args

import pytest
from pydantic import ValidationError

from pyrit.backend.models.attacks import MessagePieceRequest
from pyrit.models import PromptDataType


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
