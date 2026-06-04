# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — the data-type serializers moved to ``pyrit.io.serializers``.

Importing names from ``pyrit.models.data_type_serializer`` still works for one
release but emits a one-time ``DeprecationWarning`` per name. Import from
``pyrit.io`` instead. This shim will be removed in 0.17.0.
"""

from __future__ import annotations

from pyrit.common.deprecation import module_deprecation_getattr

__all__ = [
    "AllowedCategories",
    "AudioPathDataTypeSerializer",
    "BinaryPathDataTypeSerializer",
    "DataTypeSerializer",
    "data_serializer_factory",
    "ErrorDataTypeSerializer",
    "ImagePathDataTypeSerializer",
    "TextDataTypeSerializer",
    "URLDataTypeSerializer",
    "VideoPathDataTypeSerializer",
]

__getattr__ = module_deprecation_getattr(
    old_module="pyrit.models.data_type_serializer",
    target_module="pyrit.io.serializers",
    names=__all__,
    removed_in="0.17.0",
)


def __dir__() -> list[str]:
    return sorted(__all__)
