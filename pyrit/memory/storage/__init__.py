# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Storage layer for PyRIT: storage backends and multi-modal data serializers.

Provides the disk and blob storage adapters (``StorageIO`` and its
implementations) and the data-type serializers (``data_serializer_factory`` and
the per-type ``*DataTypeSerializer`` classes) used to read and write prompt
payloads such as text, images, audio, and video.

These serializers write payload files into the location configured on the active
memory instance (``results_path`` / ``results_storage_io``), which is why they
live alongside ``pyrit.memory``: the database holds the records and this package
holds the blob payloads those records point to.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.memory.storage.data_url_converter import convert_local_image_to_data_url_async
    from pyrit.memory.storage.serializers import (
        AllowedCategories,
        AudioPathDataTypeSerializer,
        BinaryPathDataTypeSerializer,
        DataTypeSerializer,
        ErrorDataTypeSerializer,
        ImagePathDataTypeSerializer,
        TextDataTypeSerializer,
        URLDataTypeSerializer,
        VideoPathDataTypeSerializer,
        data_serializer_factory,
        set_message_piece_sha256_async,
        set_seed_sha256_async,
    )
    from pyrit.memory.storage.storage import AzureBlobStorageIO, DiskStorageIO, StorageIO, SupportedContentType

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AllowedCategories": "pyrit.memory.storage.serializers",
    "AudioPathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "AzureBlobStorageIO": "pyrit.memory.storage.storage",
    "BinaryPathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "convert_local_image_to_data_url_async": "pyrit.memory.storage.data_url_converter",
    "DataTypeSerializer": "pyrit.memory.storage.serializers",
    "data_serializer_factory": "pyrit.memory.storage.serializers",
    "DiskStorageIO": "pyrit.memory.storage.storage",
    "ErrorDataTypeSerializer": "pyrit.memory.storage.serializers",
    "ImagePathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "set_message_piece_sha256_async": "pyrit.memory.storage.serializers",
    "set_seed_sha256_async": "pyrit.memory.storage.serializers",
    "StorageIO": "pyrit.memory.storage.storage",
    "SupportedContentType": "pyrit.memory.storage.storage",
    "TextDataTypeSerializer": "pyrit.memory.storage.serializers",
    "URLDataTypeSerializer": "pyrit.memory.storage.serializers",
    "VideoPathDataTypeSerializer": "pyrit.memory.storage.serializers",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
