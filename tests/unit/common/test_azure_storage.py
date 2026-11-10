# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared Azure Storage URI helpers."""

import pytest

from pyrit.common.azure_storage import has_sas_signature, is_azure_blob_uri, redact_url_credentials, redact_urls


@pytest.mark.parametrize(
    "uri",
    [
        "https://account.blob.core.windows.net/container",
        "https://account.blob.core.chinacloudapi.cn/container/blob",
        "https://account.blob.core.usgovcloudapi.net/container",
        "https://account.blob.core.cloudapi.de/container",
    ],
)
def test_is_azure_blob_uri_accepts_known_azure_authorities(uri: str) -> None:
    assert is_azure_blob_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://attacker.blob.example.com/container",
        "https://blob.core.windows.net/container",
        "https://user@account.blob.core.windows.net/container",
        "https://account.blob.core.windows.net:8443/container",
        "http://account.blob.core.windows.net/container",
        "https://account.blob.core.windows.net",
    ],
)
def test_is_azure_blob_uri_rejects_untrusted_or_incomplete_uri(uri: str) -> None:
    assert not is_azure_blob_uri(uri)


def test_is_azure_blob_uri_enforces_path_depth() -> None:
    assert not is_azure_blob_uri(
        "https://account.blob.core.windows.net/container",
        min_path_segments=2,
    )
    assert is_azure_blob_uri(
        "https://account.blob.core.windows.net/container/blob.yaml",
        min_path_segments=2,
    )


def test_has_sas_signature_requires_non_empty_signature() -> None:
    assert has_sas_signature("https://account.blob.core.windows.net/container?sp=rw&sig=secret")
    assert not has_sas_signature("https://account.blob.core.windows.net/container?sp=rw&sig=")


def test_redact_url_credentials_removes_query_and_fragment() -> None:
    uri = "https://account.blob.core.windows.net/container/blob.yaml?sp=rw&sig=secret#fragment"

    assert redact_url_credentials(uri) == "https://account.blob.core.windows.net/container/blob.yaml"


def test_redact_url_credentials_removes_user_name_and_password() -> None:
    assert redact_url_credentials("https://user:SECRET@host:8443/path?q=1") == "https://host:8443/path"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("got https://acct.blob.core.windows.net/c/b?sv=1&sig=SECRET.", "got https://acct.blob.core.windows.net/c/b"),
        ("to wss://user:SECRET@host/p#part failed", "to wss://host/p failed"),
        ("first http://a/b?x=1 then https://c/d", "first http://a/b then https://c/d"),
        ("bad https://[x?sig=SECRET", "bad <URL hidden>"),
        ("key rediss://:Ab3/xYz+k9=@cache:6380/0 refused", "key rediss://cache:6380/0 refused"),
        ("got https://user:pa#ss@host/x", "got https://host/x"),
        ("endpoint_https://user:SECRET@host/v1", "endpoint_https://host/v1"),
        ("host/path?api-version=1&sig=SECRET&se=2030", "host/path?api-version=1&sig=<hidden>&se=2030"),
        ("could not be coerced to float: 'hot'", "could not be coerced to float: 'hot'"),
    ],
)
def test_redact_urls_removes_credentials_from_each_url_in_text(text: str, expected: str) -> None:
    assert redact_urls(text) == expected
