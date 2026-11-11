# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import json
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from pyrit.backend.models import CreateConverterRequest, CreateScorerRequest
from pyrit.backend.models.saved_entries import UpdateSavedEntryRequest
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.services import ConverterService, get_converter_service, get_target_service
from pyrit.backend.services.component_lifecycle import release_component_async
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.backend.services.saved_entries import (
    NotSaveableError,
    SavedEntryConflictError,
    SavedEntryReplacement,
    hold_saved_entry_lock_async,
    save_then_register_async,
)
from pyrit.backend.services.saved_entry_restore import build_saved_entry_async
from pyrit.backend.services.saved_entry_service import (
    SavedEntryCaller,
    SavedEntryForbiddenError,
    SavedEntryInUseError,
    SavedEntryRequestError,
    SavedEntryService,
)
from pyrit.backend.services.scorer_service import ScorerService, get_scorer_service
from pyrit.backend.services.technique_service import TechniqueService, get_technique_service
from pyrit.converter import SearchReplaceConverter, ToneConverter
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RegistryRestoreReport,
    RestoreFailureReason,
    SavedEntryOwner,
)
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.instance_registry import (
    SAVED_ENTRY_OWNER_METADATA_KEY,
    RegistryNameTakenError,
    registration_origin,
    registry_entry_origin,
)
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from pyrit.setup.initializers.techniques import build_technique_factories
from unit.mocks import MockPromptTarget

ADA = SavedEntryOwner(oid="00000000-0000-0000-0000-000000000001", name="Ada")
BEN_OID = "00000000-0000-0000-0000-000000000002"
CONVERTERS, TARGETS = RegistryRecipeKind.CONVERTERS, RegistryRecipeKind.TARGETS
_SAVED_CHAT = {
    "schema_version": 1,
    "class_module": "pyrit.prompt_target.openai.openai_chat_target",
    "class_name": "OpenAIChatTarget",
    "auth_mode": "identity",
    "params": {"endpoint": "https://contoso.openai.azure.com/openai/v1", "model_name": "gpt-4o", "headers": "{}"},
    "owner": {"oid": ADA.oid, "name": "Ada"},
}

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    # The cached services hold the registries they were made with, so they're made again with the new ones.
    caches = (get_target_service, get_converter_service, get_scorer_service, get_technique_service)
    registries = (TargetRegistry, ConverterRegistry, ScorerRegistry, AttackTechniqueRegistry)
    for registry in registries:
        registry.reset_registry_singleton()
    for cache in caches:
        cache.cache_clear()
    yield
    for registry in registries:
        registry.reset_registry_singleton()
    for cache in caches:
        cache.cache_clear()


def _caller(oid: str | None = ADA.oid, *, admin: bool = False, sign_in: bool = True) -> SavedEntryCaller:
    return SavedEntryCaller(oid=oid, is_admin=admin, sign_in=sign_in)


def _file(root: Path, kind: RegistryRecipeKind, name: str) -> Path:
    return root / kind.value / f"{name}.json"


def _write(root: Path, kind: RegistryRecipeKind, name: str, document: dict[str, Any] | str) -> None:
    path = _file(root, kind, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")


async def _restore(root: Path) -> RegistryRestoreReport:
    """Restore the saved entries through the API's create, as startup does."""
    for cache in (get_target_service, get_converter_service, get_scorer_service, get_technique_service):
        cache.cache_clear()
    build_async = partial(build_saved_entry_async, upload_directory=None)
    return await RegistryRecipeRestorer(source=str(root)).restore_async(build_async=build_async)


async def _save_replace(root: Path) -> None:
    await ConverterService().create_converter_async(
        request=CreateConverterRequest(
            name="cat_replace", type="SearchReplaceConverter", params={"pattern": "cat", "replace": "dog"}, persist=True
        ),
        owner=ADA,
        recipes_source=str(root),
    )


async def _read(root: Path, kind: RegistryRecipeKind, name: str, caller: SavedEntryCaller | None = None) -> Any:
    return await SavedEntryService().get_saved_entry_async(
        kind=kind, name=name, recipes_source=str(root), caller=caller or _caller()
    )


async def _edit(
    root: Path,
    kind: RegistryRecipeKind,
    name: str,
    *,
    version: str,
    recipe: dict[str, Any],
    caller: SavedEntryCaller | None = None,
    runtime: RuntimeLifecycle | None = None,
) -> Any:
    return await SavedEntryService().update_saved_entry_async(
        kind=kind,
        name=name,
        request=UpdateSavedEntryRequest(expected_version=version, recipe=recipe),
        recipes_source=str(root),
        caller=caller or _caller(),
        runtime=runtime,
    )


async def test_the_owner_changes_a_saved_converter_and_a_restart_keeps_the_change(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    old = ConverterRegistry.get_registry_singleton().instances.get("cat_replace")
    details = await _read(tmp_path, CONVERTERS, "cat_replace")
    assert details.recipe == {
        "schema_version": 1,
        "class_module": "pyrit.converter.search_replace_converter",
        "class_name": "SearchReplaceConverter",
        "params": {"pattern": "cat", "replace": "dog"},
    }

    edited = await _edit(
        tmp_path,
        CONVERTERS,
        "cat_replace",
        version=details.version,
        recipe={**details.recipe, "params": {"pattern": "cat", "replace": "bird"}},
    )

    content = _file(tmp_path, CONVERTERS, "cat_replace").read_bytes()
    assert json.loads(content) == {
        "schema_version": 1,
        "class_module": "pyrit.converter.search_replace_converter",
        "class_name": "SearchReplaceConverter",
        "params": {"pattern": "cat", "replace": "bird"},
        "owner": {"oid": ADA.oid, "name": "Ada"},
    }
    assert edited.version == hashlib.sha256(content).hexdigest()
    assert (edited.running, edited.still_using_old, edited.saved_by_me) == (True, [], True)
    entry = ConverterRegistry.get_registry_singleton().instances.get_entry("cat_replace")
    assert entry is not None
    assert entry.instance is not old
    assert entry.instance.get_identifier().params["replace_list"] == ["bird"]
    assert registry_entry_origin(entry.metadata) is RegistryEntryOrigin.SAVED
    assert entry.metadata[SAVED_ENTRY_OWNER_METADATA_KEY] == ADA
    ConverterRegistry.reset_registry_singleton()
    assert (await _restore(tmp_path)).failures == []
    restored = ConverterRegistry.get_registry_singleton().instances.get("cat_replace")
    assert restored.get_identifier().params["replace_list"] == ["bird"]


async def test_an_administrator_changes_someone_elses_target_and_it_stays_theirs(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert (await _restore(tmp_path)).failures == []
    admin = _caller(BEN_OID, admin=True)
    details = await _read(tmp_path, TARGETS, "saved_chat", admin)

    edited = await _edit(
        tmp_path,
        TARGETS,
        "saved_chat",
        version=details.version,
        recipe={**details.recipe, "params": {**details.recipe["params"], "model_name": "gpt-4o-mini"}},
        caller=admin,
    )

    assert (edited.saved_by, edited.saved_by_me) == ("Ada", False)
    saved = json.loads(_file(tmp_path, TARGETS, "saved_chat").read_text())
    assert saved["params"]["model_name"] == "gpt-4o-mini"
    assert saved["owner"] == {"oid": ADA.oid, "name": "Ada"}
    target = TargetRegistry.get_registry_singleton().instances.get("saved_chat")
    assert target._model_name == "gpt-4o-mini"  # type: ignore[attr-defined]


async def test_a_saved_scorer_and_technique_change_through_their_create_checks(tmp_path: Path) -> None:
    await ScorerService().create_scorer_async(
        request=CreateScorerRequest(
            name="leak", type="SubStringScorer", params={"substring": "secret", "categories": ["leak"]}, persist=True
        ),
        owner=ADA,
        recipes_source=str(tmp_path),
    )
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    await TechniqueService().create_async(
        request=CreateTechniqueRequest(
            name="patient_send", type="PromptSendingAttack", params={"max_attempts_on_failure": 1}, persist=True
        ),
        owner=ADA,
        recipes_source=str(tmp_path),
    )
    scorer = await _read(tmp_path, RegistryRecipeKind.SCORERS, "leak")
    technique = await _read(tmp_path, RegistryRecipeKind.TECHNIQUES, "patient_send")

    await _edit(
        tmp_path,
        RegistryRecipeKind.SCORERS,
        "leak",
        version=scorer.version,
        recipe={**scorer.recipe, "params": {"substring": "password", "categories": ["leak"]}},
    )
    await _edit(
        tmp_path,
        RegistryRecipeKind.TECHNIQUES,
        "patient_send",
        version=technique.version,
        recipe={**technique.recipe, "params": {"max_attempts_on_failure": 3}, "tags": ["patient"]},
    )

    assert json.loads(_file(tmp_path, RegistryRecipeKind.SCORERS, "leak").read_text())["params"]["substring"] == (
        "password"
    )
    saved = json.loads(_file(tmp_path, RegistryRecipeKind.TECHNIQUES, "patient_send").read_text())
    assert (saved["params"], saved["tags"]) == ({"max_attempts_on_failure": 3}, ["patient"])
    entry = AttackTechniqueRegistry.get_registry_singleton().instances.get_entry("patient_send")
    assert entry is not None
    assert entry.tags == {"patient": ""}


async def test_a_saved_technique_keeps_its_attack_type(tmp_path: Path) -> None:
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    await TechniqueService().create_async(
        request=CreateTechniqueRequest(name="patient_send", type="PromptSendingAttack", persist=True),
        owner=ADA,
        recipes_source=str(tmp_path),
    )
    details = await _read(tmp_path, RegistryRecipeKind.TECHNIQUES, "patient_send")

    with pytest.raises(SavedEntryRequestError) as refusal:
        await _edit(
            tmp_path,
            RegistryRecipeKind.TECHNIQUES,
            "patient_send",
            version=details.version,
            recipe={**details.recipe, "type": "CrescendoAttack"},
        )

    assert str(refusal.value) == "A saved technique's attack type can't change. Delete it and save a new one instead."
    assert AttackTechniqueRegistry.get_registry_singleton().instances.get("patient_send") is not None


async def test_replacing_a_saved_entry_needs_the_lock_an_edit_holds(tmp_path: Path) -> None:
    recipe = ComponentRecipe(class_module="m", class_name="SearchReplaceConverter", params={})
    register = AsyncMock()

    with pytest.raises(RuntimeError):
        await save_then_register_async(
            kind=CONVERTERS,
            name="cat_replace",
            recipe=recipe,
            recipes_source=str(tmp_path),
            register=register,
            replacing=SavedEntryReplacement(version="v1", previous=recipe, running=True),
        )

    register.assert_not_called()


async def test_a_task_that_holds_the_saved_entry_lock_cant_wait_on_it_again() -> None:
    async def take_again() -> None:
        async with hold_saved_entry_lock_async():
            pass

    async with hold_saved_entry_lock_async():
        # Without the check this would wait forever; the timeout turns that into a different error.
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(take_again(), timeout=2)

    async with hold_saved_entry_lock_async():
        pass


async def test_an_edit_needs_the_version_it_read_and_the_right_to_change_it(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    details = await _read(tmp_path, CONVERTERS, "cat_replace")
    changed = {**details.recipe, "params": {"pattern": "cat", "replace": "bird"}}

    with pytest.raises(SavedEntryInUseError):
        await _edit(tmp_path, CONVERTERS, "cat_replace", version="0" * 64, recipe=changed)
    with pytest.raises(SavedEntryForbiddenError) as refusal:
        await _edit(
            tmp_path, CONVERTERS, "cat_replace", version=details.version, recipe=changed, caller=_caller(BEN_OID)
        )

    assert str(refusal.value) == "Only Ada or an administrator can change saved converter 'cat_replace'."
    other = await _read(tmp_path, CONVERTERS, "cat_replace", _caller(BEN_OID))
    assert (other.can_change, other.recipe) == (False, None)
    assert json.loads(_file(tmp_path, CONVERTERS, "cat_replace").read_text())["params"]["replace"] == "dog"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"class_name": "ROT13Converter"}, "A saved converter's class can't change."),
        ({"owner": {"oid": BEN_OID, "name": "Ben"}}, "Who saved a saved converter can't change"),
        ({"source": "other"}, "Only the params of a saved converter"),
        ({"auth_mode": "identity"}, "Only the params of a saved converter"),
        ({"params": None}, "A saved converter's params must be an object."),
    ],
)
async def test_an_edit_keeps_the_class_and_holds_only_what_a_saved_entry_can(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    await _save_replace(tmp_path)
    details = await _read(tmp_path, CONVERTERS, "cat_replace")

    with pytest.raises(SavedEntryRequestError) as refusal:
        await _edit(tmp_path, CONVERTERS, "cat_replace", version=details.version, recipe={**details.recipe, **change})

    assert str(refusal.value).startswith(message)
    assert ConverterRegistry.get_registry_singleton().instances.get("cat_replace") is not None


async def test_settings_a_new_entry_couldnt_save_leave_the_saved_one_as_it_was(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    _write(
        tmp_path,
        CONVERTERS,
        "formal_tone",
        {
            "class_module": "pyrit.converter.tone_converter",
            "class_name": "ToneConverter",
            "params": {"converter_target": "saved_chat", "tone": "formal"},
            "owner": {"oid": ADA.oid, "name": "Ada"},
        },
    )
    assert (await _restore(tmp_path)).failures == []
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="launch_chat")
    converters = ConverterRegistry.get_registry_singleton().instances
    old = converters.get("formal_tone")
    details = await _read(tmp_path, CONVERTERS, "formal_tone")
    before = _file(tmp_path, CONVERTERS, "formal_tone").read_bytes()

    with pytest.raises(NotSaveableError):
        await _edit(
            tmp_path,
            CONVERTERS,
            "formal_tone",
            version=details.version,
            recipe={**details.recipe, "params": {"converter_target": "launch_chat", "tone": "formal"}},
        )

    assert _file(tmp_path, CONVERTERS, "formal_tone").read_bytes() == before
    assert converters.get("formal_tone") is old
    assert registry_entry_origin(converters.get_entry("formal_tone").metadata) is RegistryEntryOrigin.SAVED


async def test_an_entry_that_holds_the_edited_one_keeps_the_old_object_until_a_restart(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert (await _restore(tmp_path)).failures == []
    targets = TargetRegistry.get_registry_singleton().instances
    old = targets.get("saved_chat")
    tone = ToneConverter(converter_target=old, tone="formal")
    ConverterRegistry.get_registry_singleton().instances.register(tone, name="formal_tone")
    details = await _read(tmp_path, TARGETS, "saved_chat")

    with patch("pyrit.backend.services.saved_entry_service.release_component_async", AsyncMock()) as release:
        edited = await _edit(
            tmp_path,
            TARGETS,
            "saved_chat",
            version=details.version,
            recipe={**details.recipe, "params": {**details.recipe["params"], "model_name": "gpt-4o-mini"}},
        )

    assert edited.still_using_old == ["temporary converter 'formal_tone'"]
    assert targets.get("saved_chat") is not old
    assert tone._converter_target is old  # type: ignore[attr-defined]
    release.assert_not_awaited()


async def test_an_entry_registered_while_the_edit_rebuilds_keeps_the_old_object(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert (await _restore(tmp_path)).failures == []
    old = TargetRegistry.get_registry_singleton().instances.get("saved_chat")
    details = await _read(tmp_path, TARGETS, "saved_chat")
    replace_recipe = RegistryRecipeStorage.replace_recipe

    def replace_while_a_request_registers_a_holder(storage: RegistryRecipeStorage, **kwargs: Any) -> str:
        ConverterRegistry.get_registry_singleton().instances.register(
            ToneConverter(converter_target=old, tone="formal"), name="formal_tone"
        )
        return replace_recipe(storage, **kwargs)

    with (
        patch.object(RegistryRecipeStorage, "replace_recipe", replace_while_a_request_registers_a_holder),
        patch("pyrit.backend.services.saved_entry_service.release_component_async", AsyncMock()) as release,
    ):
        edited = await _edit(
            tmp_path,
            TARGETS,
            "saved_chat",
            version=details.version,
            recipe={**details.recipe, "params": {**details.recipe["params"], "model_name": "gpt-4o-mini"}},
        )

    assert edited.still_using_old == ["temporary converter 'formal_tone'"]
    release.assert_not_awaited()


@pytest.mark.parametrize("change", [{"name": "other_send"}, {"persist": False}])
async def test_a_saved_technique_keeps_its_name_and_stays_saved(tmp_path: Path, change: dict[str, Any]) -> None:
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    await TechniqueService().create_async(
        request=CreateTechniqueRequest(name="patient_send", type="PromptSendingAttack", persist=True),
        owner=ADA,
        recipes_source=str(tmp_path),
    )
    details = await _read(tmp_path, RegistryRecipeKind.TECHNIQUES, "patient_send")

    with pytest.raises(SavedEntryRequestError) as refusal:
        await _edit(
            tmp_path,
            RegistryRecipeKind.TECHNIQUES,
            "patient_send",
            version=details.version,
            recipe={**details.recipe, **change},
        )

    assert str(refusal.value) == (
        "A saved technique keeps its name and stays saved. Delete it and save a new one instead."
    )
    assert AttackTechniqueRegistry.get_registry_singleton().instances.get("patient_send") is not None


async def test_a_saved_target_needs_its_params_as_an_object(tmp_path: Path) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert (await _restore(tmp_path)).failures == []
    details = await _read(tmp_path, TARGETS, "saved_chat")

    with pytest.raises(SavedEntryRequestError) as refusal:
        await _edit(tmp_path, TARGETS, "saved_chat", version=details.version, recipe={**details.recipe, "params": None})

    assert str(refusal.value) == "A saved target's params must be an object."
    assert TargetRegistry.get_registry_singleton().instances.get("saved_chat") is not None


@pytest.mark.parametrize("held", [False, True])
async def test_the_old_entry_is_released_only_when_nothing_could_use_it(tmp_path: Path, held: bool) -> None:
    _write(tmp_path, TARGETS, "saved_chat", _SAVED_CHAT)
    assert (await _restore(tmp_path)).failures == []
    old = TargetRegistry.get_registry_singleton().instances.get("saved_chat")
    runtime = RuntimeLifecycle(app=FastAPI(), source=ConfigurationFileService(config_file_value=None))
    details = await _read(tmp_path, TARGETS, "saved_chat")

    with (
        patch.object(runtime, "_has_active_work", return_value=held),
        patch("pyrit.backend.services.saved_entry_service.release_component_async", AsyncMock()) as release,
    ):
        await _edit(
            tmp_path,
            TARGETS,
            "saved_chat",
            version=details.version,
            recipe={**details.recipe, "params": {**details.recipe["params"], "model_name": "gpt-4o-mini"}},
            runtime=runtime,
        )

    if held:
        release.assert_not_awaited()
    else:
        release.assert_awaited_once_with(old)


async def test_an_edit_that_fixes_an_entry_that_failed_to_restore_only_saves_it(tmp_path: Path) -> None:
    broken = {**_SAVED_CHAT, "params": {**_SAVED_CHAT["params"], "endpoint": "https://api.example.test/v1"}}
    _write(tmp_path, TARGETS, "saved_chat", broken)
    assert [failure.name for failure in (await _restore(tmp_path)).failures] == ["saved_chat"]
    instances = TargetRegistry.get_registry_singleton().instances
    details = await _read(tmp_path, TARGETS, "saved_chat")

    release = AsyncMock(wraps=release_component_async)
    with patch("pyrit.backend.services.target_service.release_component_async", release):
        edited = await _edit(
            tmp_path,
            TARGETS,
            "saved_chat",
            version=details.version,
            recipe={**details.recipe, "params": _SAVED_CHAT["params"]},
        )

    assert edited.running is False
    # The next apply or start restores it into the registries as they are, so the edit must not have registered it.
    assert instances.get("saved_chat") is None
    # Nothing holds the target the edit built to check the new settings, so it's released.
    release.assert_awaited_once()
    assert isinstance(release.await_args.args[0], OpenAIChatTarget)
    assert (await _restore(tmp_path)).failures == []
    assert isinstance(instances.get("saved_chat"), OpenAIChatTarget)


async def test_an_edit_that_doesnt_fix_an_entry_that_failed_to_restore_leaves_its_file(tmp_path: Path) -> None:
    broken = {**_SAVED_CHAT, "params": {**_SAVED_CHAT["params"], "endpoint": "https://api.example.test/v1"}}
    _write(tmp_path, TARGETS, "saved_chat", broken)
    assert len((await _restore(tmp_path)).failures) == 1
    instances = TargetRegistry.get_registry_singleton().instances
    details = await _read(tmp_path, TARGETS, "saved_chat")
    before = _file(tmp_path, TARGETS, "saved_chat").read_bytes()

    with pytest.raises(ValueError):
        await _edit(tmp_path, TARGETS, "saved_chat", version=details.version, recipe=details.recipe)

    assert instances.get("saved_chat") is None
    assert _file(tmp_path, TARGETS, "saved_chat").read_bytes() == before


async def test_a_file_rewritten_after_the_edit_read_it_isnt_overwritten(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    instances = ConverterRegistry.get_registry_singleton().instances
    old = instances.get("cat_replace")
    details = await _read(tmp_path, CONVERTERS, "cat_replace")
    path = _file(tmp_path, CONVERTERS, "cat_replace")
    read_recipe = RegistryRecipeStorage.read_recipe

    def read_then_rewrite(storage: RegistryRecipeStorage, name: str) -> Any:
        stored = read_recipe(storage, name)
        path.write_text(path.read_text().replace('"dog"', '"fish"'), encoding="utf-8")
        return stored

    with patch.object(RegistryRecipeStorage, "read_recipe", read_then_rewrite):
        with pytest.raises(SavedEntryConflictError) as refusal:
            await _edit(
                tmp_path,
                CONVERTERS,
                "cat_replace",
                version=details.version,
                recipe={**details.recipe, "params": {"pattern": "cat", "replace": "bird"}},
            )

    assert "changed since it was read" in str(refusal.value)
    assert '"fish"' in path.read_text()
    assert instances.get("cat_replace") is old


@pytest.mark.parametrize(
    ("kind", "document"),
    [
        (TARGETS, {"class_module": "pyrit.prompt_target.gone", "class_name": "GoneTarget", "params": {}}),
        (CONVERTERS, {"class_module": "pyrit.converter.gone", "class_name": "GoneConverter", "params": {}}),
        (RegistryRecipeKind.SCORERS, {"class_module": "pyrit.score.gone", "class_name": "GoneScorer", "params": {}}),
        (RegistryRecipeKind.TECHNIQUES, {"type": "GoneAttack", "params": {}}),
    ],
)
async def test_a_saved_entry_whose_class_is_gone_cant_be_changed(
    tmp_path: Path, kind: RegistryRecipeKind, document: dict[str, Any]
) -> None:
    _write(tmp_path, kind, "gone", {**document, "owner": {"oid": ADA.oid, "name": "Ada"}})
    details = await _read(tmp_path, kind, "gone")
    before = _file(tmp_path, kind, "gone").read_bytes()

    # A plain ValueError is a 400 that names the class, rather than a server error.
    with pytest.raises(ValueError) as refusal:
        await _edit(tmp_path, kind, "gone", version=details.version, recipe={**details.recipe, "params": {"a": 1}})

    assert "Gone" in str(refusal.value)
    assert _file(tmp_path, kind, "gone").read_bytes() == before


async def test_a_file_that_cant_be_read_cant_be_changed(tmp_path: Path) -> None:
    _write(tmp_path, CONVERTERS, "broken", "not json")
    details = await _read(tmp_path, CONVERTERS, "broken", _caller(BEN_OID, admin=True))

    with pytest.raises(SavedEntryInUseError) as refusal:
        await _edit(tmp_path, CONVERTERS, "broken", version=details.version, recipe={}, caller=_caller(admin=True))

    assert "can't be read or isn't a valid entry, so it can't be changed" in str(refusal.value)


async def test_a_name_a_startup_entry_took_cant_be_edited_into_coming_back(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    ConverterRegistry.reset_registry_singleton()
    instances = ConverterRegistry.get_registry_singleton().instances
    startup = SearchReplaceConverter(pattern="fish", replace="dog")
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        instances.register(startup, name="cat_replace")
    details = await _read(tmp_path, CONVERTERS, "cat_replace")
    before = _file(tmp_path, CONVERTERS, "cat_replace").read_bytes()

    with pytest.raises(SavedEntryConflictError):
        await _edit(
            tmp_path,
            CONVERTERS,
            "cat_replace",
            version=details.version,
            recipe={**details.recipe, "params": {"pattern": "cat", "replace": "bird"}},
        )

    assert instances.get("cat_replace") is startup
    assert _file(tmp_path, CONVERTERS, "cat_replace").read_bytes() == before


async def test_an_edit_of_an_entry_whose_name_a_startup_entry_took_leaves_the_startup_entry(tmp_path: Path) -> None:
    await _save_replace(tmp_path)
    ConverterRegistry.reset_registry_singleton()
    instances = ConverterRegistry.get_registry_singleton().instances
    startup = SearchReplaceConverter(pattern="fish", replace="dog")
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        instances.register(startup, name="cat_replace")
    failures = (await _restore(tmp_path)).failures
    assert [failure.reason for failure in failures] == [RestoreFailureReason.NAME_CLASH]
    details = await _read(tmp_path, CONVERTERS, "cat_replace")

    with pytest.raises(SavedEntryConflictError, match="already exists"):
        await _edit(
            tmp_path,
            CONVERTERS,
            "cat_replace",
            version=details.version,
            recipe={**details.recipe, "params": {"pattern": "cat", "replace": "bird"}},
        )

    assert instances.get("cat_replace") is startup


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (RegistryNameTakenError("Instance 'cat_replace' already exists"), SavedEntryConflictError),
        (ValueError("refused"), ValueError),
    ],
)
async def test_a_new_entry_that_cant_be_registered_puts_the_old_file_and_entry_back(
    tmp_path: Path, error: Exception, raised: type[Exception]
) -> None:
    await _save_replace(tmp_path)
    instances = ConverterRegistry.get_registry_singleton().instances
    old = instances.get("cat_replace")
    details = await _read(tmp_path, CONVERTERS, "cat_replace")
    before = _file(tmp_path, CONVERTERS, "cat_replace").read_bytes()
    register = type(instances).register

    def refuse_the_new_one(registry: Any, instance: Any, **kwargs: Any) -> None:
        if instance is not old:
            raise error
        register(registry, instance, **kwargs)

    with patch.object(type(instances), "register", refuse_the_new_one):
        with pytest.raises(raised) as refusal:
            await _edit(
                tmp_path,
                CONVERTERS,
                "cat_replace",
                version=details.version,
                recipe={**details.recipe, "params": {"pattern": "cat", "replace": "bird"}},
            )

    assert type(refusal.value) is raised
    assert _file(tmp_path, CONVERTERS, "cat_replace").read_bytes() == before
    assert instances.get("cat_replace") is old
