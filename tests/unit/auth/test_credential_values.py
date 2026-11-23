# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validate credential and header formats before sending them."""

import pytest

from pyrit.auth import clean_credential_value, clean_request_headers


@pytest.mark.parametrize("value", ["caf\u00e9", "sample\u2028"])
def test_non_ascii_credentials_are_rejected_without_echoing_the_value(value: str) -> None:
    with pytest.raises(ValueError) as key_error:
        clean_credential_value(value, name="api_key")
    assert str(key_error.value) == "'api_key' must contain only ASCII characters."

    with pytest.raises(ValueError) as header_error:
        clean_request_headers({"X-Test": value})
    assert str(header_error.value) == "Header 'X-Test' must contain only ASCII characters."


def test_ascii_header_values_can_contain_an_internal_tab() -> None:
    assert clean_request_headers({"X-Test": " blue\tgreen "}) == {"X-Test": "blue\tgreen"}
