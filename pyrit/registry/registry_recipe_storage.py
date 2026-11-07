# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage for saved registry recipes, one folder per registry."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from pyrit.common.azure_storage import is_azure_blob_uri
from pyrit.models.catalog.registry_recipe import (
    ComponentRecipe,
    IgnoredRecipeFile,
    RegistryRecipeKind,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
from pyrit.registry.file_document_storage import FileDocumentStorage


@dataclass(frozen=True)
class RecipeListing:
    """Every saved recipe of one kind, with the ones that couldn't be read or parsed."""

    recipes: dict[str, StoredRecipe] = field(default_factory=dict)
    failures: tuple[RestoreFailure, ...] = ()
    ignored: tuple[IgnoredRecipeFile, ...] = ()


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

        Returns:
            RecipeListing: Parsed recipes keyed by name, plus the failures and ignored files.

        Raises:
            OSError: If the local folder can't be listed.
            AzureError: If the Blob container can't be listed.
        """
        listing = self._list_documents_with_issues()
        recipes: dict[str, StoredRecipe] = {}
        failures = [
            self._failure(name=issue.name, reason=RestoreFailureReason.UNREADABLE_RECIPE, message=issue.message)
            for issue in listing.unreadable
        ]
        for name, content in listing.documents.items():
            parsed = self._parse_recipe(name=name, content=content)
            if isinstance(parsed, RestoreFailure):
                failures.append(parsed)
            else:
                recipes[name] = parsed
        ignored = tuple(
            IgnoredRecipeFile(kind=self._kind, file_name=issue.file_name, message=issue.message)
            for issue in listing.ignored
        )
        return RecipeListing(recipes=recipes, failures=tuple(failures), ignored=ignored)

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
            TimeoutError: If a local write could not acquire the document lock.
            OSError: If the local folder can't be written.
            AzureError: If the Blob container can't be written.
        """
        content = recipe.model_dump_json(indent=2, exclude_none=True).encode("utf-8")
        return self._save_document_conditional(name=name, content=content, expected_version=None)

    def delete_recipe(self, name: str) -> bool:
        """
        Delete a saved recipe, such as one whose entry couldn't be registered after it was saved.

        Args:
            name (str): The entry's name.

        Returns:
            bool: True if the recipe existed and was deleted, False if it was already gone.

        Raises:
            ValueError: If the name isn't a legal registry name.
            TimeoutError: If a local delete could not acquire the document lock.
            OSError: If the local folder can't be written.
            AzureError: If the Blob container can't be written.
        """
        return self._delete_document(name)

    def _parse_recipe(self, *, name: str, content: bytes) -> StoredRecipe | RestoreFailure:
        """
        Parse one stored document, checking its schema version before anything else.

        Returns:
            StoredRecipe | RestoreFailure: The recipe, or why it can't be used.
        """
        version = self._compute_version(content)
        invalid = RestoreFailureReason.INVALID_RECIPE
        try:
            payload = json.loads(content.decode("utf-8"))
        except ValueError:
            return self._failure(name=name, version=version, reason=invalid, message="Not valid UTF-8 JSON.")
        except RecursionError:
            return self._failure(name=name, version=version, reason=invalid, message="Nested too deeply to read.")
        if not isinstance(payload, dict):
            return self._failure(name=name, version=version, reason=invalid, message="Not a JSON object.")
        schema_version = payload.get("schema_version")
        current = ComponentRecipe.CURRENT_SCHEMA_VERSION
        if isinstance(schema_version, int) and schema_version > current:
            message = f"Saved by a newer PyRIT (format version {schema_version}; this one reads up to {current})."
            return self._failure(
                name=name, version=version, reason=RestoreFailureReason.NEWER_SCHEMA_VERSION, message=message
            )
        model = TechniqueRecipe if self._kind is RegistryRecipeKind.TECHNIQUES else ComponentRecipe
        try:
            recipe = model.model_validate(payload)
        except ValidationError as error:
            return self._failure(
                name=name, version=version, reason=invalid, message=self._describe_validation_error(error)
            )
        except RecursionError:
            return self._failure(name=name, version=version, reason=invalid, message="Nested too deeply to read.")
        return StoredRecipe(ref=SavedEntryRef(kind=self._kind, name=name), recipe=recipe, version=version)

    def _failure(
        self, *, name: str, reason: RestoreFailureReason, message: str, version: str | None = None
    ) -> RestoreFailure:
        """
        Build a failure for one entry in this registry's folder.

        Returns:
            RestoreFailure: The failure.
        """
        return RestoreFailure(kind=self._kind, name=name, reason=reason, message=message, version=version)

    @staticmethod
    def _describe_validation_error(error: ValidationError) -> str:
        """
        Summarize a validation error without echoing the stored values.

        Returns:
            str: One line per problem, naming the field and what's wrong with it.
        """
        problems = [
            f"{'.'.join(str(part) for part in issue['loc']) or 'document'}: {issue['msg']}" for issue in error.errors()
        ]
        return "Not a valid saved recipe: " + "; ".join(problems)
