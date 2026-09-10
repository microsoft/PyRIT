# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for CoPyRIT deployment configuration."""

import argparse
import json
from unittest.mock import patch

import pytest

from infra import deploy_instance


def test_managed_identity_blob_uri_accepts_credential_free_azure_uri() -> None:
    uri = "https://account.blob.core.windows.net/config/config.yaml"

    assert deploy_instance._managed_identity_blob_uri(uri) == uri


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
        deploy_instance._managed_identity_blob_uri(uri)


def test_post_deploy_enables_spa_and_device_code_authentication() -> None:
    with patch.object(deploy_instance, "run_az") as run_az:
        deploy_instance.post_deploy(
            app_object_id="app-object-id",
            fqdn="copyrit.example.com",
        )

    args = run_az.call_args.kwargs["args"]
    body = json.loads(args[args.index("--body") + 1])
    assert body == {
        "spa": {"redirectUris": ["https://copyrit.example.com"]},
        "isFallbackPublicClient": True,
    }
