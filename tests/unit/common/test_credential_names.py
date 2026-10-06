# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.common.credential_names import CredentialNames


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "Authorization",
        "aUtHoRiZaTiOn",
        "X-Api-Key",
        "XApiKey",
        "xapikey",
        "XaPiKeY",
        "auThToKeN",
        "accesstoken",
        "SASToken",
        "clientSecret",
        "webhook_secret",
        "auth_token",
        "connection_string",
        "AWSAccessKeyId",
        "X-Amz-Credential",
        "X-Goog-Signature",
        "Authentication",
        "azure_ad_token",
        "client_assertion",
        "PRIVATE-TOKEN",
        "X-Vault-Token",
        "vertex_credentials",
        "azure_storage_sas_token",
        "watsonx_token",
        "oci_key",
        "authorization_token",
        "client_private_key",
    ],
)
def test_names_that_always_label_a_credential(name: str) -> None:
    assert CredentialNames.is_credential(name)


@pytest.mark.parametrize("name", ["key", "code", "cOde", "sig", "signature"])
def test_names_that_label_a_credential_only_where_one_is_expected(name: str) -> None:
    assert not CredentialNames.is_credential(name)
    assert CredentialNames.is_credential(name, where_expected=True)


@pytest.mark.parametrize(
    "name",
    [
        "max_tokens",
        "maxTokens",
        "prompt_cache_key",
        "promptCacheKey",
        "eos_token",
        "api-version",
        "model",
        "type",
        "keyword",
        "email_signature",
    ],
)
def test_ordinary_names_are_not_credentials(name: str) -> None:
    assert not CredentialNames.is_credential(name, where_expected=True)


@pytest.mark.parametrize(
    ("name", "is_map"),
    [
        ("params", True),
        ("default_query", True),
        ("defaultHeaders", True),
        ("Authentication", True),
        ("hEaDeRs", True),
        ("json_data", False),
    ],
)
def test_credential_maps_are_recognized(name: str, is_map: bool) -> None:
    assert CredentialNames.is_credential_map(name) is is_map


@pytest.mark.parametrize("name", ["api_key", "Api-Key", "apiKey", "APIKey", "API KEY", "aPi_kEy"])
def test_fold_reads_every_spelling_alike(name: str) -> None:
    assert CredentialNames.fold(name) == "apikey"


@pytest.mark.parametrize(
    ("value", "is_flag"), [("true", True), (" Off ", True), ("0", True), ("10", False), ("abc", False)]
)
def test_switch_values_are_flags(value: str, is_flag: bool) -> None:
    assert CredentialNames.is_flag(value) is is_flag
