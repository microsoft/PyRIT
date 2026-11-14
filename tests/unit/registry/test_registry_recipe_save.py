# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import AzureError

from pyrit.converter import Base64Converter
from pyrit.models import ComponentType, Parameter, RegistryReference
from pyrit.models.catalog import ComponentRecipe, RegistryEntryOrigin, RegistryRecipeKind
from pyrit.prompt_target import OpenAIChatTarget, RoundRobinTarget, TextTarget
from pyrit.registry import (
    ConverterRegistry,
    NotSaveableError,
    SavedEntryConflictError,
    SavedEntryStorageError,
    ScorerRegistry,
    TargetRegistry,
    prepare_saved_params_async,
    refuse_temporary_references,
    save_then_register_async,
)
from pyrit.registry.instance_registry import RegistryNameTakenError, registration_origin, registry_entry_origin
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget

_AZURE_ENDPOINT = "https://contoso.openai.azure.com/openai/v1"
_TEXT_RECIPE = ComponentRecipe(class_module="pyrit.prompt_target.text_target", class_name="TextTarget")
_REGISTRIES = (TargetRegistry, ConverterRegistry, ScorerRegistry)


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()
    yield
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()


@pytest.fixture
def startup_and_temporary_entries() -> None:
    targets = TargetRegistry.get_registry_singleton().instances
    converters = ConverterRegistry.get_registry_singleton().instances
    scorers = ScorerRegistry.get_registry_singleton().instances
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        targets.register(MockPromptTarget(), name="startup_chat")
        converters.register(Base64Converter(), name="startup_base64")
        scorers.register(SubStringScorer(substring="x", categories=["leak"]), name="startup_leak")
    targets.register(MockPromptTarget(), name="launch_chat")
    converters.register(Base64Converter(), name="launch_base64")
    scorers.register(SubStringScorer(substring="y", categories=["leak"]), name="launch_leak")


def _reference(name: str, component_type: ComponentType) -> Parameter:
    return Parameter(name=name, description=name, reference=RegistryReference(component_type=component_type))


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
    ("error", "reason"),
    [
        (PermissionError(13, "Permission denied", "/srv/recipes/targets/x.json"), "Permission denied"),
        (OSError("disk full"), "OSError"),
        (AzureError("unreachable"), "AzureError"),
    ],
)
async def test_save_then_register_async_reports_storage_failures_as_unavailable(
    tmp_path: Path, error: Exception, reason: str
) -> None:
    register = MagicMock()

    with (
        patch.object(RegistryRecipeStorage, "_save_local_conditional", side_effect=error),
        patch("pyrit.registry.registry_recipe_save.logger") as log,
        pytest.raises(SavedEntryStorageError, match="Saved targets can't be written right now."),
    ):
        await _save_text_target_async(source=tmp_path, register=register)

    register.assert_not_called()
    message = log.error.call_args.args[0] % log.error.call_args.args[1:]
    assert f"'contoso_gpt4o' couldn't be written to {tmp_path / 'targets'} ({reason})" in message
    assert not any(detail in message for detail in ("/srv/recipes", "disk full", "unreachable"))
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
    message = log.error.call_args.args[0] % log.error.call_args.args[1:]
    assert "'contoso_gpt4o'" in message
    assert "couldn't be deleted (OSError)" in message
    assert "disk gone" not in message


@pytest.mark.usefixtures("patch_central_database", "startup_and_temporary_entries")
@pytest.mark.parametrize(
    ("component_type", "value", "named"),
    [
        (ComponentType.TARGET, "launch_chat", "target 'launch_chat'"),
        (ComponentType.CONVERTER, "launch_base64", "converter 'launch_base64'"),
        (ComponentType.SCORER, ["startup_leak", "launch_leak"], "scorer 'launch_leak'"),
    ],
)
def test_refuse_temporary_references_refuses_an_entry_that_is_gone_after_a_restart(
    component_type: ComponentType, value: object, named: str
) -> None:
    with pytest.raises(NotSaveableError) as refusal:
        refuse_temporary_references(parameters=[_reference("uses", component_type)], params={"uses": value})

    assert str(refusal.value) == (
        f"Uses temporary {named}, which won't exist after a restart. It can still be created without saving it."
    )


@pytest.mark.usefixtures("patch_central_database", "startup_and_temporary_entries")
@pytest.mark.parametrize(
    ("component_type", "value"),
    [
        (ComponentType.TARGET, "startup_chat"),
        (ComponentType.CONVERTER, ["startup_base64"]),
        (ComponentType.SCORER, "startup_leak"),
        (ComponentType.TARGET, "not_registered"),
        (ComponentType.TARGET, None),
        (ComponentType.TARGET, ""),
    ],
)
def test_refuse_temporary_references_accepts_entries_that_come_back_or_a_reference_left_unset(
    component_type: ComponentType, value: object
) -> None:
    refuse_temporary_references(parameters=[_reference("uses", component_type)], params={"uses": value})


def test_refuse_temporary_references_refuses_a_kind_that_saved_entries_cant_use() -> None:
    with pytest.raises(NotSaveableError) as refusal:
        refuse_temporary_references(parameters=[_reference("uses", ComponentType.SCENARIO)], params={"uses": "nightly"})

    assert str(refusal.value) == (
        "Uses scenario 'nightly', which saved entries can't use yet. It can still be created without saving it."
    )


_TONE_RECIPE = ComponentRecipe(
    class_module="pyrit.converter.tone_converter",
    class_name="ToneConverter",
    params={"converter_target": "saved_chat", "tone": "formal"},
)


async def test_save_then_register_async_refuses_a_recipe_naming_an_entry_deleted_after_it_was_checked(
    tmp_path: Path,
) -> None:
    register = MagicMock()

    with pytest.raises(SavedEntryConflictError) as refusal:
        await save_then_register_async(
            kind=RegistryRecipeKind.CONVERTERS,
            name="formal_tone",
            recipe=_TONE_RECIPE,
            register=register,
            source=str(tmp_path),
        )

    assert str(refusal.value) == "Target 'saved_chat' was deleted while this was being saved, so it can't be saved now."
    register.assert_not_called()
    assert not (tmp_path / "converters" / "formal_tone.json").exists()


@pytest.mark.usefixtures("patch_central_database")
async def test_save_then_register_async_refuses_a_recipe_naming_a_temporary_entry_that_took_a_deleted_ones_name(
    tmp_path: Path,
) -> None:
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="saved_chat")

    with pytest.raises(SavedEntryConflictError):
        await save_then_register_async(
            kind=RegistryRecipeKind.CONVERTERS,
            name="formal_tone",
            recipe=_TONE_RECIPE,
            register=MagicMock(),
            source=str(tmp_path),
        )

    assert not (tmp_path / "converters" / "formal_tone.json").exists()


async def test_save_then_register_async_leaves_a_recipe_that_another_writer_replaced_when_registering_fails(
    tmp_path: Path,
) -> None:
    theirs = ComponentRecipe(class_module="pyrit.converter", class_name="ROT13Converter")

    def register(_metadata: dict[str, Any]) -> None:
        (tmp_path / "converters" / "formal.json").write_text(theirs.model_dump_json(), encoding="utf-8")
        raise RuntimeError("register failed")

    with pytest.raises(RuntimeError, match="register failed"):
        await save_then_register_async(
            kind=RegistryRecipeKind.CONVERTERS,
            name="formal",
            recipe=ComponentRecipe(class_module="pyrit.converter", class_name="Base64Converter"),
            register=register,
            source=str(tmp_path),
        )

    assert json.loads((tmp_path / "converters" / "formal.json").read_text())["class_name"] == "ROT13Converter"
