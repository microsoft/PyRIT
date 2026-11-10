# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage for saved registry recipes, one folder per registry."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import ValidationError

from pyrit.common.azure_storage import is_azure_blob_uri
from pyrit.models.catalog import (
    ComponentRecipe,
    IgnoredRecipeFile,
    RegistryRecipeKind,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
from pyrit.models.catalog.registry_recipe import RECIPE_SCHEMA_VERSION
from pyrit.registry.file_document_storage import FileDocumentStorage, describe_storage_error

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_FAILURE_MESSAGE_LENGTH = 1_024
_MAX_LOCATION_PART_LENGTH = 64


def shorten_text(text: str, *, limit: int) -> str:
    """
    Cut text that's longer than *limit*, marking the cut.

    Returns:
        str: The text, at most *limit* characters long.
    """
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def escape_unprintable(text: str) -> str:
    """
    Write each character that can't be shown as it is as a backslash escape.

    That covers a new line or a terminal escape in a stored key, which would otherwise start a new log line or
    change a terminal's colors, and the bytes of a file name that isn't UTF-8.

    Returns:
        str: The text, on one line and safe to encode as UTF-8.
    """
    if text.isprintable():
        return text
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode("ascii")
        for character in text
    )


def safe_failure_text(text: str) -> str:
    """
    Make text that can repeat stored content safe for a failure report: escaped, then shortened.

    Returns:
        str: The text, at most ``MAX_FAILURE_MESSAGE_LENGTH`` characters long.
    """
    # Escaping never makes text shorter, so what's past the cap can't show and needn't be escaped.
    return shorten_text(escape_unprintable(text[: MAX_FAILURE_MESSAGE_LENGTH + 1]), limit=MAX_FAILURE_MESSAGE_LENGTH)


@dataclass(frozen=True)
class RecipeListing:
    """Every saved recipe of one kind, with the ones that couldn't be read or parsed."""

    recipes: Mapping[str, StoredRecipe] = field(default_factory=lambda: MappingProxyType({}))
    failures: tuple[RestoreFailure, ...] = ()
    ignored: tuple[IgnoredRecipeFile, ...] = ()
    # The error behind each recipe that couldn't be read, by name, for the log only.
    causes: Mapping[str, BaseException] = field(default_factory=lambda: MappingProxyType({}), compare=False, repr=False)


class RegistryRecipeStorage(FileDocumentStorage):
    """
    Read the saved recipes of one registry as JSON documents.

    Each registry has its own folder under the configured source (``targets``, ``converters``,
    ``scorers``, ``techniques``), so a target and a converter can share a name. Storage is read
    through on every call, like scenario presets, because the folder can be shared between
    processes.
    """

    def __init__(self, *, kind: RegistryRecipeKind, source: str | None = None) -> None:
        """
        Initialize storage for one registry's folder.

        Args:
            kind (RegistryRecipeKind): The registry whose recipes this storage reads.
            source (str | None): Local directory or Azure Blob URI that holds the per-registry
                folders. Defaults to ``registry_recipes`` under the PyRIT configuration directory.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        root = source or str(self._get_default_storage_dir())
        super().__init__(
            source=self._get_kind_source(root=root, kind=kind),
            extension=".json",
            source_label=f"Saved {kind.value}",
        )
        self._kind = kind

    @staticmethod
    def _get_default_storage_dir() -> Path:
        """
        Get the default directory that holds the per-registry folders.

        Returns:
            Path: Path to ``~/.pyrit/registry_recipes/``.
        """
        # Deferred: importing pyrit.common.path triggers pyrit __init__.py
        from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

        return CONFIGURATION_DIRECTORY_PATH / "registry_recipes"

    @staticmethod
    def _get_kind_source(*, root: str, kind: RegistryRecipeKind) -> str:
        """
        Append the registry's folder to the configured source.

        For Azure Blob the folder goes on the URL path, so a SAS token in the query string is kept.

        Returns:
            str: The local directory or Blob URI for the registry's folder.
        """
        if is_azure_blob_uri(root):
            parsed = urlparse(root)
            return parsed._replace(path=f"{parsed.path.rstrip('/')}/{kind.value}").geturl()
        return str(Path(root) / kind.value)

    def list_recipes(self) -> RecipeListing:
        """
        Read every saved recipe of this kind and report the ones that can't be used.

        Text from the OS, such as a file name that isn't UTF-8, is escaped here so a report can be sent as JSON.

        Returns:
            RecipeListing: Parsed recipes keyed by name, plus the failures and ignored files.

        Raises:
            DocumentStorageUnavailableError: If the folder or container can't be listed.
        """
        listing = self._list_documents_with_issues()
        recipes: dict[str, StoredRecipe] = {}
        failures = [
            self._failure(
                name=issue.name,
                reason=RestoreFailureReason.UNREADABLE_RECIPE,
                message=issue.message,
            )
            for issue in listing.unreadable
        ]
        causes = {issue.name: issue.error for issue in listing.unreadable if issue.error is not None}
        for name, content in listing.documents.items():
            parsed = self._parse_recipe(name=name, content=content)
            if isinstance(parsed, RestoreFailure):
                failures.append(parsed)
            else:
                recipes[name] = parsed
        ignored = tuple(
            IgnoredRecipeFile(
                kind=self._kind,
                file_name=escape_unprintable(issue.file_name),
                message=safe_failure_text(issue.message),
            )
            for issue in listing.ignored
        )
        return RecipeListing(
            recipes=MappingProxyType(recipes),
            failures=tuple(failures),
            ignored=ignored,
            causes=MappingProxyType(causes),
        )

    def create_recipe(self, *, name: str, recipe: ComponentRecipe | TechniqueRecipe) -> str:
        """
        Save a new recipe under a name that no saved recipe of this kind has.

        Args:
            name (str): The entry's name, which is also its document name.
            recipe (ComponentRecipe | TechniqueRecipe): The recipe to save.

        Returns:
            str: The version of the saved document.

        Raises:
            DocumentConflictError: If a recipe with this name is already saved.
            ValueError: If the name isn't a legal registry name.
            DocumentStorageUnavailableError: If the folder or container can't be written, or a local write
                could not acquire the document lock.
        """
        return self._save_document_conditional(name=name, content=self._serialize(recipe), expected_version=None)

    def read_recipe(self, name: str) -> StoredRecipe | RestoreFailure | None:
        """
        Read one saved recipe.

        Args:
            name (str): The entry's name.

        Returns:
            StoredRecipe | RestoreFailure | None: The recipe, why its file can't be used, or None if no
                recipe of this kind has the name.

        Raises:
            ValueError: If the name isn't a legal registry name.
            AzureError: If the Blob container can't be read.
        """
        try:
            content = self._read_document_bytes(name)
        except OSError as error:
            return self._failure(
                name=name, reason=RestoreFailureReason.UNREADABLE_RECIPE, message=describe_storage_error(error)
            )
        if content is None:
            return None
        return self._parse_recipe(name=name, content=content)

    def delete_recipe(self, name: str, *, expected_version: str | None = None) -> bool:
        """
        Delete a saved recipe.

        Args:
            name (str): The entry's name.
            expected_version (str | None): The version the caller read, so a recipe that changed since then
                isn't deleted, or None to delete whatever is stored. Defaults to None.

        Returns:
            bool: True if the recipe existed and was deleted, False if it was already gone.

        Raises:
            DocumentConflictError: If the stored version isn't ``expected_version``.
            ValueError: If the name isn't a legal registry name.
            DocumentStorageUnavailableError: If the folder or container can't be written, or a local delete
                could not acquire the document lock.
        """
        return self._delete_document(name, expected_version=expected_version)

    @staticmethod
    def _serialize(recipe: ComponentRecipe | TechniqueRecipe) -> bytes:
        """
        Write a recipe as JSON, labeled with the format version this PyRIT writes.

        A recipe built in memory reads as version 1 unless it says otherwise, so the label is set here;
        otherwise, once the version moves past 1, new files would be labeled with the old version.

        Returns:
            bytes: The document.
        """
        current = recipe.model_copy(update={"schema_version": RECIPE_SCHEMA_VERSION})
        return current.model_dump_json(indent=2, exclude_none=True).encode("utf-8")

    def _parse_recipe(self, *, name: str, content: bytes) -> StoredRecipe | RestoreFailure:
        """
        Parse one stored document, checking its schema version before anything else.

        Returns:
            StoredRecipe | RestoreFailure: The recipe, or why it can't be used.
        """
        version = self._compute_version(content)
        invalid = RestoreFailureReason.INVALID_RECIPE
        too_deep = "Nested too deeply to read."
        try:
            # Recipes can be written by hand, and Windows tools often start a file with a byte-order mark.
            payload = json.loads(content.decode("utf-8-sig"))
        except ValueError:
            return self._failure(name=name, version=version, reason=invalid, message="Not valid UTF-8 JSON.")
        except RecursionError:
            return self._failure(name=name, version=version, reason=invalid, message=too_deep)
        if not isinstance(payload, dict):
            return self._failure(name=name, version=version, reason=invalid, message="Not a JSON object.")
        try:
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            message = "Has a \\u escape that isn't a character (an unpaired surrogate)."
            return self._failure(name=name, version=version, reason=invalid, message=message)
        except RecursionError:
            return self._failure(name=name, version=version, reason=invalid, message=too_deep)
        model = TechniqueRecipe if self._kind is RegistryRecipeKind.TECHNIQUES else ComponentRecipe
        schema_version = payload.get("schema_version")
        if isinstance(schema_version, int) and schema_version > RECIPE_SCHEMA_VERSION:
            message = (
                f"Saved by a newer PyRIT (format version {schema_version}; "
                f"this one reads up to {RECIPE_SCHEMA_VERSION})."
            )
            return self._failure(
                name=name, version=version, reason=RestoreFailureReason.NEWER_SCHEMA_VERSION, message=message
            )
        try:
            recipe = model.model_validate(payload)
            return StoredRecipe(ref=SavedEntryRef(kind=self._kind, name=name), recipe=recipe, version=version)
        except ValidationError as error:
            # pydantic stops at a fixed depth and reports it as a recursion error, though a JSON file can't hold a loop.
            if any(issue["type"] == "recursion_loop" for issue in error.errors()):
                return self._failure(name=name, version=version, reason=invalid, message=too_deep)
            return self._failure(
                name=name, version=version, reason=invalid, message=self._describe_validation_error(error)
            )

    def _failure(
        self, *, name: str, reason: RestoreFailureReason, message: str, version: str | None = None
    ) -> RestoreFailure:
        """
        Build a failure for one entry in this registry's folder.

        The message can repeat stored content, such as a key name or a version number, so it's escaped and shortened.

        Returns:
            RestoreFailure: The failure.
        """
        return RestoreFailure(
            kind=self._kind, name=name, reason=reason, message=safe_failure_text(message), version=version
        )

    @staticmethod
    def _describe_validation_error(error: ValidationError) -> str:
        """
        Summarize a validation error, naming each field and what's wrong with it, without the stored values.

        A field's location can repeat a stored key, such as an unknown one, so each part of a location
        is escaped and shortened. ``_failure`` shortens the whole summary.

        Returns:
            str: The summary.
        """
        problems = []
        for issue in error.errors():
            location = ".".join(
                shorten_text(escape_unprintable(str(part)), limit=_MAX_LOCATION_PART_LENGTH) for part in issue["loc"]
            )
            problems.append(f"{location or 'document'}: {issue['msg']}")
        return "Not a valid saved recipe: " + "; ".join(problems)
