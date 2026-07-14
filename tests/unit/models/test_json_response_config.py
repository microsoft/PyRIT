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
        "response_format": "something_really_improbably_to_have_here",
    }
    config = JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is False
    assert config.json_schema is None
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_to_metadata_disabled_is_empty():
    assert JsonResponseConfig(enabled=False).to_metadata() == {}
    # A schema on a disabled config is not requested, so nothing is serialized.
    assert JsonResponseConfig(enabled=False, json_schema={"type": "object"}).to_metadata() == {}


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
