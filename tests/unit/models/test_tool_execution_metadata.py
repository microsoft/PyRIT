# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from pydantic import ValidationError

from pyrit.models import ToolExecutionMetadata


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
