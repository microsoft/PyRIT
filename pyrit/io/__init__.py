# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
I/O layer for PyRIT: storage backends and multi-modal data serializers.

Provides the disk and blob storage adapters (``StorageIO`` and its
implementations) and the data-type serializers (``data_serializer_factory`` and
the per-type ``*DataTypeSerializer`` classes) used to read and write prompt
payloads such as text, images, audio, and video.

Unlike ``pyrit.models``, modules in this package may depend on ``pyrit.memory``
and ``pyrit.auth`` (resolved lazily to avoid import cycles).
"""

from pyrit.io.serializers import (
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
from pyrit.io.storage import (
    AzureBlobStorageIO,
    DiskStorageIO,
    StorageIO,
    SupportedContentType,
)

__all__ = [
    "AllowedCategories",
    "AudioPathDataTypeSerializer",
    "AzureBlobStorageIO",
    "BinaryPathDataTypeSerializer",
    "DataTypeSerializer",
    "data_serializer_factory",
    "DiskStorageIO",
    "ErrorDataTypeSerializer",
    "ImagePathDataTypeSerializer",
    "set_message_piece_sha256_async",
    "set_seed_sha256_async",
    "StorageIO",
    "SupportedContentType",
    "TextDataTypeSerializer",
    "URLDataTypeSerializer",
    "VideoPathDataTypeSerializer",
]
