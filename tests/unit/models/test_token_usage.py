# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from types import SimpleNamespace

from pyrit.models import TokenUsage, read_usage_int, read_usage_value


def test_to_metadata_uses_input_output_key_names_and_omits_none():
    usage = TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30, cached_tokens=5)
    metadata = usage.to_metadata()
    assert metadata["token_usage_input_tokens"] == 10
    assert metadata["token_usage_output_tokens"] == 20
    assert metadata["token_usage_total_tokens"] == 30
    assert metadata["token_usage_cached_tokens"] == 5
    assert "token_usage_reasoning_tokens" not in metadata


def test_to_metadata_includes_extra():
    usage = TokenUsage(input_tokens=1, output_tokens=2, extra={"output_audio_tokens": 9})
    metadata = usage.to_metadata()
    assert metadata["token_usage_input_tokens"] == 1
    assert metadata["token_usage_output_audio_tokens"] == 9


def test_round_trip_through_metadata():
    original = TokenUsage(
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        reasoning_tokens=4,
        cached_tokens=5,
        extra={"output_audio_tokens": 3},
    )
    restored = TokenUsage.from_metadata(original.to_metadata())
    assert restored == original


def test_from_metadata_reads_input_output_suffixes():
    metadata = {"token_usage_input_tokens": 8, "token_usage_output_tokens": 12}
    restored = TokenUsage.from_metadata(metadata)
    assert restored is not None
    assert restored.input_tokens == 8
    assert restored.output_tokens == 12


def test_from_metadata_routes_unknown_int_keys_to_extra():
    metadata = {"token_usage_input_tokens": 10, "token_usage_output_audio_tokens": 4}
    restored = TokenUsage.from_metadata(metadata)
    assert restored is not None
    assert restored.extra == {"output_audio_tokens": 4}


def test_from_metadata_ignores_cost_and_unrelated_keys():
    metadata = {
        "token_usage_input_tokens": 10,
        "token_usage_cost": "0.0021",
        "unrelated_key": 99,
    }
    restored = TokenUsage.from_metadata(metadata)
    assert restored is not None
    assert restored.input_tokens == 10
    assert "cost" not in restored.extra
    assert restored.extra == {}


def test_from_metadata_returns_none_without_token_usage_keys():
    assert TokenUsage.from_metadata({"partial_content": "x"}) is None


def test_read_usage_value_reads_attribute_objects():
    usage = SimpleNamespace(input_tokens=11, input_tokens_details=SimpleNamespace(cached_tokens=3))
    assert read_usage_value(source=usage, name="input_tokens") == 11
    assert read_usage_value(source=usage, name="input_tokens_details").cached_tokens == 3


def test_read_usage_value_reads_mappings():
    usage = {"input_tokens": 11, "input_tokens_details": {"cached_tokens": 3}}
    assert read_usage_value(source=usage, name="input_tokens") == 11
    assert read_usage_value(source=usage, name="input_tokens_details") == {"cached_tokens": 3}


def test_read_usage_value_returns_none_for_missing_and_none_source():
    assert read_usage_value(source=SimpleNamespace(), name="input_tokens") is None
    assert read_usage_value(source=None, name="input_tokens") is None
    assert read_usage_value(source={}, name="input_tokens") is None


def test_read_usage_int_guards_non_integer_values():
    usage = SimpleNamespace(input_tokens=11, output_tokens=None, total_tokens="30", cached_tokens=True)
    assert read_usage_int(source=usage, name="input_tokens") == 11
    assert read_usage_int(source=usage, name="output_tokens") is None
    assert read_usage_int(source=usage, name="total_tokens") is None
    assert read_usage_int(source=usage, name="cached_tokens") is None


def test_read_usage_int_reads_mappings_and_missing_sources():
    assert read_usage_int(source={"input_tokens": 7}, name="input_tokens") == 7
    assert read_usage_int(source=None, name="input_tokens") is None
