# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry owning scenario suite storage and version-checked resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pyrit.models.catalog.scenario_suite import (
    ScenarioSuite,
    ScenarioSuiteRegistryEntry,
    StoredScenarioSuite,
)
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata
from pyrit.registry.scenario_suite_storage import ScenarioSuiteConflictError, ScenarioSuiteStorage

logger = logging.getLogger(__name__)


class ScenarioSuiteNameRegisteredError(ValueError):
    """A suite name is owned by code in this deployment and cannot be stored."""

    def __init__(self, name: str) -> None:
        """
        Initialize the error with the name code already owns.

        Args:
            name (str): The suite name a document tried to claim.
        """
        self.name = name
        super().__init__(
            f"Scenario suite '{name}' is registered by code in this deployment and cannot be "
            f"saved as a document. Unlike a version conflict, re-reading will not clear this."
        )


@dataclass(frozen=True)
class ScenarioSuiteMetadata(RegistryMetadata):
    """Metadata for the intentionally empty buildable suite class catalog."""


class ScenarioSuiteRegistry(Registry["ScenarioSuite", ScenarioSuiteMetadata]):
    """
    Own registered and stored scenario suite resolution.

    Stored suites are operator-owned JSON documents that can be edited and deleted.
    Registered suites are contributed in process by code and are read-only. A stored
    document shadows a registered suite with the same name so operator data takes
    precedence, while new writes to a code-owned name are refused.

    Stored suites are read through on every call. Their versions describe the current
    document bytes, so caching them would weaken optimistic concurrency.
    """

    def __init__(self, *, storage: ScenarioSuiteStorage | None = None) -> None:
        """
        Initialize the registry without touching storage.

        Args:
            storage (ScenarioSuiteStorage | None): Storage to read and write suites
                through. Defaults to the standard directory, constructed on first use.
        """
        super().__init__()
        self._storage = storage
        self.instances: InstanceRegistry[ScenarioSuite] = DefaultInstanceRegistry(instance_type=ScenarioSuite)

    def _discover(self) -> None:
        """Register no classes because suites are definitions rather than buildable classes."""

    def _metadata_class(self) -> type[ScenarioSuiteMetadata]:
        """Return the metadata class for the empty buildable catalog."""
        return ScenarioSuiteMetadata

    @property
    def source(self) -> str:
        """Credential-free storage source suites are read from and written to."""
        return self._get_storage().display_source

    def configure_source(self, source: str | None) -> None:
        """
        Point the registry at a local directory or Azure Blob source.

        Args:
            source (str | None): Configured source, or None for the default directory.
        """
        self._storage = ScenarioSuiteStorage(source=source)

    def scan_stored_suites(self) -> int:
        """
        Read every stored suite once so unusable documents are reported at startup.

        Returns:
            int: The number of suites that parsed cleanly.
        """
        try:
            suites = self._get_storage().list_suites()
        except Exception:
            logger.exception("Could not read scenario suites at startup; the suite library may be unavailable.")
            return 0

        logger.info(f"Loaded {len(suites)} scenario suite(s) from {self.source}.")
        return len(suites)

    def register_suite(self, *, suite: ScenarioSuite, tags: dict[str, str] | list[str] | None = None) -> None:
        """
        Contribute one read-only suite from code for the lifetime of this process.

        Args:
            suite (ScenarioSuite): The suite, registered under its own name.
            tags (dict[str, str] | list[str] | None): Tags for discovery through ``instances``.

        Raises:
            ValueError: If a suite is already registered under this name.
        """
        try:
            self.instances.register(suite, name=suite.name, tags=tags)
        except ValueError as exc:
            raise ValueError(f"Scenario suite '{suite.name}' is already registered.") from exc

        if self._stored_version(name=suite.name) is not None:
            logger.warning(
                f"Scenario suite '{suite.name}' was registered but a stored document of the same "
                f"name already exists; the stored document takes precedence."
            )

    def list_suites(self) -> list[ScenarioSuiteRegistryEntry]:
        """
        Read every suite this deployment offers.

        Returns:
            list[ScenarioSuiteRegistryEntry]: Stored and registered suites sorted by
            name, with a stored document shadowing a registration of the same name.
        """
        entries = {
            name: ScenarioSuiteRegistryEntry(suite=stored.suite, origin="stored", version=stored.version)
            for name, stored in self._get_storage().list_suites().items()
        }
        for entry in self.instances.get_all_instances():
            entries.setdefault(
                entry.name,
                ScenarioSuiteRegistryEntry(suite=entry.instance, origin="registered"),
            )
        return [entries[name] for name in sorted(entries)]

    def get_suite(self, *, name: str) -> ScenarioSuiteRegistryEntry | None:
        """
        Read one suite together with its origin and optional document version.

        Args:
            name (str): The suite name.

        Returns:
            ScenarioSuiteRegistryEntry | None: The suite, or None if absent or malformed.
        """
        stored = self._get_storage().load_suite(name)
        if stored is not None:
            return ScenarioSuiteRegistryEntry(suite=stored.suite, origin="stored", version=stored.version)

        registered = self.instances.get(name)
        return ScenarioSuiteRegistryEntry(suite=registered, origin="registered") if registered is not None else None

    def get_suite_version(self, *, name: str) -> str | None:
        """
        Read the version of one stored document without parsing it.

        Args:
            name (str): The suite name.

        Returns:
            str | None: The stored document version, or None when no document exists.
        """
        return self._get_storage().get_suite_version(name)

    def save_suite(self, *, suite: ScenarioSuite, expected_version: str | None) -> StoredScenarioSuite:
        """
        Persist one suite behind an optimistic-concurrency check.

        Args:
            suite (ScenarioSuite): The suite to persist.
            expected_version (str | None): None for create, or the version being replaced.

        Returns:
            StoredScenarioSuite: The persisted suite and its new version.

        Raises:
            ScenarioSuiteConflictError: If the stored version does not match ``expected_version``.
            ScenarioSuiteNameRegisteredError: If code already registered this name.
            ValueError: If the suite name is not legal.
        """
        try:
            self.instances.validate_name_available(suite.name)
        except ValueError as exc:
            raise ScenarioSuiteNameRegisteredError(suite.name) from exc

        stored = self._get_storage().save_suite(suite=suite, expected_version=expected_version)
        logger.info(f"Saved scenario suite '{suite.name}'.")
        return stored

    def delete_suite(self, *, name: str) -> bool:
        """
        Delete one stored suite without affecting a registered suite.

        Args:
            name (str): The suite name.

        Returns:
            bool: True if a stored document was deleted, otherwise False.
        """
        deleted = self._get_storage().delete_suite(name)
        if deleted:
            logger.info(f"Deleted scenario suite '{name}'.")
        return deleted

    def resolve_suite(self, *, name: str, expected_version: str | None) -> ScenarioSuiteRegistryEntry | None:
        """
        Read one suite for execution, confirming the version the caller reviewed.

        Args:
            name (str): The suite name.
            expected_version (str | None): Reviewed stored version, or None to accept
                whichever suite is currently available.

        Returns:
            ScenarioSuiteRegistryEntry | None: The resolved suite, or None if absent.

        Raises:
            ScenarioSuiteConflictError: If the available version differs from the
                caller's expected version.
        """
        stored = self._get_storage().load_suite(name)
        if stored is not None:
            if expected_version is not None and expected_version != stored.version:
                raise ScenarioSuiteConflictError(
                    name=name,
                    expected_version=expected_version,
                    actual_version=stored.version,
                )
            return ScenarioSuiteRegistryEntry(suite=stored.suite, origin="stored", version=stored.version)

        registered = self.instances.get(name)
        if registered is None:
            return None
        if expected_version is not None:
            raise ScenarioSuiteConflictError(name=name, expected_version=expected_version, actual_version=None)
        return ScenarioSuiteRegistryEntry(suite=registered, origin="registered")

    def _get_storage(self) -> ScenarioSuiteStorage:
        """
        Return storage, constructing the default backend on first use.

        Returns:
            ScenarioSuiteStorage: The configured storage backend.
        """
        if self._storage is None:
            self._storage = ScenarioSuiteStorage()
        return self._storage

    def _stored_version(self, *, name: str) -> str | None:
        """
        Read a stored document version for collision logging.

        Args:
            name (str): The suite name.

        Returns:
            str | None: The version, or None when absent or unavailable.
        """
        try:
            return self._get_storage().get_suite_version(name)
        except Exception:
            logger.debug(f"Could not check storage for an existing scenario suite '{name}'.", exc_info=True)
            return None
