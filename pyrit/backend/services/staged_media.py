# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Expiring attachment bytes, separate from permanent result media."""

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

logger = logging.getLogger(__name__)


class StagedMediaStore:
    """Keep uploaded bytes for one day, including across backend restarts."""

    PREFIX = "pyrit-upload:"
    RETENTION_SECONDS = 24 * 60 * 60
    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(self, directory: Path) -> None:
        """Use a private staging directory outside permanent results storage."""
        self._directory = directory

    async def stage_async(self, data: bytes) -> str:
        """
        Store bytes without adding anything to permanent results storage.

        Returns:
            An opaque upload reference valid for one day.
        """
        token = await asyncio.to_thread(self._write, data)
        return f"{self.PREFIX}{token}"

    async def read_async(self, value: str) -> bytes:
        """
        Read a live upload or reject an expired, missing, or invalid reference.

        Returns:
            The staged file bytes.
        """
        try:
            token = UUID(value.removeprefix(self.PREFIX))
        except ValueError as exc:
            raise ValueError("Invalid attachment upload reference.") from exc
        if value != f"{self.PREFIX}{token}":
            raise ValueError("Invalid attachment upload reference.")
        return await asyncio.to_thread(self._read, token)

    async def cleanup_expired_async(self) -> None:
        """Remove only expired upload files, never permanent result media."""
        await asyncio.to_thread(self._cleanup_expired)

    async def run_cleanup_async(self) -> None:
        """Sweep on startup and periodically, including uploads abandoned by crashed clients."""
        while True:
            try:
                await self.cleanup_expired_async()
            except OSError:
                logger.exception("Could not clean up expired attachment uploads.")
            await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)

    def _write(self, data: bytes) -> UUID:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Refreshes must keep the reference stable for message-save idempotency.
        token = uuid5(NAMESPACE_URL, f"pyrit-upload:{hashlib.sha256(data).hexdigest()}")
        path = self._directory / f"{uuid4()}.upload"
        stream = path.open("xb")
        try:
            with stream:
                stream.write(data)
            path.replace(self._directory / f"{token}.upload")
        finally:
            path.unlink(missing_ok=True)
        return token

    def _read(self, token: UUID) -> bytes:
        path = self._directory / f"{token}.upload"
        try:
            if path.is_symlink() or path.stat().st_mtime + self.RETENTION_SECONDS <= time.time():
                raise ValueError("Attachment upload expired. Retry the upload before saving or sending.")
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError("Attachment upload expired. Retry the upload before saving or sending.") from exc

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.RETENTION_SECONDS
        for path in self._directory.glob("*.upload"):
            try:
                if path.name != f"{UUID(path.stem)}.upload" or path.is_symlink():
                    continue
                if path.stat().st_mtime <= cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except ValueError:
                continue
            except OSError:
                logger.exception("Could not delete expired attachment upload %s.", path.name)


staged_media_store = StagedMediaStore(CONFIGURATION_DIRECTORY_PATH / "media-uploads")
