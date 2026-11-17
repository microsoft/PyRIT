# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Sanitized dataset API failures, including recoverable upload cleanup."""

from uuid import UUID

from pyrit.exceptions.exception_classes import PyritException


class DatasetAPIException(PyritException):
    """An actionable dataset request failure using the backend's problem-detail contract."""

    def __init__(self, *, status_code: int, message: str, cleanup_id: UUID | None = None) -> None:
        """Retain a cleanup resource only when rollback requires a later retry."""
        super().__init__(status_code=status_code, message=message)
        self.cleanup_id = cleanup_id
