# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for deployment argument validation."""

import argparse

import pytest

from infra.deploy_instance import _managed_identity_blob_uri


def test_managed_identity_blob_uri_accepts_credential_free_azure_uri() -> None:
    uri = "https://account.blob.core.windows.net/config/config.yaml"

    assert _managed_identity_blob_uri(uri) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://account.blob.core.windows.net/config/config.yaml?sig=secret",
        "https://account.blob.core.windows.net/config/config.yaml#fragment",
        "https://attacker.blob.example.com/config/config.yaml",
        "http://account.blob.core.windows.net/config/config.yaml",
    ],
)
def test_managed_identity_blob_uri_rejects_credentialed_or_untrusted_uri(uri: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="credential-free"):
        _managed_identity_blob_uri(uri)
