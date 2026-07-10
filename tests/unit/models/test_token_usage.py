# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from types import SimpleNamespace

from pyrit.models import TokenUsage


def _usage(**kwargs):
    """Build a lightweight stand-in for a provider usage object from keyword attributes."""
    return SimpleNamespace(**kwargs)


def test_from_provider_usage_maps_prompt_completion():
    usage = _usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    result = TokenUsage.from_provider_usage(usage)
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.total_tokens == 30
    assert result.cached_tokens is None
    assert result.reasoning_tokens is None
    assert result.extra == {}


def test_from_provider_usage_accepts_responses_api_names():
    usage = _usage(input_tokens=7, output_tokens=3, total_tokens=10)
    result = TokenUsage.from_provider_usage(usage)
    assert result.input_tokens == 7
    assert result.output_tokens == 3


def test_from_provider_usage_derives_total_when_missing():
    usage = _usage(prompt_tokens=4, completion_tokens=6)
    result = TokenUsage.from_provider_usage(usage)
    assert result.total_tokens == 10


def test_from_provider_usage_reads_nested_details():
    usage = _usage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        prompt_tokens_details=_usage(cached_tokens=40, audio_tokens=8, cache_write_tokens=5),
        completion_tokens_details=_usage(
            reasoning_tokens=12, audio_tokens=3, accepted_prediction_tokens=2, rejected_prediction_tokens=1
        ),
    )
    result = TokenUsage.from_provider_usage(usage)
    assert result.cached_tokens == 40
    assert result.reasoning_tokens == 12
    assert result.extra == {
        "input_audio_tokens": 8,
        "cache_write_tokens": 5,
        "output_audio_tokens": 3,
        "accepted_prediction_tokens": 2,
        "rejected_prediction_tokens": 1,
    }


def test_from_provider_usage_reads_responses_api_detail_names():
    usage = _usage(
        input_tokens=100,
        output_tokens=50,
        input_tokens_details=_usage(cached_tokens=9),
        output_tokens_details=_usage(reasoning_tokens=11),
    )
    result = TokenUsage.from_provider_usage(usage)
    assert result.cached_tokens == 9
    assert result.reasoning_tokens == 11


def test_from_provider_usage_ignores_non_int_and_bool():
    usage = _usage(prompt_tokens=True, completion_tokens="5", total_tokens=None)
    result = TokenUsage.from_provider_usage(usage)
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.total_tokens is None


def test_from_provider_usage_handles_missing_details():
    usage = _usage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    result = TokenUsage.from_provider_usage(usage)
    assert result.cached_tokens is None
    assert result.reasoning_tokens is None
    assert result.extra == {}


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
