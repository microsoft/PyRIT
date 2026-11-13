# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend target service.
"""

import asyncio
import json
import os
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from pyrit.backend.models import CreateTargetRequest
from pyrit.backend.services import TargetService, get_target_service
from pyrit.backend.services.saved_entry_restore import build_saved_entry_async
from pyrit.models import ComponentIdentifier
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RegistryRestoreReport,
    RestoreFailureReason,
    SavedEntryOwner,
    ServerCredential,
    TargetInstance,
)
from pyrit.models.component_spec import SourceInstanceSpec
from pyrit.prompt_target import GitHubCopilotTarget, PromptTarget, TargetCapabilities, TextTarget
from pyrit.registry import NotSaveableError, SavedEntryConflictError, TargetRegistry
from pyrit.registry.instance_registry import registration_origin
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from unit.mocks import MockPromptTarget


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the TargetRegistry singleton before each test."""
    TargetRegistry.reset_registry_singleton()
    yield
    TargetRegistry.reset_registry_singleton()


def _mock_target_identifier(*, class_name: str = "MockTarget", **kwargs) -> ComponentIdentifier:
    """Create a mock target identifier using ComponentIdentifier."""
    params = {
        "endpoint": kwargs.get("endpoint"),
        "model_name": kwargs.get("model_name"),
        "temperature": kwargs.get("temperature"),
        "top_p": kwargs.get("top_p"),
        "max_requests_per_minute": kwargs.get("max_requests_per_minute"),
    }
    # Filter out None values to match ComponentIdentifier.of behavior
    clean_params = {k: v for k, v in params.items() if v is not None}
    return ComponentIdentifier(
        class_name=class_name,
        class_module="tests.unit.backend.test_target_service",
        params=clean_params,
    )


def _mock_prompt_target(*, identifier: ComponentIdentifier | None = None) -> MagicMock:
    """Create a MagicMock PromptTarget with an identifier and default capabilities."""
    mock_target = MagicMock(spec=PromptTarget)
    mock_target.get_identifier.return_value = identifier if identifier is not None else _mock_target_identifier()
    mock_target.capabilities = TargetCapabilities()
    mock_target.generation_ranges = {}
    return mock_target


async def _test_token_provider() -> str:
    """Shared async token provider used in Entra authentication tests."""
    return "test-token"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("value", [None, "", "   "])
async def test_manual_model_field_never_inherits_ambient_default_async(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "ambient-model")
    request = CreateTargetRequest(
        type="OpenAIChatTarget",
        params={"endpoint": "https://api.openai.com", "api_key": "test-key", "model_name": value},
        auth_mode="api_key",
    )
    with pytest.raises(ValueError, match="explicit connection fields: model_name"):
        await TargetService().create_target_async(request=request)


class TestListTargets:
    """Tests for TargetService.list_targets method."""

    async def test_list_targets_returns_empty_when_no_targets(self) -> None:
        """Test that list_targets returns empty list when no targets exist."""
        service = TargetService()

        result = await service.list_targets_async()

        assert result.items == []
        assert result.pagination.has_more is False

    async def test_list_targets_returns_targets_from_registry(self) -> None:
        """Test that list_targets returns targets from registry."""
        service = TargetService()

        # Register a mock target
        mock_target = _mock_prompt_target(identifier=_mock_target_identifier(endpoint="http://test"))
        service._registry.instances.register(mock_target, name="target-1")

        result = await service.list_targets_async()

        assert len(result.items) == 1
        assert result.items[0].target_registry_name == "target-1"
        assert result.items[0].identifier.class_name == "MockTarget"
        assert result.pagination.has_more is False

    async def test_list_targets_paginates_with_limit(self) -> None:
        """Test that list_targets respects the limit parameter."""
        service = TargetService()

        for i in range(5):
            mock_target = _mock_prompt_target()
            service._registry.instances.register(mock_target, name=f"target-{i}")

        result = await service.list_targets_async(limit=3)

        assert len(result.items) == 3
        assert result.pagination.limit == 3
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == result.items[-1].target_registry_name

    async def test_list_targets_cursor_returns_next_page(self) -> None:
        """Test that list_targets cursor skips to the correct position."""
        service = TargetService()

        for i in range(5):
            mock_target = _mock_prompt_target()
            service._registry.instances.register(mock_target, name=f"target-{i}")

        first_page = await service.list_targets_async(limit=2)
        second_page = await service.list_targets_async(limit=2, cursor=first_page.pagination.next_cursor)

        assert len(second_page.items) == 2
        assert second_page.items[0].target_registry_name != first_page.items[0].target_registry_name
        assert second_page.pagination.has_more is True

    async def test_list_targets_last_page_has_no_more(self) -> None:
        """Test that the last page has has_more=False and no next_cursor."""
        service = TargetService()

        for i in range(3):
            mock_target = _mock_prompt_target()
            service._registry.instances.register(mock_target, name=f"target-{i}")

        first_page = await service.list_targets_async(limit=2)
        last_page = await service.list_targets_async(limit=2, cursor=first_page.pagination.next_cursor)

        assert len(last_page.items) == 1
        assert last_page.pagination.has_more is False
        assert last_page.pagination.next_cursor is None


class TestGetTarget:
    """Tests for TargetService.get_target method."""

    async def test_get_target_returns_none_for_nonexistent(self) -> None:
        """Test that get_target returns None for non-existent target."""
        service = TargetService()

        result = await service.get_target_async(target_registry_name="nonexistent-id")

        assert result is None

    async def test_get_target_returns_target_from_registry(self) -> None:
        """Test that get_target returns target built from registry object."""
        service = TargetService()

        mock_target = _mock_prompt_target()
        service._registry.instances.register(mock_target, name="target-1")

        result = await service.get_target_async(target_registry_name="target-1")

        assert result is not None
        assert result.target_registry_name == "target-1"
        assert result.identifier.class_name == "MockTarget"

    async def test_list_targets_includes_extra_params_in_target_specific(self) -> None:
        """Test that extra identifier params (reasoning_effort etc.) appear in target_specific_params."""
        service = TargetService()

        identifier = ComponentIdentifier(
            class_name="OpenAIResponseTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "o3",
                "temperature": 1.0,
                "reasoning_effort": "high",
                "reasoning_summary": "auto",
                "max_output_tokens": 4096,
            },
        )
        mock_target = _mock_prompt_target(identifier=identifier)
        service._registry.instances.register(mock_target, name="response-target")

        result = await service.list_targets_async()

        assert len(result.items) == 1
        target = result.items[0]
        assert target.identifier.temperature == 1.0
        assert target.target_specific_params is not None
        assert target.target_specific_params["reasoning_effort"] == "high"
        assert target.target_specific_params["reasoning_summary"] == "auto"
        assert target.target_specific_params["max_output_tokens"] == 4096

    async def test_get_target_includes_extra_params_in_target_specific(self) -> None:
        """Test that get_target returns target_specific_params with extra identifier params."""
        service = TargetService()

        identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
                "frequency_penalty": 0.5,
                "seed": 42,
            },
        )
        mock_target = _mock_prompt_target(identifier=identifier)
        service._registry.instances.register(mock_target, name="chat-target")

        result = await service.get_target_async(target_registry_name="chat-target")

        assert result is not None
        assert result.target_specific_params is not None
        assert result.target_specific_params["frequency_penalty"] == 0.5
        assert result.target_specific_params["seed"] == 42


class TestGetTargetObject:
    """Tests for TargetService.get_target_object method."""

    def test_get_target_object_returns_none_for_nonexistent(self) -> None:
        """Test that get_target_object returns None for non-existent target."""
        service = TargetService()

        result = service.get_target_object(target_registry_name="nonexistent-id")

        assert result is None

    def test_get_target_object_returns_object_from_registry(self) -> None:
        """Test that get_target_object returns the actual target object."""
        service = TargetService()
        mock_target = MagicMock(spec=PromptTarget)
        service._registry.instances.register(mock_target, name="target-1")

        result = service.get_target_object(target_registry_name="target-1")

        assert result is mock_target


class TestListTargetTypes:
    """Tests for TargetService.list_target_types_async method."""

    def test_types_reject_unsupported_auth_mode(self) -> None:
        with pytest.raises(ValueError, match="Unsupported target authentication mode: 'oauth'"):
            TargetService._get_supported_auth_modes(("oauth",))

    async def test_types_return_known_target_types(self) -> None:
        """The projection exposes constructible target classes from the registry."""
        service = TargetService()

        result = await service.list_target_types_async()

        target_types = [item.target_type for item in result.items]
        assert "OpenAIChatTarget" in target_types
        assert "AzureMLChatTarget" in target_types

    async def test_types_include_declarative_auth_facts(self) -> None:
        """Type entries surface the per-class auth facts the frontend needs."""
        service = TargetService()

        result = await service.list_target_types_async()

        openai_entry = next(item for item in result.items if item.target_type == "OpenAIChatTarget")
        assert openai_entry.supported_auth_modes == ["api_key", "identity", "none"]
        azure_ml_entry = next(item for item in result.items if item.target_type == "AzureMLChatTarget")
        assert azure_ml_entry.supported_auth_modes == ["api_key", "identity"]

    async def test_types_report_no_auth_modes_for_credential_free_target_async(self) -> None:
        """TextTarget needs no credentials, so its type entry advertises no auth modes."""
        service = TargetService()

        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == "TextTarget")
        assert entry.supported_auth_modes == []

    async def test_types_say_which_auth_modes_a_saved_target_can_use_and_why_none_can(self) -> None:
        service = TargetService()

        result = await service.list_target_types_async()

        entries = {item.target_type: item for item in result.items}
        saveability = {
            target_type: (entries[target_type].saveable_auth_modes, entries[target_type].not_saveable_reason)
            for target_type in ("OpenAIChatTarget", "AzureMLChatTarget", "TextTarget", "GitHubCopilotTarget")
        }
        assert saveability == {
            "OpenAIChatTarget": (["identity", "none"], None),
            "AzureMLChatTarget": (["identity"], None),
            "TextTarget": (["api_key"], None),
            "GitHubCopilotTarget": (
                [],
                (
                    "'GitHubCopilotTarget' needs a credential in 'github_token', which saved entries can't hold or "
                    "take from this server yet."
                ),
            ),
        }
        assert entries["HTTPTarget"].saveable_auth_modes == []
        assert entries["HTTPTarget"].not_saveable_reason == (
            "'HTTPTarget' can't be saved. Its raw HTTP request can carry credentials."
        )

    async def test_types_include_github_copilot_form_parameters_async(self) -> None:
        service = TargetService()

        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == "GitHubCopilotTarget")
        parameters = {parameter.name: parameter for parameter in entry.parameters}
        assert parameters["model_name"].required
        assert not parameters["github_token"].required
        assert parameters["github_token"].sensitive
        assert {"working_directory", "retain_session", "response_timeout_seconds", "max_requests_per_minute"} <= set(
            parameters
        )
        assert not {"endpoint", "api_key", "underlying_model"} & set(parameters)
        assert "identity" not in entry.supported_auth_modes

    async def test_types_include_hugging_face_target_async(self) -> None:
        service = TargetService()

        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == "HuggingFaceChatTarget")
        assert "trust_remote_code" in {parameter.name for parameter in entry.parameters}

    async def test_types_include_azure_blob_target_with_both_auth_modes_async(self) -> None:
        service = TargetService()

        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == "AzureBlobStorageTarget")
        assert {"container_url", "sas_token"} <= {parameter.name for parameter in entry.parameters}
        assert "api_key" in entry.supported_auth_modes
        assert "identity" in entry.supported_auth_modes

    @pytest.mark.parametrize(
        ("target_type", "parameter_names"),
        [
            ("OpenAIChatTarget", {"endpoint", "model_name"}),
            ("AzureBlobStorageTarget", {"container_url"}),
            ("HackAPromptTarget", {"cookie", "session_id"}),
            ("HuggingFaceChatTarget", {"hf_access_token"}),
            ("PromptShieldTarget", {"endpoint"}),
            ("AzureMLChatTarget", {"endpoint"}),
        ],
    )
    async def test_types_mark_env_backed_parameters_required(
        self,
        target_type: str,
        parameter_names: set[str],
    ) -> None:
        """Environment-backed values enforced by targets are required metadata."""
        service = TargetService()

        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == target_type)
        parameters = {parameter.name: parameter for parameter in entry.parameters}
        assert all(parameters[name].required for name in parameter_names)

    async def test_types_include_structured_parameters(self) -> None:
        service = TargetService()

        types_result = await service.list_target_types_async()

        types_entry = next(item for item in types_result.items if item.target_type == "RoundRobinTarget")
        targets_parameter = next(param for param in types_entry.parameters if param.name == "targets")
        assert targets_parameter.reference_type == "target"
        assert targets_parameter.type_name == "list[str]"
        assert targets_parameter.is_list is True
        weights_parameter = next(param for param in types_entry.parameters if param.name == "weights")
        assert weights_parameter.type_name == "list[int] | None"
        assert weights_parameter.is_list is True
        assert weights_parameter.required is False

    async def test_types_keep_registry_parameter_order_without_mutating_metadata(self) -> None:
        service = TargetService()
        result = await service.list_target_types_async()
        metadata_by_name = {
            metadata.class_name: metadata for metadata in service._registry.get_all_registered_class_metadata()
        }

        for entry in result.items:
            entry_names = [parameter.name for parameter in entry.parameters]
            registry_names = [parameter.name for parameter in metadata_by_name[entry.target_type].parameters]
            assert entry_names == [name for name in registry_names if name in entry_names]

        registry_openai = {parameter.name: parameter for parameter in metadata_by_name["OpenAIChatTarget"].parameters}
        assert registry_openai["endpoint"].required is False
        assert registry_openai["model_name"].required is False

    async def test_types_expose_component_external_inputs(self) -> None:
        service = TargetService()
        result = await service.list_target_types_async()

        parameters = {entry.target_type: {parameter.name for parameter in entry.parameters} for entry in result.items}
        assert {"PlaywrightTarget", "PlaywrightCopilotTarget", "WebsocketTarget"}.isdisjoint(parameters)
        assert "api_key" in parameters["OpenAIChatTarget"]
        assert "custom_configuration" not in parameters["OpenAIChatTarget"]
        assert "n_seconds" in parameters["OpenAIVideoTarget"]

    async def test_registry_metadata_keeps_parameters_the_api_cannot_set(self) -> None:
        metadata = TargetService()._registry.get_registered_class_metadata("OpenAIChatTarget")
        assert metadata is not None
        registry_parameters = {parameter.name: parameter for parameter in metadata.parameters}

        assert not registry_parameters["custom_configuration"].is_external_input

    async def test_types_describe_the_video_duration_as_an_int(self) -> None:
        service = TargetService()
        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == "OpenAIVideoTarget")
        n_seconds = next(
            parameter for parameter in entry.model_dump(mode="json")["parameters"] if parameter["name"] == "n_seconds"
        )
        assert {key: n_seconds[key] for key in ("type_name", "is_list", "choices", "default", "required")} == {
            "type_name": "int",
            "is_list": False,
            "choices": None,
            "default": "4",
            "required": False,
        }
        metadata = service._registry.get_registered_class_metadata("OpenAIVideoTarget")
        assert metadata is not None
        registry_n_seconds = next(parameter for parameter in metadata.parameters if parameter.name == "n_seconds")
        assert "Literal['4', '8', '12']" in registry_n_seconds.type_name

    async def test_types_cold_and_warm_results_are_equal(self) -> None:
        service = TargetService()

        cold = await service.list_target_types_async()
        warm = await service.list_target_types_async()

        assert cold == warm

    async def test_types_refresh_after_runtime_class_registration(self) -> None:
        service = TargetService()
        initial = await service.list_target_types_async()

        service._registry.register_class(MockPromptTarget)
        refreshed = await service.list_target_types_async()

        assert all(item.target_type != "MockPromptTarget" for item in initial.items)
        assert any(item.target_type == "MockPromptTarget" for item in refreshed.items)

    @pytest.mark.parametrize(
        ("target_type", "parameter_name", "type_name", "required", "choices"),
        [
            (
                "AzureBlobStorageTarget",
                "blob_content_type",
                "SupportedContentType",
                False,
                ["text/plain", "text/html"],
            ),
        ],
    )
    async def test_types_include_enum_parameters(
        self,
        target_type: str,
        parameter_name: str,
        type_name: str,
        required: bool,
        choices: list[str],
    ) -> None:
        """Enum parameters are exposed with their required state and allowed values."""
        service = TargetService()

        result = await service.list_target_types_async()

        entry = next(item for item in result.items if item.target_type == target_type)
        parameter = next(param for param in entry.parameters if param.name == parameter_name)
        assert parameter.required is required
        assert parameter.type_name == type_name
        assert parameter.choices == choices

    async def test_types_leave_out_upload_targets_without_server_upload_folder(self) -> None:
        service = TargetService()

        result = await service.list_target_types_async()

        target_types = {item.target_type for item in result.items}
        assert "HTTPXAPITarget" not in target_types
        assert "HTTPTarget" in target_types

    async def test_types_list_upload_targets_without_their_upload_folder(self, tmp_path: Path) -> None:
        service = TargetService()

        result = await service.list_target_types_async(upload_directory=str(tmp_path))

        entry = next(item for item in result.items if item.target_type == "HTTPXAPITarget")
        parameter_names = {parameter.name for parameter in entry.parameters}
        assert "allowed_upload_directory" not in parameter_names
        assert "file_path" in parameter_names


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("github_token", "environment_token", "expected_token"),
    [
        ("test-github-token", "", "test-github-token"),
        (None, "environment-github-token", "environment-github-token"),
        ("test-github-token", "environment-github-token", "test-github-token"),
    ],
)
async def test_create_github_copilot_target_async(
    *, github_token: str | None, environment_token: str, expected_token: str
) -> None:
    service = TargetService()
    params = {"model_name": "gpt-5-mini"}
    if github_token is not None:
        params["github_token"] = github_token

    with (
        patch.dict("sys.modules", {"copilot": ModuleType("copilot")}),
        patch.dict(os.environ, {"GITHUB_TOKEN": environment_token}),
    ):
        target = await service.create_target_async(
            request=CreateTargetRequest(type="GitHubCopilotTarget", params=params)
        )

    assert target.identifier.class_name == "GitHubCopilotTarget"
    assert target.identifier.params["model_name"] == "gpt-5-mini"
    target_obj = service.get_target_object(target_registry_name=target.target_registry_name)
    assert isinstance(target_obj, GitHubCopilotTarget)
    assert target_obj._github_token == expected_token
    assert "github_token" not in target.model_dump_json()
    assert "test-github-token" not in target.model_dump_json()
    assert "environment-github-token" not in target.model_dump_json()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("github_token", "environment_token"),
    [
        (None, None),
        (None, ""),
        (None, " \t"),
        ("", ""),
        (" \t", ""),
        ("", "environment-github-token"),
    ],
)
async def test_create_github_copilot_target_requires_token_async(
    *, github_token: str | None, environment_token: str | None
) -> None:
    service = TargetService()
    params = {"model_name": "gpt-5-mini"}
    if github_token is not None:
        params["github_token"] = github_token
    environment = {"GH_TOKEN": "sdk-fallback-token"}
    if environment_token is not None:
        environment["GITHUB_TOKEN"] = environment_token

    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(service._registry, "create_instance_async") as create_instance,
        pytest.raises(
            ValueError,
            match=r"Enter GitHub Token or configure GITHUB_TOKEN for this CoPyRIT instance.*\.env/\.env\.local",
        ),
    ):
        await service.create_target_async(
            request=CreateTargetRequest(name="copilot", type="GitHubCopilotTarget", params=params)
        )

    create_instance.assert_not_called()
    assert service.get_target_object(target_registry_name="copilot") is None


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("register", [True, False])
async def test_create_github_copilot_target_accepts_working_directory_async(tmp_path: Path, register: bool) -> None:
    params = {"model_name": "gpt-5-mini", "github_token": "test-github-token", "working_directory": str(tmp_path)}

    with patch.dict("sys.modules", {"copilot": ModuleType("copilot")}):
        target = await TargetService().create_target_async(
            request=CreateTargetRequest(
                name="copilot" if register else None, type="GitHubCopilotTarget", register=register, params=params
            )
        )

    assert target.identifier.params["working_directory"] == str(tmp_path.resolve())


class _FileTarget(MockPromptTarget):
    """A target with a parameter that names a file on the server."""

    def __init__(self, *, data_file: Path | None = None) -> None:
        super().__init__()
        self.data_file = data_file


class _RequiredFileTarget(MockPromptTarget):
    """A target that cannot be built without a file on the server."""

    def __init__(self, *, data_file: Path) -> None:
        super().__init__()
        self.data_file = data_file


class _RequiredUploadTarget(MockPromptTarget):
    """A target that uploads local files from a folder only the server sets."""

    upload_directory_parameter = "upload_dir"

    def __init__(self, *, upload_dir: Path) -> None:
        super().__init__()
        self.upload_dir = upload_dir


@pytest.mark.usefixtures("patch_central_database")
async def test_target_path_parameters_are_left_out_and_rejected() -> None:
    service = TargetService()
    service._registry.register_class(_FileTarget)
    service._registry.register_class(_RequiredFileTarget)

    types = await service.list_target_types_async()
    with pytest.raises(ValueError, match="'data_file' of '_FileTarget' names a server file"):
        await service.create_target_async(
            request=CreateTargetRequest(name="files", type="_FileTarget", params={"data_file": "/etc/hosts"})
        )

    entries = {item.target_type: item for item in types.items}
    assert "data_file" not in [parameter.name for parameter in entries["_FileTarget"].parameters]
    assert "_RequiredFileTarget" not in entries
    assert service.get_target_object(target_registry_name="files") is None


@pytest.mark.usefixtures("patch_central_database")
async def test_types_list_a_target_whose_required_path_is_the_server_upload_folder(tmp_path: Path) -> None:
    service = TargetService()
    service._registry.register_class(_RequiredUploadTarget)

    with_folder = await service.list_target_types_async(upload_directory=str(tmp_path))
    without_folder = await service.list_target_types_async()

    entry = next(item for item in with_folder.items if item.target_type == "_RequiredUploadTarget")
    assert "upload_dir" not in [parameter.name for parameter in entry.parameters]
    assert all(item.target_type != "_RequiredUploadTarget" for item in without_folder.items)


class _RebuildableFolderTarget(MockPromptTarget):
    """A rebuildable target with a folder callers may set and an upload folder only the server sets."""

    upload_directory_parameter = "allowed_upload_directory"
    caller_path_parameters = frozenset({"working_directory"})

    def __init__(
        self,
        *,
        label: str = "source",
        working_directory: Path | None = None,
        allowed_upload_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self.label = label
        self.working_directory = working_directory
        self.allowed_upload_directory = allowed_upload_directory

    def get_reconstruction_parameters(self) -> dict[str, object]:
        return {
            "label": self.label,
            "working_directory": self.working_directory,
            "allowed_upload_directory": self.allowed_upload_directory,
        }


def _register_folder_source(*, service: TargetService, folder: Path) -> _RebuildableFolderTarget:
    service._registry.register_class(_RebuildableFolderTarget)
    source = _RebuildableFolderTarget(working_directory=folder, allowed_upload_directory=folder)
    service._registry.instances.register(source, name="folders")
    return source


@pytest.mark.usefixtures("patch_central_database")
async def test_build_from_source_rejects_an_upload_folder_override_async(tmp_path: Path) -> None:
    service = TargetService()
    source = _register_folder_source(service=service, folder=tmp_path)
    spec = SourceInstanceSpec(
        source_name="folders", source_hash=source.get_identifier().hash, params={"allowed_upload_directory": "/"}
    )

    with pytest.raises(
        ValueError, match="'allowed_upload_directory' of '_RebuildableFolderTarget' names a server file"
    ):
        await service.build_from_source_async(spec)


@pytest.mark.usefixtures("patch_central_database")
async def test_build_from_source_accepts_a_working_directory_override_async(tmp_path: Path) -> None:
    service = TargetService()
    source = _register_folder_source(service=service, folder=tmp_path)
    other = tmp_path / "other"

    rebuilt = await service.build_from_source_async(
        SourceInstanceSpec(
            source_name="folders", source_hash=source.get_identifier().hash, params={"working_directory": str(other)}
        )
    )

    assert isinstance(rebuilt, _RebuildableFolderTarget)
    assert rebuilt.working_directory == other
    assert rebuilt.allowed_upload_directory == tmp_path


@pytest.mark.usefixtures("patch_central_database")
async def test_build_from_source_keeps_the_source_folders_async(tmp_path: Path) -> None:
    service = TargetService()
    source = _register_folder_source(service=service, folder=tmp_path)

    rebuilt = await service.build_from_source_async(
        SourceInstanceSpec(source_name="folders", source_hash=source.get_identifier().hash, params={"label": "copy"})
    )

    assert isinstance(rebuilt, _RebuildableFolderTarget)
    assert rebuilt is not source
    assert rebuilt.label == "copy"
    assert rebuilt.working_directory == tmp_path
    assert rebuilt.allowed_upload_directory == tmp_path


@pytest.mark.usefixtures("patch_central_database")
class TestCreateTargetCodeExecution:
    """Admin gate for targets whose construction runs model repository code."""

    @staticmethod
    def _request(params: dict[str, object] | None = None) -> CreateTargetRequest:
        return CreateTargetRequest(
            name="hf",
            type="HuggingFaceChatTarget",
            params={"model_id": "m", "hf_access_token": "test-token", **(params or {})},
        )

    @pytest.mark.parametrize(
        ("params", "expected_written"),
        [
            ({}, None),
            ({"trust_remote_code": False}, False),
            ({"trust_remote_code": "false"}, False),
            ({"trust_remote_code": "0"}, False),
            ({"trust_remote_code": "no"}, False),
        ],
    )
    async def test_remote_code_disabled_is_allowed_for_non_admin(
        self, params: dict[str, object], expected_written: bool | None
    ) -> None:
        service = TargetService()
        resolved = dict(params)

        await service._enforce_code_execution_permission_async(
            target_type="HuggingFaceChatTarget", params=resolved, is_admin=False
        )

        assert resolved.get("trust_remote_code") is expected_written

    @pytest.mark.parametrize("value", [True, "true", "True", " TRUE ", "1", "yes"])
    async def test_remote_code_enabled_requires_admin(self, value: object) -> None:
        service = TargetService()

        with pytest.raises(PermissionError, match="requires administrator access"):
            await service._enforce_code_execution_permission_async(
                target_type="HuggingFaceChatTarget", params={"trust_remote_code": value}, is_admin=False
            )

    @pytest.mark.parametrize("value", [True, "true", "1", "yes"])
    async def test_admin_may_enable_remote_code_and_value_is_coerced(self, value: object) -> None:
        service = TargetService()
        resolved: dict[str, object] = {"trust_remote_code": value}

        await service._enforce_code_execution_permission_async(
            target_type="HuggingFaceChatTarget", params=resolved, is_admin=True
        )

        assert resolved["trust_remote_code"] is True

    @pytest.mark.parametrize("is_admin", [False, True])
    @pytest.mark.parametrize("value", [1, 2, 1.0, [True]])
    async def test_non_boolean_remote_code_is_rejected(self, value: object, is_admin: bool) -> None:
        service = TargetService()

        with pytest.raises(ValueError, match="trust_remote_code"):
            await service._enforce_code_execution_permission_async(
                target_type="HuggingFaceChatTarget", params={"trust_remote_code": value}, is_admin=is_admin
            )

    async def test_target_without_code_execution_parameter_is_untouched(self) -> None:
        service = TargetService()
        resolved = {"endpoint": "https://example.test"}

        await service._enforce_code_execution_permission_async(
            target_type="OpenAIChatTarget", params=resolved, is_admin=False
        )

        assert resolved == {"endpoint": "https://example.test"}

    @pytest.mark.parametrize("value", [True, "true", "1", "yes"])
    async def test_create_target_rejects_non_admin_remote_code_before_construction(self, value: object) -> None:
        service = TargetService()

        with (
            patch.object(service._registry, "create_instance_async") as create_instance,
            pytest.raises(PermissionError, match="requires administrator access"),
        ):
            await service.create_target_async(request=self._request({"trust_remote_code": value}), is_admin=False)

        create_instance.assert_not_called()
        assert service.get_target_object(target_registry_name="hf") is None

    async def test_create_target_rejects_non_boolean_remote_code_before_construction(self) -> None:
        service = TargetService()

        with (
            patch.object(service._registry, "create_instance_async") as create_instance,
            pytest.raises(ValueError, match="trust_remote_code"),
        ):
            await service.create_target_async(request=self._request({"trust_remote_code": 1}), is_admin=False)

        create_instance.assert_not_called()

    async def test_create_target_without_code_execution_parameter_ignores_admin_flag(self, sqlite_instance) -> None:
        service = TargetService()

        target = await service.create_target_async(
            request=CreateTargetRequest(name="text", type="TextTarget", params={}), is_admin=False
        )

        assert target.identifier.class_name == "TextTarget"

    @pytest.mark.parametrize("value", [True, "true", "yes"])
    async def test_create_target_forwards_coerced_true_to_constructor_for_admin(self, value: object) -> None:
        service = TargetService()
        stop = RuntimeError("stop after construction")

        with (
            patch.object(service._registry, "create_instance_async", side_effect=stop) as create_instance,
            pytest.raises(RuntimeError, match="stop after construction"),
        ):
            await service.create_target_async(request=self._request({"trust_remote_code": value}), is_admin=True)

        assert create_instance.call_args.kwargs["params"]["trust_remote_code"] is True

    @pytest.mark.parametrize("value", [False, "false", "no", "0"])
    async def test_create_target_forwards_coerced_false_to_constructor_for_non_admin(self, value: object) -> None:
        service = TargetService()
        stop = RuntimeError("stop after construction")

        with (
            patch.object(service._registry, "create_instance_async", side_effect=stop) as create_instance,
            pytest.raises(RuntimeError, match="stop after construction"),
        ):
            await service.create_target_async(request=self._request({"trust_remote_code": value}), is_admin=False)

        assert create_instance.call_args.kwargs["params"]["trust_remote_code"] is False

    async def test_declared_code_execution_parameter_missing_from_metadata_fails_closed(self) -> None:
        service = TargetService()
        hugging_face_cls = service._registry.get_class("HuggingFaceChatTarget")

        with (
            patch.object(hugging_face_cls, "code_execution_parameter", "nonexistent_flag"),
            pytest.raises(ValueError, match="does not expose it"),
        ):
            await service._enforce_code_execution_permission_async(
                target_type="HuggingFaceChatTarget", params={"nonexistent_flag": True}, is_admin=False
            )


@pytest.mark.usefixtures("patch_central_database")
class TestCreateTargetServerCredential:
    """Admin gate for targets that would authenticate with the server's own stored credentials."""

    @staticmethod
    def _request(params: dict[str, object] | None = None, *, auth_mode: str = "api_key") -> CreateTargetRequest:
        return CreateTargetRequest(
            name="blob",
            type="AzureBlobStorageTarget",
            params={"container_url": "https://test.blob.core.windows.net/test", **(params or {})},
            auth_mode=auth_mode,  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize("value", ["sas-token", "   leading-space"])
    def test_caller_supplied_credential_is_allowed_for_non_admin(self, value: str) -> None:
        service = TargetService()

        service._enforce_server_credential_permission(
            target_type="AzureBlobStorageTarget", params={"sas_token": value}, is_admin=False
        )

    @pytest.mark.parametrize("params", [{}, {"sas_token": None}, {"sas_token": ""}])
    def test_missing_credential_requires_admin(self, params: dict[str, object]) -> None:
        service = TargetService()

        with pytest.raises(PermissionError, match="requires administrator access"):
            service._enforce_server_credential_permission(
                target_type="AzureBlobStorageTarget", params=params, is_admin=False
            )

    @pytest.mark.parametrize("params", [{}, {"sas_token": ""}])
    def test_missing_credential_is_allowed_for_admin(self, params: dict[str, object]) -> None:
        service = TargetService()

        service._enforce_server_credential_permission(
            target_type="AzureBlobStorageTarget", params=params, is_admin=True
        )

    def test_target_without_server_credential_parameter_is_untouched(self) -> None:
        service = TargetService()

        service._enforce_server_credential_permission(
            target_type="OpenAIChatTarget", params={"endpoint": "https://example.test"}, is_admin=False
        )

    async def test_create_target_identity_mode_requires_admin_before_construction(self) -> None:
        service = TargetService()

        with (
            patch.object(service._registry, "create_instance_async") as create_instance,
            pytest.raises(PermissionError, match="requires administrator access"),
        ):
            await service.create_target_async(request=self._request(auth_mode="identity"), is_admin=False)

        create_instance.assert_not_called()
        assert service.get_target_object(target_registry_name="blob") is None

    async def test_create_target_identity_mode_with_caller_sas_requires_admin(self) -> None:
        """Identity mode drops the caller SAS, so it still authenticates with the server's credentials."""
        service = TargetService()

        with (
            patch.object(service._registry, "create_instance_async") as create_instance,
            pytest.raises(PermissionError, match="requires administrator access"),
        ):
            await service.create_target_async(
                request=self._request({"sas_token": "caller-sas"}, auth_mode="identity"), is_admin=False
            )

        create_instance.assert_not_called()

    async def test_create_target_api_key_without_sas_requires_admin_before_construction(self) -> None:
        service = TargetService()

        with (
            patch.object(service._registry, "create_instance_async") as create_instance,
            pytest.raises(PermissionError, match="requires administrator access"),
        ):
            await service.create_target_async(request=self._request(), is_admin=False)

        create_instance.assert_not_called()

    async def test_create_target_without_caller_sas_requires_admin_even_with_environment_sas(self, monkeypatch) -> None:
        """An environment SAS is a server credential, so a caller who supplies none still needs admin."""
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_SAS_TOKEN", "server-environment-sas")
        service = TargetService()

        with (
            patch.object(service._registry, "create_instance_async") as create_instance,
            pytest.raises(PermissionError, match="requires administrator access"),
        ):
            await service.create_target_async(request=self._request(), is_admin=False)

        create_instance.assert_not_called()

    async def test_create_target_with_caller_sas_reaches_construction_for_non_admin(self) -> None:
        service = TargetService()
        stop = RuntimeError("stop after construction")

        with (
            patch.object(service._registry, "create_instance_async", side_effect=stop) as create_instance,
            pytest.raises(RuntimeError, match="stop after construction"),
        ):
            await service.create_target_async(request=self._request({"sas_token": "caller-sas"}), is_admin=False)

        create_instance.assert_called_once()

    async def test_create_target_identity_mode_reaches_construction_for_admin(self) -> None:
        service = TargetService()
        stop = RuntimeError("stop after construction")

        with (
            patch.object(service._registry, "create_instance_async", side_effect=stop) as create_instance,
            pytest.raises(RuntimeError, match="stop after construction"),
        ):
            await service.create_target_async(request=self._request(auth_mode="identity"), is_admin=True)

        create_instance.assert_called_once()


class TestCreateTarget:
    """Tests for TargetService.create_target method."""

    async def test_create_target_rejects_parameters_that_take_python_objects(self, sqlite_instance) -> None:
        service = TargetService()
        request = CreateTargetRequest(name="text", type="TextTarget", params={"custom_configuration": {}})

        with pytest.raises(ValueError, match="'custom_configuration' of 'TextTarget' cannot be set through the API"):
            await service.create_target_async(request=request)

        assert service.get_target_object(target_registry_name="text") is None

    async def test_create_target_accepts_scalar_alternative_of_union(self, sqlite_instance) -> None:
        service = TargetService()
        request = CreateTargetRequest(
            name="video",
            type="OpenAIVideoTarget",
            params={
                "endpoint": "https://example.openai.azure.com/openai/v1",
                "api_key": "test-key",
                "model_name": "sora-2",
                "n_seconds": 8,
            },
        )

        await service.create_target_async(request=request)

        assert service.get_target_object(target_registry_name="video")._n_seconds == "8"

    async def test_create_target_raises_for_invalid_type(self) -> None:
        """Test that create_target raises for invalid target type."""
        service = TargetService()

        request = CreateTargetRequest(
            type="NonExistentTarget",
            params={},
        )

        with pytest.raises(ValueError, match="not found"):
            await service.create_target_async(request=request)

    async def test_create_target_success(self, sqlite_instance) -> None:
        """Test successful target creation."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
        )

        result = await service.create_target_async(request=request)

        assert result.target_registry_name is not None
        assert result.identifier.class_name == "TextTarget"

    async def test_create_target_uses_explicit_registry_name(self, sqlite_instance) -> None:
        service = TargetService()

        result = await service.create_target_async(
            request=CreateTargetRequest(name="text-target", type="TextTarget", params={}),
        )

        assert result.target_registry_name == "text-target"
        assert service.get_target_object(target_registry_name="text-target") is not None

    async def test_create_target_rejects_duplicate_name(self, sqlite_instance) -> None:
        service = TargetService()
        service._registry.instances.register(MockPromptTarget(), name="shared-name")

        with pytest.raises(ValueError, match="already exists"):
            await service.create_target_async(
                request=CreateTargetRequest(name="shared-name", type="TextTarget", params={}),
            )

    @pytest.mark.parametrize("name", ["types"])
    async def test_create_target_rejects_reserved_route_name(self, sqlite_instance, name: str) -> None:
        service = TargetService()

        with pytest.raises(ValueError, match="reserved"):
            await service.create_target_async(
                request=CreateTargetRequest(name=name, type="TextTarget", params={}),
            )

    async def test_create_target_registers_nothing_when_response_mapping_fails(self, sqlite_instance) -> None:
        service = TargetService()

        with (
            patch(
                "pyrit.backend.services.target_service.target_object_to_instance",
                side_effect=RuntimeError("mapping failed"),
            ),
            pytest.raises(RuntimeError, match="mapping failed"),
        ):
            await service.create_target_async(
                request=CreateTargetRequest(name="unmapped", type="TextTarget", params={}),
            )

        assert service.get_target_object(target_registry_name="unmapped") is None

    async def test_create_target_delegates_construction_to_registry(self, sqlite_instance) -> None:
        """Every target construction path is owned by the registry, through its external-input path."""
        service = TargetService()
        with patch.object(
            service._registry,
            "create_instance_async",
            wraps=service._registry.create_instance_async,
        ) as create:
            await service.create_target_async(request=CreateTargetRequest(type="TextTarget", params={}))

        create.assert_awaited_once_with(
            name="TextTarget", params={}, external_input=True, allowed_path_params=frozenset()
        )

    async def test_create_target_rejects_invalid_enum_value(self, sqlite_instance) -> None:
        """An invalid enum value from the JSON request raises a parameter error before target construction."""
        service = TargetService()
        request = CreateTargetRequest(
            type="AzureBlobStorageTarget",
            params={
                "container_url": "https://test.blob.core.windows.net/test",
                "sas_token": "valid_sas_token",
                "blob_content_type": "unknown",
            },
        )

        with pytest.raises(ValueError, match="Parameter 'blob_content_type'.*expected one of"):
            await service.create_target_async(request=request)

    async def test_create_azure_blob_target_coerces_content_type_string(self, sqlite_instance) -> None:
        """An explicit blob content type from JSON is coerced before target construction."""
        service = TargetService()
        request = CreateTargetRequest(
            type="AzureBlobStorageTarget",
            params={
                "container_url": "https://test.blob.core.windows.net/test",
                "sas_token": "valid_sas_token",
                "blob_content_type": "text/html",
            },
        )

        result = await service.create_target_async(request=request)

        target = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target._blob_content_type == "text/html"

    async def test_create_target_registers_in_registry(self, sqlite_instance) -> None:
        """Test that create_target registers object in registry."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
        )

        result = await service.create_target_async(request=request)

        # Object should be retrievable from registry
        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj is not None

    async def test_create_target_model_name_not_overridden_by_env_var(self, sqlite_instance) -> None:
        """Test that explicit model_name is not overridden by underlying_model env var."""
        with patch.dict(os.environ, {"OPENAI_CHAT_UNDERLYING_MODEL": "gpt-4o"}):
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={
                    "model_name": "claude-sonnet-4-6",
                    "endpoint": "https://test.openai.azure.com/",
                    "api_key": "test-key",
                },
            )

            result = await service.create_target_async(request=request)

            assert result.identifier.model_name == "claude-sonnet-4-6"
            # underlying_model_name is empty since no underlying_model was passed
            assert not result.identifier.underlying_model_name

    async def test_create_target_with_different_underlying_model(self, sqlite_instance) -> None:
        """Test that explicit underlying_model is used when it differs from model_name."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "model_name": "my-gpt4o-deployment",
                "endpoint": "https://test.openai.azure.com/",
                "api_key": "test-key",
                "underlying_model": "gpt-4o",
            },
        )

        result = await service.create_target_async(request=request)

        assert result.identifier.model_name == "my-gpt4o-deployment"
        assert result.identifier.underlying_model_name == "gpt-4o"

    async def test_create_upload_target_requires_server_upload_folder(self, sqlite_instance) -> None:
        service = TargetService()
        request = CreateTargetRequest(name="upload", type="HTTPXAPITarget", params={"http_url": "https://example.com"})

        with pytest.raises(ValueError, match="Set target_upload_directory in the server configuration"):
            await service.create_target_async(request=request)

        assert service.get_target_object(target_registry_name="upload") is None

    async def test_create_upload_target_rejects_caller_upload_folder(self, sqlite_instance, tmp_path: Path) -> None:
        service = TargetService()
        request = CreateTargetRequest(
            name="upload",
            type="HTTPXAPITarget",
            params={"http_url": "https://example.com", "allowed_upload_directory": "/"},
        )

        with pytest.raises(ValueError, match="'allowed_upload_directory' of 'HTTPXAPITarget' is set by the server"):
            await service.create_target_async(request=request, upload_directory=str(tmp_path))

        assert service.get_target_object(target_registry_name="upload") is None

    async def test_create_upload_target_uses_server_upload_folder(self, sqlite_instance, tmp_path: Path) -> None:
        service = TargetService()
        request = CreateTargetRequest(name="upload", type="HTTPXAPITarget", params={"http_url": "https://example.com"})

        await service.create_target_async(request=request, upload_directory=str(tmp_path))

        target = service.get_target_object(target_registry_name="upload")
        assert target.allowed_upload_directory == tmp_path.resolve()

    async def test_create_target_without_uploads_ignores_server_upload_folder(
        self, sqlite_instance, tmp_path: Path
    ) -> None:
        service = TargetService()
        request = CreateTargetRequest(name="text", type="TextTarget", params={})

        await service.create_target_async(request=request, upload_directory=str(tmp_path))

        assert service.get_target_object(target_registry_name="text") is not None


class TestCreateTargetEntraAuth:
    """Entra auth at the service boundary: the service only omits the api_key and
    confirms the target type supports Entra. Endpoint trust + token minting are the
    target's job and are covered in the target-level tests (see
    tests/unit/prompt_target/target/)."""

    async def test_create_openai_target_with_entra_omits_key_and_target_mints_token(self, sqlite_instance) -> None:
        """Entra path: the service omits the api_key so the target mints its own token."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with patch(
                "pyrit.auth.openai_auth.get_azure_openai_auth",
                return_value=_test_token_provider,
            ) as mock_get_auth:
                service = TargetService()

                request = CreateTargetRequest(
                    type="OpenAIChatTarget",
                    params={
                        "endpoint": "https://test.openai.azure.com/",
                        "model_name": "gpt-4o",
                    },
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                mock_get_auth.assert_called_once_with("https://test.openai.azure.com/")
                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                # OpenAI target preserves async callables verbatim through ensure_async_token_provider.
                assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]

    async def test_create_openai_target_with_identity_drops_user_api_key(self, sqlite_instance) -> None:
        """Any api_key supplied alongside auth_mode='identity' must be discarded."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with patch(
                "pyrit.auth.openai_auth.get_azure_openai_auth",
                return_value=_test_token_provider,
            ):
                service = TargetService()

                request = CreateTargetRequest(
                    type="OpenAIChatTarget",
                    params={
                        "endpoint": "https://test.openai.azure.com/",
                        "model_name": "gpt-4o",
                        "api_key": "should-be-ignored",
                    },
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]
                # The literal "should-be-ignored" string must never appear.
                assert target_obj._api_key != "should-be-ignored"  # type: ignore[attr-defined]

    async def test_create_openai_target_with_identity_does_not_mutate_request_params(self, sqlite_instance) -> None:
        """The CreateTargetRequest.params object must remain unchanged after creation."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with patch(
                "pyrit.auth.openai_auth.get_azure_openai_auth",
                return_value=_test_token_provider,
            ):
                service = TargetService()

                original_params = {
                    "endpoint": "https://test.openai.azure.com/",
                    "model_name": "gpt-4o",
                    "api_key": "original-key",
                }
                request = CreateTargetRequest(
                    type="OpenAIChatTarget",
                    params=dict(original_params),
                    auth_mode="identity",
                )

                await service.create_target_async(request=request)

                # The caller's request.params must be unchanged after the call.
                assert request.params == original_params

    async def test_create_azureml_target_with_identity_omits_key_and_target_mints_token(self, sqlite_instance) -> None:
        """AzureML identity path: the service omits the key so the target mints the ML scope token."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AZURE_ML_KEY", None)
            with patch(
                "pyrit.prompt_target.azure_ml_chat_target.get_azure_async_token_provider",
                return_value=_test_token_provider,
            ) as mock_get_provider:
                service = TargetService()

                request = CreateTargetRequest(
                    type="AzureMLChatTarget",
                    params={"endpoint": "https://my-aml.region.inference.ml.azure.com/score"},
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                mock_get_provider.assert_called_once_with("https://ml.azure.com/.default")
                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                # AzureMLChatTarget stores the provider on _api_key_provider; static _api_key is cleared.
                assert target_obj._api_key_provider is _test_token_provider  # type: ignore[attr-defined]
                assert target_obj._api_key == ""  # type: ignore[attr-defined]

    async def test_create_azure_blob_target_with_identity_preserves_auth_intent(self, sqlite_instance) -> None:
        service = TargetService()
        request = CreateTargetRequest(
            type="AzureBlobStorageTarget",
            params={"container_url": "https://test.blob.core.windows.net/test"},
            auth_mode="identity",
        )

        result = await service.create_target_async(request=request, is_admin=True)

        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj._auth_mode == "identity"  # type: ignore[attr-defined]

    async def test_create_azure_blob_target_with_identity_discards_sas_token(self, sqlite_instance) -> None:
        """A caller-supplied sas_token must not silently override identity-based auth."""
        service = TargetService()
        request = CreateTargetRequest(
            type="AzureBlobStorageTarget",
            params={
                "container_url": "https://test.blob.core.windows.net/test",
                "sas_token": "attacker-supplied-token",
            },
            auth_mode="identity",
        )

        result = await service.create_target_async(request=request, is_admin=True)

        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj._sas_token is None  # type: ignore[attr-defined]
        assert target_obj._auth_mode == "identity"  # type: ignore[attr-defined]

    async def test_create_openai_target_with_identity_non_azure_endpoint_raises(self, sqlite_instance) -> None:
        """The target (not the service) rejects an unrecognized endpoint under identity auth."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={"endpoint": "https://api.openai.com/", "model_name": "gpt-4o"},
                auth_mode="identity",
            )

            with pytest.raises(ValueError, match="Identity-based authentication requires a recognized Azure"):
                await service.create_target_async(request=request)

    async def test_create_openai_target_with_identity_ignores_env_api_key(self, sqlite_instance) -> None:
        """Regression: a key in the environment must not override an explicit identity choice."""

        with patch.dict(os.environ, {"OPENAI_CHAT_KEY": "sk-SECRET-FROM-DOTENV"}):
            with patch(
                "pyrit.auth.openai_auth.get_azure_openai_auth",
                return_value=_test_token_provider,
            ):
                service = TargetService()

                request = CreateTargetRequest(
                    type="OpenAIChatTarget",
                    params={
                        "endpoint": "https://test.openai.azure.com/",
                        "model_name": "gpt-4o",
                    },
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]

    async def test_create_azureml_target_with_identity_ignores_env_api_key(self, sqlite_instance) -> None:
        """Regression: AZURE_ML_KEY must not override an explicit identity choice."""

        with patch.dict(os.environ, {"AZURE_ML_KEY": "key-from-dotenv"}):
            with patch(
                "pyrit.prompt_target.azure_ml_chat_target.get_azure_async_token_provider",
                return_value=_test_token_provider,
            ):
                service = TargetService()

                request = CreateTargetRequest(
                    type="AzureMLChatTarget",
                    params={"endpoint": "https://my-aml.region.inference.ml.azure.com/score"},
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                assert target_obj._api_key_provider is _test_token_provider  # type: ignore[attr-defined]
                assert target_obj._api_key == ""  # type: ignore[attr-defined]

    async def test_create_target_with_api_key_mode_preserves_env_var_fallback(self, sqlite_instance) -> None:
        """api_key requests keep the historical resolution order, including the env-var fallback."""
        with patch.dict(os.environ, {"OPENAI_CHAT_KEY": "sk-from-env"}):
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={
                    "endpoint": "https://test.openai.azure.com/",
                    "model_name": "gpt-4o",
                },
            )

            result = await service.create_target_async(request=request)

            target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
            assert target_obj is not None
            assert target_obj._api_key == "sk-from-env"  # type: ignore[attr-defined]

    async def test_create_target_params_auth_mode_conflicting_with_api_key_request_raises(
        self, sqlite_instance
    ) -> None:
        """params must not be a second channel that silently overrides a supplied key with identity."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "endpoint": "https://test.openai.azure.com/",
                "model_name": "gpt-4o",
                "api_key": "sk-user-supplied",
                "auth_mode": "identity",
            },
            auth_mode="api_key",
        )

        with pytest.raises(ValueError, match="Conflicting authentication modes"):
            await service.create_target_async(request=request)

    async def test_create_target_params_auth_mode_conflicting_with_identity_request_raises(
        self, sqlite_instance
    ) -> None:
        """The opposite conflict direction is rejected too, rather than silently resolved."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "endpoint": "https://test.openai.azure.com/",
                "model_name": "gpt-4o",
                "auth_mode": "api_key",
            },
            auth_mode="identity",
        )

        with pytest.raises(ValueError, match="Conflicting authentication modes"):
            await service.create_target_async(request=request)

    async def test_create_target_params_auth_mode_matching_request_is_accepted(self, sqlite_instance) -> None:
        """A redundant but agreeing params auth_mode is harmless."""
        with patch.dict(os.environ, {"OPENAI_CHAT_KEY": "sk-SECRET-FROM-DOTENV"}):
            with patch(
                "pyrit.auth.openai_auth.get_azure_openai_auth",
                return_value=_test_token_provider,
            ):
                service = TargetService()

                request = CreateTargetRequest(
                    type="OpenAIChatTarget",
                    params={
                        "endpoint": "https://test.openai.azure.com/",
                        "model_name": "gpt-4o",
                        "auth_mode": "identity",
                    },
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]

    async def test_create_target_identity_unsupported_type_raises(self, sqlite_instance) -> None:
        """Identity-based auth is only supported for targets that declare it."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
            auth_mode="identity",
        )

        with pytest.raises(ValueError, match="does not support identity-based authentication"):
            await service.create_target_async(request=request)


class TestCreateRoundRobinTarget:
    """Service-level tests for building RoundRobinTarget through the registry.

    The service passes ``targets`` (registry names) to ``registry.create_instance``;
    the resolver turns the names into live target objects and RoundRobinTarget owns
    its own construction validation (dedup, class/config consistency). Those rules
    are covered in tests/unit/prompt_target/test_round_robin_target.py — here we only
    exercise the service wiring.
    """

    async def test_create_round_robin_target_resolves_registry_names(self, sqlite_instance) -> None:
        """RoundRobinTarget creation resolves registry names to live target objects."""
        service = TargetService()

        target_a = MockPromptTarget()
        target_b = MockPromptTarget()
        service._registry.instances.register(target_a, name="target-a")
        service._registry.instances.register(target_b, name="target-b")

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"targets": ["target-a", "target-b"], "weights": [2, 1]},
        )

        result = await service.create_target_async(request=rr_request)

        assert result.identifier.class_name == "RoundRobinTarget"
        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj._targets == [target_a, target_b]
        assert target_obj._weights == [2, 1]

    async def test_create_round_robin_target_fewer_than_2_raises(self, sqlite_instance) -> None:
        """A single inner target bubbles up RoundRobinTarget's own validation error."""
        service = TargetService()

        service._registry.instances.register(MockPromptTarget(), name="only-one")

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"targets": ["only-one"]},
        )

        with pytest.raises(ValueError, match="at least 2 targets"):
            await service.create_target_async(request=rr_request)

    async def test_create_round_robin_target_unknown_name_raises(self, sqlite_instance) -> None:
        """A non-existent registry name is rejected by the resolver."""
        service = TargetService()

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"targets": ["does-not-exist-a", "does-not-exist-b"]},
        )

        with pytest.raises(ValueError, match="not found"):
            await service.create_target_async(request=rr_request)


class TestTargetServiceSingleton:
    """Tests for get_target_service singleton function."""

    def test_get_target_service_returns_target_service(self) -> None:
        """Test that get_target_service returns a TargetService instance."""
        get_target_service.cache_clear()

        service = get_target_service()
        assert isinstance(service, TargetService)

    def test_get_target_service_returns_same_instance(self) -> None:
        """Test that get_target_service returns the same instance."""
        get_target_service.cache_clear()

        service1 = get_target_service()
        service2 = get_target_service()
        assert service1 is service2


class TestFrontendBackendCompatibilitySync:
    """Guard against drift between frontend isCompatible() and backend TARGET_EVAL_PARAMS.

    The frontend pre-filters the Create RoundRobinTarget dropdown using a hardcoded
    set of fields (target_type + TARGET_EVAL_PARAMS). If the backend adds a new
    behavioral param, this test fails and reminds the developer to update
    frontend/src/components/Config/CreateTargetDialog.tsx → isCompatible().
    """

    def test_target_eval_params_match_frontend_iscompatible(self) -> None:
        """TARGET_EVAL_PARAMS must be exactly {underlying_model_name, temperature, top_p}.

        If this fails, someone added or removed a param from TARGET_EVAL_PARAMS.
        Update the frontend isCompatible() function in CreateTargetDialog.tsx
        to check the same fields, then update the expected set here.
        """
        from pyrit.models import TARGET_EVAL_PARAMS

        expected = {"underlying_model_name", "temperature", "top_p"}
        assert expected == TARGET_EVAL_PARAMS, (
            f"TARGET_EVAL_PARAMS changed to {TARGET_EVAL_PARAMS}. "
            f"Update the frontend isCompatible() in CreateTargetDialog.tsx to match, "
            f"then update this test's expected set."
        )

    def test_target_eval_param_fallbacks_match_frontend(self) -> None:
        """TARGET_EVAL_PARAM_FALLBACKS must match the fallback rule implemented in
        the frontend effectiveUnderlyingModel() helper in CreateTargetDialog.tsx.

        If this fails, someone added or changed a fallback. Update
        effectiveUnderlyingModel() (and any sibling resolvers) in
        CreateTargetDialog.tsx so the frontend pre-filter agrees with what the
        backend RoundRobinTarget._validate_behavioral_consistency check accepts,
        then update this test's expected dict.
        """
        from pyrit.models import TARGET_EVAL_PARAM_FALLBACKS

        expected = {"underlying_model_name": "model_name"}
        assert expected == TARGET_EVAL_PARAM_FALLBACKS, (
            f"TARGET_EVAL_PARAM_FALLBACKS changed to {TARGET_EVAL_PARAM_FALLBACKS}. "
            f"Update effectiveUnderlyingModel() in CreateTargetDialog.tsx to match, "
            f"then update this test's expected dict."
        )

    def test_required_target_parameters_match_frontend_form_capabilities(self) -> None:
        """
        Guard the dynamic target form against new required Python-only inputs.

        Scalar, choice, simple-list, and dictionary parameters are rendered from
        metadata. Connection/auth parameters and RoundRobin inputs have dedicated
        controls. The remaining targets require live Python objects and must stay
        excluded until the frontend gains an explicit policy for them.
        """
        connection_parameters = {
            "api_key",
            "endpoint",
            "model_name",
            "underlying_model",
            "underlying_model_name",
        }
        scalar_types = {"bool", "float", "int", "str"}
        simple_list_types = {"list[bool]", "list[float]", "list[int]", "list[str]"}
        unsupported_required: dict[str, list[str]] = {}

        registry = TargetRegistry.get_registry_singleton()
        for metadata in registry.get_all_registered_class_metadata():
            unsupported_names: list[str] = []
            for parameter in metadata.parameters:
                if not parameter.required:
                    continue
                if parameter.name in connection_parameters:
                    continue
                if metadata.class_name == "RoundRobinTarget" and parameter.name in {"targets", "weights"}:
                    continue
                if parameter.reference_type or parameter.variants:
                    unsupported_names.append(parameter.name)
                    continue
                if parameter.choices:
                    continue
                type_name = parameter.type_name.removesuffix(" | None")
                if type_name in scalar_types or type_name in simple_list_types:
                    continue
                if type_name == "dict" or type_name.startswith("dict["):
                    continue
                unsupported_names.append(parameter.name)
            if unsupported_names:
                unsupported_required[metadata.class_name] = sorted(unsupported_names)

        assert unsupported_required == {
            "PlaywrightCopilotTarget": ["page"],
            "PlaywrightTarget": ["interaction_func", "page"],
            "WebsocketTarget": ["initialization_strings", "message_builder", "response_parser"],
        }, (
            "The target registry's required parameters changed. Update "
            "frontend/src/components/Config/targetParameterPolicy.ts with a renderable or "
            "explicitly unsupported policy, then update this expected catalog."
        )


class TestCreateTargetNoKeyAuth:
    """No-key auth at the service boundary: the service confirms support and drops any key."""

    async def test_create_openai_target_with_no_key_reads_no_server_credentials(self, sqlite_instance) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_CHAT_KEY": "server-key", "OPENAI_ADDITIONAL_REQUEST_HEADERS": '{"X-Server-Secret": "s"}'},
        ):
            service = TargetService()
            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={
                    "endpoint": "http://localhost:11434/v1",
                    "model_name": "llama3",
                    "api_key": "should-be-ignored",
                },
                auth_mode="none",
            )

            result = await service.create_target_async(request=request)

        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj._api_key == "not-needed"  # type: ignore[attr-defined]
        assert target_obj._headers == {}  # type: ignore[attr-defined]

    async def test_create_target_with_no_key_rejects_unsupported_type(self, sqlite_instance) -> None:
        service = TargetService()
        request = CreateTargetRequest(
            type="AzureMLChatTarget",
            params={"endpoint": "https://example.inference.ml.azure.com/score"},
            auth_mode="none",
        )

        with pytest.raises(ValueError, match="does not support no-key authentication"):
            await service.create_target_async(request=request)


_AZURE_ENDPOINT = "https://contoso.openai.azure.com/openai/v1"


def _save_request(
    *,
    name: str = "contoso_gpt4o",
    params: dict[str, Any] | None = None,
    auth_mode: str = "identity",
    persist: bool = True,
) -> CreateTargetRequest:
    return CreateTargetRequest(
        name=name,
        type="OpenAIChatTarget",
        params=params if params is not None else {"endpoint": _AZURE_ENDPOINT, "model_name": "gpt-4o"},
        auth_mode=auth_mode,
        persist=persist,
    )


def _saved_document(source: Path, name: str) -> dict[str, Any]:
    return json.loads((source / "targets" / f"{name}.json").read_text())


async def _restore(source: Path, *, server_credentials: list[ServerCredential] | None = None) -> RegistryRestoreReport:
    """Restore the saved targets through the API's create, as startup does."""
    get_target_service.cache_clear()
    credentials = server_credentials or []
    build_async = partial(build_saved_entry_async, upload_directory=None, server_credentials=credentials)
    restorer = RegistryRecipeRestorer(source=str(source), server_credentials=credentials)
    return await restorer.restore_async(build_async=build_async)


@pytest.mark.usefixtures("sqlite_instance")
class TestSaveTarget:
    async def test_saves_an_identity_target_that_restore_rebuilds(self, tmp_path: Path) -> None:
        service = TargetService()
        owner = SavedEntryOwner(oid="00000000-0000-0000-0000-000000000001", name="Ada")

        target = await service.create_target_async(request=_save_request(), owner=owner, recipes_source=str(tmp_path))

        assert target.origin is RegistryEntryOrigin.SAVED
        assert _saved_document(tmp_path, "contoso_gpt4o") == {
            "schema_version": 1,
            "class_module": "pyrit.prompt_target.openai.openai_chat_target",
            "class_name": "OpenAIChatTarget",
            "auth_mode": "identity",
            "params": {"endpoint": _AZURE_ENDPOINT, "model_name": "gpt-4o", "headers": "{}"},
            "owner": {"oid": owner.oid, "name": "Ada"},
        }
        saved_hash = service.get_target_object(target_registry_name="contoso_gpt4o").get_identifier().hash
        TargetRegistry.reset_registry_singleton()
        report = await _restore(tmp_path)
        assert report.failures == []
        restored = TargetRegistry.get_registry_singleton().instances.get("contoso_gpt4o")
        assert restored is not None
        assert restored.get_identifier().hash == saved_hash

    async def test_saves_a_no_key_target_without_an_owner_when_sign_in_is_off(self, tmp_path: Path) -> None:
        request = _save_request(
            name="local_llm", params={"endpoint": "http://localhost:11434/v1", "model_name": "llama3"}, auth_mode="none"
        )

        target = await TargetService().create_target_async(request=request, recipes_source=str(tmp_path))

        assert target.origin is RegistryEntryOrigin.SAVED
        document = _saved_document(tmp_path, "local_llm")
        assert document["auth_mode"] == "none"
        assert "auth_mode" not in document["params"]
        assert "owner" not in document

    async def test_a_target_that_cant_be_saved_is_neither_created_nor_saved(self, tmp_path: Path) -> None:
        service = TargetService()
        request = _save_request(
            params={"endpoint": _AZURE_ENDPOINT, "model_name": "gpt-4o", "api_key": "sk-typed-secret"},
            auth_mode="api_key",
        )

        with pytest.raises(NotSaveableError, match="can't hold credentials"):
            await service.create_target_async(request=request, recipes_source=str(tmp_path))

        assert service.get_target_object(target_registry_name="contoso_gpt4o") is None
        assert not (tmp_path / "targets").exists()

    @pytest.mark.parametrize(
        ("target_type", "params", "reason"),
        [
            (
                "HTTPTarget",
                {"http_request": "POST /chat HTTP/1.1\nHost: example.test\n\n{PROMPT}"},
                "'HTTPTarget' can't be saved. Its raw HTTP request can carry credentials.",
            ),
            (
                "WebSocketCopilotTarget",
                {},
                (
                    "'WebSocketCopilotTarget' can't be saved. It signs in with the Copilot account this server is set "
                    "up with."
                ),
            ),
        ],
    )
    async def test_refuses_to_save_a_type_that_cant_be_saved(
        self, tmp_path: Path, target_type: str, params: dict[str, str], reason: str
    ) -> None:
        service = TargetService()
        request = CreateTargetRequest(name="unsaveable", type=target_type, params=params, persist=True)

        with pytest.raises(NotSaveableError) as refusal:
            await service.create_target_async(request=request, is_admin=True, recipes_source=str(tmp_path))

        assert str(refusal.value) == f"{reason} It can still be created without saving it."
        assert service.get_target_object(target_registry_name="unsaveable") is None
        assert not (tmp_path / "targets").exists()

    async def test_a_taken_name_is_a_conflict_only_when_saving(self, tmp_path: Path) -> None:
        service = TargetService()
        await service.create_target_async(request=_save_request(persist=False))

        with pytest.raises(SavedEntryConflictError, match="already exists"):
            await service.create_target_async(request=_save_request(), recipes_source=str(tmp_path))
        with pytest.raises(ValueError, match="already exists") as temporary_clash:
            await service.create_target_async(request=_save_request(persist=False))

        assert not isinstance(temporary_clash.value, SavedEntryConflictError)
        assert not (tmp_path / "targets").exists()

    async def test_a_saved_file_with_the_name_is_a_conflict_that_leaves_the_file(self, tmp_path: Path) -> None:
        service = TargetService()
        storage = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path))
        storage.create_recipe(name="contoso_gpt4o", recipe=ComponentRecipe(class_module="m", class_name="TextTarget"))
        before = (tmp_path / "targets" / "contoso_gpt4o.json").read_bytes()

        with (
            patch("pyrit.backend.services.target_service.release_component_async", new_callable=AsyncMock) as release,
            pytest.raises(SavedEntryConflictError, match="A saved target named 'contoso_gpt4o' already exists."),
        ):
            await service.create_target_async(request=_save_request(), recipes_source=str(tmp_path))

        release.assert_awaited_once()
        assert service.get_target_object(target_registry_name="contoso_gpt4o") is None
        assert (tmp_path / "targets" / "contoso_gpt4o.json").read_bytes() == before

    async def test_two_saves_of_one_name_save_one_target(self, tmp_path: Path) -> None:
        service = TargetService()
        mini = {"endpoint": _AZURE_ENDPOINT, "model_name": "gpt-4o-mini"}

        results = await asyncio.gather(
            service.create_target_async(request=_save_request(), recipes_source=str(tmp_path)),
            service.create_target_async(request=_save_request(params=mini), recipes_source=str(tmp_path)),
            return_exceptions=True,
        )

        assert sorted(type(result).__name__ for result in results) == ["SavedEntryConflictError", "TargetInstance"]
        registered = service.get_target_object(target_registry_name="contoso_gpt4o")
        assert _saved_document(tmp_path, "contoso_gpt4o")["params"]["model_name"] == registered._model_name

    async def test_saves_a_round_robin_only_over_targets_that_come_back_after_a_restart(self, tmp_path: Path) -> None:
        service = TargetService()
        with registration_origin(RegistryEntryOrigin.CONFIGURATION):
            for name in ("startup_one", "startup_two"):
                await service.create_target_async(request=_save_request(name=name, persist=False))
        await service.create_target_async(request=_save_request(name="launch_member", persist=False))
        await service.create_target_async(request=_save_request(name="saved_member"), recipes_source=str(tmp_path))

        def pool(name: str, *members: str) -> CreateTargetRequest:
            return CreateTargetRequest(
                name=name, type="RoundRobinTarget", params={"targets": list(members)}, persist=True
            )

        with pytest.raises(NotSaveableError, match="Uses temporary target 'launch_member'"):
            await service.create_target_async(
                request=pool("pool", "startup_one", "launch_member"), recipes_source=str(tmp_path)
            )
        startup_pool = await service.create_target_async(
            request=pool("startup_pool", "startup_one", "startup_two"), recipes_source=str(tmp_path)
        )
        mixed_pool = await service.create_target_async(
            request=pool("mixed_pool", "startup_one", "saved_member"), recipes_source=str(tmp_path)
        )

        assert (startup_pool.origin, mixed_pool.origin) == (RegistryEntryOrigin.SAVED, RegistryEntryOrigin.SAVED)
        assert _saved_document(tmp_path, "mixed_pool")["params"] == {"targets": ["startup_one", "saved_member"]}
        assert not (tmp_path / "targets" / "pool.json").exists()

    async def test_saves_a_class_registered_under_another_name_so_restore_finds_it(self, tmp_path: Path) -> None:
        class AliasedTextTarget(TextTarget):
            pass

        TargetRegistry.get_registry_singleton().register_class(AliasedTextTarget, name="contoso_text")
        request = CreateTargetRequest(name="aliased", type="contoso_text", params={}, persist=True)

        await TargetService().create_target_async(request=request, recipes_source=str(tmp_path))

        assert _saved_document(tmp_path, "aliased")["class_name"] == "contoso_text"
        TargetRegistry.reset_registry_singleton()
        TargetRegistry.get_registry_singleton().register_class(AliasedTextTarget, name="contoso_text")
        assert (await _restore(tmp_path)).failures == []

    async def test_refuses_to_save_a_target_whose_class_a_launch_script_added(self, tmp_path: Path) -> None:
        class LaunchScriptTextTarget(TextTarget):
            pass

        with registration_origin(RegistryEntryOrigin.TEMPORARY):
            TargetRegistry.get_registry_singleton().register_class(LaunchScriptTextTarget)
        service = TargetService()
        request = CreateTargetRequest(name="launched", type="LaunchScriptTextTarget", params={}, persist=True)

        types = {item.target_type: item for item in (await service.list_target_types_async()).items}
        with pytest.raises(NotSaveableError, match="'LaunchScriptTextTarget' was added by a scan's launch"):
            await service.create_target_async(request=request, recipes_source=str(tmp_path))
        await service.create_target_async(request=request.model_copy(update={"persist": False}))

        launched = types["LaunchScriptTextTarget"]
        assert (launched.saveable_auth_modes, launched.not_saveable_reason) == (
            [],
            (
                "'LaunchScriptTextTarget' was added by a scan's launch script, so a saved entry of it couldn't be "
                "restored after a restart."
            ),
        )
        assert types["TextTarget"].not_saveable_reason is None
        assert not (tmp_path / "targets").exists()
        assert service.get_target_object(target_registry_name="launched") is not None

    async def test_types_say_an_openai_target_cant_be_saved_while_the_server_sets_sdk_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Ocp-Apim-Subscription-Key: server-secret")

        result = await TargetService().list_target_types_async()

        entries = {item.target_type: item for item in result.items}
        assert entries["OpenAIChatTarget"].saveable_auth_modes == []
        assert "OPENAI_CUSTOM_HEADERS" in (entries["OpenAIChatTarget"].not_saveable_reason or "")
        assert entries["TextTarget"].saveable_auth_modes == ["api_key"]

    async def test_a_reserved_name_is_refused_as_invalid_not_as_taken(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="reserved") as refusal:
            await TargetService().create_target_async(request=_save_request(name="types"), recipes_source=str(tmp_path))

        assert not isinstance(refusal.value, (SavedEntryConflictError, NotSaveableError))

    async def test_an_address_that_is_not_a_full_web_address_is_an_error_to_fix(self, tmp_path: Path) -> None:
        service = TargetService()
        request = _save_request(params={"endpoint": "localhost:11434/v1", "model_name": "llama3"}, auth_mode="none")

        with pytest.raises(ValueError) as refusal:
            await service.create_target_async(request=request, recipes_source=str(tmp_path))

        assert not isinstance(refusal.value, NotSaveableError)
        assert str(refusal.value) == "'endpoint' must be a full http(s) or ws(s) address in a saved target."
        assert service.get_target_object(target_registry_name="contoso_gpt4o") is None
        assert not (tmp_path / "targets").exists()

    async def test_lists_where_each_target_comes_from(self, tmp_path: Path) -> None:
        service = TargetService()
        with registration_origin(RegistryEntryOrigin.CONFIGURATION):
            service._registry.instances.register(MockPromptTarget(), name="from_startup_script")
        with registration_origin(RegistryEntryOrigin.TEMPORARY):
            service._registry.instances.register(MockPromptTarget(), name="from_launch_script")
        await service.create_target_async(request=_save_request(name="temporary_one", persist=False))
        await service.create_target_async(request=_save_request(name="saved_one"), recipes_source=str(tmp_path))

        listed = await service.list_targets_async()

        assert {item.target_registry_name: item.origin for item in listed.items} == {
            "from_startup_script": RegistryEntryOrigin.CONFIGURATION,
            "from_launch_script": RegistryEntryOrigin.TEMPORARY,
            "temporary_one": RegistryEntryOrigin.TEMPORARY,
            "saved_one": RegistryEntryOrigin.SAVED,
        }
        fetched = await service.get_target_async(target_registry_name="temporary_one")
        assert isinstance(fetched, TargetInstance)
        assert fetched.origin is RegistryEntryOrigin.TEMPORARY


_CONTOSO = ServerCredential(name="contoso", env_var="CONTOSO_TEST_KEY", endpoint="https://api.contoso.com/v1")


def _listed_request(
    *, persist: bool = False, endpoint: str = "https://api.contoso.com/v1", target_type: str = "OpenAIChatTarget"
) -> CreateTargetRequest:
    return CreateTargetRequest(
        name="contoso_chat",
        type=target_type,
        params={"endpoint": endpoint, "model_name": "gpt-4o"} if target_type == "OpenAIChatTarget" else {},
        server_credential="contoso",
        persist=persist,
    )


@pytest.mark.usefixtures("sqlite_instance")
class TestServerCredentials:
    async def test_builds_a_target_with_a_listed_key_without_returning_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONTOSO_TEST_KEY", "listed-key")
        service = TargetService()

        target = await service.create_target_async(request=_listed_request(), server_credentials=[_CONTOSO])

        target_obj = service.get_target_object(target_registry_name="contoso_chat")
        assert target_obj._api_key == "listed-key"  # type: ignore[attr-defined]
        assert target.origin is RegistryEntryOrigin.TEMPORARY
        assert "listed-key" not in target.model_dump_json()

    async def test_saves_the_credential_name_and_restore_reads_the_key_again(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CONTOSO_TEST_KEY", "listed-key")
        service = TargetService()

        target = await service.create_target_async(
            request=_listed_request(persist=True), recipes_source=str(tmp_path), server_credentials=[_CONTOSO]
        )

        assert target.origin is RegistryEntryOrigin.SAVED
        assert _saved_document(tmp_path, "contoso_chat") == {
            "schema_version": 1,
            "class_module": "pyrit.prompt_target.openai.openai_chat_target",
            "class_name": "OpenAIChatTarget",
            "auth_mode": "api_key",
            "params": {"endpoint": "https://api.contoso.com/v1", "model_name": "gpt-4o", "headers": "{}"},
            "server_credential": "contoso",
        }
        monkeypatch.setenv("CONTOSO_TEST_KEY", "rotated-key")
        TargetRegistry.reset_registry_singleton()
        assert (await _restore(tmp_path, server_credentials=[_CONTOSO])).failures == []
        restored = TargetRegistry.get_registry_singleton().instances.get("contoso_chat")
        assert restored is not None
        assert restored._api_key == "rotated-key"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"credentials": []}, "This server has no server credential named 'contoso'."),
            (
                {"endpoint": "https://api.contoso.com.evil.example/v1"},
                (
                    "Server credential 'contoso' is only sent to https://api.contoso.com/v1, so 'endpoint' must be "
                    "that address."
                ),
            ),
            ({"unset": True}, "Server credential 'contoso' isn't set on this server."),
            ({"target_type": "TextTarget"}, "'TextTarget' can't use a server credential."),
        ],
    )
    async def test_refuses_a_credential_it_cant_use_for_this_target(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changes: dict[str, Any], message: str
    ) -> None:
        if changes.get("unset"):
            monkeypatch.delenv("CONTOSO_TEST_KEY", raising=False)
        else:
            monkeypatch.setenv("CONTOSO_TEST_KEY", "listed-key")
        request = _listed_request(
            persist=True,
            endpoint=changes.get("endpoint", "https://api.contoso.com/v1"),
            target_type=changes.get("target_type", "OpenAIChatTarget"),
        )

        with pytest.raises(ValueError) as refusal:
            await TargetService().create_target_async(
                request=request, recipes_source=str(tmp_path), server_credentials=changes.get("credentials", [_CONTOSO])
            )

        assert str(refusal.value) == message
        assert TargetRegistry.get_registry_singleton().instances.get("contoso_chat") is None
        assert not (tmp_path / "targets").exists()

    @pytest.mark.parametrize(
        ("credentials", "unset", "message"),
        [
            ([], False, "This server has no server credential named 'contoso'."),
            ([_CONTOSO], True, "Server credential 'contoso' isn't set on this server."),
        ],
    )
    async def test_a_saved_target_doesnt_restore_without_its_listed_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        credentials: list[ServerCredential],
        unset: bool,
        message: str,
    ) -> None:
        monkeypatch.setenv("CONTOSO_TEST_KEY", "listed-key")
        await TargetService().create_target_async(
            request=_listed_request(persist=True), recipes_source=str(tmp_path), server_credentials=[_CONTOSO]
        )
        if unset:
            monkeypatch.delenv("CONTOSO_TEST_KEY")
        TargetRegistry.reset_registry_singleton()

        failures = (await _restore(tmp_path, server_credentials=credentials)).failures

        assert [(failure.name, failure.reason, failure.message) for failure in failures] == [
            ("contoso_chat", RestoreFailureReason.UNSUPPORTED_CREDENTIAL, message)
        ]
        assert "contoso_chat" not in TargetRegistry.get_registry_singleton().instances.get_names()

    async def test_types_list_the_credentials_each_type_can_use(self) -> None:
        response = await TargetService().list_target_types_async(server_credentials=[_CONTOSO])

        choices = {item.target_type: [(c.name, c.endpoint) for c in item.server_credentials] for item in response.items}
        assert choices["OpenAIChatTarget"] == [("contoso", "https://api.contoso.com/v1")]
        assert choices["TextTarget"] == []
        assert "CONTOSO_TEST_KEY" not in response.model_dump_json()


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"params": {"api_key": "typed-key"}}, "Send either an api_key or a server_credential, not both"),
        ({"auth_mode": "identity"}, "server_credential needs auth_mode 'api_key'"),
        ({"server_credential": "Contoso"}, "server_credential"),
        (
            {"register": False, "source": {"source_name": "startup_chat", "source_hash": "abc123", "params": {}}},
            "server_credential can't be used with source",
        ),
    ],
)
def test_a_request_with_a_server_credential_sends_no_key_of_its_own(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CreateTargetRequest.model_validate(
            {"name": "contoso_chat", "type": "OpenAIChatTarget", "params": {}, "server_credential": "contoso", **fields}
        )
