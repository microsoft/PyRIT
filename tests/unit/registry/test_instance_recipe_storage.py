# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for saved instance recipe storage."""

import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import CredentialReference, InstanceRecipe, StoredInstanceRecipe
from pyrit.models.identifiers.class_name_utils import REGISTRY_NAME_PATTERN
from pyrit.registry.file_document_storage import (
    DocumentConflictError,
    _release_exclusive_lock,
    _try_acquire_exclusive_lock,
)
from pyrit.registry.instance_recipe_storage import InstanceRecipeStorage


def _recipe(*, name: str = "team-chat", kind: ComponentType = ComponentType.TARGET) -> InstanceRecipe:
    return InstanceRecipe(
        kind=kind,
        name=name,
        type="OpenAIChatTarget",
        params={"endpoint": "https://example.test/v1", "model_name": "gpt-4o"},
        credentials={"api_key": CredentialReference(env_var="TEAM_CHAT_KEY")},
        auth_mode="api_key" if kind is ComponentType.TARGET else None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> InstanceRecipeStorage:
    return InstanceRecipeStorage(source=str(tmp_path))


@pytest.mark.parametrize("name", ["team-chat", "Team.Chat", "z-last", "1st", "CON", "a" * 64, "--", "Ünïcode"])
def test_document_name_is_a_legal_document_name(name: str) -> None:
    for kind in InstanceRecipe.PERSISTED_KINDS:
        document_name = InstanceRecipeStorage.get_document_name(kind=kind, name=name)

        assert re.fullmatch(REGISTRY_NAME_PATTERN, document_name)
        assert document_name.startswith(f"{kind.value}_")


def test_document_name_keeps_names_that_differ_only_in_case_apart() -> None:
    first = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="MyChat")
    second = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="mychat")

    assert first != second
    assert first.lower() != second.lower()


def test_document_name_separates_kinds_and_is_deterministic() -> None:
    target = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="shared")
    scorer = InstanceRecipeStorage.get_document_name(kind=ComponentType.SCORER, name="shared")

    assert target != scorer
    assert target == InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="shared")


def test_saved_recipe_round_trips_with_its_version(storage: InstanceRecipeStorage) -> None:
    saved = storage.save_recipe(recipe=_recipe(), expected_version=None)

    loaded = storage.load_recipe(kind=ComponentType.TARGET, name="team-chat")

    assert loaded == saved
    assert storage.list_recipes() == ([saved], [])


def test_saved_document_holds_references_not_values(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    storage.save_recipe(recipe=_recipe(), expected_version=None)

    [document] = tmp_path.glob("*.json")
    payload = json.loads(document.read_text(encoding="utf-8"))

    assert payload["credentials"] == {"api_key": {"env_var": "TEAM_CHAT_KEY"}}
    assert "api_key" not in payload["params"]
    assert payload["schema_version"] == 1


def test_converter_recipe_omits_the_authentication_mode(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    storage.save_recipe(recipe=_recipe(kind=ComponentType.CONVERTER), expected_version=None)

    [document] = tmp_path.glob("*.json")

    assert "auth_mode" not in json.loads(document.read_text(encoding="utf-8"))


def test_creating_an_existing_recipe_conflicts(storage: InstanceRecipeStorage) -> None:
    storage.save_recipe(recipe=_recipe(), expected_version=None)

    with pytest.raises(DocumentConflictError, match="already exists"):
        storage.save_recipe(recipe=_recipe(), expected_version=None)


def test_update_with_current_version_replaces_and_stale_version_conflicts(storage: InstanceRecipeStorage) -> None:
    first = storage.save_recipe(recipe=_recipe(), expected_version=None)
    changed = _recipe().model_copy(update={"params": {"endpoint": "https://other.test/v1"}})

    second = storage.save_recipe(recipe=changed, expected_version=first.version)

    assert second.version != first.version
    with pytest.raises(DocumentConflictError, match="changed by someone else"):
        storage.save_recipe(recipe=_recipe(), expected_version=first.version)
    assert storage.load_recipe(kind=ComponentType.TARGET, name="team-chat") == second


def test_delete_requires_the_current_version(storage: InstanceRecipeStorage) -> None:
    saved = storage.save_recipe(recipe=_recipe(), expected_version=None)

    with pytest.raises(DocumentConflictError):
        storage.delete_recipe(kind=ComponentType.TARGET, name="team-chat", expected_version="0" * 64)
    storage.delete_recipe(kind=ComponentType.TARGET, name="team-chat", expected_version=saved.version)

    assert storage.load_recipe(kind=ComponentType.TARGET, name="team-chat") is None


def _write(tmp_path: Path, *, kind: ComponentType, name: str, content: bytes) -> None:
    document_name = InstanceRecipeStorage.get_document_name(kind=kind, name=name)
    (tmp_path / f"{document_name}.json").write_bytes(content)


@pytest.mark.parametrize(
    ("content", "detail", "recovers_name"),
    [
        (b"{not json", "not valid JSON", False),
        (b"\xff\xfe", "not valid JSON", False),
        (b"[1, 2]", "not a JSON object", False),
        (b'{"kind": "target", "name": "broken"}', "type", True),
        (b'{"kind": "target", "name": "broken", "type": "X", "surprise": 1}', "surprise", True),
        (b'{"kind": "target", "name": "other", "type": "X", "surprise": 1}', "surprise", False),
    ],
)
def test_unreadable_document_is_reported_with_its_version(
    storage: InstanceRecipeStorage, tmp_path: Path, content: bytes, detail: str, recovers_name: bool
) -> None:
    _write(tmp_path, kind=ComponentType.TARGET, name="broken", content=content)

    recipes, unreadable = storage.list_recipes()

    assert recipes == []
    [entry] = unreadable
    assert entry.kind is ComponentType.TARGET
    assert entry.name == (
        "broken" if recovers_name else InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="broken")
    )
    assert detail in entry.reason
    assert "Repair or delete the document" in entry.reason
    assert entry.version == InstanceRecipeStorage._compute_version(content)
    assert entry.content == content
    assert storage.load_recipe(kind=ComponentType.TARGET, name="broken") == entry


def test_recipe_from_a_newer_pyrit_is_reported_by_name(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    content = json.dumps({"schema_version": 2, "kind": "target", "name": "future", "type": "X", "new": 1}).encode()
    _write(tmp_path, kind=ComponentType.TARGET, name="future", content=content)

    [entry] = storage.list_recipes()[1]

    assert entry.name == "future"
    assert "newer version of PyRIT" in entry.reason
    assert entry.content == content
    assert "content" not in entry.model_dump()


def test_document_this_pyrit_cannot_use_is_found_by_its_document_name_too(
    storage: InstanceRecipeStorage, tmp_path: Path
) -> None:
    content = json.dumps({"schema_version": 2, "kind": "target", "name": "future", "type": "X"}).encode()
    _write(tmp_path, kind=ComponentType.TARGET, name="future", content=content)
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="future")
    entry = storage.load_recipe(kind=ComponentType.TARGET, name="future")
    assert entry is not None and entry.version is not None

    assert storage.load_recipe(kind=ComponentType.TARGET, name=document_name) == entry
    storage.delete_recipe(kind=ComponentType.TARGET, name=document_name, expected_version=entry.version)
    assert storage.list_recipes() == ([], [])


@pytest.mark.parametrize("schema_version", [1, 2])
@pytest.mark.parametrize("name", ["x" * 257, "team/chat", "with space", "target_reserved_0123456789ab"])
def test_document_naming_an_instance_the_api_cannot_address_is_reported_by_document_name(
    storage: InstanceRecipeStorage, tmp_path: Path, name: str, schema_version: int
) -> None:
    content = json.dumps({"schema_version": schema_version, "kind": "target", "name": name, "type": "X"}).encode()
    _write(tmp_path, kind=ComponentType.TARGET, name=name, content=content)
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name=name)

    recipes, [entry] = storage.list_recipes()

    assert recipes == []
    assert entry.name == document_name
    assert storage.load_recipe(kind=ComponentType.TARGET, name=document_name) == entry
    storage.delete_recipe(kind=ComponentType.TARGET, name=document_name, expected_version=entry.version)
    assert storage.list_recipes() == ([], [])


def test_document_whose_name_was_edited_is_not_trusted(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    content = _recipe(name="renamed").model_dump_json().encode()
    _write(tmp_path, kind=ComponentType.TARGET, name="original", content=content)

    recipes, [entry] = storage.list_recipes()

    assert recipes == []
    assert entry.name == InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="original")
    assert "kind or name was changed" in entry.reason


@pytest.mark.parametrize("document_name", ["notes", "target_notes", "target_notes_0123456789"])
def test_documents_this_storage_did_not_write_are_ignored(
    storage: InstanceRecipeStorage, tmp_path: Path, document_name: str
) -> None:
    content = {"schema_version": 2, "kind": "target", "name": "target_notes", "type": "X", "params": {"ref": "x"}}
    (tmp_path / f"{document_name}.json").write_text(json.dumps(content), encoding="utf-8")

    assert storage.list_recipes() == ([], [])
    assert storage.load_recipe(kind=ComponentType.TARGET, name=document_name) is None


def test_unreadable_document_can_be_replaced_with_its_version(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    _write(tmp_path, kind=ComponentType.TARGET, name="team-chat", content=b"{not json")
    broken = storage.load_recipe(kind=ComponentType.TARGET, name="team-chat")
    assert broken is not None

    repaired = storage.save_recipe(recipe=_recipe(), expected_version=broken.version)

    assert isinstance(repaired, StoredInstanceRecipe)
    assert storage.list_recipes() == ([repaired], [])


def test_document_that_storage_cannot_read_is_reported_without_a_version(
    storage: InstanceRecipeStorage, tmp_path: Path
) -> None:
    saved = storage.save_recipe(recipe=_recipe(name="healthy"), expected_version=None)
    content = _recipe(name="locked").model_dump_json().encode()
    _write(tmp_path, kind=ComponentType.TARGET, name="locked", content=content)
    locked = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="locked")
    read_bytes = Path.read_bytes

    def _read_bytes(path: Path) -> bytes:
        if path.stem == locked:
            raise PermissionError("access denied")
        return read_bytes(path)

    with patch.object(Path, "read_bytes", _read_bytes):
        recipes, [entry] = storage.list_recipes()

    assert recipes == [saved]
    assert entry.kind is ComponentType.TARGET
    assert entry.name == locked
    assert entry.version is None
    assert entry.content is None
    assert "could not be read from storage: access denied" in entry.reason


def test_a_local_read_of_a_saved_recipe_waits_for_its_lock(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    storage.save_recipe(recipe=_recipe(), expected_version=None)
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="team-chat")
    descriptor = os.open(tmp_path / f".{document_name}.json.lock", os.O_RDWR)
    assert _try_acquire_exclusive_lock(descriptor)

    try:
        with (
            patch.object(InstanceRecipeStorage, "LOCK_TIMEOUT_SECONDS", 0.1),
            pytest.raises(TimeoutError, match="another writer still holds it"),
        ):
            storage.load_recipe(kind=ComponentType.TARGET, name="team-chat")
    finally:
        _release_exclusive_lock(descriptor)
        os.close(descriptor)


def test_reading_or_deleting_a_name_with_nothing_saved_leaves_no_lock_file(
    storage: InstanceRecipeStorage, tmp_path: Path
) -> None:
    assert storage.load_recipe(kind=ComponentType.TARGET, name="missing") is None
    with pytest.raises(DocumentConflictError):
        storage.delete_recipe(kind=ComponentType.TARGET, name="missing", expected_version="0" * 64)

    assert list(tmp_path.iterdir()) == []


def test_a_blob_store_reads_a_recipe_without_taking_a_local_lock(tmp_path: Path) -> None:
    saved = InstanceRecipeStorage(source=str(tmp_path)).save_recipe(recipe=_recipe(), expected_version=None)
    client = MagicMock()
    client.__enter__.return_value = client
    client.download_blob.return_value.readall.return_value = saved.content
    storage = InstanceRecipeStorage(source="https://account.blob.core.windows.net/recipes?sv=2024&sig=secret")

    with (
        patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client),
        patch.object(storage, "_local_document_lock") as local_lock,
    ):
        loaded = storage.load_recipe(kind=ComponentType.TARGET, name="team-chat")

    assert loaded == saved
    local_lock.assert_not_called()


def test_storage_errors_have_the_source_sas_signature_removed() -> None:
    storage = InstanceRecipeStorage(source="https://account.blob.core.windows.net/recipes?sv=2024&sig=sig%2Bsecret")

    redacted = storage.redact("GET /recipes?sv=2024&sig=sig%2Bsecret failed: signature sig+secret mismatch")

    assert "sig%2Bsecret" not in redacted
    assert "sig+secret" not in redacted
    assert "sv=2024" in redacted


@pytest.mark.parametrize(
    ("name", "reserved"),
    [
        (InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="team-chat"), True),
        ("target_0123456789ab", True),
        ("target_team_chat", False),
        (InstanceRecipeStorage.get_document_name(kind=ComponentType.CONVERTER, name="team-chat"), False),
    ],
)
def test_names_shaped_like_document_names_of_the_kind_are_recognized(name: str, reserved: bool) -> None:
    assert InstanceRecipeStorage.is_document_name(kind=ComponentType.TARGET, name=name) is reserved


def test_document_that_does_not_name_its_instance_is_found_and_deleted_by_document_name(
    storage: InstanceRecipeStorage, tmp_path: Path
) -> None:
    _write(tmp_path, kind=ComponentType.TARGET, name="broken", content=b"{not json")
    [entry] = storage.list_recipes()[1]
    assert entry.version is not None

    assert storage.load_recipe(kind=ComponentType.TARGET, name=entry.name) == entry
    storage.delete_recipe(kind=ComponentType.TARGET, name=entry.name, expected_version=entry.version)

    assert list(tmp_path.glob("*.json")) == []


def test_document_name_does_not_address_a_readable_recipe(storage: InstanceRecipeStorage) -> None:
    saved = storage.save_recipe(recipe=_recipe(), expected_version=None)
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="team-chat")

    assert storage.load_recipe(kind=ComponentType.TARGET, name=document_name) is None
    with pytest.raises(DocumentConflictError):
        storage.delete_recipe(kind=ComponentType.TARGET, name=document_name, expected_version=saved.version)
    assert storage.load_recipe(kind=ComponentType.TARGET, name="team-chat") == saved


@pytest.mark.parametrize("other_content", [b"{not json", b"{also not json"])
def test_document_name_addresses_its_own_document_even_when_one_is_saved_under_it_as_a_name(
    storage: InstanceRecipeStorage, tmp_path: Path, other_content: bytes
) -> None:
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="owner")
    _write(tmp_path, kind=ComponentType.TARGET, name="owner", content=b"{not json")
    _write(tmp_path, kind=ComponentType.TARGET, name=document_name, content=other_content)
    listed = {entry.name: entry for entry in storage.list_recipes()[1]}

    found = storage.load_recipe(kind=ComponentType.TARGET, name=document_name)
    storage.delete_recipe(kind=ComponentType.TARGET, name=document_name, expected_version=listed[document_name].version)

    assert found == listed[document_name]
    assert [entry.name for entry in storage.list_recipes()[1]] == [
        InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name=document_name)
    ]


def test_document_name_of_a_readable_recipe_does_not_delete_a_document_saved_under_it_as_a_name(
    storage: InstanceRecipeStorage, tmp_path: Path
) -> None:
    saved = storage.save_recipe(recipe=_recipe(), expected_version=None)
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="team-chat")
    assert saved.content is not None
    _write(tmp_path, kind=ComponentType.TARGET, name=document_name, content=saved.content)

    with pytest.raises(DocumentConflictError):
        storage.delete_recipe(kind=ComponentType.TARGET, name=document_name, expected_version=saved.version)

    assert len(list(tmp_path.glob("*.json"))) == 2


def test_document_shaped_name_too_long_for_a_document_finds_nothing(storage: InstanceRecipeStorage) -> None:
    name = f"target_{'a' * 60}_0123456789ab"
    assert InstanceRecipeStorage.is_document_name(kind=ComponentType.TARGET, name=name)

    assert storage.load_recipe(kind=ComponentType.TARGET, name=name) is None
    with pytest.raises(DocumentConflictError):
        storage.delete_recipe(kind=ComponentType.TARGET, name=name, expected_version="0" * 64)


def test_restoring_a_replaced_recipe_puts_back_its_exact_bytes(storage: InstanceRecipeStorage, tmp_path: Path) -> None:
    original = _recipe().model_dump_json().encode()
    _write(tmp_path, kind=ComponentType.TARGET, name="team-chat", content=original)
    previous = storage.load_recipe(kind=ComponentType.TARGET, name="team-chat")
    assert isinstance(previous, StoredInstanceRecipe)
    changed = _recipe().model_copy(update={"params": {"endpoint": "https://other.test/v1"}})
    replacement = storage.save_recipe(recipe=changed, expected_version=previous.version)

    storage.restore_recipe(previous=previous, expected_version=replacement.version)

    [document] = tmp_path.glob("*.json")
    assert document.read_bytes() == original
    assert storage.load_recipe(kind=ComponentType.TARGET, name="team-chat") == previous


def test_restoring_a_replaced_recipe_conflicts_once_storage_changed(storage: InstanceRecipeStorage) -> None:
    previous = storage.save_recipe(recipe=_recipe(), expected_version=None)
    changed = _recipe().model_copy(update={"params": {"endpoint": "https://other.test/v1"}})
    storage.save_recipe(recipe=changed, expected_version=previous.version)

    with pytest.raises(DocumentConflictError):
        storage.restore_recipe(previous=previous, expected_version=previous.version)


def test_default_source_is_under_the_pyrit_configuration_directory() -> None:
    assert InstanceRecipeStorage().display_source.replace("\\", "/").endswith(".pyrit/instance_recipes")
