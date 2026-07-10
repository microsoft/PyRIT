# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import TokenUsage


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
