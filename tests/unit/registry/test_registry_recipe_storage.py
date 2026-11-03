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

from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH
from pyrit.models.catalog.registry_recipe import (
    ComponentRecipe,
    IgnoredRecipeFile,
    RegistryRecipeKind,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryRef,
    TechniqueRecipe,
)
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage

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


class TestRestoreReportText:
    def test_text_that_is_not_utf8_is_escaped_so_it_can_be_sent_as_json(self) -> None:
        ignored = IgnoredRecipeFile(
            kind=RegistryRecipeKind.TARGETS, file_name="bad\udcffname.json", message="bad\udcffname"
        )
        failure = RestoreFailure(
            kind=RegistryRecipeKind.TARGETS,
            name="x\udcff",
            reason=RestoreFailureReason.MISSING_DEPENDENCY,
            message="got \udcff",
            dependency=SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="ghost\ud800"),
        )

        assert ignored.file_name == "bad\\udcffname.json"
        assert (failure.name, failure.message) == ("x\\udcff", "got \\udcff")
        assert failure.dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="ghost\\ud800")
        json.dumps([ignored.model_dump(mode="json"), failure.model_dump(mode="json")], ensure_ascii=False).encode(
            "utf-8"
        )

    def test_valid_text_is_kept_as_written(self) -> None:
        ref = SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="caf\u00e9 \uff10\uff0e\uff15 \U0001f600")

        assert ref.name == "caf\u00e9 \uff10\uff0e\uff15 \U0001f600"


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
        json.dumps(ignored.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")

    def test_reports_unreadable_and_ignored_files(self, tmp_path: Path) -> None:
        folder = tmp_path / "scorers"
        _write(folder, "locked", json.dumps(_TARGET))
        _write(folder, "Bad-Name", "{}")
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path.name == "locked.json":
                raise PermissionError(13, "Permission denied", str(path))
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes):
            listing = RegistryRecipeStorage(kind=RegistryRecipeKind.SCORERS, source=str(tmp_path)).list_recipes()

        (failure,) = listing.failures
        assert (failure.name, failure.reason, failure.message) == (
            "locked",
            RestoreFailureReason.UNREADABLE_RECIPE,
            "Permission denied",
        )
        assert failure.version is None
        (ignored,) = listing.ignored
        assert (ignored.kind, ignored.file_name) == (RegistryRecipeKind.SCORERS, "Bad-Name.json")
        assert "Bad-Name" in ignored.message
