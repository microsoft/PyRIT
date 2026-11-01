# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import copy
import json
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, assert_type

import pytest
from pydantic import ValidationError

from pyrit.models import WebSearchConfig, WebSearchMode

if TYPE_CHECKING:
    from collections.abc import Mapping


class DerivedWebSearchConfig(WebSearchConfig):
    """Subclass used to verify that from_metadata returns the calling class."""


@pytest.mark.parametrize("mode", list(WebSearchMode))
def test_web_search_policy_round_trips(mode: WebSearchMode) -> None:
    config = WebSearchConfig(mode=mode)
    assert WebSearchConfig.from_metadata(metadata=config.to_metadata()) == config
    assert WebSearchConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("mode", list(WebSearchMode))
def test_web_search_policy_round_trips_through_json_metadata(mode: WebSearchMode) -> None:
    config = WebSearchConfig(mode=mode)
    metadata = json.loads(json.dumps(config.to_metadata()))
    assert WebSearchConfig.from_metadata(metadata=metadata) == config


def test_absent_policy_preserves_existing_target_behavior() -> None:
    assert WebSearchConfig.from_metadata(metadata=None).mode == WebSearchMode.DEFAULT
    assert WebSearchConfig.from_metadata(metadata={}).to_metadata() == {}


def test_default_policy_serializes_to_empty_metadata() -> None:
    assert WebSearchConfig(mode=WebSearchMode.DEFAULT).to_metadata() == {}


@pytest.mark.parametrize("mode", [WebSearchMode.DISABLED, WebSearchMode.REQUIRED])
def test_explicit_policy_serializes_under_metadata_key(mode: WebSearchMode) -> None:
    assert WebSearchConfig(mode=mode).to_metadata() == {WebSearchConfig.METADATA_KEY: {"mode": mode.value}}


@pytest.mark.parametrize("mode", list(WebSearchMode))
def test_explicit_policy_is_read_from_metadata(mode: WebSearchMode) -> None:
    config = WebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: {"mode": mode.value}})
    assert config.mode == mode


def test_policy_is_read_from_read_only_mapping() -> None:
    metadata: Mapping[str, Any] = MappingProxyType({WebSearchConfig.METADATA_KEY: {"mode": "required"}})
    assert WebSearchConfig.from_metadata(metadata=metadata).mode == WebSearchMode.REQUIRED
    assert WebSearchConfig.from_metadata(metadata=MappingProxyType({})).mode == WebSearchMode.DEFAULT


def test_unrelated_metadata_keys_are_ignored() -> None:
    assert WebSearchConfig.from_metadata(metadata={"response_format": "json"}).mode == WebSearchMode.DEFAULT


@pytest.mark.parametrize("metadata", [None, {}, {WebSearchConfig.METADATA_KEY: {"mode": "required"}}])
def test_from_metadata_does_not_mutate_metadata(metadata: dict[str, Any] | None) -> None:
    snapshot = copy.deepcopy(metadata)
    WebSearchConfig.from_metadata(metadata=metadata)
    assert metadata == snapshot


def test_explicit_policy_is_not_aliased_to_metadata_payload() -> None:
    payload = {"mode": "required"}
    config = WebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: payload})
    payload["mode"] = "disabled"
    assert config.mode == WebSearchMode.REQUIRED


@pytest.mark.parametrize("value", [{"mode": "auto"}, {"mode": True}, {"mode": "disabled", "unknown": True}, None])
def test_invalid_policy_is_not_silently_ignored(value: object) -> None:
    with pytest.raises(ValidationError):
        WebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: value})


@pytest.mark.parametrize("value", ["required", 1, [], ["mode"]])
def test_non_object_policy_payload_is_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        WebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: value})


def test_policy_is_frozen() -> None:
    config = WebSearchConfig(mode=WebSearchMode.DISABLED)
    with pytest.raises(ValidationError):
        config.mode = WebSearchMode.REQUIRED  # type: ignore[misc]


def test_policy_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        WebSearchConfig(mode=WebSearchMode.DEFAULT, unknown=True)  # type: ignore[call-arg]


class TestWebSearchConfigFromMetadataSelf:
    """Tests that WebSearchConfig.from_metadata preserves the calling class on both paths."""

    def test_absent_policy_returns_subclass(self) -> None:
        config = DerivedWebSearchConfig.from_metadata(metadata=None)
        assert_type(config, DerivedWebSearchConfig)
        assert type(config) is DerivedWebSearchConfig
        assert config.mode == WebSearchMode.DEFAULT

    def test_empty_metadata_returns_subclass(self) -> None:
        config = DerivedWebSearchConfig.from_metadata(metadata={})
        assert_type(config, DerivedWebSearchConfig)
        assert type(config) is DerivedWebSearchConfig

    @pytest.mark.parametrize("mode", list(WebSearchMode))
    def test_explicit_policy_returns_subclass(self, mode: WebSearchMode) -> None:
        config = DerivedWebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: {"mode": mode.value}})
        assert_type(config, DerivedWebSearchConfig)
        assert type(config) is DerivedWebSearchConfig
        assert config.mode == mode

    def test_subclass_round_trips_through_metadata(self) -> None:
        original = DerivedWebSearchConfig(mode=WebSearchMode.REQUIRED)
        restored = DerivedWebSearchConfig.from_metadata(metadata=original.to_metadata())
        assert_type(restored, DerivedWebSearchConfig)
        assert restored == original

    def test_subclass_rejects_invalid_payload(self) -> None:
        with pytest.raises(ValidationError):
            DerivedWebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: {"mode": "auto"}})

    def test_base_returns_web_search_config(self) -> None:
        absent = WebSearchConfig.from_metadata(metadata=None)
        explicit = WebSearchConfig.from_metadata(metadata={WebSearchConfig.METADATA_KEY: {"mode": "disabled"}})
        assert_type(absent, WebSearchConfig)
        assert_type(explicit, WebSearchConfig)
        assert type(absent) is WebSearchConfig
        assert type(explicit) is WebSearchConfig
