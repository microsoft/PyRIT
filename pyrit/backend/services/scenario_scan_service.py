# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario scan definition service."""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pyrit.backend.models.scenario_scans import (
    ScenarioScanEntryStatus,
    ScenarioScanListResponse,
    ScenarioScanResponse,
)
from pyrit.models.catalog import PresetIssue, ScenarioScan, ScenarioScanEntry, StoredScenarioScan
from pyrit.registry import ScenarioPresetRegistry, ScenarioScanStorage


@dataclass(frozen=True)
class _PresetProjection:
    """Current preset metadata reused by duplicate scan references."""

    origin: Literal["stored", "registered"] | None
    version: str | None
    issues: tuple[PresetIssue, ...]


class ScenarioScanService:
    """Wire adapter over ``ScenarioScanStorage`` with live preset-status projection."""

    def __init__(self, *, storage: ScenarioScanStorage | None = None) -> None:
        """
        Initialize the service.

        Args:
            storage (ScenarioScanStorage | None): Storage to use, constructed lazily by default.
        """
        self._storage = storage

    @property
    def source(self) -> str:
        """Credential-free storage source scans are read from and written to."""
        return self._get_storage().display_source

    def configure_source(self, source: str | None) -> None:
        """
        Point the service at a local directory or Azure Blob source.

        Args:
            source (str | None): Configured source, or None for the default directory.
        """
        self._storage = ScenarioScanStorage(source=source)

    async def list_scans_async(self) -> ScenarioScanListResponse:
        """
        Read every scenario scan and project current preset metadata.

        Returns:
            ScenarioScanListResponse: The configured source and every readable scan.
        """
        items = await asyncio.to_thread(self._read_all_responses)
        return ScenarioScanListResponse(source=self.source, items=items)

    async def get_scan_async(self, *, name: str) -> ScenarioScanResponse | None:
        """
        Read one scenario scan.

        Returns:
            ScenarioScanResponse | None: The scan and current entry statuses, or None if absent.
        """
        stored = await asyncio.to_thread(self._get_storage().load_scan, name)
        return None if stored is None else await asyncio.to_thread(self._to_response, stored)

    async def save_scan_async(
        self,
        *,
        scan: ScenarioScan,
        expected_version: str | None,
    ) -> ScenarioScanResponse:
        """
        Persist one scenario scan without enforcing advisory preset issues.

        Returns:
            ScenarioScanResponse: The persisted scan, its new version, and current entry statuses.
        """
        stored = await asyncio.to_thread(
            self._get_storage().save_scan,
            scan=scan,
            expected_version=expected_version,
        )
        return await asyncio.to_thread(self._to_response, stored)

    async def delete_scan_async(self, *, name: str) -> bool:
        """
        Delete one stored scenario scan.

        Returns:
            bool: True if a scan was deleted, False if it was already absent.
        """
        return await asyncio.to_thread(self._get_storage().delete_scan, name)

    def _get_storage(self) -> ScenarioScanStorage:
        """
        Return storage, constructing the default backend on first use.

        Returns:
            ScenarioScanStorage: The configured storage backend.
        """
        if self._storage is None:
            self._storage = ScenarioScanStorage()
        return self._storage

    def _read_all_responses(self) -> list[ScenarioScanResponse]:
        """
        Read and project every scan in one worker-thread hop.

        Returns:
            list[ScenarioScanResponse]: Every readable scan, sorted by name.
        """
        stored = self._get_storage().list_scans()
        return [self._to_response(stored[name]) for name in sorted(stored)]

    @staticmethod
    def _to_response(stored: StoredScenarioScan) -> ScenarioScanResponse:
        """
        Attach current preset metadata to one stored scan.

        Returns:
            ScenarioScanResponse: The scan response.
        """
        return ScenarioScanResponse(
            scan=stored.scan,
            version=stored.version,
            entry_statuses=ScenarioScanService._resolve_entry_statuses(entries=stored.scan.entries),
        )

    @staticmethod
    def _resolve_entry_statuses(*, entries: list[ScenarioScanEntry]) -> list[ScenarioScanEntryStatus]:
        """
        Resolve every distinct preset once while preserving entry order and identity.

        Returns:
            list[ScenarioScanEntryStatus]: Statuses in the same order as ``entries``.
        """
        registry = ScenarioPresetRegistry.get_registry_singleton()
        projections = {
            preset_name: ScenarioScanService._resolve_preset_projection(
                preset_name=preset_name,
                registry=registry,
            )
            for preset_name in dict.fromkeys(entry.preset_name for entry in entries)
        }
        return [
            ScenarioScanEntryStatus(
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
        Resolve one referenced preset into advisory scan-entry metadata.

        Returns:
            _PresetProjection: Current preset origin, version, and advisory issues.
        """
        entry = registry.get_preset(name=preset_name)
        if entry is not None:
            return _PresetProjection(
                origin=entry.origin,
                version=entry.version,
                issues=tuple(registry.check_preset(preset=entry.preset)),
            )

        stored_version = registry.get_preset_version(name=preset_name)
        if stored_version is None:
            message = f"Scenario preset '{preset_name}' is not available in this deployment."
        else:
            message = (
                f"Scenario preset '{preset_name}' exists but is invalid or unreadable; "
                "repair or replace the preset before running this scan."
            )
        return _PresetProjection(
            origin=None,
            version=stored_version,
            issues=(PresetIssue(field="preset_name", message=message),),
        )


@lru_cache(maxsize=1)
def get_scenario_scan_service() -> ScenarioScanService:
    """
    Get the global scenario scan service instance.

    Returns:
        ScenarioScanService: The singleton scenario scan service instance.
    """
    return ScenarioScanService()
