# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — the storage I/O classes now live in
``pyrit.memory.storage``.

Importing names from ``pyrit.models.storage_io`` still works for one release but
emits a one-time ``DeprecationWarning`` per name. Import from
``pyrit.memory.storage`` instead. This shim will be removed in 0.17.0.
"""

from __future__ import annotations

from pyrit.common.deprecation import module_deprecation_getattr

__all__ = [
    "AzureBlobStorageIO",
    "DiskStorageIO",
    "StorageIO",
    "SupportedContentType",
]

__getattr__ = module_deprecation_getattr(
    old_module="pyrit.models.storage_io",
    target_module="pyrit.memory.storage.storage",
    names=__all__,
    removed_in="0.17.0",
)


def __dir__() -> list[str]:
    return sorted(__all__)
