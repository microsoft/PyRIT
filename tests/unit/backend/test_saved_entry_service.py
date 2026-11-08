# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from pyrit.backend.models import CreateConverterRequest
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.services import ConverterService
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.backend.services.saved_entries import (
    SavedEntryConflictError,
    SavedEntryStorageError,
    save_then_register_async,
)
from pyrit.backend.services.saved_entry_service import (
    SavedEntryCaller,
    SavedEntryForbiddenError,
    SavedEntryInUseError,
    SavedEntryNotFoundError,
    SavedEntryService,
    SavedEntryVersionRequiredError,
)
from pyrit.backend.services.technique_service import TechniqueService
from pyrit.converter import SearchReplaceConverter, ToneConverter
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryOwner,
)
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.file_document_storage import DocumentConflictError
from pyrit.registry.instance_registry import registry_entry_origin
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.mocks import MockPromptTarget

ADA = SavedEntryOwner(oid="00000000-0000-0000-0000-000000000001", name="Ada")
BEN_OID = "00000000-0000-0000-0000-000000000002"
CONVERTERS, TARGETS = RegistryRecipeKind.CONVERTERS, RegistryRecipeKind.TARGETS
_SAVED_CHAT = {
    "schema_version": 1,
    "class_module": "pyrit.prompt_target.openai.openai_chat_target",
    "class_name": "OpenAIChatTarget",
    "params": {
        "auth_mode": "identity",
        "endpoint": "https://contoso.openai.azure.com/openai/v1",
        "model_name": "gpt-4o",
        "headers": "{}",
    },
    "owner": {"oid": ADA.oid, "name": "Ada"},
}

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    registries = (TargetRegistry, ConverterRegistry, ScorerRegistry, AttackTechniqueRegistry)
    for registry in registries:
        registry.reset_registry_singleton()
    yield
    for registry in registries:
        registry.reset_registry_singleton()


def _caller(oid: str | None = None, *, admin: bool = False, sign_in: bool = True) -> SavedEntryCaller:
    return SavedEntryCaller(oid=oid, is_admin=admin, sign_in=sign_in)


def _write(root: Path, kind: RegistryRecipeKind, name: str, document: dict[str, Any] | str) -> Path:
    folder = root / kind.value
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
    return path


def _restore(root: Path) -> list[RestoreFailure]:
    return RegistryRecipeRestorer(source=str(root)).restore().failures


async def _save_replace(root: Path, name: str = "cat_replace") -> None:
    await ConverterService().create_converter_async(
        request=CreateConverterRequest(
            name=name, type="SearchReplaceConverter", params={"pattern": "cat", "replace": "dog"}, persist=True
        ),
        owner=ADA,
        recipes_source=str(root),
    )


async def _version(root: Path, kind: RegistryRecipeKind, name: str, caller: SavedEntryCaller | None = None) -> str:
    details = await SavedEntryService().get_saved_entry_async(
        kind=kind, name=name, recipes_source=str(root), caller=caller or _caller(ADA.oid)
    )
    assert details.version is not None
    return details.version


async def _delete(
    root: Path,
    kind: RegistryRecipeKind,
    name: str,
    *,
    version: str | None,
    caller: SavedEntryCaller | None = None,
    runtime: RuntimeLifecycle | None = None,
) -> None:
    await SavedEntryService().delete_saved_entry_async(
        kind=kind,
        name=name,
        expected_version=version,
        recipes_source=str(root),
        caller=caller or _caller(ADA.oid),
        runtime=runtime,
    )


def _runtime() -> RuntimeLifecycle:
    return RuntimeLifecycle(app=FastAPI(), source=ConfigurationFileService(config_file_value=None))


async def test_whoever_saved_a_converter_deletes_it_and_it_doesnt_come_back(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    service = SavedEntryService()

    details = await service.get_saved_entry_async(
        kind=CONVERTERS, name="cat_replace", recipes_source=str(tmp_path), caller=_caller(ADA.oid)
    )
    assert (details.saved_by, details.saved_by_me, details.can_change, details.problem) == ("Ada", True, True, None)
    await _delete(tmp_path, CONVERTERS, "cat_replace", version=details.version)

    assert not (tmp_path / "converters" / "cat_replace.json").exists()
    assert ConverterRegistry.get_registry_singleton().instances.get("cat_replace") is None
    ConverterRegistry.reset_registry_singleton()
    assert RegistryRecipeRestorer(source=str(tmp_path)).restore().restored == []


@pytest.mark.parametrize(
    ("caller", "may_delete"),
    [
        (_caller(BEN_OID), False),
        (_caller(BEN_OID, admin=True), True),
        (_caller(None, sign_in=False), True),
    ],
)
async def test_only_whoever_saved_it_or_an_administrator_may_change_it(
    tmp_path: Path, caller: SavedEntryCaller, may_delete: bool
) -> None:
    await _save_replace(tmp_path)
    version = await _version(tmp_path, CONVERTERS, "cat_replace")

    details = await SavedEntryService().get_saved_entry_async(
        kind=CONVERTERS, name="cat_replace", recipes_source=str(tmp_path), caller=caller
    )
    assert (details.saved_by_me, details.can_change) == (False, may_delete)
    if may_delete:
        await _delete(tmp_path, CONVERTERS, "cat_replace", version=version, caller=caller)
        assert not (tmp_path / "converters" / "cat_replace.json").exists()
        return
    with pytest.raises(SavedEntryForbiddenError) as refusal:
        await _delete(tmp_path, CONVERTERS, "cat_replace", version=version, caller=caller)
    assert str(refusal.value) == "Only Ada or an administrator can delete saved converter 'cat_replace'."
    assert (tmp_path / "converters" / "cat_replace.json").exists()
    assert ConverterRegistry.get_registry_singleton().instances.get("cat_replace") is not None


async def test_a_delete_needs_the_version_it_read_and_keeps_a_file_that_changed(tmp_path: Path) -> None:
    await _save_replace(tmp_path)

    with pytest.raises(SavedEntryVersionRequiredError):
        await _delete(tmp_path, CONVERTERS, "cat_replace", version=None)
    with pytest.raises(SavedEntryInUseError) as refusal:
        await _delete(tmp_path, CONVERTERS, "cat_replace", version="0" * 64)

    assert str(refusal.value) == (
        "Saved converter 'cat_replace' changed since it was read. Look at it again before deleting it."
    )
    assert (tmp_path / "converters" / "cat_replace.json").exists()
    assert ConverterRegistry.get_registry_singleton().instances.get("cat_replace") is not None


async def test_a_name_without_a_saved_file_isnt_found_and_its_startup_entry_stays(tmp_path: Path) -> None:
    instances = ConverterRegistry.get_registry_singleton().instances
    instances.register(SearchReplaceConverter(pattern="cat", replace="dog"), name="startup_replace")
    instances.mark_configuration_entries()

    for name in ("startup_replace", "never_saved"):
        with pytest.raises(SavedEntryNotFoundError) as refusal:
            await _delete(tmp_path, CONVERTERS, name, version="0" * 64)
        assert str(refusal.value) == f"No saved converter is named '{name}'."
    assert instances.get("startup_replace") is not None


async def test_deleting_a_saved_file_whose_name_a_startup_entry_took_keeps_that_entry(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    ConverterRegistry.reset_registry_singleton()
    instances = ConverterRegistry.get_registry_singleton().instances
    startup = SearchReplaceConverter(pattern="fish", replace="dog")
    instances.register(startup, name="cat_replace")
    instances.mark_configuration_entries()
    assert [failure.reason for failure in _restore(tmp_path)] == [RestoreFailureReason.NAME_CLASH]

    await _delete(tmp_path, CONVERTERS, "cat_replace", version=await _version(tmp_path, CONVERTERS, "cat_replace"))

    assert not (tmp_path / "converters" / "cat_replace.json").exists()
    assert instances.get("cat_replace") is startup


async def test_an_entry_another_saved_entry_names_isnt_deleted(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    tone = {
        "class_module": "pyrit.converter.tone_converter",
        "class_name": "ToneConverter",
        "params": {"converter_target": "saved_chat", "tone": "formal"},
    }
    _write(tmp_path, CONVERTERS, "formal_tone", tone)
    _write(
        tmp_path,
        RegistryRecipeKind.TECHNIQUES,
        "formal_send",
        {"type": "PromptSendingAttack", "request_converters": ["formal_tone"]},
    )
    assert _restore(tmp_path) == []

    with pytest.raises(SavedEntryInUseError) as target_refusal:
        await _delete(tmp_path, TARGETS, "saved_chat", version=await _version(tmp_path, TARGETS, "saved_chat"))
    with pytest.raises(SavedEntryInUseError) as converter_refusal:
        await _delete(
            tmp_path,
            CONVERTERS,
            "formal_tone",
            version=await _version(tmp_path, CONVERTERS, "formal_tone"),
            caller=_caller(sign_in=False),
        )

    assert str(target_refusal.value) == (
        "Saved target 'saved_chat' is used by saved converter 'formal_tone'. Delete or change it first."
    )
    assert str(converter_refusal.value) == (
        "Saved converter 'formal_tone' is used by saved technique 'formal_send'. Delete or change it first."
    )
    assert TargetRegistry.get_registry_singleton().instances.get("saved_chat") is not None
    assert (tmp_path / "targets" / "saved_chat.json").exists()


async def test_an_entry_a_temporary_entry_holds_isnt_deleted(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert _restore(tmp_path) == []
    targets = TargetRegistry.get_registry_singleton().instances
    converters = ConverterRegistry.get_registry_singleton().instances
    converters.register(ToneConverter(converter_target=targets.get("saved_chat"), tone="formal"), name="formal_tone")

    with pytest.raises(SavedEntryInUseError) as refusal:
        await _delete(tmp_path, TARGETS, "saved_chat", version=await _version(tmp_path, TARGETS, "saved_chat"))

    assert str(refusal.value) == (
        "Saved target 'saved_chat' is used by temporary converter 'formal_tone'. Delete or change it first. "
        "A temporary entry is gone after the backend restarts or is reinitialized."
    )
    assert targets.get("saved_chat") is not None


async def test_a_converter_a_temporary_technique_holds_isnt_deleted(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    await TechniqueService().create_async(
        request=CreateTechniqueRequest(
            name="replace_send", type="PromptSendingAttack", request_converters=["cat_replace"]
        )
    )

    with pytest.raises(SavedEntryInUseError) as refusal:
        await _delete(tmp_path, CONVERTERS, "cat_replace", version=await _version(tmp_path, CONVERTERS, "cat_replace"))

    assert "temporary technique 'replace_send'" in str(refusal.value)


async def test_a_target_a_temporary_technique_holds_through_its_converter_isnt_deleted(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert _restore(tmp_path) == []
    converters = ConverterRegistry.get_registry_singleton().instances
    tone = ToneConverter(
        converter_target=TargetRegistry.get_registry_singleton().instances.get("saved_chat"), tone="formal"
    )
    converters.register(tone, name="formal_tone")
    await TechniqueService().create_async(
        request=CreateTechniqueRequest(
            name="formal_send", type="PromptSendingAttack", request_converters=["formal_tone"]
        )
    )
    # The converter is no longer registered, but the technique still holds it, and through it the target.
    converters.unregister("formal_tone")

    with pytest.raises(SavedEntryInUseError) as refusal:
        await _delete(tmp_path, TARGETS, "saved_chat", version=await _version(tmp_path, TARGETS, "saved_chat"))

    assert "temporary technique 'formal_send'" in str(refusal.value)


async def test_a_file_rewritten_after_it_was_read_isnt_deleted(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    version = await _version(tmp_path, CONVERTERS, "cat_replace")
    path = tmp_path / "converters" / "cat_replace.json"
    read_recipe = RegistryRecipeStorage.read_recipe

    def read_then_rewrite(storage: RegistryRecipeStorage, name: str) -> Any:
        stored = read_recipe(storage, name)
        path.write_text(path.read_text().replace('"dog"', '"bird"'), encoding="utf-8")
        return stored

    with patch.object(RegistryRecipeStorage, "read_recipe", read_then_rewrite):
        with pytest.raises(SavedEntryInUseError) as refusal:
            await _delete(tmp_path, CONVERTERS, "cat_replace", version=version)

    assert "changed since it was read" in str(refusal.value)
    assert '"bird"' in path.read_text()
    assert ConverterRegistry.get_registry_singleton().instances.get("cat_replace") is not None


async def test_a_running_entry_isnt_deleted_while_work_could_be_using_it(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    runtime = _runtime()

    with (
        patch.object(runtime, "_has_active_work", return_value=True),
        patch("pyrit.backend.services.saved_entry_service._IDLE_WAIT_SECONDS", 0.1),
    ):
        with pytest.raises(SavedEntryInUseError) as refusal:
            await _delete(
                tmp_path,
                CONVERTERS,
                "cat_replace",
                version=await _version(tmp_path, CONVERTERS, "cat_replace"),
                runtime=runtime,
            )

    assert str(refusal.value) == (
        "Saved converter 'cat_replace' could be in use by a request, run, or send in progress. "
        "Try again when it finishes."
    )
    assert ConverterRegistry.get_registry_singleton().instances.get("cat_replace") is not None
    assert (tmp_path / "converters" / "cat_replace.json").exists()


async def test_a_delete_waits_for_requests_in_flight_to_finish(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    runtime = _runtime()
    version = await _version(tmp_path, CONVERTERS, "cat_replace")

    with patch.object(runtime, "_has_active_work", side_effect=[True, True, False]) as active:
        await _delete(tmp_path, CONVERTERS, "cat_replace", version=version, runtime=runtime)

    assert active.call_count == 3
    assert not (tmp_path / "converters" / "cat_replace.json").exists()


async def test_an_entry_that_failed_to_restore_is_deleted_and_no_longer_listed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        CONVERTERS,
        "ghost",
        {"class_module": "nowhere", "class_name": "Nope", "owner": {"oid": ADA.oid, "name": "Ada"}},
    )
    runtime = _runtime()
    runtime.restore_failures = _restore(tmp_path)
    assert [failure.name for failure in runtime.restore_failures] == ["ghost"]

    # Nothing registered can be using it, so work in progress doesn't stop the delete.
    with patch.object(runtime, "_has_active_work", return_value=True):
        await _delete(
            tmp_path, CONVERTERS, "ghost", version=await _version(tmp_path, CONVERTERS, "ghost"), runtime=runtime
        )

    assert not (tmp_path / "converters" / "ghost.json").exists()
    assert runtime.restore_failures == []


@pytest.mark.parametrize(
    ("document", "read_error", "problem"),
    [
        ("not json", None, RestoreFailureReason.INVALID_RECIPE),
        (
            {"class_module": "m", "class_name": "C"},
            PermissionError(13, "Permission denied"),
            RestoreFailureReason.UNREADABLE_RECIPE,
        ),
    ],
)
async def test_a_file_that_doesnt_say_who_saved_it_needs_an_administrator(
    tmp_path: Path, document: dict[str, Any] | str, read_error: OSError | None, problem: RestoreFailureReason
) -> None:
    _write(tmp_path, CONVERTERS, "broken", document)
    read = patch.object(RegistryRecipeStorage, "_read_document_bytes", side_effect=read_error) if read_error else None
    service = SavedEntryService()
    if read is not None:
        read.start()
    try:
        details = await service.get_saved_entry_async(
            kind=CONVERTERS, name="broken", recipes_source=str(tmp_path), caller=_caller(ADA.oid)
        )
        assert (details.problem, details.saved_by, details.can_change) == (problem, None, False)
        assert (details.version is None) is (read_error is not None)
        with pytest.raises(SavedEntryForbiddenError) as refusal:
            await _delete(tmp_path, CONVERTERS, "broken", version=details.version)
        assert str(refusal.value) == (
            "Only an administrator can delete saved converter 'broken', since its file doesn't say who saved it."
        )

        await _delete(tmp_path, CONVERTERS, "broken", version=details.version, caller=_caller(BEN_OID, admin=True))
    finally:
        if read is not None:
            read.stop()

    assert not (tmp_path / "converters" / "broken.json").exists()


@pytest.mark.parametrize(
    "error", [OSError("disk"), DocumentConflictError(name="cat_replace", expected_version="a", actual_version="b")]
)
async def test_an_entry_whose_file_cant_be_deleted_is_put_back(tmp_path: Path, error: Exception) -> None:
    await _save_replace(tmp_path)
    instances = ConverterRegistry.get_registry_singleton().instances
    entry = instances.get_entry("cat_replace")
    assert entry is not None
    version = await _version(tmp_path, CONVERTERS, "cat_replace")

    with patch.object(RegistryRecipeStorage, "delete_recipe", side_effect=error):
        expected = SavedEntryInUseError if isinstance(error, DocumentConflictError) else SavedEntryStorageError
        with pytest.raises(expected):
            await _delete(tmp_path, CONVERTERS, "cat_replace", version=version)

    restored = instances.get_entry("cat_replace")
    assert restored is not None
    assert restored.instance is entry.instance
    assert registry_entry_origin(restored.metadata) is RegistryEntryOrigin.SAVED
    assert (tmp_path / "converters" / "cat_replace.json").exists()


@pytest.mark.parametrize("also_registered", [False, True])
async def test_a_deleted_entry_is_released_unless_it_is_still_registered(tmp_path: Path, also_registered: bool) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert _restore(tmp_path) == []
    targets = TargetRegistry.get_registry_singleton().instances
    target = targets.get("saved_chat")
    if also_registered:
        targets.register(target, name="same_chat")

    with patch("pyrit.backend.services.saved_entry_service.release_component_async", AsyncMock()) as release:
        await _delete(tmp_path, TARGETS, "saved_chat", version=await _version(tmp_path, TARGETS, "saved_chat"))

    assert targets.get("saved_chat") is None
    if also_registered:
        release.assert_not_awaited()
    else:
        release.assert_awaited_once_with(target)


async def test_a_save_naming_an_entry_deleted_after_it_was_checked_is_refused(tmp_path: Path) -> None:
    recipe = ComponentRecipe(
        class_module="pyrit.converter.tone_converter",
        class_name="ToneConverter",
        params={"converter_target": "saved_chat", "tone": "formal"},
    )
    register = AsyncMock()

    with pytest.raises(SavedEntryConflictError) as refusal:
        await save_then_register_async(
            kind=CONVERTERS, name="formal_tone", recipe=recipe, recipes_source=str(tmp_path), register=register
        )

    assert str(refusal.value) == "Target 'saved_chat' was deleted while this was being saved, so it can't be saved now."
    register.assert_not_called()
    assert not (tmp_path / "converters" / "formal_tone.json").exists()


async def test_a_save_naming_a_temporary_entry_that_took_a_deleted_ones_name_is_refused(tmp_path: Path) -> None:
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="saved_chat")
    recipe = ComponentRecipe(
        class_module="pyrit.converter.tone_converter",
        class_name="ToneConverter",
        params={"converter_target": "saved_chat", "tone": "formal"},
    )

    with pytest.raises(SavedEntryConflictError):
        await save_then_register_async(
            kind=CONVERTERS, name="formal_tone", recipe=recipe, recipes_source=str(tmp_path), register=AsyncMock()
        )

    assert not (tmp_path / "converters" / "formal_tone.json").exists()


async def test_a_delete_waits_for_a_save_in_progress_and_then_sees_it(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert _restore(tmp_path) == []
    version = await _version(tmp_path, TARGETS, "saved_chat")
    writing, finish_writing = threading.Event(), threading.Event()
    create_recipe = RegistryRecipeStorage.create_recipe

    def create_recipe_slowly(storage: RegistryRecipeStorage, *, name: str, recipe: ComponentRecipe) -> str:
        writing.set()
        finish_writing.wait(TEST_WATCHDOG_SECONDS)
        return create_recipe(storage, name=name, recipe=recipe)

    request = CreateConverterRequest(
        name="formal_tone",
        type="ToneConverter",
        params={"converter_target": "saved_chat", "tone": "formal"},
        persist=True,
    )
    with patch.object(RegistryRecipeStorage, "create_recipe", create_recipe_slowly):
        save = asyncio.create_task(
            ConverterService().create_converter_async(request=request, recipes_source=str(tmp_path))
        )
        try:
            assert await asyncio.to_thread(writing.wait, TEST_WATCHDOG_SECONDS)
            delete = asyncio.create_task(_delete(tmp_path, TARGETS, "saved_chat", version=version))
            await asyncio.sleep(0.05)
            assert not delete.done()
        finally:
            finish_writing.set()
        await save

    with pytest.raises(SavedEntryInUseError) as refusal:
        await delete
    assert "saved converter 'formal_tone'" in str(refusal.value)
    assert (tmp_path / "targets" / "saved_chat.json").exists()
