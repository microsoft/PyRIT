# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage for the saved recipes of named targets, converters, and scorers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, ClassVar

from pydantic import ValidationError

from pyrit.common.url_credentials import UrlCredentials
from pyrit.models.catalog.instance_recipe import (
    INSTANCE_NAME_PATTERN,
    InstanceRecipe,
    StoredInstanceRecipe,
    UnrestorableInstance,
)
from pyrit.registry.file_document_storage import DocumentConflictError, FileDocumentStorage

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.models.parameter import ComponentType

logger = logging.getLogger(__name__)


class InstanceRecipeStorage(FileDocumentStorage):
    """
    Read and write saved instance recipes as JSON documents in a directory or blob container.

    Instance names may use characters a document name cannot (upper case, ``.`` and ``-``),
    and names that differ only in case must not share a file on a case-insensitive file
    system. Each recipe is therefore stored under a derived document name: its kind, a
    readable slug, and a digest of the exact name. The kind and name inside the document
    are authoritative; a document whose contents do not map back to its own name is
    reported rather than trusted.

    A document that cannot be read is reported with the reason, and with its version when
    its content could be read, instead of being skipped, so a broken recipe is visible and
    can be deleted. A document that does not say which instance it belongs to is reported,
    and can be deleted, under its own document name.
    """

    _SLUG_MAX_LENGTH: ClassVar[int] = 40
    _DIGEST_LENGTH: ClassVar[int] = 12

    def __init__(self, *, source: str | None = None) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Args:
            source (str | None): Local directory or Azure Blob source URI. Defaults to
                ``instance_recipes`` under the PyRIT configuration directory.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        super().__init__(
            source=source or str(self._get_default_storage_dir()),
            extension=".json",
            source_label="Instance recipe",
        )

    @classmethod
    def get_document_name(cls, *, kind: ComponentType, name: str) -> str:
        """
        Derive the document name that stores the recipe of one instance.

        Args:
            kind (ComponentType): The registry the instance belongs to.
            name (str): The exact registry name of the instance.

        Returns:
            str: A legal document name, unique for the kind and exact name.
        """
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[: cls._SLUG_MAX_LENGTH].rstrip("_")
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[: cls._DIGEST_LENGTH]
        return "_".join(part for part in (kind.value, slug, digest) if part)

    @classmethod
    def is_document_name(cls, *, kind: ComponentType, name: str) -> bool:
        """
        Return whether a name has the shape of a document name of the kind.

        Such names are reserved for instances: a document that cannot be read and does not say
        which instance it belongs to is reported under its document name, which an instance of
        the same name would make ambiguous.

        Returns:
            bool: Whether the name looks like ``<kind>_<slug>_<digest>``.
        """
        pattern = rf"{kind.value}_(?:[a-z0-9_]+_)?[0-9a-f]{{{cls._DIGEST_LENGTH}}}"
        return re.fullmatch(pattern, name) is not None

    @classmethod
    def get_addressed_document_name(cls, *, kind: ComponentType, name: str) -> str:
        """
        Get the name of the document a name addresses.

        Returns:
            str: The name itself when it has the shape of a document name of the kind, which is
            reserved for documents, otherwise the name of the document that stores the recipe of
            the instance of that name.
        """
        return name if cls.is_document_name(kind=kind, name=name) else cls.get_document_name(kind=kind, name=name)

    def redact(self, text: str) -> str:
        """
        Remove the credentials the configured source URL carries, such as a SAS signature, from text.

        Returns:
            str: The text, such as a storage error message, with those credentials replaced by ``***``.
        """
        for secret in sorted(set(UrlCredentials.secrets(self._source)), key=len, reverse=True):
            text = text.replace(secret, UrlCredentials.MASK)
        return text

    def list_recipes(self) -> tuple[list[StoredInstanceRecipe], list[UnrestorableInstance]]:
        """
        Read every saved recipe.

        Returns:
            tuple[list[StoredInstanceRecipe], list[UnrestorableInstance]]: The readable
            recipes, and one entry for each saved document that could not be read.
        """
        recipes: list[StoredInstanceRecipe] = []
        unreadable: list[UnrestorableInstance] = []
        # Every replacement is atomic, so a listing reads each document whole without taking its lock.
        documents, read_errors = self._list_documents_and_read_errors()
        for document_name in sorted(documents.keys() | read_errors.keys()):
            kind = self._get_document_kind(document_name)
            if kind is None:
                logger.warning(
                    f"Ignoring '{document_name}.json' in {self.display_source}: it is not an instance recipe."
                )
                continue
            if document_name in read_errors:
                unreadable.append(
                    self._inaccessible(kind=kind, document_name=document_name, error=read_errors[document_name])
                )
                continue
            result = self._read_recipe(kind=kind, document_name=document_name, content=documents[document_name])
            if isinstance(result, StoredInstanceRecipe):
                recipes.append(result)
            else:
                unreadable.append(result)
        return recipes, unreadable

    def load_recipe(self, *, kind: ComponentType, name: str) -> StoredInstanceRecipe | UnrestorableInstance | None:
        """
        Read the saved recipe of one instance.

        Returns:
            StoredInstanceRecipe | UnrestorableInstance | None: The recipe, the reason its
            document cannot be read (with the version needed to delete it), or ``None`` if
            nothing is saved under the name.
        """
        with self._local_read_lock(kind=kind, name=name):
            found = self._find_recipe(kind=kind, name=name)
        return None if found is None else found[1]

    def save_recipe(self, *, recipe: InstanceRecipe, expected_version: str | None) -> StoredInstanceRecipe:
        """
        Save one recipe if storage still holds the expected version.

        Args:
            recipe (InstanceRecipe): The recipe to save.
            expected_version (str | None): ``None`` to create a recipe that must not exist
                yet, or the version returned when the saved recipe was read.

        Returns:
            StoredInstanceRecipe: The saved recipe and its new version.

        Raises:
            DocumentConflictError: If storage does not hold ``expected_version``.
        """
        content = self._serialize(recipe)
        version = self._save_document_conditional(
            name=self.get_document_name(kind=recipe.kind, name=recipe.name),
            content=content,
            expected_version=expected_version,
        )
        return StoredInstanceRecipe(recipe=recipe, version=version, content=content)

    def restore_recipe(self, *, previous: StoredInstanceRecipe, expected_version: str) -> None:
        """
        Put back a replaced recipe exactly as it was stored, if storage still holds its replacement.

        Args:
            previous (StoredInstanceRecipe): The recipe that was replaced, as it was read.
            expected_version (str): The version of the replacement.

        Raises:
            DocumentConflictError: If storage does not hold ``expected_version``.
        """
        self._save_document_conditional(
            name=self.get_document_name(kind=previous.recipe.kind, name=previous.recipe.name),
            content=previous.content if previous.content is not None else self._serialize(previous.recipe),
            expected_version=expected_version,
        )

    def delete_recipe(self, *, kind: ComponentType, name: str, expected_version: str) -> None:
        """
        Delete one saved recipe if storage still holds the expected version.

        Raises:
            DocumentConflictError: If the recipe is absent or holds another version.
        """
        with self._local_read_lock(kind=kind, name=name):
            found = self._find_recipe(kind=kind, name=name)
        if found is None:
            raise DocumentConflictError(
                name=self.get_addressed_document_name(kind=kind, name=name),
                expected_version=expected_version,
                actual_version=None,
            )
        self._delete_document_conditional(name=found[0], expected_version=expected_version)

    @staticmethod
    def _serialize(recipe: InstanceRecipe) -> bytes:
        """
        Encode a recipe as the JSON document that stores it.

        Returns:
            bytes: The document content.
        """
        return (json.dumps(recipe.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )

    def _find_recipe(
        self, *, kind: ComponentType, name: str
    ) -> tuple[str, StoredInstanceRecipe | UnrestorableInstance] | None:
        """
        Find and read the document that holds the recipe of one instance.

        A name with the shape of a document name addresses that document, never the document an
        instance of that name would be saved in: such names are reserved for documents. It finds
        the document only when it cannot be used as a recipe, which is how one that does not say
        which instance it belongs to is reported. A usable recipe is found only by its instance name.

        Returns:
            tuple[str, StoredInstanceRecipe | UnrestorableInstance] | None: The document name
            and what it holds, or ``None`` if nothing is saved under the name.
        """
        document_name = self.get_addressed_document_name(kind=kind, name=name)
        try:
            content = self._read_document_bytes(document_name)
        except ValueError:
            return None
        if content is None:
            return None
        result = self._read_recipe(kind=kind, document_name=document_name, content=content)
        if document_name == name and isinstance(result, StoredInstanceRecipe):
            return None
        return document_name, result

    def _local_read_lock(self, *, kind: ComponentType, name: str) -> AbstractContextManager[None]:
        """
        Return the lock that keeps a local read of the document a name addresses from overlapping its replacement.

        A document that does not exist has nothing to overlap, so reading it takes no lock and
        leaves no lock file behind.

        Returns:
            AbstractContextManager[None]: The lock of an existing local document, or a no-op.
        """
        if self._is_blob:
            return nullcontext()
        document_name = self.get_addressed_document_name(kind=kind, name=name)
        path = self._local_directory(create=True) / f"{document_name}{self._extension}"
        return self._local_document_lock(path) if path.exists() else nullcontext()

    def _read_recipe(
        self, *, kind: ComponentType, document_name: str, content: bytes
    ) -> StoredInstanceRecipe | UnrestorableInstance:
        """
        Parse one saved document, or explain why it cannot be used.

        Returns:
            StoredInstanceRecipe | UnrestorableInstance: The recipe, or the reason it cannot be read.
        """
        version = self._compute_version(content)
        try:
            payload = json.loads(content.decode("utf-8"))
        except ValueError:
            return self._unreadable(
                kind=kind, name=document_name, document_name=document_name, content=content, detail="not valid JSON"
            )
        if not isinstance(payload, dict):
            return self._unreadable(
                kind=kind, name=document_name, document_name=document_name, content=content, detail="not a JSON object"
            )

        schema_version = payload.get("schema_version")
        name = self._get_instance_name(kind=kind, document_name=document_name, payload=payload)
        if isinstance(schema_version, int) and schema_version > InstanceRecipe.CURRENT_SCHEMA_VERSION:
            return UnrestorableInstance(
                kind=kind,
                name=name,
                reason=f"It was saved by a newer version of PyRIT (recipe format {schema_version}).",
                version=version,
                content=content,
            )

        try:
            recipe = InstanceRecipe.model_validate(payload)
        except ValidationError as error:
            detail = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors())
            return self._unreadable(kind=kind, name=name, document_name=document_name, content=content, detail=detail)
        if recipe.kind is not kind or self.get_document_name(kind=kind, name=recipe.name) != document_name:
            return self._unreadable(
                kind=kind,
                name=name,
                document_name=document_name,
                content=content,
                detail="its kind or name was changed",
            )
        if self.is_document_name(kind=kind, name=recipe.name):
            return self._unreadable(
                kind=kind,
                name=name,
                document_name=document_name,
                content=content,
                detail="its name is reserved for saved document names",
            )
        return StoredInstanceRecipe(recipe=recipe, version=version, content=content)

    def _get_instance_name(self, *, kind: ComponentType, document_name: str, payload: dict[str, object]) -> str:
        """
        Get the instance name a saved document belongs to, even when the document is invalid.

        Returns:
            str: The name inside the document when it is a valid, unreserved instance name that maps
            back to this document, otherwise the document name, so the document can always be addressed
            to delete it and a document-shaped name always means a document.
        """
        name = payload.get("name")
        if (
            isinstance(name, str)
            and re.fullmatch(INSTANCE_NAME_PATTERN, name)
            and not self.is_document_name(kind=kind, name=name)
            and self.get_document_name(kind=kind, name=name) == document_name
        ):
            return name
        return document_name

    def _unreadable(
        self, *, kind: ComponentType, name: str, document_name: str, content: bytes, detail: str
    ) -> UnrestorableInstance:
        """
        Describe a saved document that cannot be parsed into a recipe.

        Returns:
            UnrestorableInstance: The report, naming the document so it can be fixed by hand, with its content.
        """
        source = self._get_document_source(document_name)
        logger.warning(f"Cannot read saved instance recipe {source}: {detail}")
        return UnrestorableInstance(
            kind=kind,
            name=name,
            reason=f"The saved document {source} cannot be read: {detail}. Repair or delete the document.",
            version=self._compute_version(content),
            content=content,
        )

    def _inaccessible(self, *, kind: ComponentType, document_name: str, error: Exception) -> UnrestorableInstance:
        """
        Describe a saved document whose content could not be read from storage.

        Returns:
            UnrestorableInstance: The report, without a version because no content was read.
        """
        source = self._get_document_source(document_name)
        detail = self.redact(str(error))
        logger.warning(f"Cannot read saved instance recipe {source}: {detail}")
        return UnrestorableInstance(
            kind=kind,
            name=document_name,
            reason=(
                f"The saved document {source} could not be read from storage: {detail}. "
                "Make it readable, then restart or reinitialize the backend."
            ),
        )

    @classmethod
    def _get_document_kind(cls, document_name: str) -> ComponentType | None:
        """
        Return the kind encoded in a document name.

        Returns:
            ComponentType | None: The kind, or ``None`` for a document this storage did not write.
        """
        return next(
            (kind for kind in InstanceRecipe.PERSISTED_KINDS if cls.is_document_name(kind=kind, name=document_name)),
            None,
        )

    @staticmethod
    def _get_default_storage_dir() -> Path:
        """
        Get the default directory for saved instance recipes.

        Returns:
            Path: Path to ``~/.pyrit/instance_recipes/``.
        """
        # Deferred: importing pyrit.common.path triggers pyrit __init__.py
        from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

        return CONFIGURATION_DIRECTORY_PATH / "instance_recipes"
