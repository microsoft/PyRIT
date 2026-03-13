# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target.http_target.http_target import HTTPTarget
from pyrit.prompt_target.http_target.http_target_callback_functions import (
    get_http_target_json_response_callback_function,
    get_http_target_regex_matching_callback_function,
)

sample_request = (
    'POST / HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\n{"prompt": "{PLACEHOLDER_PROMPT}"}'
)


@pytest.fixture
def mock_callback_function() -> Callable:
    return get_http_target_json_response_callback_function(key="mock_key")


@pytest.fixture
def mock_http_target(mock_callback_function, sqlite_instance) -> HTTPTarget:
    return HTTPTarget(
        http_request=sample_request,
        prompt_regex_string="{PLACEHOLDER_PROMPT}",
        callback_function=mock_callback_function,
    )


@pytest.fixture
def mock_http_response() -> MagicMock:
    mock_response = MagicMock()
    mock_response.content = b'{"mock_key": "value1"}'
    return mock_response


def test_parse_json_response_no_match(mock_http_response):
    parse_json_response = get_http_target_json_response_callback_function(key="nonexistant_key")
    result = parse_json_response(mock_http_response)
    assert result == ""


def test_parse_json_response_match(mock_http_response, mock_callback_function):
    result = mock_callback_function(mock_http_response)
    assert result == "value1"


def test_parse_raw_http_request(mock_http_target):
    headers, body, url, method, version = mock_http_target.parse_raw_http_request(sample_request)
    assert url == "https://example.com/"
    assert method == "POST"
    assert headers == {"host": "example.com", "content-type": "application/json"}
    assert body == '{"prompt": "{PLACEHOLDER_PROMPT}"}'
    assert version == "HTTP/1.1"


def test_parse_regex_response_no_match():
    mock_response = MagicMock()
    mock_response.content = b"<html><body>No match here</body></html>"
    parse_html_function = get_http_target_regex_matching_callback_function(key=r'no_results\/[^\s"]+')
    result = parse_html_function(mock_response)
    assert result == "b'<html><body>No match here</body></html>'"


def test_parse_regex_response_match():
    mock_response = MagicMock()
    mock_response.content = b"<html><body>Match: 1234</body></html>"
    parse_html_response = get_http_target_regex_matching_callback_function(r"Match: (\d+)")
    result = parse_html_response(mock_response)
    assert result == "Match: 1234"


def test_parse_json_response_positive_array_index():
    mock_response = MagicMock()
    mock_response.content = b'{"data": [{"items": ["a", "b", "c"]}, {"items": ["x", "y", "z"]}]}'
    parse_json_response = get_http_target_json_response_callback_function(key="data[0].items[1]")
    result = parse_json_response(mock_response)
    assert result == "b"


def test_parse_json_response_negative_array_index():
    mock_response = MagicMock()
    mock_response.content = b'{"data": [{"items": ["a", "b", "c"]}, {"items": ["x", "y", "z"]}]}'
    parse_json_response = get_http_target_json_response_callback_function(key="data[0].items[-1]")
    result = parse_json_response(mock_response)
    assert result == "c"
