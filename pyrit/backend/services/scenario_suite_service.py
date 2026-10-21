# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario suite definition service."""

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from azure.core.exceptions import AzureError

from pyrit.backend.models.scenario_suites import (
    ScenarioSuiteEntryStatus,
    ScenarioSuiteListResponse,
    ScenarioSuiteResponse,
)
from pyrit.models.catalog import PresetIssue, ScenarioSuite, ScenarioSuiteEntry, StoredScenarioSuite
from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PresetProjection:
    """Current preset metadata reused by duplicate suite references."""

    origin: Literal["stored", "registered"] | None
    version: str | None
    issues: tuple[PresetIssue, ...]


class ScenarioSuiteService:
    """Wire adapter over ``ScenarioSuiteStorage`` with live preset-status projection."""

    def __init__(self, *, storage: ScenarioSuiteStorage | None = None) -> None:
        """
        Initialize the service.

        Args:
            storage (ScenarioSuiteStorage | None): Storage to use, constructed lazily by default.
        """
        self._storage = storage

    @property
    def source(self) -> str:
        """Credential-free storage source suites are read from and written to."""
        return self._get_storage().display_source

    def configure_source(self, source: str | None) -> None:
        """
        Point the service at a local directory or Azure Blob source.

        Args:
            source (str | None): Configured source, or None for the default directory.
        """
        self._storage = ScenarioSuiteStorage(source=source)

    async def list_suites_async(self) -> ScenarioSuiteListResponse:
        """
        Read every scenario suite and project current preset metadata.

        Returns:
            ScenarioSuiteListResponse: The configured source and every readable suite.
        """
        items = await asyncio.to_thread(self._read_all_responses)
        return ScenarioSuiteListResponse(source=self.source, items=items)

    async def get_suite_async(self, *, name: str) -> ScenarioSuiteResponse | None:
        """
        Read one scenario suite.

        Returns:
            ScenarioSuiteResponse | None: The suite and current entry statuses, or None if absent.
        """
        stored = await self.load_suite_async(name=name)
        return None if stored is None else await asyncio.to_thread(self._to_response, stored=stored)

    async def load_suite_async(self, *, name: str) -> StoredScenarioSuite | None:
        """
        Read one stored suite without projecting referenced presets.

        Returns:
            StoredScenarioSuite | None: The stored suite, or None if absent.
        """
        return await asyncio.to_thread(self._get_storage().load_suite, name)

    async def save_suite_async(
        self,
        *,
        suite: ScenarioSuite,
        expected_version: str | None,
    ) -> ScenarioSuiteResponse:
        """
        Persist one scenario suite without enforcing advisory preset issues.

        Returns:
            ScenarioSuiteResponse: The persisted suite, its new version, and current entry statuses.
        """
        stored = await asyncio.to_thread(
            self._get_storage().save_suite,
            suite=suite,
            expected_version=expected_version,
        )
        return await asyncio.to_thread(self._to_response, stored=stored)

    async def delete_suite_async(self, *, name: str) -> bool:
        """
        Delete one stored scenario suite.

        Returns:
            bool: True if a suite was deleted, False if it was already absent.
        """
        return await asyncio.to_thread(self._get_storage().delete_suite, name)

    def _get_storage(self) -> ScenarioSuiteStorage:
        """
        Return storage, constructing the default backend on first use.

        Returns:
            ScenarioSuiteStorage: The configured storage backend.
        """
        if self._storage is None:
            self._storage = ScenarioSuiteStorage()
        return self._storage

    def _read_all_responses(self) -> list[ScenarioSuiteResponse]:
        """
        Read and project every suite in one worker-thread hop.

        Returns:
            list[ScenarioSuiteResponse]: Every readable suite, sorted by name.
        """
        stored = self._get_storage().list_suites()
        projections: dict[str, _PresetProjection] = {}
        return [self._to_response(stored=stored[name], projections=projections) for name in sorted(stored)]

    @staticmethod
    def _to_response(
        *,
        stored: StoredScenarioSuite,
        projections: dict[str, _PresetProjection] | None = None,
    ) -> ScenarioSuiteResponse:
        """
        Attach current preset metadata to one stored suite.

        Returns:
            ScenarioSuiteResponse: The suite response.
        """
        return ScenarioSuiteResponse(
            suite=stored.suite,
            version=stored.version,
            entry_statuses=ScenarioSuiteService._resolve_entry_statuses(
                entries=stored.suite.entries,
                projections=projections,
            ),
        )

    @staticmethod
    def _resolve_entry_statuses(
        *,
        entries: list[ScenarioSuiteEntry],
        projections: dict[str, _PresetProjection] | None = None,
    ) -> list[ScenarioSuiteEntryStatus]:
        """
        Resolve every distinct preset once while preserving entry order and identity.

        Returns:
            list[ScenarioSuiteEntryStatus]: Statuses in the same order as ``entries``.
        """
        registry = ScenarioPresetRegistry.get_registry_singleton()
        if projections is None:
            projections = {}
        for preset_name in dict.fromkeys(entry.preset_name for entry in entries):
            if preset_name not in projections:
                projections[preset_name] = ScenarioSuiteService._resolve_preset_projection(
                    preset_name=preset_name,
                    registry=registry,
                )
        return [
            ScenarioSuiteEntryStatus(
                entry_id=entry.entry_id,
                preset_name=entry.preset_name,
                origin=projections[entry.preset_name].origin,
                preset_version=projections[entry.preset_name].version,
                issues=list(projections[entry.preset_name].issues),
            )
            for entry in entries
        ]

    @staticmethod
    def _resolve_preset_projection(
        *,
        preset_name: str,
        registry: ScenarioPresetRegistry,
    ) -> _PresetProjection:
        """
        Resolve one referenced preset into advisory suite-entry metadata.

        Returns:
            _PresetProjection: Current preset origin, version, and advisory issues.
        """
        try:
            entry = registry.get_preset(name=preset_name)
            if entry is not None:
                return _PresetProjection(
                    origin=entry.origin,
                    version=entry.version,
                    issues=tuple(registry.check_preset(preset=entry.preset)),
                )

            stored_version = registry.get_preset_version(name=preset_name)
        except (AzureError, OSError):
            logger.warning(
                "Could not project scenario preset '%s' because preset storage is unavailable.",
                preset_name,
                exc_info=True,
            )
            return _PresetProjection(
                origin=None,
                version=None,
                issues=(
                    PresetIssue(
                        field="preset_name",
                        message=(
                            f"Scenario preset '{preset_name}' could not be checked because preset storage is "
                            "temporarily unavailable; retry when storage is available."
                        ),
                    ),
                ),
            )

        if stored_version is None:
            message = f"Scenario preset '{preset_name}' is not available in this deployment."
        else:
            message = (
                f"Scenario preset '{preset_name}' exists but is invalid or unreadable; "
                "repair or replace the preset before running this suite."
            )
        return _PresetProjection(
            origin=None,
            version=stored_version,
            issues=(PresetIssue(field="preset_name", message=message),),
        )


@lru_cache(maxsize=1)
def get_scenario_suite_service() -> ScenarioSuiteService:
    """
    Get the global scenario suite service instance.

    Returns:
        ScenarioSuiteService: The singleton scenario suite service instance.
    """
    return ScenarioSuiteService()
