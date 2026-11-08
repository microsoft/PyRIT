# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models.catalog import ServerCredential, normalized_endpoint


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://API.Contoso.com/v1", "https://api.contoso.com/v1/"),
        ("https://api.contoso.com:443/v1", "https://api.contoso.com/v1"),
        ("http://localhost:80/v1", "http://LOCALHOST/v1"),
    ],
)
def test_normalized_endpoint_treats_the_same_address_alike(left: str, right: str) -> None:
    assert normalized_endpoint(left) == normalized_endpoint(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://api.contoso.com/v1", "https://api.contoso.com/v2"),
        ("https://api.contoso.com/V1", "https://api.contoso.com/v1"),
        ("https://api.contoso.com:8443/v1", "https://api.contoso.com/v1"),
        ("http://api.contoso.com/v1", "https://api.contoso.com/v1"),
        ("https://api.contoso.com.evil.example/v1", "https://api.contoso.com/v1"),
    ],
)
def test_normalized_endpoint_tells_different_addresses_apart(left: str, right: str) -> None:
    assert normalized_endpoint(left) != normalized_endpoint(right)


def test_a_server_credential_names_a_variable_and_one_endpoint() -> None:
    credential = ServerCredential(name="contoso", env_var="CONTOSO_KEY", endpoint="https://api.contoso.com/v1")

    assert (credential.name, credential.env_var, credential.endpoint) == (
        "contoso",
        "CONTOSO_KEY",
        "https://api.contoso.com/v1",
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "Contoso"},
        {"env_var": "CONTOSO-KEY"},
        {"endpoint": "api.contoso.com/v1"},
        {"endpoint": "ftp://api.contoso.com/v1"},
        {"endpoint": "https://user:pass@api.contoso.com/v1"},
        {"endpoint": "https://api.contoso.com/v1?key=secret"},
        {"endpoint": "https://api.contoso.com/v1#key"},
        {"extra": "field"},
    ],
)
def test_a_server_credential_refuses_a_bad_entry(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ServerCredential.model_validate(
            {"name": "contoso", "env_var": "CONTOSO_KEY", "endpoint": "https://api.contoso.com/v1", **fields}
        )
