# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage backends for scenario suite definitions."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pyrit.models.catalog.scenario_suite import ScenarioSuite, StoredScenarioSuite
from pyrit.registry.file_document_storage import DocumentConflictError, FileDocumentStorage

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class ScenarioSuiteConflictError(DocumentConflictError):
    """A stored scenario suite changed after the caller read it."""

    def __init__(self, *, name: str, expected_version: str | None, actual_version: str | None) -> None:
        """Initialize the error with the versions that failed to match."""
        super().__init__(
            name=name,
            expected_version=expected_version,
            actual_version=actual_version,
            label="Scenario suite",
        )


class ScenarioSuiteStorage(FileDocumentStorage):
    """Read and write scenario suites as versioned JSON documents."""

    def __init__(self, *, source: str | None = None) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Args:
            source (str | None): Local directory or Azure Blob source URI. Defaults to
                ``scenario_suites`` under the PyRIT configuration directory.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        super().__init__(
            source=source or str(self._get_default_storage_dir()),
            extension=".json",
            source_label="Scenario suite",
        )

    @staticmethod
    def _get_default_storage_dir() -> Path:
        """
        Get the default directory for storing scenario suites.

        Returns:
            Path: Path to ``~/.pyrit/scenario_suites/``, created if needed.
        """
        from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

        suites_dir = CONFIGURATION_DIRECTORY_PATH / "scenario_suites"
        suites_dir.mkdir(parents=True, exist_ok=True)
        return suites_dir

    def get_suite_source(self, name: str) -> str:
        """
        Get the credential-free location of one stored suite.

        Returns:
            str: Local file path or Azure Blob URI for the suite.
        """
        return self._get_document_source(name)

    def list_suites(self) -> dict[str, StoredScenarioSuite]:
        """
        Read every stored suite, skipping any that cannot be parsed.

        Returns:
            dict[str, StoredScenarioSuite]: Stored suites keyed by name.
        """
        suites: dict[str, StoredScenarioSuite] = {}
        for name, content in self._list_documents().items():
            suite = self._parse_suite(name=name, content=content)
            if suite is not None:
                suites[suite.name] = StoredScenarioSuite(suite=suite, version=self._compute_version(content))
        return suites

    def load_suite(self, name: str) -> StoredScenarioSuite | None:
        """
        Read one stored suite.

        Returns:
            StoredScenarioSuite | None: The suite and its version, or ``None`` if absent or malformed.
        """
        content = self._read_document_bytes(name)
        if content is None:
            return None
        suite = self._parse_suite(name=name, content=content)
        if suite is None:
            return None
        return StoredScenarioSuite(suite=suite, version=self._compute_version(content))

    def get_suite_version(self, name: str) -> str | None:
        """
        Read the version of one stored suite document without parsing it.

        Returns:
            str | None: The document version, or ``None`` if no document is stored.
        """
        content = self._read_document_bytes(name)
        return None if content is None else self._compute_version(content)

    def save_suite(self, *, suite: ScenarioSuite, expected_version: str | None) -> StoredScenarioSuite:
        """
        Persist one scenario suite behind an optimistic-concurrency check.

        Returns:
            StoredScenarioSuite: The persisted suite and its new version.

        Raises:
            ScenarioSuiteConflictError: If the stored version does not match ``expected_version``.
            ValueError: If the suite name is not a legal registry name.
            TimeoutError: If a local write could not acquire the document lock.
        """
        content = self._serialize_suite(suite)
        version = self._save_document_conditional(name=suite.name, content=content, expected_version=expected_version)
        return StoredScenarioSuite(suite=suite, version=version)

    def delete_suite(self, name: str) -> bool:
        """
        Delete one stored scenario suite.

        Returns:
            bool: True if a document was deleted, False if it was already absent.
        """
        return self._delete_document(name)

    def _conflict_error(
        self, *, name: str, expected_version: str | None, actual_version: str | None
    ) -> ScenarioSuiteConflictError:
        """
        Report a refused write as a scenario suite conflict.

        Returns:
            ScenarioSuiteConflictError: The error describing the version mismatch.
        """
        return ScenarioSuiteConflictError(name=name, expected_version=expected_version, actual_version=actual_version)

    @staticmethod
    def _serialize_suite(suite: ScenarioSuite) -> bytes:
        """
        Serialize a suite while preserving entry order exactly.

        Returns:
            bytes: Encoded JSON document content.
        """
        payload = suite.model_dump(mode="json", exclude_none=True, exclude={"name"})
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def _parse_suite(*, name: str, content: bytes) -> ScenarioSuite | None:
        """
        Parse one stored scenario suite document.

        Returns:
            ScenarioSuite | None: The parsed suite, or ``None`` if it is malformed.
        """
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(f"Skipping stored scenario suite '{name}': it is not valid UTF-8 text.")
            return None

        try:
            payload = json.loads(text)
        except ValueError:
            logger.exception(f"Skipping stored scenario suite '{name}': it is not valid JSON.")
            return None

        if not isinstance(payload, dict):
            logger.error(f"Skipping stored scenario suite '{name}': it is not a JSON object.")
            return None

        fields = {**payload, "name": name}
        try:
            return ScenarioSuite.model_validate(fields)
        except Exception:
            logger.exception(f"Skipping stored scenario suite '{name}': it is not a valid scenario suite.")
            return None
