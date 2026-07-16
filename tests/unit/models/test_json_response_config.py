# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.models import JsonResponseConfig


def test_with_none():
    config = JsonResponseConfig.from_metadata(metadata=None)
    assert config.enabled is False
    assert config.json_schema is None
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_json_object():
    metadata = {
        "response_format": "json",
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema is None
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_json_string_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    metadata = {
        "response_format": "json",
        "json_schema": json.dumps(schema),
        "json_schema_name": "TestSchema",
        "json_schema_strict": False,
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema == schema
    assert config.schema_name == "TestSchema"
    assert config.strict is False


def test_with_json_schema_object():
    schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
    metadata = {
        "response_format": "json",
        "json_schema": schema,
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema == schema
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_empty_json_schema_object():
    metadata = {
        "response_format": "json",
        "json_schema": {},
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema == {}
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_invalid_json_schema_string():
    metadata = {
        "response_format": "json",
        "json_schema": "{invalid_json: true}",
    }
    with pytest.raises(ValueError) as e:
        JsonResponseConfig.from_metadata(metadata=metadata)
    assert "Invalid JSON schema provided" in str(e.value)


def test_other_response_format():
    metadata = {
        "response_format": "something_really_improbable_to_have_here",
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is False
    assert config.json_schema is None
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_schema_without_response_format_is_disabled():
    # A schema is meaningless without the response_format marker, so it must be dropped.
    metadata = {
        "json_schema": {"type": "object"},
        "json_schema_name": "TestSchema",
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is False
    assert config.json_schema is None


def test_with_empty_json_schema_string():
    metadata = {
        "response_format": "json",
        "json_schema": "",
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema is None


def test_ignores_unrelated_metadata_keys():
    # Real prompt_metadata carries other keys (e.g. token usage) that must be ignored.
    metadata = {
        "response_format": "json",
        "total_tokens": 42,
        "some_other_key": "value",
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema is None


def test_to_metadata_disabled_is_empty():
    assert JsonResponseConfig(enabled=False).to_metadata() == {}


def test_schema_implies_enabled():
    # A schema is meaningless without JSON output, so providing one forces enabled.
    config = JsonResponseConfig(json_schema={"type": "object"})
    assert config.enabled is True
    # An explicit enabled=False alongside a schema is coerced rather than kept contradictory.
    coerced = JsonResponseConfig(enabled=False, json_schema={"type": "object"})
    assert coerced.enabled is True
    assert coerced.to_metadata()["response_format"] == "json"


def test_to_metadata_enabled_without_schema():
    assert JsonResponseConfig(enabled=True).to_metadata() == {"response_format": "json"}


def test_to_metadata_enabled_with_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    metadata = JsonResponseConfig(
        enabled=True,
        json_schema=schema,
        schema_name="TestSchema",
        strict=False,
    ).to_metadata()
    assert metadata == {
        "response_format": "json",
        "json_schema": schema,
        "json_schema_name": "TestSchema",
        "json_schema_strict": False,
    }


@pytest.mark.parametrize(
    "config",
    [
        JsonResponseConfig(enabled=False),
        JsonResponseConfig(enabled=True),
        JsonResponseConfig(enabled=True, json_schema={"type": "object"}),
        JsonResponseConfig(
            enabled=True,
            json_schema={"type": "object", "properties": {"a": {"type": "integer"}}},
            schema_name="Custom",
            strict=False,
        ),
    ],
)
def test_metadata_round_trips(config):
    assert JsonResponseConfig.from_metadata(metadata=config.to_metadata()) == config
