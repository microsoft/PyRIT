# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_target.hugging_face.hugging_face_endpoint_target import (
    HuggingFaceEndpointTarget,
)


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
