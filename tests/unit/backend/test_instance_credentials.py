# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for keeping credentials out of saved instance recipes."""

import json
from typing import Any

import pytest

from pyrit.backend.services.instance_credentials import InstanceCredentials
from pyrit.models import ComponentType, Parameter
from pyrit.models.catalog.instance_recipe import CredentialReference, InstanceRecipe

_PARAMETERS = [
    Parameter(name="api_key", description="", param_type=str | None),
    Parameter(name="bare", description="", param_type=dict),
    Parameter(name="endpoint", description="", param_type=str),
    Parameter(name="params", description="", param_type=dict[str, Any] | None),
    Parameter(name="patterns", description="", param_type=dict[str, str] | None),
    Parameter(name="settings", description="", param_type=dict[str, Any] | None),
]


def _recipe(params: dict[str, Any]) -> InstanceRecipe:
    return InstanceRecipe(kind=ComponentType.TARGET, name="target", type="ExampleTarget", params=params)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"api_key": "sk-1"}, "'api_key' cannot be sent as a value"),
        ({"endpoint": "ftp://user:pw@files.test/"}, "'endpoint' contains a credential in its URL"),
        ({"endpoint": "https://api.test/?subscription-key=abc"}, "'endpoint' contains a credential in its URL"),
        ({"settings": {"Proxy-Authorization": "Basic abc"}}, "'settings.Proxy-Authorization' holds a credential"),
        ({"settings": {"hosts": ["https://a.test/?sig=abc"]}}, r"'settings.hosts\[0\]' contains a credential"),
        ({"patterns": {"name": "https://user:pw@a.test/"}}, "'patterns.name' contains a credential in its URL"),
        ({"params": {"code": "fn-key"}}, "'params.code' holds a credential"),
        ({"settings": {"default_query": {"sig": ["abc"]}}}, "'settings.default_query.sig' holds a credential"),
        ({"settings": {"webhook_secret": "whsec_abc"}}, "'settings.webhook_secret' holds a credential"),
        ({"settings": {"headers": {"X-Goog-Api-Key": "abc"}}}, "'settings.headers.X-Goog-Api-Key' holds a credential"),
        ({"endpoint": "https://ghp_token@api.test/"}, "'endpoint' contains a credential in its URL"),
        ({"settings": {"auth": ["user", "pass"]}}, "'settings.auth' holds a credential"),
        ({"settings": {"clientSecret": "abc"}}, "'settings.clientSecret' holds a credential"),
        ({"settings": {"connection_string": "Endpoint=sb://x"}}, "'settings.connection_string' holds a credential"),
        (
            {"settings": {"data_sources": [{"parameters": {"authentication": {"type": "api_key", "key": "k1"}}}]}},
            r"'settings.data_sources\[0\].parameters.authentication.key' holds a credential",
        ),
        ({"params": "api-version=1&api-key=abc"}, "'params' holds a credential"),
        ({"params": [["api-key", "abc"]]}, r"'params\[0\]' holds a credential"),
        ({"params": [{"code": "abc"}]}, r"'params\[0\].code' holds a credential"),
        ({"settings": {"default_query": [["sig", "abc"]]}}, r"'settings.default_query\[0\]' holds a credential"),
        ({"settings": {"authentication": [{"key": "abc"}]}}, r"'settings.authentication\[0\].key' holds a credential"),
        ({"endpoint": "https://api.test/chat?auth_token=abc"}, "'endpoint' contains a credential in its URL"),
        ({"settings": {"headers": [["Authorization", "Bearer abc"]]}}, r"'settings.headers\[0\]' holds a credential"),
        ({"endpoint": "https://api.test/chat?cOde=abc"}, "'endpoint' contains a credential in its URL"),
        (
            {"settings": {"default_headers": {"aUtHoRiZaTiOn": "Bearer abc"}}},
            "'settings.default_headers.aUtHoRiZaTiOn' holds a credential",
        ),
        ({"settings": {"hEaDeRs": {"KEY": "abc"}}}, "'settings.hEaDeRs.KEY' holds a credential"),
        (
            {"settings": {"default_headers": {"xapikey": "abc"}}},
            "'settings.default_headers.xapikey' holds a credential",
        ),
        ({"params": {"api-key": 12345678}}, "'params.api-key' holds a credential"),
        ({"params": "api-key=abc&label=two words"}, "'params' holds a credential"),
        ({"bare": {"token": "abc"}}, "'bare.token' holds a credential"),
        ({"settings": {"azure_ad_token": "eyJ-token"}}, "'settings.azure_ad_token' holds a credential"),
        (
            {"settings": {"default_headers": {"Authentication": "Basic abc"}}},
            "'settings.default_headers.Authentication' holds a credential",
        ),
        ({"endpoint": "https://gitlab.test/api?private_token=abc"}, "'endpoint' contains a credential in its URL"),
        ({"endpoint": "https://user:abc/def==@api.test/"}, "'endpoint' contains a credential in its URL"),
        ({"endpoint": "https://api.test//user:pw@evil.test/x"}, "'endpoint' contains a credential in its URL"),
        (
            {"endpoint": "https://proxy.test/v1?upstream=https://user:pw@up.test/v1"},
            "'endpoint' contains a credential in its URL",
        ),
        (
            {"endpoint": "https://proxy.test/v1?upstream=https%3A%2F%2Fuser%3Apw%40up.test%2Fv1"},
            "'endpoint' contains a credential in its URL",
        ),
        (
            {"endpoint": "https://proxy.test/forward?upstream=https://up.test/v1?code=abc"},
            "'endpoint' contains a credential in its URL",
        ),
        (
            {"settings": {"dependentRequired": {"api_key": "sk-raw"}}},
            "'settings.dependentRequired.api_key' holds a credential",
        ),
        (
            {"settings": {"dependencies": {"api_key": ["sk-raw", {"nested": 1}]}}},
            "'settings.dependencies.api_key' holds a credential",
        ),
        (
            {"settings": {"config": {"dependentRequired": {"api_key": ["sk-raw"]}}}},
            "'settings.config.dependentRequired.api_key' holds a credential",
        ),
        (
            {"settings": {"type": "object", "allOf": [{"dependentRequired": {"api_key": "sk-raw"}}]}},
            r"'settings.allOf\[0\].dependentRequired.api_key' holds a credential",
        ),
        (
            {"settings": {"type": "object", "allOf": [{"api_key": "sk-raw"}]}},
            r"'settings.allOf\[0\].api_key' holds a credential",
        ),
        (
            {
                "settings": {
                    "tools": [{"function": {"parameters": {"type": "object"}}}],
                    "config": {"dependentRequired": {"api_key": ["sk-raw"]}},
                }
            },
            "'settings.config.dependentRequired.api_key' holds a credential",
        ),
    ],
)
def test_check_recipe_rejects_a_recipe_that_would_save_a_credential(params: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        InstanceCredentials.check_recipe(recipe=_recipe(params), parameters=_PARAMETERS)


def test_rejection_names_the_credential_references_the_type_accepts() -> None:
    with pytest.raises(ValueError, match="Send credentials through credentials.api_key instead"):
        InstanceCredentials.check_recipe(recipe=_recipe({"settings": {"token": "abc"}}), parameters=_PARAMETERS)


def test_a_parameter_the_catalog_marks_sensitive_is_reference_only() -> None:
    # A component's identifier type can mark more parameters sensitive than ComponentIdentifier lists.
    parameters = [*_PARAMETERS, Parameter(name="signing_secret", description="", param_type=str, sensitive=True)]
    referenced = _recipe({}).model_copy(
        update={"credentials": {"signing_secret": CredentialReference(env_var="SIGNING_SECRET")}}
    )

    with pytest.raises(ValueError, match="'signing_secret' cannot be sent as a value"):
        InstanceCredentials.check_recipe(recipe=_recipe({"signing_secret": "abc"}), parameters=parameters)
    assert InstanceCredentials.check_recipe(recipe=referenced, parameters=parameters) == referenced


@pytest.mark.parametrize(
    "params",
    [
        {"api_key": None},
        {"endpoint": "https://api.test/v1?model=gpt-4o&key="},
        {"endpoint": "data:text/plain;base64,a2V5PXNlY3JldA=="},
        {"endpoint": "see https://user:pw@a.test/ for details"},
        {"patterns": {"password": r"password\s*=\s*\S+"}},
        {"settings": {"max_tokens": 5, "authorization": None, "prompt_cache_key": "team"}},
        {"settings": {"tools": [{"function": {"parameters": {"properties": {"password": {"type": "string"}}}}}]}},
        {"settings": {"key": "value", "eos_token": "</s>", "params": {"api-version": "2024-10-21", "key": ""}}},
        {"params": {"model": "gpt-4o", "stream": "true"}},
        {"settings": {"authentication": {"type": "system_assigned_managed_identity"}}},
        {"params": "api-version=2024-10-21"},
        {"params": [["api-version", "1"]]},
        {"settings": {"query": "how do I reset my password"}},
        {"settings": {"query": "find key=value pairs in my notes"}},
        {"endpoint": "https://api.test/login?reset_password=true&has_secret=false"},
        {"endpoint": "https://graph.microsoft.com/v1.0/users/alice@contoso.com/messages"},
        {"endpoint": "https://api.test/users//@me?email=a@b.test"},
        {"endpoint": "https://login.test/authorize?redirect_uri=https%3A%2F%2Fapp.test&login_hint=alice%40contoso.com"},
        {"endpoint": "https://login.test/authorize?redirect_uri=https://app.test&login_hint=alice@contoso.com"},
        {"params": {"key": 1, "code": "0", "has_secret": False}},
        {
            "settings": {
                "with_credentials": True,
                "tools": [
                    {
                        "function": {
                            "parameters": {
                                "properties": {"api_key": {"type": "string"}, "endpoint": {"type": "string"}},
                                "dependentRequired": {"api_key": ["endpoint"]},
                            }
                        }
                    }
                ],
            }
        },
        {
            "settings": {
                "tools": [
                    {
                        "function": {
                            "parameters": {
                                "type": "object",
                                "allOf": [
                                    {"properties": {"api_key": {"type": "string"}, "endpoint": {"type": "string"}}}
                                ],
                                "dependentRequired": {"api_key": ["endpoint"]},
                            }
                        }
                    },
                    {
                        "function": {
                            "parameters": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                                "dependencies": {"password": ["username"]},
                            }
                        }
                    },
                ]
            }
        },
        {
            "settings": {
                "tools": [
                    {
                        "function": {
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "token": {"type": "string"},
                                    "user": {"type": "string"},
                                    "login": {"anyOf": [{"dependentRequired": {"password": ["user"]}}]},
                                },
                                "allOf": [
                                    {"dependentRequired": {"token": ["user"]}},
                                    {"if": {"required": ["token"]}, "then": {"dependencies": {"api_key": ["user"]}}},
                                ],
                            }
                        }
                    }
                ]
            }
        },
    ],
)
def test_check_recipe_keeps_values_that_hold_no_credential(params: dict[str, Any]) -> None:
    checked = InstanceCredentials.check_recipe(recipe=_recipe(params), parameters=_PARAMETERS)

    assert checked.params == {name: value for name, value in params.items() if name != "api_key"}


def test_redact_removes_values_in_every_form_and_the_secrets_inside_them() -> None:
    credentials: dict[str, object] = {
        "api_key": 'sk-"quoted"-1',
        "headers": {"Authorization": "Bearer tok-123456"},
        "http_request": "GET https://user:pw-123456@api.test/chat?sig=sig-123456 HTTP/1.1\nCookie: session=abc123456",
    }
    message = " ".join(
        [
            json.dumps({"key": 'sk-"quoted"-1'}),
            "token tok-123456",
            "url https://user:pw-123456@api.test/chat?sig=sig-123456",
            "cookie session=abc123456",
        ]
    )

    redacted = InstanceCredentials.redact(message=message, credentials=credentials)

    for secret in ("quoted", "tok-123456", "pw-123456", "sig-123456", "abc123456"):
        assert secret not in redacted
    assert "api.test/chat" in redacted


def test_redact_keeps_ordinary_query_values_of_a_credential() -> None:
    credentials: dict[str, object] = {"http_request": "GET https://api.test/chat?retry=true&code=fn-123456 HTTP/1.1"}

    redacted = InstanceCredentials.redact(message="expected true, got fn-123456", credentials=credentials)

    assert redacted == "expected true, got ***"


def test_redact_removes_a_short_credential_only_where_it_stands_alone() -> None:
    redacted = InstanceCredentials.redact(message="key abc is invalid; abcdef stays", credentials={"api_key": "abc"})

    assert redacted == "key *** is invalid; abcdef stays"


def test_redact_removes_short_secrets_inside_a_credential_where_they_stand_alone() -> None:
    credentials: dict[str, object] = {
        "http_request": "GET https://me:pw@api.test:99999/f?code=q7z HTTP/1.1\nHost: api.test\n\n",
        "headers": {"Authorization": "Bearer t0k"},
    }
    message = (
        "Invalid port in HTTP destination: https://me:pw@api.test:99999/f?code=q7z "
        "(https%3A%2F%2Fme%3Apw%40api.test), token t0k; q7zz, apw, and t0ken stay"
    )

    redacted = InstanceCredentials.redact(message=message, credentials=credentials)

    assert redacted == (
        "Invalid port in HTTP destination: https://***@api.test:99999/f?code=*** "
        "(https%3A%2F%2F***%40api.test), token ***; q7zz, apw, and t0ken stay"
    )


def test_redact_removes_the_secrets_of_the_url_a_raw_request_target_and_host_make() -> None:
    credentials: dict[str, object] = {
        "http_request": "GET /f?code=fn-123456 HTTP/1.1\nHost: user:pw-123456@api.test:99999\n\n",
    }
    message = "Invalid port in HTTP destination: https://user:pw-123456@api.test:99999/f?code=fn-123456"

    redacted = InstanceCredentials.redact(message=message, credentials=credentials)

    assert redacted == "Invalid port in HTTP destination: https://***@api.test:99999/f?code=***"


def test_redact_matches_escapes_in_either_letter_case() -> None:
    message = 'token sk%2f%C3%a9%2Bkey%3d1234 or "sk/\\u00E9+key=1234"'

    redacted = InstanceCredentials.redact(message=message, credentials={"api_key": "sk/é+key=1234"})

    assert redacted == 'token *** or "***"'


def test_redact_removes_the_secrets_inside_a_json_object_credential() -> None:
    credentials: dict[str, object] = {"headers": '{"Authorization": "Bearer tok-123456", "X-Keys": ["key-654321"]}'}

    redacted = InstanceCredentials.redact(
        message="token tok-123456 and key key-654321 rejected", credentials=credentials
    )

    assert redacted == "token *** and key *** rejected"


@pytest.mark.parametrize(
    ("http_request", "message"),
    [
        ("GET f?code=fn-123456 HTTP/1.1\nHost: api.test:99999\n\n", "https://api.test:99999f?code=fn-123456"),
        ("GET ?code=fn-123456 HTTP/1.1\r\nHost: api.test:99999\r\n\r\n", "https://api.test:99999?code=fn-123456"),
        ("GET /f?code=fn#123456 HTTP/1.1\nHost: api.test:99999\n\n", "https://api.test:99999/f?code=fn#123456"),
        ("GET /f HTTP/1.1\nHost: user:pw#123456@api.test:99999\n\n", "https://user:pw#123456@api.test:99999/f"),
        ("GET /f HTTP/1.1\nHost: user:pw/123456@api.test:99999\n\n", "https://user:pw/123456@api.test:99999/f"),
        (
            "GET /f HTTP/1.1\nHost: user:pw-123456@api.test\uff1a443\n\n",
            "netloc 'user:pw-123456@api.test\uff1a443' contains invalid characters under NFKC normalization",
        ),
        (
            "CONNECT user:pw-123456@api.test:99999 HTTP/1.1\nHost: api.test:99999\n\n",
            "https://api.test:99999user:pw-123456@api.test:99999",
        ),
        (
            "GET https://user:pw/123456@api.test/f HTTP/1.1\nHost: api.test\n\n",
            "Invalid port in HTTP destination: https://user:pw/123456@api.test/f",
        ),
        (
            "GET https://user:pw#123456@api.test/f HTTP/1.1\nHost: api.test\n\n",
            "Invalid port in HTTP destination: https://user:pw#123456@api.test/f",
        ),
        (
            "GET //user:pw/123456@evil.test/f HTTP/1.1\nHost: api.test\n\n",
            "https://api.test//user:pw/123456@evil.test/f",
        ),
        ("GET /f HTTP/1.1\nHost: user:12/123456@api.test\n\n", "https://user:12/123456@api.test/f"),
        (
            "GET https://alice@example.com:pw/123456@api.test/f HTTP/1.1\nHost: api.test\n\n",
            "Invalid port in HTTP destination: https://alice@example.com:pw/123456@api.test/f",
        ),
    ],
)
def test_redact_removes_the_secrets_of_a_raw_request_that_a_url_parser_rejects(http_request: str, message: str) -> None:
    redacted = InstanceCredentials.redact(message=message, credentials={"http_request": http_request})

    assert "123456" not in redacted
    assert "api.test" in redacted


@pytest.mark.parametrize(
    ("http_request", "message"),
    [
        (
            "GET /users/@me HTTP/1.1\nHost: api.github.com:99999\n\n",
            "Invalid port in HTTP destination: https://api.github.com:99999/users/@me",
        ),
        (
            "GET /port@example.com/messages HTTP/1.1\nHost: api.test:99999\n\n",
            "Invalid port in HTTP destination: https://api.test:99999/port@example.com/messages",
        ),
        (
            "GET https://api.github.com:99999/users/@me HTTP/1.1\nHost: api.github.com\n\n",
            "Invalid port in HTTP destination: https://api.github.com:99999/users/@me",
        ),
        (
            "GET https://graph.test:99999/v1.0/users/alice@contoso.com/messages HTTP/1.1\nHost: graph.test\n\n",
            "Invalid port in HTTP destination: https://graph.test:99999/v1.0/users/alice@contoso.com/messages",
        ),
    ],
)
def test_redact_keeps_at_signs_that_are_not_user_information(http_request: str, message: str) -> None:
    assert InstanceCredentials.redact(message=message, credentials={"http_request": http_request}) == message


def test_redact_removes_the_secrets_of_a_url_inside_a_raw_request_body() -> None:
    credentials: dict[str, object] = {
        "http_request": 'POST /f HTTP/1.1\nHost: api.test\n\n{"callback":"https://user:pw/123456@hook.test/x"}',
    }

    redacted = InstanceCredentials.redact(message="callback password pw/123456 rejected", credentials=credentials)

    assert redacted == "callback password *** rejected"


def test_redact_masks_the_credentials_of_any_url_left_in_the_message() -> None:
    credentials: dict[str, object] = {"http_request": "GET /f?code=fn~123456 HTTP/1.1\nHost: api.test\n\n"}

    redacted = InstanceCredentials.redact(
        message="bad URL https://api.test/f?code=fn%7E123456", credentials=credentials
    )

    assert redacted == "bad URL https://api.test/f?code=***"


def test_redact_removes_the_secrets_of_a_deeply_nested_json_credential() -> None:
    nested = "[" * 5000 + '"tok-123456"' + "]" * 5000
    too_deep_to_decode = "[" * 50000 + '"tok-654321"' + "]" * 50000

    assert InstanceCredentials.redact(message="token tok-123456", credentials={"headers": nested}) == "token ***"
    assert InstanceCredentials.redact(message="token tok-654321", credentials={"headers": too_deep_to_decode}) == (
        "token ***"
    )


def test_redact_removes_encoded_and_tab_separated_secrets_inside_a_credential() -> None:
    credentials: dict[str, object] = {
        "http_request": "GET https://api.test/chat?code=fn%2Bsecret%2F1 HTTP/1.1\nAuthorization: Bearer\ttok-123456",
    }
    message = "bad URL https://api.test/chat?code=fn%2Bsecret%2F1 (fn+secret/1) and token tok-123456"

    redacted = InstanceCredentials.redact(message=message, credentials=credentials)

    for secret in ("fn%2Bsecret%2F1", "fn+secret/1", "tok-123456"):
        assert secret not in redacted
