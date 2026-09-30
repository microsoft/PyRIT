# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared Azure Storage URI helpers."""

import pytest

from pyrit.common.azure_storage import has_sas_signature, is_azure_blob_uri, redact_url_credentials


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
