# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage backends for scenario scan definitions."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pyrit.models.catalog.scenario_scan import ScenarioScan, StoredScenarioScan
from pyrit.registry.file_document_storage import DocumentConflictError, FileDocumentStorage

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class ScenarioScanConflictError(DocumentConflictError):
    """A stored scenario scan changed after the caller read it."""

    def __init__(self, *, name: str, expected_version: str | None, actual_version: str | None) -> None:
        """Initialize the error with the versions that failed to match."""
        super().__init__(
            name=name,
            expected_version=expected_version,
            actual_version=actual_version,
            label="Scenario scan",
        )


class ScenarioScanStorage(FileDocumentStorage):
    """Read and write scenario scans as versioned JSON documents."""

    def __init__(self, *, source: str | None = None) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Args:
            source (str | None): Local directory or Azure Blob source URI. Defaults to
                ``scenario_scans`` under the PyRIT configuration directory.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        super().__init__(
            source=source or str(self._get_default_storage_dir()),
            extension=".json",
            source_label="Scenario scan",
        )

    @staticmethod
    def _get_default_storage_dir() -> Path:
        """
        Get the default directory for storing scenario scans.

        Returns:
            Path: Path to ``~/.pyrit/scenario_scans/``, created if needed.
        """
        from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

        scans_dir = CONFIGURATION_DIRECTORY_PATH / "scenario_scans"
        scans_dir.mkdir(parents=True, exist_ok=True)
        return scans_dir

    def get_scan_source(self, name: str) -> str:
        """
        Get the credential-free location of one stored scan.

        Returns:
            str: Local file path or Azure Blob URI for the scan.
        """
        return self._get_document_source(name)

    def list_scans(self) -> dict[str, StoredScenarioScan]:
        """
        Read every stored scan, skipping any that cannot be parsed.

        Returns:
            dict[str, StoredScenarioScan]: Stored scans keyed by name.
        """
        scans: dict[str, StoredScenarioScan] = {}
        for name, content in self._list_documents().items():
            scan = self._parse_scan(name=name, content=content)
            if scan is not None:
                scans[scan.name] = StoredScenarioScan(scan=scan, version=self._compute_version(content))
        return scans

    def load_scan(self, name: str) -> StoredScenarioScan | None:
        """
        Read one stored scan.

        Returns:
            StoredScenarioScan | None: The scan and its version, or ``None`` if absent or malformed.
        """
        content = self._read_document_bytes(name)
        if content is None:
            return None
        scan = self._parse_scan(name=name, content=content)
        if scan is None:
            return None
        return StoredScenarioScan(scan=scan, version=self._compute_version(content))

    def get_scan_version(self, name: str) -> str | None:
        """
        Read the version of one stored scan document without parsing it.

        Returns:
            str | None: The document version, or ``None`` if no document is stored.
        """
        content = self._read_document_bytes(name)
        return None if content is None else self._compute_version(content)

    def save_scan(self, *, scan: ScenarioScan, expected_version: str | None) -> StoredScenarioScan:
        """
        Persist one scenario scan behind an optimistic-concurrency check.

        Returns:
            StoredScenarioScan: The persisted scan and its new version.

        Raises:
            ScenarioScanConflictError: If the stored version does not match ``expected_version``.
            ValueError: If the scan name is not a legal registry name.
            TimeoutError: If a local write could not acquire the document lock.
        """
        content = self._serialize_scan(scan)
        version = self._save_document_conditional(name=scan.name, content=content, expected_version=expected_version)
        return StoredScenarioScan(scan=scan, version=version)

    def delete_scan(self, name: str) -> bool:
        """
        Delete one stored scenario scan.

        Returns:
            bool: True if a document was deleted, False if it was already absent.
        """
        return self._delete_document(name)

    def _conflict_error(
        self, *, name: str, expected_version: str | None, actual_version: str | None
    ) -> ScenarioScanConflictError:
        """
        Report a refused write as a scenario scan conflict.

        Returns:
            ScenarioScanConflictError: The error describing the version mismatch.
        """
        return ScenarioScanConflictError(name=name, expected_version=expected_version, actual_version=actual_version)

    @staticmethod
    def _serialize_scan(scan: ScenarioScan) -> bytes:
        """
        Serialize a scan while preserving entry order exactly.

        Returns:
            bytes: Encoded JSON document content.
        """
        payload = scan.model_dump(mode="json", exclude_none=True, exclude={"name"})
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def _parse_scan(*, name: str, content: bytes) -> ScenarioScan | None:
        """
        Parse one stored scenario scan document.

        Returns:
            ScenarioScan | None: The parsed scan, or ``None`` if it is malformed.
        """
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(f"Skipping stored scenario scan '{name}': it is not valid UTF-8 text.")
            return None

        try:
            payload = json.loads(text)
        except ValueError:
            logger.exception(f"Skipping stored scenario scan '{name}': it is not valid JSON.")
            return None

        if not isinstance(payload, dict):
            logger.error(f"Skipping stored scenario scan '{name}': it is not a JSON object.")
            return None

        fields = {**payload, "name": name}
        try:
            return ScenarioScan.model_validate(fields)
        except Exception:
            logger.exception(f"Skipping stored scenario scan '{name}': it is not a valid scenario scan.")
            return None
