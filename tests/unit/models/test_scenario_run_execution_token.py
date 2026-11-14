# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from pydantic import ValidationError

from pyrit.models import ScenarioRunExecutionToken


def test_scenario_run_execution_token_metadata_round_trip() -> None:
    token = ScenarioRunExecutionToken(token="a" * 32)
    metadata = json.loads(json.dumps(token.to_metadata()))

    assert metadata == {ScenarioRunExecutionToken.METADATA_KEY: "a" * 32}
    assert ScenarioRunExecutionToken.from_metadata(metadata) == token


def test_scenario_run_execution_token_from_metadata_returns_none_when_absent() -> None:
    assert ScenarioRunExecutionToken.from_metadata({}) is None


@pytest.mark.parametrize("value", ["not-a-token", "A" * 32, "a" * 31])
def test_scenario_run_execution_token_from_metadata_rejects_malformed_value(value: str) -> None:
    with pytest.raises(ValidationError):
        ScenarioRunExecutionToken.from_metadata({ScenarioRunExecutionToken.METADATA_KEY: value})
