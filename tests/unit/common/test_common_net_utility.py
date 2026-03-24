# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from pyrit.common.net_utility import (
    extract_url_parameters,
    get_httpx_client,
    make_request_and_raise_if_error_async,
    remove_url_parameters,
)


@pytest.mark.parametrize(
    "use_async, expected_type",
    [
        (False, httpx.Client),
        (True, httpx.AsyncClient),
    ],
)
def test_get_httpx_client_type(use_async, expected_type):
    client = get_httpx_client(use_async=use_async)
    assert isinstance(client, expected_type)


@respx.mock
@pytest.mark.asyncio
async def test_make_request_and_raise_if_error_success():
    url = "http://testserver/api/test"
    method = "GET"
    mock_route = respx.get(url).respond(200, json={"status": "ok"})
    response = await make_request_and_raise_if_error_async(endpoint_uri=url, method=method)
    assert mock_route.called
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@respx.mock
@pytest.mark.asyncio
async def test_make_request_and_raise_if_error_failure():
    url = "http://testserver/api/fail"
    method = "GET"
    mock_route = respx.get(url).respond(500)

    # Mock asyncio.sleep to avoid 1s fixed wait delay
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError):
            await make_request_and_raise_if_error_async(endpoint_uri=url, method=method)
        assert mock_route.called
        assert len(mock_route.calls) == 2


@respx.mock
@pytest.mark.asyncio
async def test_make_request_and_raise_if_error_retries():
    url = "http://testserver/api/retry"
    method = "GET"
    call_count = 0

    def response_callback(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"status": "ok"})

    mock_route = respx.route(method=method, url=url).mock(side_effect=response_callback)

    # Mock asyncio.sleep to avoid 1s fixed wait delay
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError):
            await make_request_and_raise_if_error_async(endpoint_uri=url, method=method)
        assert call_count == 2, "The request should have been retried exactly once."
        assert mock_route.called


@pytest.mark.asyncio
async def test_debug_is_false_by_default():
    with patch("pyrit.common.net_utility.get_httpx_client") as mock_get_httpx_client:
        mock_client_instance = MagicMock()
        mock_get_httpx_client.return_value = mock_client_instance

        await make_request_and_raise_if_error_async(endpoint_uri="http://example.com", method="GET")

        mock_get_httpx_client.assert_called_with(debug=False, use_async=True)


def test_extract_url_parameters_simple():
    url = "https://api.example.com/endpoint?api-version=2024-10-21&key=value"
    params = extract_url_parameters(url)
    assert params == {"api-version": "2024-10-21", "key": "value"}


def test_extract_url_parameters_no_params():
    url = "https://api.example.com/endpoint"
    params = extract_url_parameters(url)
    assert params == {}


def test_extract_url_parameters_empty_query():
    url = "https://api.example.com/endpoint?"
    params = extract_url_parameters(url)
    assert params == {}


def test_extract_url_parameters_with_fragment():
    url = "https://api.example.com/endpoint?param1=value1&param2=value2#section"
    params = extract_url_parameters(url)
    assert params == {"param1": "value1", "param2": "value2"}


def test_extract_url_parameters_special_characters():
    url = "https://api.example.com/endpoint?name=John%20Doe&email=test%40example.com"
    params = extract_url_parameters(url)
    assert params == {"name": "John Doe", "email": "test@example.com"}


def test_extract_url_parameters_preserves_blank_values():
    url = "https://api.example.com/endpoint?api-version=&mode=test"
    params = extract_url_parameters(url)
    assert params == {"api-version": "", "mode": "test"}


def test_extract_url_parameters_preserves_empty_values():
    url = "https://api.example.com/endpoint?alpha=&beta=1"
    params = extract_url_parameters(url)
    assert params == {"alpha": "", "beta": "1"}


def test_remove_url_parameters_simple():
    url = "https://api.example.com/endpoint?api-version=2024-10-21&key=value"
    clean_url = remove_url_parameters(url)
    assert clean_url == "https://api.example.com/endpoint"


def test_remove_url_parameters_no_params():
    url = "https://api.example.com/endpoint"
    clean_url = remove_url_parameters(url)
    assert clean_url == "https://api.example.com/endpoint"


def test_remove_url_parameters_with_fragment():
    url = "https://api.example.com/endpoint?param=value#section"
    clean_url = remove_url_parameters(url)
    assert clean_url == "https://api.example.com/endpoint#section"


def test_remove_url_parameters_with_path_params():
    url = "https://api.example.com/endpoint;pathparam?query=value"
    clean_url = remove_url_parameters(url)
    assert clean_url == "https://api.example.com/endpoint;pathparam"


def test_remove_url_parameters_complex():
    url = "https://user:pass@api.example.com:8080/path/to/endpoint?key1=value1&key2=value2#fragment"
    clean_url = remove_url_parameters(url)
    assert clean_url == "https://user:pass@api.example.com:8080/path/to/endpoint#fragment"


@respx.mock
@pytest.mark.asyncio
async def test_make_request_and_raise_if_error_preserves_blank_query_values():
    url = "http://testserver/api/test?api-version=&mode=test"
    mock_route = respx.get(url).respond(200, json={"status": "ok"})

    response = await make_request_and_raise_if_error_async(endpoint_uri=url, method="GET")

    assert mock_route.called
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@respx.mock
@pytest.mark.asyncio
async def test_make_request_and_raise_if_error_preserves_empty_query_values():
    url = "http://testserver/api/test"
    route = respx.get(url).respond(200, json={"status": "ok"})

    await make_request_and_raise_if_error_async(endpoint_uri=f"{url}?alpha=&beta=1", method="GET")

    assert route.called
    assert str(route.calls[0].request.url) == "http://testserver/api/test?alpha=&beta=1"
