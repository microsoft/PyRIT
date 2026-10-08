# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire what a local directory holds, as condition-independent surface evidence."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Observation,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceObservationPayload,
)

if TYPE_CHECKING:
    from pyrit.models import SurfaceScorable

logger = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 1 << 16


class LocalFileSurfaceSource:
    """
    Read the files a ``SurfaceScorable`` names under one caller-owned root directory.

    The root is the directory the system under test writes into, such as a mounted sandbox
    workspace, and a scorable's ``uri`` is read relative to it: ``/data/out.txt`` is
    ``<root>/data/out.txt``. Nothing outside the root is read, including through symbolic
    links the system under test may have created.

    Only the scope's ``window`` can be checked here, against each file's modification time.
    A file a run wrote with its original timestamp preserved, or one another process touched
    during the window, is attributed wrongly; correlating an external write to a run is best
    effort. ``attempt_id`` and ``labels`` need a source that controls how writes are emitted,
    so this source records in the observation's metadata that it did not apply them.
    """

    def __init__(self, *, root: str | Path, max_files: int = 1000, max_content_bytes: int = 1_000_000) -> None:
        """
        Initialize bounded acquisition under one root directory.

        Args:
            root (str | Path): The directory scorable locations are read relative to.
            max_files (int): The most locations one glob scorable may cover.
            max_content_bytes (int): The most bytes of each file retained as text evidence.

        Raises:
            ValueError: If a limit is not positive.
        """
        if max_files < 1 or max_content_bytes < 1:
            raise ValueError("max_files and max_content_bytes must be positive.")
        self._root = Path(root)
        self._max_files = max_files
        self._max_content_bytes = max_content_bytes

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the reader version, root and limits.

        Returns:
            ComponentIdentifier: The nonsecret source configuration.
        """
        return ComponentIdentifier.of(
            self,
            params={
                "acquisition_version": 1,
                "root": str(self._root),
                "max_files": self._max_files,
                "max_content_bytes": self._max_content_bytes,
            },
        )

    async def acquire_async(self, *, scorable: SurfaceScorable) -> Observation:
        """
        Acquire one bounded snapshot of the named locations.

        Args:
            scorable (SurfaceScorable): The locations to read and the scope they must fall in.

        Returns:
            Observation: The snapshot, including acquisition and coverage state.

        Raises:
            ValueError: If the scorable names another surface or leaves the root.
        """
        if scorable.surface != "file":
            raise ValueError(f"LocalFileSurfaceSource reads the 'file' surface, not {scorable.surface!r}.")
        relative = PurePosixPath(scorable.uri.lstrip("/"))
        if ".." in relative.parts or not relative.parts:
            raise ValueError("A file surface locator must name a location inside the root.")
        return await asyncio.to_thread(self._acquire, scorable, relative)

    def _acquire(self, scorable: SurfaceScorable, relative: PurePosixPath) -> Observation:
        try:
            root = self._root.resolve(strict=True)
        except OSError:
            root = None
        if root is None or not root.is_dir():
            return self._observation(
                scorable=scorable, acquisition=Acquisition.UNAVAILABLE, reasons=("surface_root_unavailable",)
            )

        reasons: list[str] = []
        if scorable.match == "exact":
            candidates = [root / relative] if (root / relative).is_symlink() or (root / relative).exists() else []
        else:
            candidates = sorted(
                path for path in root.glob(str(relative)) if path.is_file() or (path.is_symlink() and not path.is_dir())
            )
            if len(candidates) > self._max_files:
                reasons.append("file_limit_exceeded")
                candidates = candidates[: self._max_files]

        entries: list[SurfaceEntry] = []
        excluded = 0
        window = scorable.scope.window if scorable.scope is not None else None
        for path in candidates:
            location = "/" + path.relative_to(root).as_posix()
            if scorable.match == "exact":
                location = scorable.uri
            try:
                target = path.resolve(strict=True)
            except OSError:
                reasons.append("dangling_link")
                continue
            if not target.is_relative_to(root):
                reasons.append("link_outside_root")
                continue
            if not target.is_file():
                reasons.append("not_a_file")
                continue
            try:
                modified_at = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC)
                if window is not None and not window[0] <= modified_at <= window[1]:
                    excluded += 1
                    continue
                entries.append(self._read(target=target, location=location, modified_at=modified_at))
            except OSError as error:
                logger.warning("Reading a surface location failed (%s).", type(error).__name__)
                reasons.append("read_failed")

        return self._observation(
            scorable=scorable,
            acquisition=Acquisition.PARTIAL if reasons else Acquisition.COMPLETE,
            reasons=tuple(dict.fromkeys(reasons)),
            entries=tuple(entries),
            excluded=excluded,
        )

    def _read(self, *, target: Path, location: str, modified_at: datetime) -> SurfaceEntry:
        digest = hashlib.sha256()
        retained = bytearray()
        size = 0
        with target.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
                if len(retained) < self._max_content_bytes:
                    retained.extend(chunk[: self._max_content_bytes - len(retained)])
        truncated = size > len(retained)
        content = _decode_text(bytes(retained), truncated=truncated)
        return SurfaceEntry(
            uri=location,
            size_bytes=size,
            sha256=digest.hexdigest(),
            modified_at=modified_at,
            content=content,
            content_truncated=truncated and content is not None,
        )

    def _observation(
        self,
        *,
        scorable: SurfaceScorable,
        acquisition: Acquisition,
        reasons: tuple[str, ...],
        entries: tuple[SurfaceEntry, ...] = (),
        excluded: int = 0,
    ) -> Observation:
        # Only the window can be checked against a file system. Record any correlation keys this
        # source could not apply, so a reader does not mistake them for checked attribution.
        unapplied: list[str] = []
        if scorable.scope is not None and scorable.scope.attempt_id is not None:
            unapplied.append("attempt_id")
        if scorable.scope is not None and scorable.scope.labels:
            unapplied.append("labels")
        return Observation(
            source_identifier=self.get_identifier(),
            acquisition=acquisition,
            scorable=scorable,
            metadata={"scope_keys_not_applied": ",".join(unapplied)} if unapplied else {},
            payload=SurfaceObservationPayload(
                scope=scorable,
                entries=entries,
                coverage=SurfaceCoverage(complete=acquisition is Acquisition.COMPLETE, reasons=reasons),
                excluded_outside_scope=excluded,
            ),
        )


def _decode_text(data: bytes, *, truncated: bool) -> str | None:
    """
    Decode retained bytes as UTF-8 text, or return None for content that is not text.

    A retention cut can split one multi-byte character at the end, so a truncated prefix may
    drop up to three trailing bytes. Anything else that fails to decode is not text.

    Returns:
        str | None: The text, or None when the bytes are not UTF-8.
    """
    for trim in range(4 if truncated else 1):
        try:
            return data[: len(data) - trim].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None
