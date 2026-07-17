# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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

from pyrit.memory.storage.data_url_converter import (
    convert_local_image_to_data_url_async,
)
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
from pyrit.memory.storage.storage import (
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
    "convert_local_image_to_data_url_async",
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
