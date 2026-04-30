# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_target.hugging_face.hugging_face_endpoint_target import (
    HuggingFaceEndpointTarget,
)

# HuggingFaceEndpointTarget emits a DeprecationWarning on construction
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def hugging_face_endpoint_target(patch_central_database) -> HuggingFaceEndpointTarget:
    return HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )


def test_hugging_face_endpoint_initializes(hugging_face_endpoint_target: HuggingFaceEndpointTarget):
    assert hugging_face_endpoint_target


def test_hugging_face_endpoint_sets_endpoint_and_rate_limit():
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        max_requests_per_minute=30,
    )
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://api-inference.huggingface.co/models/test-model"
    assert target._max_requests_per_minute == 30


def test_invalid_temperature_too_low_raises(patch_central_database):
    with pytest.raises(Exception, match="temperature must be between 0 and 2"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=-0.1,
        )


def test_invalid_temperature_too_high_raises(patch_central_database):
    with pytest.raises(Exception, match="temperature must be between 0 and 2"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=2.1,
        )


def test_invalid_top_p_too_low_raises(patch_central_database):
    with pytest.raises(Exception, match="top_p must be between 0 and 1"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            top_p=-0.1,
        )


def test_invalid_top_p_too_high_raises(patch_central_database):
    with pytest.raises(Exception, match="top_p must be between 0 and 1"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            top_p=1.1,
        )


def test_valid_temperature_and_top_p(patch_central_database):
    # Should not raise any exceptions
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        temperature=1.5,
        top_p=0.9,
    )
    assert target._temperature == 1.5
    assert target._top_p == 0.9


def test_identifier_includes_generation_params():
    """New generation params (top_k, do_sample, repetition_penalty) appear in the identifier."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        top_k=40,
        do_sample=True,
        repetition_penalty=1.2,
    )
    identifier = target.get_identifier()
    assert identifier.params["top_k"] == 40
    assert identifier.params["do_sample"] is True
    assert identifier.params["repetition_penalty"] == 1.2


def test_identifier_excludes_none_generation_params():
    """None-valued generation params are excluded from the identifier."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )
    identifier = target.get_identifier()
    assert "top_k" not in identifier.params
    assert "do_sample" not in identifier.params
    assert "repetition_penalty" not in identifier.params


def test_sampling_params_without_do_sample_warns():
    """Setting temperature != 1.0 without do_sample=True emits a warning."""
    with pytest.warns(UserWarning, match="do_sample is not True"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=0.7,
        )


def test_sampling_params_with_do_sample_no_warning():
    """Setting temperature != 1.0 with do_sample=True does not warn."""
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", UserWarning)
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=0.7,
            do_sample=True,
        )


@pytest.mark.filterwarnings("default::DeprecationWarning")
def test_init_emits_deprecation_warning():
    """HuggingFaceEndpointTarget emits a DeprecationWarning on construction."""
    with pytest.warns(DeprecationWarning, match="deprecated and will be removed"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
        )
