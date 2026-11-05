# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any, assert_type

import pytest
from pydantic import ValidationError

from pyrit.models import ToolExecutionMetadata


class DerivedToolExecutionMetadata(ToolExecutionMetadata):
    """Subclass used to verify that metadata reconstruction preserves the caller type."""


def test_from_metadata_preserves_subclass_type() -> None:
    metadata: dict[str, Any] = {ToolExecutionMetadata.METADATA_KEY: {"invoked": True}}

    restored = DerivedToolExecutionMetadata.from_metadata(metadata=metadata)

    assert_type(restored, DerivedToolExecutionMetadata | None)
    assert restored is not None
    assert_type(restored, DerivedToolExecutionMetadata)
    assert type(restored) is DerivedToolExecutionMetadata
    assert restored.invoked is True


def test_from_metadata_does_not_mutate_input() -> None:
    metadata: dict[str, Any] = {
        "unrelated": "retained",
        ToolExecutionMetadata.METADATA_KEY: {"invoked": False},
    }
    original = metadata.copy()

    assert DerivedToolExecutionMetadata.from_metadata(metadata=metadata) is not None
    assert metadata == original


@pytest.mark.parametrize("invoked", [True, False])
def test_tool_execution_metadata_round_trip(invoked: bool) -> None:
    execution = ToolExecutionMetadata(invoked=invoked)
    metadata = json.loads(json.dumps({"unrelated": "retained", **execution.to_metadata()}))

    assert metadata[ToolExecutionMetadata.METADATA_KEY] == {"invoked": invoked}
    assert ToolExecutionMetadata.from_metadata(metadata=metadata) == execution
    assert metadata["unrelated"] == "retained"


def test_tool_execution_metadata_absent() -> None:
    assert ToolExecutionMetadata.from_metadata(metadata={"unrelated": True}) is None


@pytest.mark.parametrize("value", [None, True, {}, {"invoked": "false"}, {"invoked": 1}, {"invoked": True, "extra": 1}])
def test_tool_execution_metadata_rejects_malformed_status(value: object) -> None:
    with pytest.raises(ValidationError):
        ToolExecutionMetadata.from_metadata(metadata={ToolExecutionMetadata.METADATA_KEY: value})
