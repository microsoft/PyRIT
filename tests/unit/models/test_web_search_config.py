# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models import WebSearchConfig, WebSearchMode


@pytest.mark.parametrize("mode", list(WebSearchMode))
def test_web_search_policy_round_trips(mode: WebSearchMode) -> None:
    config = WebSearchConfig(mode=mode)
    assert WebSearchConfig.from_metadata(metadata=config.to_metadata()) == config
    assert WebSearchConfig.model_validate_json(config.model_dump_json()) == config


def test_absent_policy_preserves_existing_target_behavior() -> None:
    assert WebSearchConfig.from_metadata(metadata=None).mode == WebSearchMode.DEFAULT
    assert WebSearchConfig.from_metadata(metadata={}).to_metadata() == {}


@pytest.mark.parametrize("value", [{"mode": "auto"}, {"mode": True}, {"mode": "disabled", "unknown": True}, None])
def test_invalid_policy_is_not_silently_ignored(value: object) -> None:
    with pytest.raises(ValidationError):
        WebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: value})
