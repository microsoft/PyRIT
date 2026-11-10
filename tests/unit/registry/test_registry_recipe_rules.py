# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest

from pyrit.models.catalog import RestoreFailureReason, ServerCredential
from pyrit.registry import ConverterRegistry, TargetRegistry
from pyrit.registry.registry_recipe_rules import (
    SavedEntryRefusedError,
    check_saveable_class,
    check_saved_entry_params,
    resolve_listed_credential,
)

_ENDPOINT = "https://contoso.openai.azure.com/openai/v1"
_OPENAI_CLASSES = (
    "OpenAIChatTarget",
    "OpenAICompletionTarget",
    "OpenAIImageTarget",
    "OpenAIResponseTarget",
    "OpenAITTSTarget",
    "OpenAIVideoTarget",
    "RealtimeTarget",
)


@pytest.fixture(autouse=True)
def isolated_target_registry() -> Iterator[None]:
    for registry in (TargetRegistry, ConverterRegistry):
        registry.reset_registry_singleton()
    yield
    for registry in (TargetRegistry, ConverterRegistry):
        registry.reset_registry_singleton()


def _check(class_name: str, params: dict[str, Any]) -> None:
    registry = TargetRegistry.get_registry_singleton()
    cls = registry.get_class(class_name)
    check_saved_entry_params(cls=cls, parameters=registry.get_class_metadata(cls).parameters, params=params)


def test_check_saved_entry_params_accepts_a_target_that_takes_no_credentials() -> None:
    assert TargetRegistry.get_registry_singleton().get_class("TextTarget").supported_auth_modes == ()

    _check("TextTarget", {})


def _refusal(class_name: str, params: dict[str, Any]) -> SavedEntryRefusedError:
    with pytest.raises(SavedEntryRefusedError) as refusal:
        _check(class_name, params)
    return refusal.value


@pytest.mark.parametrize(
    ("class_name", "params"),
    [
        *((name, {"auth_mode": "identity", "endpoint": _ENDPOINT, "headers": "{}"}) for name in _OPENAI_CLASSES),
        ("OpenAIChatTarget", {"auth_mode": "none", "endpoint": "http://localhost:11434/v1"}),
        ("OpenAIChatTarget", {"auth_mode": "none", "endpoint": "http://localhost:11434/v1", "headers": "{}"}),
        ("AzureMLChatTarget", {"auth_mode": "identity", "endpoint": "https://contoso.inference.ml.azure.com/score"}),
        ("PromptShieldTarget", {"auth_mode": "identity", "endpoint": "https://contoso.cognitiveservices.azure.com/"}),
        (
            "AzureBlobStorageTarget",
            {"auth_mode": "identity", "container_url": "https://contoso.blob.core.windows.net/prompts"},
        ),
        ("A2ATarget", {"endpoint": "https://agent.example.test"}),
        ("TextTarget", {}),
        ("RoundRobinTarget", {"targets": ["contoso_gpt4o", "contoso_gpt4o_mini"]}),
    ],
)
def test_check_saved_entry_params_accepts_entra_id_and_no_key(class_name: str, params: dict[str, Any]) -> None:
    _check(class_name, params)


@pytest.mark.parametrize("class_name", _OPENAI_CLASSES)
@pytest.mark.parametrize("auth_mode", [None, "api_key"])
def test_check_saved_entry_params_refuses_an_openai_target_without_a_key_or_identity(
    class_name: str, auth_mode: str | None
) -> None:
    params = {"endpoint": _ENDPOINT, "headers": "{}"} | ({"auth_mode": auth_mode} if auth_mode else {})

    refusal = _refusal(class_name, params)

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal) == f"'api_key' is empty, so '{class_name}' can use this server's credential instead."


@pytest.mark.parametrize(
    ("class_name", "params", "name"),
    [
        ("AzureMLChatTarget", {"endpoint": "https://contoso.inference.ml.azure.com/score"}, "api_key"),
        ("AzureMLChatTarget", {"endpoint": "https://contoso.inference.ml.azure.com/score", "api_key": ""}, "api_key"),
        ("PromptShieldTarget", {"endpoint": "https://contoso.cognitiveservices.azure.com/"}, "api_key"),
        ("AzureBlobStorageTarget", {"container_url": "https://contoso.blob.core.windows.net/prompts"}, "sas_token"),
        ("GitHubCopilotTarget", {"model_name": "gpt-4o"}, "github_token"),
        ("HackAPromptTarget", {"challenge_slug": "pathogen", "competition_slug": "cbrne"}, "session_id"),
        ("LiteLLMChatTarget", {"model_name": "gpt-4o"}, "api_key"),
        ("LiteLLMDecisionTarget", {"model_name": "gpt-4o"}, "api_key"),
    ],
)
def test_check_saved_entry_params_refuses_an_empty_key_the_class_reads_from_this_server(
    class_name: str, params: dict[str, Any], name: str
) -> None:
    refusal = _refusal(class_name, params)

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal).startswith(f"'{name}' is empty, so '{class_name}' can use this server's credential")


def test_check_saved_entry_params_refuses_identity_without_headers() -> None:
    refusal = _refusal("OpenAIChatTarget", {"auth_mode": "identity", "endpoint": _ENDPOINT})

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal).endswith("Saved entries set it to '{}'.")


@pytest.mark.parametrize("auth_mode", ["identity", "none"])
def test_check_saved_entry_params_refuses_headers_that_can_carry_a_credential(auth_mode: str) -> None:
    headers = '{"api-key": "sk-header-secret"}'

    refusal = _refusal("OpenAIChatTarget", {"auth_mode": auth_mode, "endpoint": _ENDPOINT, "headers": headers})

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal) == "'headers' can carry a credential, which saved entries can't hold."


@pytest.mark.parametrize(
    ("class_name", "phrase"),
    [
        ("WebSocketCopilotTarget", "Copilot account"),
        ("HTTPTarget", "raw HTTP request"),
        ("HTTPXAPITarget", "uploads files"),
        ("HuggingFaceChatTarget", "Hugging Face token or model files"),
    ],
)
def test_check_saved_entry_params_refuses_a_class_that_cant_be_saved(class_name: str, phrase: str) -> None:
    refusal = _refusal(class_name, {})

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CLASS
    assert phrase in str(refusal)


@pytest.mark.parametrize(
    "url",
    [
        _ENDPOINT,
        "https://contoso.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21",
        "https://contoso.openai.azure.com/openai/v1?API-Version=preview",
        "http://localhost:11434/v1",
    ],
)
def test_check_saved_entry_params_accepts_a_url_without_a_credential(url: str) -> None:
    _check("OpenAIChatTarget", {"auth_mode": "identity", "endpoint": url, "headers": "{}"})


@pytest.mark.parametrize(
    "url",
    [
        "https://contoso.blob.core.windows.net/prompts?sv=2024-11-04&sig=SECRET",
        "https://contoso.blob.core.windows.net/prompts%3Fsv%3D2024-11-04%26sig%3DSECRET",
        "https://contoso.blob.core.windows.net/prompts%253Fsig%253DSECRET",
        "https://contoso.blob.core.windows.net/prompts&sig=SECRET",
        "https://user:SECRET@contoso.example/v1",
        "https://SECRET@contoso.example/v1",
        "https://contoso.example/v1#access_token=SECRET",
        "https://contoso.example/v1?code=SECRET",
        "https://contoso.example/v1\uff1fcode=SECRET",
        "https://contoso.example/v1?api-version=2024-10-21;code=SECRET",
        "https://contoso.example/v1%2525253Fsig%2525253DSECRET",
        "https://contoso.example/v1\uff053Fsig\uff053DSECRET",
        "https://contoso.example/v1?=SECRET",
        "https://contoso.example/v1?api-version=2024-10-21&=SECRET",
        "https://contoso.example/v1?api-version=2024-10-21?code=SECRET",
        "https://contoso.example/v1;code=SECRET",
        "https://contoso.example/v1;SECRET",
        "https://contoso.example/v1&SECRET",
        "https://contoso.blob.core.windows.net;sig=SECRET/prompts",
        "https://contoso.blob.core.windows.net;SIG=SECRET/prompts",
        "https://contoso.example/v1?sv=SECRET",
        "https://contoso.openai.azure.com/openai/v1?api-key=SECRET",
        "https://contoso.cognitiveservices.azure.com/?subscription-key=SECRET",
        "https://contoso.example/v1?api-version=2024-10-21=SECRET",
        "https://contoso.example/v1/key=SECRET",
    ],
)
def test_check_saved_entry_params_refuses_a_url_that_can_carry_a_credential(url: str) -> None:
    refusal = _refusal("OpenAIChatTarget", {"auth_mode": "identity", "endpoint": url, "headers": "{}"})

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal).startswith("'endpoint' can carry a credential in a URL")
    assert "SECRET" not in str(refusal)


def test_check_saved_entry_params_refuses_a_path_it_cant_finish_reading() -> None:
    card_path = "%41"
    for _ in range(10):
        card_path = quote(card_path, safe="")

    refusal = _refusal("A2ATarget", {"endpoint": "https://agent.example.test", "agent_card_path": card_path})

    assert str(refusal).startswith("'agent_card_path' can carry a credential in a URL")


@pytest.mark.parametrize(
    ("class_name", "params", "name"),
    [
        ("OpenAIChatTarget", {"auth_mode": "identity", "headers": "{}"}, "endpoint"),
        ("OpenAIChatTarget", {"auth_mode": "identity", "endpoint": "   ", "headers": "{}"}, "endpoint"),
        ("AzureMLChatTarget", {"auth_mode": "identity"}, "endpoint"),
        ("PromptShieldTarget", {"auth_mode": "identity"}, "endpoint"),
        ("AzureBlobStorageTarget", {"auth_mode": "identity"}, "container_url"),
    ],
)
def test_check_saved_entry_params_refuses_a_destination_left_to_this_server(
    class_name: str, params: dict[str, Any], name: str
) -> None:
    refusal = _refusal(class_name, params)

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal).startswith(f"'{name}' is empty, so '{class_name}' would send requests where this server's")


@pytest.mark.parametrize("card_path", [None, "/.well-known/agent-card.json", "agents/redteam/card.json"])
def test_check_saved_entry_params_accepts_an_agent_card_path_without_a_credential(card_path: str | None) -> None:
    params = {"endpoint": "https://agent.example.test"} | ({"agent_card_path": card_path} if card_path else {})

    _check("A2ATarget", params)


@pytest.mark.parametrize(
    "card_path",
    [
        "card?access_token=SECRET",
        "/.well-known/agent.json?api_key=SECRET",
        "card#SECRET",
        "//SECRET@h",
        "card.json&access_token=SECRET",
        "card.json;session=SECRET",
        "card.json;SECRET",
        "card.json&SECRET",
        "card.json?api-version=1",
    ],
)
def test_check_saved_entry_params_refuses_an_agent_card_path_that_can_carry_a_credential(card_path: str) -> None:
    refusal = _refusal("A2ATarget", {"endpoint": "https://agent.example.test", "agent_card_path": card_path})

    assert str(refusal).startswith("'agent_card_path' can carry a credential in a URL")
    assert "SECRET" not in str(refusal)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://agent.example.test",
        "file://localhost/tmp/card.json",
        "https:agent.example.test/card",
        "https://[agent.example.test?access_token=SECRET",
        123,
    ],
)
def test_check_saved_entry_params_refuses_an_agent_endpoint_that_isnt_a_plain_address(endpoint: object) -> None:
    refusal = _refusal("A2ATarget", {"endpoint": endpoint})

    assert str(refusal).startswith("'endpoint' can carry a credential in a URL")


@pytest.mark.parametrize(
    ("class_name", "params"),
    [
        ("AzureMLChatTarget", {"auth_mode": "none", "endpoint": "https://contoso.inference.ml.azure.com/score"}),
        ("GitHubCopilotTarget", {"auth_mode": "identity", "model_name": "gpt-4o"}),
        ("OpenAIChatTarget", {"auth_mode": ["identity"], "endpoint": _ENDPOINT, "headers": "{}"}),
    ],
)
def test_check_saved_entry_params_refuses_an_auth_mode_the_class_doesnt_support(
    class_name: str, params: dict[str, Any]
) -> None:
    refusal = _refusal(class_name, params)

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert f"'{class_name}' only supports auth_mode" in str(refusal)


def test_check_saved_entry_params_leaves_text_that_isnt_a_destination_alone() -> None:
    registry = ConverterRegistry.get_registry_singleton()
    cls = registry.get_class("PromptTemplateConverter")
    params = {"template": "https://example.com/search?q={{ prompt }}&sig=1#top"}

    check_saved_entry_params(cls=cls, parameters=registry.get_class_metadata(cls).parameters, params=params)


@pytest.mark.parametrize(
    "endpoint",
    [
        "contoso.openai.azure.com/openai/v1",
        "/openai/v1",
        "//contoso.openai.azure.com/openai/v1",
        "//user:SECRET@contoso.example/v1",
        "https://[contoso.example/v1?key=SECRET",
        "https:/contoso.example/v1?code=SECRET",
        "https:/user:SECRET@contoso.example/v1",
        "ftp://contoso.example/v1",
        "contoso.example/v1?code=SECRET",
        "https://user:SECRET\uff20contoso.example/v1",
    ],
)
def test_check_saved_entry_params_refuses_an_endpoint_that_isnt_a_full_address(endpoint: str) -> None:
    refusal = _refusal("OpenAIChatTarget", {"auth_mode": "identity", "endpoint": endpoint, "headers": "{}"})

    assert refusal.reason is RestoreFailureReason.INVALID_RECIPE
    assert str(refusal) == "'endpoint' must be a full http(s) or ws(s) address in a saved target."


@pytest.mark.parametrize("class_name", _OPENAI_CLASSES)
def test_check_saved_entry_params_refuses_an_openai_target_while_the_server_sets_sdk_headers(
    class_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Ocp-Apim-Subscription-Key: server-secret")

    refusal = _refusal(class_name, {"auth_mode": "identity", "endpoint": _ENDPOINT, "headers": "{}"})

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert "OPENAI_CUSTOM_HEADERS" in str(refusal)
    assert "server-secret" not in str(refusal)


def test_check_saved_entry_params_ignores_sdk_headers_for_other_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Ocp-Apim-Subscription-Key: server-secret")

    _check("A2ATarget", {"endpoint": "https://agent.example.test"})
    _check("TextTarget", {})


@pytest.mark.parametrize(
    ("class_name", "auth_modes"),
    [
        *((class_name, ("identity", "none")) for class_name in _OPENAI_CLASSES),
        ("AzureMLChatTarget", ("identity",)),
        ("PromptShieldTarget", ("identity",)),
        ("AzureBlobStorageTarget", ("identity",)),
        ("A2ATarget", ("api_key",)),
        ("TextTarget", ("api_key",)),
        ("RoundRobinTarget", ("api_key",)),
    ],
)
def test_check_saveable_class_lists_the_auth_modes_a_saved_target_can_use(
    class_name: str, auth_modes: tuple[str, ...]
) -> None:
    cls = TargetRegistry.get_registry_singleton().get_class(class_name)

    assert check_saveable_class(cls) == auth_modes


@pytest.mark.parametrize(
    ("class_name", "reason", "message"),
    [
        (
            "GitHubCopilotTarget",
            RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            (
                "'GitHubCopilotTarget' needs a credential in 'github_token', which saved entries can't hold or take "
                "from this server yet."
            ),
        ),
        (
            "HackAPromptTarget",
            RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            (
                "'HackAPromptTarget' needs a credential in 'session_id', which saved entries can't hold or take from "
                "this server yet."
            ),
        ),
        (
            "LiteLLMChatTarget",
            RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            (
                "'LiteLLMChatTarget' needs a credential in 'api_key', which saved entries can't hold or take from this "
                "server yet."
            ),
        ),
        (
            "LiteLLMDecisionTarget",
            RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            (
                "'LiteLLMDecisionTarget' needs a credential in 'api_key', which saved entries can't hold or take from "
                "this server yet."
            ),
        ),
        (
            "HTTPTarget",
            RestoreFailureReason.UNSUPPORTED_CLASS,
            "'HTTPTarget' can't be saved. Its raw HTTP request can carry credentials.",
        ),
        (
            "HTTPXAPITarget",
            RestoreFailureReason.UNSUPPORTED_CLASS,
            "'HTTPXAPITarget' uploads files from a folder the server chooses, which restore can't set yet.",
        ),
        (
            "HuggingFaceChatTarget",
            RestoreFailureReason.UNSUPPORTED_CLASS,
            (
                "'HuggingFaceChatTarget' can't be saved. It needs a Hugging Face token or model files on this server, "
                "and saved targets can hold neither yet."
            ),
        ),
        (
            "WebSocketCopilotTarget",
            RestoreFailureReason.UNSUPPORTED_CLASS,
            "'WebSocketCopilotTarget' can't be saved. It signs in with the Copilot account this server is set up with.",
        ),
    ],
)
def test_check_saveable_class_refuses_a_class_that_can_never_be_saved(
    class_name: str, reason: RestoreFailureReason, message: str
) -> None:
    cls = TargetRegistry.get_registry_singleton().get_class(class_name)

    with pytest.raises(SavedEntryRefusedError) as refusal:
        check_saveable_class(cls)

    assert refusal.value.reason is reason
    assert str(refusal.value) == message


def test_check_saveable_class_agrees_with_the_check_a_save_runs() -> None:
    registry = TargetRegistry.get_registry_singleton()
    for metadata in registry.get_all_registered_class_metadata():
        cls = registry.get_class(metadata.class_name)
        params: dict[str, Any] = {
            name: value for name, value in cls.server_fallback_parameters.items() if value is not None
        }
        params |= {name: _ENDPOINT for name, server_setting in cls.destination_parameters.items() if server_setting}
        try:
            saveable_auth_modes = check_saveable_class(cls)
        except SavedEntryRefusedError:
            saveable_auth_modes = ()

        for auth_mode in cls.supported_auth_modes:
            entry = {**params, "auth_mode": auth_mode}
            if auth_mode in saveable_auth_modes:
                check_saved_entry_params(cls=cls, parameters=metadata.parameters, params=entry)
            else:
                with pytest.raises(SavedEntryRefusedError):
                    check_saved_entry_params(cls=cls, parameters=metadata.parameters, params=entry)


_LISTED = ServerCredential(name="contoso", env_var="CONTOSO_TEST_KEY", endpoint="https://api.contoso.com/v1")
_MISMATCH = (
    "Server credential 'contoso' is only sent to https://api.contoso.com/v1, so 'endpoint' must be that address."
)


def _check_with_listed_key(params: dict[str, Any]) -> None:
    registry = TargetRegistry.get_registry_singleton()
    cls = registry.get_class("OpenAIChatTarget")
    check_saved_entry_params(
        cls=cls,
        parameters=registry.get_class_metadata(cls).parameters,
        params=params,
        listed_credential_parameter="api_key",
    )


def test_check_saved_entry_params_accepts_an_empty_key_a_server_credential_fills() -> None:
    _check_with_listed_key({"auth_mode": "api_key", "endpoint": "https://api.contoso.com/v1", "headers": "{}"})


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            {"auth_mode": "api_key", "endpoint": "https://api.contoso.com/v1", "headers": "{}", "api_key": "typed"},
            "Saved entries can't hold credentials ('api_key').",
        ),
        (
            {"auth_mode": "api_key", "endpoint": "https://api.contoso.com/v1"},
            (
                "'headers' is empty, so 'OpenAIChatTarget' can use this server's credential instead. "
                "Saved entries set it to '{}'."
            ),
        ),
    ],
)
def test_check_saved_entry_params_still_refuses_what_a_server_credential_doesnt_fill(
    params: dict[str, Any], message: str
) -> None:
    with pytest.raises(SavedEntryRefusedError) as refusal:
        _check_with_listed_key(params)

    assert str(refusal.value) == message


@pytest.mark.parametrize("class_name", _OPENAI_CLASSES)
def test_openai_targets_take_a_server_credential_as_their_key_for_their_endpoint(class_name: str) -> None:
    cls = TargetRegistry.get_registry_singleton().get_class(class_name)

    assert cls.listed_credential_parameters == ("api_key", "endpoint")


@pytest.mark.parametrize("endpoint", ["https://api.contoso.com/v1", "HTTPS://API.Contoso.com:443/v1/"])
def test_resolve_listed_credential_returns_the_key_for_its_endpoint(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    monkeypatch.setenv("CONTOSO_TEST_KEY", "listed-key")
    cls = TargetRegistry.get_registry_singleton().get_class("OpenAIChatTarget")

    resolved = resolve_listed_credential(
        cls=cls, params={"endpoint": endpoint}, name="contoso", server_credentials=[_LISTED]
    )

    assert resolved == ("api_key", "listed-key")


@pytest.mark.parametrize(
    ("class_name", "params", "credentials", "message"),
    [
        ("TextTarget", {}, [_LISTED], "'TextTarget' can't use a server credential."),
        (
            "OpenAIChatTarget",
            {"endpoint": "https://api.contoso.com/v1"},
            [ServerCredential(name="fabrikam", env_var="CONTOSO_TEST_KEY", endpoint="https://api.contoso.com/v1")],
            "This server has no server credential named 'contoso'.",
        ),
        ("OpenAIChatTarget", {"endpoint": "https://api.contoso.com/v2"}, [_LISTED], _MISMATCH),
        ("OpenAIChatTarget", {"endpoint": "http://api.contoso.com/v1"}, [_LISTED], _MISMATCH),
        ("OpenAIChatTarget", {"endpoint": "https://api.contoso.com:8443/v1"}, [_LISTED], _MISMATCH),
        ("OpenAIChatTarget", {"endpoint": "https://api.contoso.com.example.test/v1"}, [_LISTED], _MISMATCH),
        ("OpenAIChatTarget", {"endpoint": "https://api.contoso.com/v1?api-version=1"}, [_LISTED], _MISMATCH),
        (
            "OpenAIChatTarget",
            {"endpoint": "https://[2001:db8::1:444]/v1"},
            [ServerCredential(name="contoso", env_var="CONTOSO_TEST_KEY", endpoint="https://[2001:db8::1]:444/v1")],
            (
                "Server credential 'contoso' is only sent to https://[2001:db8::1]:444/v1, so 'endpoint' must be "
                "that address."
            ),
        ),
        ("OpenAIChatTarget", {}, [_LISTED], _MISMATCH),
    ],
)
def test_resolve_listed_credential_refuses_a_credential_this_entry_cant_use(
    monkeypatch: pytest.MonkeyPatch,
    class_name: str,
    params: dict[str, Any],
    credentials: list[ServerCredential],
    message: str,
) -> None:
    monkeypatch.setenv("CONTOSO_TEST_KEY", "listed-key")
    cls = TargetRegistry.get_registry_singleton().get_class(class_name)

    with pytest.raises(SavedEntryRefusedError) as refusal:
        resolve_listed_credential(cls=cls, params=params, name="contoso", server_credentials=credentials)

    assert refusal.value.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal.value) == message


@pytest.mark.parametrize("value", [None, ""])
def test_resolve_listed_credential_refuses_a_key_that_isnt_set(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("CONTOSO_TEST_KEY", raising=False)
    else:
        monkeypatch.setenv("CONTOSO_TEST_KEY", value)
    cls = TargetRegistry.get_registry_singleton().get_class("OpenAIChatTarget")

    with pytest.raises(SavedEntryRefusedError) as refusal:
        resolve_listed_credential(
            cls=cls, params={"endpoint": "https://api.contoso.com/v1"}, name="contoso", server_credentials=[_LISTED]
        )

    assert refusal.value.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal.value) == "Server credential 'contoso' isn't set on this server."


@pytest.mark.parametrize("class_name", _OPENAI_CLASSES)
def test_check_saveable_class_refuses_an_openai_class_while_the_server_sets_sdk_headers(
    class_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Ocp-Apim-Subscription-Key: server-secret")
    cls = TargetRegistry.get_registry_singleton().get_class(class_name)

    with pytest.raises(SavedEntryRefusedError, match="OPENAI_CUSTOM_HEADERS") as refusal:
        check_saveable_class(cls)

    assert refusal.value.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert "server-secret" not in str(refusal.value)
