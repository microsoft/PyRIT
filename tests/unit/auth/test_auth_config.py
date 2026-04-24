# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.auth.auth_config import AZURE_AI_SERVICES_DEFAULT_SCOPE, REFRESH_TOKEN_BEFORE_MSEC


def test_refresh_token_before_msec_is_int():
    assert isinstance(REFRESH_TOKEN_BEFORE_MSEC, int)


def test_refresh_token_before_msec_value():
    assert REFRESH_TOKEN_BEFORE_MSEC == 300


def test_azure_ai_services_default_scope_is_list():
    assert isinstance(AZURE_AI_SERVICES_DEFAULT_SCOPE, list)


def test_azure_ai_services_default_scope_contains_expected_entries():
    assert "https://cognitiveservices.azure.com/.default" in AZURE_AI_SERVICES_DEFAULT_SCOPE
    assert "https://ml.azure.com/.default" in AZURE_AI_SERVICES_DEFAULT_SCOPE


def test_azure_ai_services_default_scope_length():
    assert len(AZURE_AI_SERVICES_DEFAULT_SCOPE) == 2
