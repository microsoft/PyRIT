# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from typing import Any

import pytest

from pyrit.models.catalog.registry_recipe import RestoreFailureReason
from pyrit.registry.components.target_registry import TargetRegistry
from pyrit.registry.registry_recipe_rules import SavedEntryRefusedError, check_saved_entry_params

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
    TargetRegistry.reset_registry_singleton()
    yield
    TargetRegistry.reset_registry_singleton()


def _check(class_name: str, params: dict[str, Any]) -> None:
    registry = TargetRegistry.get_registry_singleton()
    cls = registry.get_class(class_name)
    check_saved_entry_params(cls=cls, parameters=registry.get_class_metadata(cls).parameters, params=params)


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
        ("PromptShieldTarget", {"endpoint": "https://contoso.cognitiveservices.azure.com/"}, "api_key"),
        ("AzureBlobStorageTarget", {"container_url": "https://contoso.blob.core.windows.net/prompts"}, "sas_token"),
        ("GitHubCopilotTarget", {"model_name": "gpt-4o"}, "github_token"),
        ("HuggingFaceChatTarget", {"model_id": "microsoft/Phi-3-mini-4k-instruct"}, "hf_access_token"),
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
        "//user:SECRET@contoso.example/v1",
        "https://user:SECRET\uff20contoso.example/v1",
        "https://contoso.example/v1#access_token=SECRET",
        "https://contoso.example/v1?code=SECRET",
        "https://contoso.example/v1\uff1fcode=SECRET",
        "https://[contoso.example/v1?key=SECRET",
    ],
)
def test_check_saved_entry_params_refuses_a_url_that_can_carry_a_credential(url: str) -> None:
    refusal = _refusal("OpenAIChatTarget", {"auth_mode": "identity", "endpoint": url, "headers": "{}"})

    assert refusal.reason is RestoreFailureReason.UNSUPPORTED_CREDENTIAL
    assert str(refusal).startswith("'endpoint' can carry a credential in a URL")
    assert "SECRET" not in str(refusal)


def test_check_saved_entry_params_reads_every_url_in_a_list() -> None:
    refusal = _refusal("RoundRobinTarget", {"targets": ["contoso_gpt4o", "https://user:SECRET@contoso.example"]})

    assert str(refusal).startswith("'targets' can carry a credential in a URL")
