# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import AzureError

from pyrit.models.catalog import ComponentRecipe, RegistryEntryOrigin, RegistryRecipeKind
from pyrit.prompt_target import OpenAIChatTarget, RoundRobinTarget, TextTarget
from pyrit.registry import (
    NotSaveableError,
    SavedEntryConflictError,
    SavedEntryStorageError,
    TargetRegistry,
    prepare_saved_params_async,
    save_then_register_async,
)
from pyrit.registry.instance_registry import RegistryNameTakenError, registration_origin, registry_entry_origin
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.mocks import MockPromptTarget

_AZURE_ENDPOINT = "https://contoso.openai.azure.com/openai/v1"
_TEXT_RECIPE = ComponentRecipe(class_module="pyrit.prompt_target.text_target", class_name="TextTarget")


@pytest.fixture(autouse=True)
def isolated_targets() -> Iterator[None]:
    TargetRegistry.reset_registry_singleton()
    yield
    TargetRegistry.reset_registry_singleton()


async def _prepare_openai_async(*, params: dict[str, Any], auth_mode: str = "identity") -> dict[str, Any]:
    return await prepare_saved_params_async(
        registry=TargetRegistry.get_registry_singleton(),
        type_name="OpenAIChatTarget",
        cls=OpenAIChatTarget,
        params={"endpoint": _AZURE_ENDPOINT, "model_name": "gpt-4o", **params},
        auth_mode=auth_mode,
    )


async def _save_text_target_async(*, source: Path, register: MagicMock) -> None:
    await save_then_register_async(
        kind=RegistryRecipeKind.TARGETS,
        name="contoso_gpt4o",
        recipe=_TEXT_RECIPE,
        register=register,
        source=str(source),
    )


@pytest.mark.usefixtures("patch_central_database")
async def test_save_then_register_async_saves_a_target_built_from_prepared_params(tmp_path: Path) -> None:
    registry = TargetRegistry.get_registry_singleton()
    params = await _prepare_openai_async(params={})
    target = await registry.create_instance_async(name="OpenAIChatTarget", params={**params, "auth_mode": "identity"})
    recipe = ComponentRecipe(
        class_module=OpenAIChatTarget.__module__, class_name="OpenAIChatTarget", auth_mode="identity", params=params
    )

    await save_then_register_async(
        kind=RegistryRecipeKind.TARGETS,
        name="contoso_gpt4o",
        recipe=recipe,
        register=lambda metadata: registry.instances.register(target, name="contoso_gpt4o", metadata=metadata),
        source=str(tmp_path),
    )

    assert json.loads((tmp_path / "targets" / "contoso_gpt4o.json").read_text()) == {
        "schema_version": 1,
        "class_module": "pyrit.prompt_target.openai.openai_chat_target",
        "class_name": "OpenAIChatTarget",
        "auth_mode": "identity",
        "params": {"endpoint": _AZURE_ENDPOINT, "model_name": "gpt-4o", "headers": "{}"},
    }
    entry = registry.instances.get_entry("contoso_gpt4o")
    assert entry is not None
    assert entry.instance is target
    assert registry_entry_origin(entry.metadata) is RegistryEntryOrigin.SAVED


@pytest.mark.parametrize(
    ("auth_mode", "params", "message"),
    [
        ("api_key", {"api_key": "sk-typed-secret"}, "can't hold credentials"),
        ("api_key", {}, "'api_key' is empty"),
        ("identity", {"headers": '{"api-key": "sk-header-secret"}'}, "'headers' can carry a credential"),
        ("identity", {"endpoint": f"{_AZURE_ENDPOINT}?code=sk-url-secret"}, "can carry a credential in a URL"),
    ],
)
async def test_prepare_saved_params_async_refuses_a_target_that_would_hold_or_use_a_key(
    auth_mode: str, params: dict[str, Any], message: str
) -> None:
    with (
        patch.dict(os.environ, {"OPENAI_CHAT_KEY": "sk-server-key"}),
        pytest.raises(NotSaveableError, match=message) as refusal,
    ):
        await _prepare_openai_async(params=params, auth_mode=auth_mode)

    assert str(refusal.value).endswith("It can still be created without saving it.")
    assert "secret" not in str(refusal.value)
    assert "sk-server-key" not in str(refusal.value)


async def test_prepare_saved_params_async_refuses_a_setting_left_to_the_server_environment() -> None:
    with (
        patch.dict(os.environ, {"OPENAI_CHAT_MODEL": "gpt-4o"}),
        pytest.raises(NotSaveableError, match="'model_name' is empty") as refusal,
    ):
        await prepare_saved_params_async(
            registry=TargetRegistry.get_registry_singleton(),
            type_name="OpenAIChatTarget",
            cls=OpenAIChatTarget,
            params={"endpoint": _AZURE_ENDPOINT},
            auth_mode="identity",
        )

    assert str(refusal.value).endswith("It can still be created without saving it.")


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"httpx_client_kwargs": {"timeout": 5}}, "'httpx_client_kwargs' of 'OpenAIChatTarget' cannot be set"),
        ({"not_a_parameter": "x"}, "Unknown parameter 'not_a_parameter'"),
    ],
)
async def test_prepare_saved_params_async_refuses_a_parameter_restore_cant_pass(
    params: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message) as refusal:
        await _prepare_openai_async(params=params)

    assert not isinstance(refusal.value, NotSaveableError)
    assert str(refusal.value).startswith("Saved entries are rebuilt from what the API accepts.")


async def test_prepare_saved_params_async_refuses_a_class_a_launch_script_added() -> None:
    class LaunchScriptTextTarget(TextTarget):
        pass

    registry = TargetRegistry.get_registry_singleton()
    with registration_origin(RegistryEntryOrigin.TEMPORARY):
        registry.register_class(LaunchScriptTextTarget, name="launch_text")

    with pytest.raises(NotSaveableError, match="'launch_text' was added by a scan's launch script"):
        await prepare_saved_params_async(
            registry=registry, type_name="launch_text", cls=LaunchScriptTextTarget, params={}
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_prepare_saved_params_async_checks_a_class_without_cached_metadata_the_way_restore_does() -> None:
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(MockPromptTarget(), name="temporary_member")
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        registry.instances.register(MockPromptTarget(), name="startup_member")

    with (
        patch.object(TargetRegistry, "get_registered_class_metadata", return_value=None),
        pytest.raises(NotSaveableError, match="Uses temporary target 'temporary_member'"),
    ):
        await prepare_saved_params_async(
            registry=registry,
            type_name="RoundRobinTarget",
            cls=RoundRobinTarget,
            params={"targets": ["startup_member", "temporary_member"]},
        )


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (RegistryNameTakenError("Instance 'contoso_gpt4o' already exists"), SavedEntryConflictError),
        (ValueError("Instance name 'contoso_gpt4o' is reserved"), ValueError),
        (RuntimeError("x"), RuntimeError),
    ],
)
async def test_save_then_register_async_deletes_the_recipe_when_registering_fails(
    tmp_path: Path, error: Exception, raised: type[Exception]
) -> None:
    with pytest.raises(raised) as refusal:
        await _save_text_target_async(source=tmp_path, register=MagicMock(side_effect=error))

    assert type(refusal.value) is raised
    assert not (tmp_path / "targets" / "contoso_gpt4o.json").exists()


@pytest.mark.parametrize(
    ("error", "reason", "cause"),
    [
        (PermissionError(13, "Permission denied", "/srv/recipes/targets/x.json"), "Permission denied", "/srv/recipes"),
        (OSError("disk full"), "OSError", "disk full"),
        (
            AzureError("unreachable https://contoso.blob.core.windows.net/recipes?sv=1&sig=sas-secret"),
            "AzureError",
            "unreachable",
        ),
    ],
)
async def test_save_then_register_async_reports_storage_failures_as_unavailable(
    tmp_path: Path, error: Exception, reason: str, cause: str
) -> None:
    register = MagicMock()

    with (
        patch.object(RegistryRecipeStorage, "_save_local_conditional", side_effect=error),
        patch("pyrit.registry.registry_recipe_save.logger") as log,
        pytest.raises(SavedEntryStorageError, match="Saved targets can't be written right now."),
    ):
        await _save_text_target_async(source=tmp_path, register=register)

    register.assert_not_called()
    message = log.error.call_args.args[0]
    assert f"'contoso_gpt4o' couldn't be written to {tmp_path / 'targets'} ({reason})" in message
    assert cause in message
    assert "sas-secret" not in message
    assert log.error.call_args.kwargs.get("exc_info") is None


async def test_save_then_register_async_reports_storage_trouble_when_a_failed_save_cant_be_undone(
    tmp_path: Path,
) -> None:
    with (
        patch.object(RegistryRecipeStorage, "delete_recipe", side_effect=OSError("disk gone")),
        patch("pyrit.registry.registry_recipe_save.logger") as log,
        pytest.raises(SavedEntryStorageError),
    ):
        await _save_text_target_async(source=tmp_path, register=MagicMock(side_effect=ValueError("taken")))

    assert (tmp_path / "targets" / "contoso_gpt4o.json").exists()
    message = log.error.call_args.args[0]
    assert "'contoso_gpt4o'" in message
    assert "couldn't be deleted (OSError)" in message
    assert "disk gone" in message


async def test_save_then_register_async_leaves_a_recipe_that_another_writer_replaced_when_registering_fails(
    tmp_path: Path,
) -> None:
    theirs = ComponentRecipe(class_module="pyrit.prompt_target.text_target", class_name="their_text_target")

    def register(_metadata: dict[str, Any]) -> None:
        (tmp_path / "targets" / "contoso_gpt4o.json").write_text(theirs.model_dump_json(), encoding="utf-8")
        raise RuntimeError("register failed")

    with pytest.raises(RuntimeError, match="register failed"):
        await _save_text_target_async(source=tmp_path, register=MagicMock(side_effect=register))

    assert json.loads((tmp_path / "targets" / "contoso_gpt4o.json").read_text())["class_name"] == "their_text_target"


@pytest.mark.parametrize("registering_fails", [False, True])
async def test_save_then_register_async_finishes_a_cancelled_save_before_passing_the_cancellation_on(
    tmp_path: Path, registering_fails: bool
) -> None:
    writing, release = threading.Event(), threading.Event()
    create_recipe = RegistryRecipeStorage.create_recipe

    def slow_create_recipe(storage: RegistryRecipeStorage, *, name: str, recipe: ComponentRecipe) -> str:
        writing.set()
        release.wait(TEST_WATCHDOG_SECONDS)
        return create_recipe(storage, name=name, recipe=recipe)

    register = MagicMock(side_effect=RuntimeError("register failed") if registering_fails else None)
    with patch.object(RegistryRecipeStorage, "create_recipe", slow_create_recipe):
        save = asyncio.create_task(_save_text_target_async(source=tmp_path, register=register))
        try:
            await asyncio.to_thread(writing.wait, TEST_WATCHDOG_SECONDS)
            for _ in range(2):
                save.cancel()
                await asyncio.sleep(0)
            assert not save.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await save

    register.assert_called_once()
    assert (tmp_path / "targets" / "contoso_gpt4o.json").exists() is not registering_fails
    assert isinstance(cancelled.value.__cause__, RuntimeError) is registering_fails
