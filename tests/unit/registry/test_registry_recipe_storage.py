# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.storage.blob import ContainerClient
from pydantic import ValidationError

from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryRecipeKind,
    RestoreFailureReason,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
from pyrit.registry.file_document_storage import DocumentStorageUnavailableError, _read_regular_file
from pyrit.registry.registry_recipe_storage import MAX_FAILURE_MESSAGE_LENGTH, RegistryRecipeStorage

_TARGET = {
    "schema_version": 1,
    "class_module": "pyrit.prompt_target.openai.openai_chat_target",
    "class_name": "OpenAIChatTarget",
    "params": {"auth_mode": "identity", "endpoint": "https://contoso.openai.azure.com/openai/v1"},
}


def _write(directory: Path, name: str, content: str | bytes) -> bytes:
    directory.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    (directory / f"{name}.json").write_bytes(data)
    return data


class TestRegistryRecipeStorageSource:
    def test_local_source_uses_a_folder_per_registry(self, tmp_path: Path) -> None:
        storage = RegistryRecipeStorage(kind=RegistryRecipeKind.CONVERTERS, source=str(tmp_path))

        assert storage.display_source == str(tmp_path / "converters")

    def test_default_source_is_under_the_pyrit_configuration_directory(self) -> None:
        storage = RegistryRecipeStorage(kind=RegistryRecipeKind.SCORERS)

        assert storage.display_source == str(CONFIGURATION_DIRECTORY_PATH / "registry_recipes" / "scorers")

    def test_blob_source_adds_the_folder_and_keeps_the_sas_token(self) -> None:
        source = "https://account.blob.core.windows.net/recipes/team?sp=rl&sig=secret"

        kind_source = RegistryRecipeStorage._get_kind_source(root=source, kind=RegistryRecipeKind.TARGETS)

        assert kind_source == "https://account.blob.core.windows.net/recipes/team/targets?sp=rl&sig=secret"

    def test_blob_container_source_uses_the_folder_as_prefix(self) -> None:
        client = MagicMock(spec=ContainerClient)
        client.__enter__.return_value = client
        client.list_blobs.return_value = [SimpleNamespace(name="techniques/formal.json")]
        client.download_blob.return_value.readall.return_value = json.dumps({"type": "RedTeamingAttack"}).encode()
        storage = RegistryRecipeStorage(
            kind=RegistryRecipeKind.TECHNIQUES, source="https://account.blob.core.windows.net/recipes?sig=secret"
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            listing = storage.list_recipes()

        client.list_blobs.assert_called_once_with(name_starts_with="techniques/")
        assert list(listing.recipes) == ["formal"]


class TestRecipeModels:
    def test_a_document_without_a_schema_version_is_version_one(self) -> None:
        document = {key: value for key, value in _TARGET.items() if key != "schema_version"}

        with patch.object(ComponentRecipe, "CURRENT_SCHEMA_VERSION", 2):
            assert ComponentRecipe.model_validate(document).schema_version == 1

    def test_rejects_a_schema_version_newer_than_the_model_reads(self) -> None:
        with pytest.raises(ValidationError, match="newer than this PyRIT reads"):
            ComponentRecipe.model_validate({**_TARGET, "schema_version": 2})

    @pytest.mark.parametrize(
        ("kind", "recipe"),
        [
            (RegistryRecipeKind.TECHNIQUES, ComponentRecipe.model_validate(_TARGET)),
            (RegistryRecipeKind.TARGETS, TechniqueRecipe.model_validate({"type": "RedTeamingAttack"})),
        ],
    )
    def test_a_stored_recipe_must_match_its_registry(
        self, kind: RegistryRecipeKind, recipe: ComponentRecipe | TechniqueRecipe
    ) -> None:
        with pytest.raises(ValidationError, match="entry needs a"):
            StoredRecipe(ref=SavedEntryRef(kind=kind, name="mismatched"), recipe=recipe, version="v1")


class TestRegistryRecipeStorageListing:
    def test_reads_component_recipes_with_their_versions(self, tmp_path: Path) -> None:
        content = _write(tmp_path / "targets", "contoso_gpt4o", json.dumps(_TARGET))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        stored = listing.recipes["contoso_gpt4o"]
        assert stored.ref.kind is RegistryRecipeKind.TARGETS
        assert stored.ref.name == "contoso_gpt4o"
        assert stored.recipe == ComponentRecipe.model_validate(_TARGET)
        assert stored.version == hashlib.sha256(content).hexdigest()
        assert listing.failures == ()

    def test_reads_technique_recipes_as_technique_recipes(self, tmp_path: Path) -> None:
        document = {"type": "RedTeamingAttack", "tags": ["custom"], "adversarial_chat": "contoso_gpt4o"}
        _write(tmp_path / "techniques", "formal_red_team", json.dumps(document))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TECHNIQUES, source=str(tmp_path)).list_recipes()

        assert listing.recipes["formal_red_team"].recipe == TechniqueRecipe.model_validate(document)

    def test_reports_documents_that_are_not_recipes(self, tmp_path: Path) -> None:
        folder = tmp_path / "targets"
        _write(folder, "not_json", "{oops")
        _write(folder, "not_utf8", b"\xff\xfe")
        _write(folder, "not_object", "[1, 2]")
        _write(folder, "extra_field", json.dumps({**_TARGET, "credentials": {"api_key": "sk-secret-value"}}))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        assert listing.recipes == {}
        assert {failure.name for failure in listing.failures} == {"not_json", "not_utf8", "not_object", "extra_field"}
        assert all(failure.reason is RestoreFailureReason.INVALID_RECIPE for failure in listing.failures)
        assert all(failure.version for failure in listing.failures)
        extra = next(failure for failure in listing.failures if failure.name == "extra_field")
        assert "credentials" in extra.message
        assert "sk-secret-value" not in extra.message

    def test_reports_a_document_nested_too_deeply(self, tmp_path: Path) -> None:
        _write(tmp_path / "targets", "deep", "[" * 100_000 + "]" * 100_000)
        _write(tmp_path / "targets", "contoso_gpt4o", json.dumps(_TARGET))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        (failure,) = listing.failures
        assert (failure.name, failure.reason) == ("deep", RestoreFailureReason.INVALID_RECIPE)
        assert list(listing.recipes) == ["contoso_gpt4o"]

    def test_limits_a_technique_description_like_the_create_request(self, tmp_path: Path) -> None:
        folder = tmp_path / "techniques"
        _write(folder, "at_limit", json.dumps({"type": "RedTeamingAttack", "description": "x" * 100_000}))
        _write(folder, "over_limit", json.dumps({"type": "RedTeamingAttack", "description": "x" * 100_001}))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TECHNIQUES, source=str(tmp_path)).list_recipes()

        assert list(listing.recipes) == ["at_limit"]
        (failure,) = listing.failures
        assert (failure.name, failure.reason) == ("over_limit", RestoreFailureReason.INVALID_RECIPE)
        assert "description" in failure.message

    def test_reports_a_newer_schema_version_before_checking_fields(self, tmp_path: Path) -> None:
        _write(tmp_path / "targets", "future", json.dumps({"schema_version": 2, "unknown_field": True}))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        (failure,) = listing.failures
        assert failure.reason is RestoreFailureReason.NEWER_SCHEMA_VERSION
        assert "newer PyRIT" in failure.message

    def test_reports_a_file_name_that_is_not_utf8(self, tmp_path: Path) -> None:
        folder = tmp_path / "targets"
        folder.mkdir()
        try:
            (folder / os.fsdecode(b"bad\xffname.json")).write_text("{}")
        except (OSError, UnicodeError):
            pytest.skip("This file system only allows UTF-8 file names")

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        (ignored,) = listing.ignored
        assert ignored.file_name == "bad\\udcffname.json"
        assert "bad\\udcffname" in ignored.message
        assert json.loads(ignored.model_dump_json())["file_name"] == "bad\\udcffname.json"

    def test_reports_unreadable_and_ignored_files(self, tmp_path: Path) -> None:
        folder = tmp_path / "scorers"
        _write(folder, "locked", json.dumps(_TARGET))
        _write(folder, "Bad-Name", "{}")

        def read(path: str) -> bytes | None:
            if Path(path).name == "locked.json":
                raise PermissionError(13, "Permission denied", path)
            return _read_regular_file(path)

        with patch("pyrit.registry.file_document_storage._read_regular_file", side_effect=read):
            listing = RegistryRecipeStorage(kind=RegistryRecipeKind.SCORERS, source=str(tmp_path)).list_recipes()

        (failure,) = listing.failures
        assert (failure.name, failure.reason, failure.message) == (
            "locked",
            RestoreFailureReason.UNREADABLE_RECIPE,
            "Permission denied",
        )
        assert failure.version is None
        assert isinstance(listing.causes["locked"], PermissionError)
        (ignored,) = listing.ignored
        assert (ignored.kind, ignored.file_name) == (RegistryRecipeKind.SCORERS, "Bad-Name.json")
        assert "Bad-Name" in ignored.message

    def test_accepts_a_byte_order_mark(self, tmp_path: Path) -> None:
        _write(tmp_path / "targets", "from_notepad", b"\xef\xbb\xbf" + json.dumps(_TARGET).encode("utf-8"))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        assert list(listing.recipes) == ["from_notepad"]

    @pytest.mark.parametrize(
        "document",
        [
            '{"class_module": "m", "class_name": "C", "params": {"text": "bad \\udcff"}}',
            '{"class_module": "m", "class_name": "C", "params": {"bad \\ud800": 1}}',
        ],
        ids=["value", "key"],
    )
    def test_reports_an_unpaired_surrogate_as_invalid(self, tmp_path: Path, document: str) -> None:
        _write(tmp_path / "targets", "odd_text", document)

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        (failure,) = listing.failures
        assert (failure.name, failure.reason) == ("odd_text", RestoreFailureReason.INVALID_RECIPE)
        assert "unpaired surrogate" in failure.message

    def test_keeps_text_that_is_valid_unicode(self, tmp_path: Path) -> None:
        document = {**_TARGET, "params": {"greeting": "caf\u00e9 \U0001f600"}}
        _write(tmp_path / "targets", "friendly", json.dumps(document))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        assert listing.recipes["friendly"].recipe.params["greeting"] == "caf\u00e9 \U0001f600"

    def test_checks_a_technique_against_the_technique_format_version(self, tmp_path: Path) -> None:
        _write(tmp_path / "techniques", "next_format", json.dumps({"schema_version": 2, "type": "RedTeamingAttack"}))
        _write(tmp_path / "targets", "next_format", json.dumps({**_TARGET, "schema_version": 2}))

        with patch.object(TechniqueRecipe, "CURRENT_SCHEMA_VERSION", 2):
            techniques = RegistryRecipeStorage(kind=RegistryRecipeKind.TECHNIQUES, source=str(tmp_path)).list_recipes()
            targets = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        assert list(techniques.recipes) == ["next_format"]
        assert targets.failures[0].reason is RestoreFailureReason.NEWER_SCHEMA_VERSION

    def test_shortens_what_a_validation_error_repeats(self, tmp_path: Path) -> None:
        long_key = "k" * 10_000
        many_keys = {f"unknown_{index:03d}_{'x' * 40}": True for index in range(100)}
        _write(tmp_path / "targets", "long_key", json.dumps({**_TARGET, long_key: True}))
        _write(tmp_path / "targets", "many_keys", json.dumps({**_TARGET, **many_keys}))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        messages = {failure.name: failure.message for failure in listing.failures}
        assert "k" * 61 + "..." in messages["long_key"]
        assert "k" * 65 not in messages["long_key"]
        assert len(messages["many_keys"]) == MAX_FAILURE_MESSAGE_LENGTH
        assert messages["many_keys"].endswith("...")

    def test_a_listing_cannot_be_changed_by_its_reader(self, tmp_path: Path) -> None:
        _write(tmp_path / "targets", "contoso_gpt4o", json.dumps(_TARGET))

        listing = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()

        with pytest.raises(TypeError):
            listing.recipes["other"] = listing.recipes["contoso_gpt4o"]  # type: ignore[index]

    def test_raises_when_the_folder_cannot_be_listed(self, tmp_path: Path) -> None:
        (tmp_path / "targets").write_text("a file where the folder should be")

        with pytest.raises(DocumentStorageUnavailableError):
            RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=str(tmp_path)).list_recipes()
