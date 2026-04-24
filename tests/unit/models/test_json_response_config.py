# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.models.json_response_config import _JsonResponseConfig


def test_with_none():
    config = _JsonResponseConfig.from_metadata(metadata=None)
    assert config.enabled is False
    assert config.schema is None
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_json_object():
    metadata = {
        "response_format": "json",
    }
    config = _JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.schema is None
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
    config = _JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.schema == schema
    assert config.schema_name == "TestSchema"
    assert config.strict is False


def test_with_json_schema_object():
    schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
    metadata = {
        "response_format": "json",
        "json_schema": schema,
    }
    config = _JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.schema == schema
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_empty_json_schema_object():
    metadata = {
        "response_format": "json",
        "json_schema": {},
    }
    config = _JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.schema == {}
    assert config.schema_name == "CustomSchema"
    assert config.strict is True


def test_with_invalid_json_schema_string():
    metadata = {
        "response_format": "json",
        "json_schema": "{invalid_json: true}",
    }
    with pytest.raises(ValueError) as e:
        _JsonResponseConfig.from_metadata(metadata=metadata)
    assert "Invalid JSON schema provided" in str(e.value)


def test_other_response_format():
    metadata = {
        "response_format": "something_really_improbably_to_have_here",
    }
    config = _JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is False
    assert config.schema is None
    assert config.schema_name == "CustomSchema"
    assert config.strict is True
