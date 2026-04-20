# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any, Literal, Optional, overload
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed


@overload
def get_httpx_client(
    use_async: Literal[True], debug: bool = False, **httpx_client_kwargs: Optional[Any]
) -> httpx.AsyncClient: ...


@overload
def get_httpx_client(
    use_async: Literal[False] = False, debug: bool = False, **httpx_client_kwargs: Optional[Any]
) -> httpx.Client: ...


def get_httpx_client(
    use_async: bool = False, debug: bool = False, **httpx_client_kwargs: Optional[Any]
) -> httpx.Client | httpx.AsyncClient:
    """
    Get the httpx client for making requests.

    Returns:
        httpx.Client or httpx.AsyncClient: The configured httpx client.
    """
    client_class = httpx.AsyncClient if use_async else httpx.Client
    proxy = "http://localhost:8080" if debug else None

    proxy = httpx_client_kwargs.pop("proxy", proxy)
    verify_certs = httpx_client_kwargs.pop("verify", not debug)
    # fun notes; httpx default is 5 seconds, httpclient is 100, urllib in indefinite
    timeout = httpx_client_kwargs.pop("timeout", 60.0)

    return client_class(proxy=proxy, verify=verify_certs, timeout=timeout, **httpx_client_kwargs)


def extract_url_parameters(url: str) -> dict[str, str]:
    """
    Extract query parameters from a URL.

    Args:
        url (str): The URL to extract parameters from.

    Returns:
        dict[str, str]: Dictionary of query parameters (flattened from lists).
    """
    parsed_url = urlparse(url)
    url_params = parse_qs(parsed_url.query)
    # Flatten params (parse_qs returns lists)
    return {k: v[0] if isinstance(v, list) and len(v) > 0 else "" for k, v in url_params.items()}


def remove_url_parameters(url: str) -> str:
    """
    Remove query parameters from a URL, returning just the base URL.

    Args:
        url (str): The URL to clean.

    Returns:
        str: The URL without query parameters.
    """
    parsed_url = urlparse(url)
    return urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            "",  # Remove query string
            parsed_url.fragment,
        )
    )


PostType = Literal["json", "data"]


@retry(stop=stop_after_attempt(2), wait=wait_fixed(1), reraise=True)
async def make_request_and_raise_if_error_async(
    endpoint_uri: str,
    method: str,
    post_type: PostType = "json",
    debug: bool = False,
    extra_url_parameters: Optional[dict[str, str]] = None,
    request_body: Optional[dict[str, object]] = None,
    files: Optional[dict[str, tuple[str, bytes, str]]] = None,
    headers: Optional[dict[str, str]] = None,
    **httpx_client_kwargs: Optional[Any],
) -> httpx.Response:
    """
    Make a request and raise an exception if it fails.

    Query parameters can be specified either:
    1. In the endpoint_uri (e.g., "https://api.com/endpoint?api-version=2024-10-21")
    2. Via the extra_url_parameters dict
    3. Both (extra_url_parameters will be merged with URL query parameters, with extra_url_parameters taking precedence)

    Returns:
        httpx.Response: The response from the request.
    """
    headers = headers or {}
    request_body = request_body or {}

    # Extract any existing query parameters from the URL
    url_params = extract_url_parameters(endpoint_uri)

    # Merge URL parameters with provided extra_url_parameters (extra_url_parameters takes precedence)
    merged_params = url_params.copy()
    if extra_url_parameters:
        merged_params.update(extra_url_parameters)

    # Get clean URL without query string (we'll pass params separately to httpx)
    clean_url = remove_url_parameters(endpoint_uri)

    async with get_httpx_client(use_async=True, debug=debug, **httpx_client_kwargs) as async_client:
        response = await async_client.request(
            method=method,
            params=merged_params if merged_params else None,
            url=clean_url,
            json=request_body if request_body and post_type == "json" and not files else None,
            data=request_body if request_body and post_type != "json" and not files else None,
            files=files if files else None,
            headers=headers,
        )

        response.raise_for_status()  # This will automatically raise an exception for 4xx and 5xx responses

    return response
