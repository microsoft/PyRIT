# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage backends for scenario presets."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING

from pyrit.models.catalog.scenario_preset import ScenarioPreset, StoredPreset
from pyrit.registry.file_document_storage import FileDocumentStorage

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class ScenarioPresetConflictError(ValueError):
    """A stored preset changed after the caller read it."""

    def __init__(self, *, name: str, expected_version: str | None, actual_version: str | None) -> None:
        """Initialize the error with the versions that failed to match."""
        self.name = name
        self.expected_version = expected_version
        self.actual_version = actual_version
        if actual_version is None:
            detail = "it no longer exists"
        elif expected_version is None:
            detail = "it already exists"
        else:
            detail = "it was changed by someone else"
        super().__init__(f"Scenario preset '{name}' could not be saved because {detail}. Reload it and reapply.")


def _document_version(content: bytes) -> str:
    """
    Create an opaque version token from stored document content.

    Returns:
        str: The document-state version token.
    """
    return hashlib.sha256(content).hexdigest()


class ScenarioPresetStorage(FileDocumentStorage):
    """
    Read and write scenario presets as JSON documents.

    Storage is read through on every call rather than cached, because the same directory
    or blob container is routinely shared between a notebook, the API, and a second
    process; a cache would serve edits those callers can no longer see.

    Writes are guarded by an optimistic-concurrency check. A caller supplies the version
    it read, and the save is refused unless storage still holds that version. The token
    is a hash of the stored bytes, so it also catches a file edited by hand or by another
    process rather than only writes made through this class.

    The check is read-then-write rather than a true compare-and-swap, so two saves racing
    within the same instant can both observe the same version and the later write wins.
    It is aimed at the realistic case - a person editing a copy that went stale minutes
    ago - not at concurrent writers. Closing that gap needs backend-specific conditional
    writes (blob ETags have no local-filesystem equivalent) and is deliberately deferred.
    """

    def __init__(self, *, source: str | None = None) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Args:
            source (str | None): Local directory or Azure Blob source URI. Defaults to
                ``scenario_presets`` under the PyRIT configuration directory.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        super().__init__(
            source=source or str(self._get_default_storage_dir()),
            extension=".json",
            source_label="Scenario preset",
        )

    @staticmethod
    def _get_default_storage_dir() -> Path:
        """
        Get the default directory for storing presets.

        Returns:
            Path: Path to ``~/.pyrit/scenario_presets/``, created if needed.
        """
        # Deferred: importing pyrit.common.path triggers pyrit __init__.py
        from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

        presets_dir = CONFIGURATION_DIRECTORY_PATH / "scenario_presets"
        presets_dir.mkdir(parents=True, exist_ok=True)
        return presets_dir

    def get_preset_source(self, name: str) -> str:
        """
        Get the credential-free location of one stored preset.

        Returns:
            str: Local file path or Azure Blob URI for the preset.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        return self._get_document_source(name)

    def list_presets(self) -> dict[str, StoredPreset]:
        """
        Read every stored preset, skipping any that cannot be parsed.

        A malformed, unreadable, or hand-edited file must not prevent the rest of the
        library from loading, so failures are logged and that preset is omitted.

        Returns:
            dict[str, StoredPreset]: Stored presets keyed by name.
        """
        presets: dict[str, StoredPreset] = {}
        for name, content in self._list_documents().items():
            preset = self._parse_preset(name=name, content=content)
            if preset is not None:
                presets[preset.name] = StoredPreset(preset=preset, version=_document_version(content))
        return presets

    def load_preset(self, name: str) -> StoredPreset | None:
        """
        Read one stored preset.

        Returns:
            StoredPreset | None: The preset and its version, or ``None`` if it is absent
            or malformed.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        content = self._read_document_bytes(name)
        if content is None:
            return None
        preset = self._parse_preset(name=name, content=content)
        if preset is None:
            return None
        return StoredPreset(preset=preset, version=_document_version(content))

    def get_preset_version(self, name: str) -> str | None:
        """
        Read the version of one stored document without parsing it.

        A document that cannot be parsed is otherwise unreachable: ``list_presets`` skips
        it, ``load_preset`` returns ``None``, and a create is refused because the document
        exists. Exposing its version lets a caller offer to overwrite the broken file
        instead of leaving the name permanently unusable. The document is never decoded
        here, so a file that is not even valid text can still be replaced.

        Returns:
            str | None: The document version, or ``None`` if no document is stored.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        content = self._read_document_bytes(name)
        return None if content is None else _document_version(content)

    def save_preset(self, *, preset: ScenarioPreset, expected_version: str | None) -> StoredPreset:
        """
        Persist one preset.

        The caller states its intent through *expected_version* rather than through
        anything on *preset*, so creating and updating are never ambiguous and a
        client-supplied version can never be trusted into storage.

        A document that exists but cannot be parsed still has a version, so creating
        over a malformed file conflicts rather than silently discarding it.

        Args:
            preset (ScenarioPreset): The preset to persist.
            expected_version (str | None): ``None`` to create a preset that must not already
                exist, or the version returned when the edited preset was read.

        Returns:
            StoredPreset: The persisted preset and its new version.

        Raises:
            ScenarioPresetConflictError: If the stored version does not match *expected_version*.
            ValueError: If the preset name is not a legal preset name.
        """
        existing_content = self._read_document_bytes(preset.name)
        actual_version = None if existing_content is None else _document_version(existing_content)
        if actual_version != expected_version:
            raise ScenarioPresetConflictError(
                name=preset.name, expected_version=expected_version, actual_version=actual_version
            )

        content = self._serialize_preset(preset)
        self._save_document(name=preset.name, content=content)
        return StoredPreset(preset=preset, version=_document_version(content))

    def delete_preset(self, name: str) -> None:
        """
        Delete one stored preset if it exists.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        self._delete_document(name)

    @staticmethod
    def _serialize_preset(preset: ScenarioPreset) -> bytes:
        """
        Serialize a preset to stored JSON.

        Unset fields are omitted rather than written as ``null`` so a stored preset reads
        as the set of decisions its author actually made. The name is omitted too: it is
        the document key, and writing it would invite a hand-editor to change it and
        expect a rename that cannot happen.

        Returns:
            bytes: Encoded JSON document content.
        """
        payload = preset.model_dump(mode="json", exclude_none=True, exclude={"name"})
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def _parse_preset(*, name: str, content: bytes) -> ScenarioPreset | None:
        """
        Parse one stored preset document.

        The document name is authoritative, overriding any ``name`` inside the payload.
        It is the storage key, so letting the payload disagree would make a load return a
        preset whose subsequent save wrote to a different file.

        Returns:
            ScenarioPreset | None: The parsed preset, or ``None`` if it is malformed.
        """
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(f"Skipping stored scenario preset '{name}': it is not valid UTF-8 text.")
            return None

        try:
            payload = json.loads(text)
        except ValueError:
            logger.exception(f"Skipping stored scenario preset '{name}': it is not valid JSON.")
            return None

        if not isinstance(payload, dict):
            logger.error(f"Skipping stored scenario preset '{name}': it is not a JSON object.")
            return None

        fields = {**payload, "name": name}
        try:
            return ScenarioPreset.model_validate(fields)
        except Exception:
            logger.exception(f"Skipping stored scenario preset '{name}': it is not a valid preset.")
            return None
