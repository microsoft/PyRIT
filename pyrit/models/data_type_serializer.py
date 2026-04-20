# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import base64
import hashlib
import os
import time
import wave
from mimetypes import guess_type
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, Union, get_args
from urllib.parse import urlparse

import aiofiles

from pyrit.common.path import DB_DATA_PATH
from pyrit.models.literals import PromptDataType
from pyrit.models.storage_io import DiskStorageIO, StorageIO

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface

# Define allowed categories for validation
AllowedCategories = Literal["seed-prompt-entries", "prompt-memory-entries"]


def data_serializer_factory(
    *,
    data_type: PromptDataType,
    value: Optional[str] = None,
    extension: Optional[str] = None,
    category: AllowedCategories,
) -> "DataTypeSerializer":
    """
    Factory method to create a DataTypeSerializer instance.

    Args:
        data_type (str): The type of the data (e.g., 'text', 'image_path', 'audio_path').
        value (str): The data value to be serialized.
        extension (Optional[str]): The file extension, if applicable.
        category (AllowedCategories): The category or context for the data (e.g., 'seed-prompt-entries').

    Returns:
        DataTypeSerializer: An instance of the appropriate serializer.

    Raises:
        ValueError: If the category is not provided or invalid.
    """
    if not category:
        raise ValueError(
            f"The 'category' argument is mandatory and must be one of the following: {get_args(AllowedCategories)}."
        )
    if value is not None:
        if data_type in ["text", "reasoning", "function_call", "tool_call", "function_call_output"]:
            return TextDataTypeSerializer(prompt_text=value, data_type=data_type)
        elif data_type == "image_path":
            return ImagePathDataTypeSerializer(category=category, prompt_text=value, extension=extension)
        elif data_type == "audio_path":
            return AudioPathDataTypeSerializer(category=category, prompt_text=value, extension=extension)
        elif data_type == "video_path":
            return VideoPathDataTypeSerializer(category=category, prompt_text=value, extension=extension)
        elif data_type == "binary_path":
            return BinaryPathDataTypeSerializer(category=category, prompt_text=value, extension=extension)
        elif data_type == "error":
            return ErrorDataTypeSerializer(prompt_text=value)
        elif data_type == "url":
            return URLDataTypeSerializer(category=category, prompt_text=value, extension=extension)
        else:
            raise ValueError(f"Data type {data_type} not supported")
    else:
        if data_type == "image_path":
            return ImagePathDataTypeSerializer(category=category, extension=extension)
        elif data_type == "audio_path":
            return AudioPathDataTypeSerializer(category=category, extension=extension)
        elif data_type == "video_path":
            return VideoPathDataTypeSerializer(category=category, extension=extension)
        elif data_type == "binary_path":
            return BinaryPathDataTypeSerializer(category=category, extension=extension)
        elif data_type == "error":
            return ErrorDataTypeSerializer(prompt_text="")
        else:
            raise ValueError(f"Data type {data_type} without prompt text not supported")


class DataTypeSerializer(abc.ABC):
    """
    Abstract base class for data type normalizers.

    Responsible for reading and saving multi-modal data types to local disk or Azure Storage Account.
    """

    data_type: PromptDataType
    value: str
    category: str
    data_sub_directory: str
    file_extension: str

    _file_path: Union[Path, str] = None

    @property
    def _memory(self) -> MemoryInterface:
        from pyrit.memory import CentralMemory

        return CentralMemory.get_memory_instance()

    def _get_storage_io(self) -> StorageIO:
        """
        Retrieve the input datasets storage handle.

        Returns:
        StorageIO: An instance of DiskStorageIO or AzureBlobStorageIO based on the storage configuration.

        Raises:
            ValueError: If the Azure Storage URL is detected but the datasets storage handle is not set.
        """
        if self._is_azure_storage_url(self.value):
            # Scenarios where a user utilizes an in-memory DuckDB but also needs to interact
            # with an Azure Storage Account, ex., XPIAWorkflow.
            return self._memory.results_storage_io
        return DiskStorageIO()

    @abc.abstractmethod
    def data_on_disk(self) -> bool:
        """
        Returns True if the data is stored on disk.
        """

    async def save_data(self, data: bytes, output_filename: Optional[str] = None) -> None:
        """
        Saves the data to storage.

        Arguments:
            data: bytes: The data to be saved.
            output_filename (optional, str): filename to store data as. Defaults to UUID if not provided
        """
        file_path = await self.get_data_filename(file_name=output_filename)
        await self._memory.results_storage_io.write_file(file_path, data)
        self.value = str(file_path)

    async def save_b64_image(self, data: str | bytes, output_filename: str = None) -> None:
        """
        Saves the base64 encoded image to storage.

        Arguments:
            data: string or bytes with base64 data
            output_filename (optional, str): filename to store image as. Defaults to UUID if not provided
        """
        file_path = await self.get_data_filename(file_name=output_filename)
        image_bytes = base64.b64decode(data)
        await self._memory.results_storage_io.write_file(file_path, image_bytes)
        self.value = str(file_path)

    async def save_formatted_audio(
        self,
        data: bytes,
        num_channels: int = 1,
        sample_width: int = 2,
        sample_rate: int = 16000,
        output_filename: Optional[str] = None,
    ) -> None:
        """
        Saves the PCM16 of other specially formatted audio data to storage.

        Arguments:
            data: bytes with audio data
            output_filename (optional, str): filename to store audio as. Defaults to UUID if not provided
            num_channels (optional, int): number of channels in audio data. Defaults to 1
            sample_width (optional, int): sample width in bytes. Defaults to 2
            sample_rate (optional, int): sample rate in Hz. Defaults to 16000
        """
        file_path = await self.get_data_filename(file_name=output_filename)

        # save audio file locally first if in AzureStorageBlob so we can use wave.open to set audio parameters
        if self._is_azure_storage_url(str(file_path)):
            local_temp_path = Path(DB_DATA_PATH, "temp_audio.wav")
            with wave.open(str(local_temp_path), "wb") as wav_file:
                wav_file.setnchannels(num_channels)
                wav_file.setsampwidth(sample_width)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(data)

            async with aiofiles.open(local_temp_path, "rb") as f:
                audio_data = await f.read()
                await self._memory.results_storage_io.write_file(file_path, audio_data)
            os.remove(local_temp_path)

        # If local, we can just save straight to disk and do not need to delete temp file after
        else:
            with wave.open(str(file_path), "wb") as wav_file:
                wav_file.setnchannels(num_channels)
                wav_file.setsampwidth(sample_width)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(data)

        self.value = str(file_path)

    async def read_data(self) -> bytes:
        """
        Reads the data from the storage.

        Returns:
            bytes: The data read from storage.
        """
        if not self.data_on_disk():
            raise TypeError(f"Data for data Type {self.data_type} is not stored on disk")

        if not self.value:
            raise RuntimeError("Prompt text not set")

        storage_io = self._get_storage_io()
        # Check if path exists
        file_exists = await storage_io.path_exists(path=self.value)
        if not file_exists:
            raise FileNotFoundError(f"File not found: {self.value}")
        # Read the contents from the path
        return await storage_io.read_file(self.value)

    async def read_data_base64(self) -> str:
        """
        Reads the data from the storage.
        """
        byte_array = await self.read_data()
        return base64.b64encode(byte_array).decode("utf-8")

    async def get_sha256(self) -> str:
        input_bytes: bytes = None

        if self.data_on_disk():
            storage_io = self._get_storage_io()
            file_exists = await storage_io.path_exists(self.value)
            if not file_exists:
                raise FileNotFoundError(f"File not found: {self.value}")

            # Read the data from storage
            input_bytes = await storage_io.read_file(self.value)
        else:
            if isinstance(self.value, str):
                input_bytes = self.value.encode("utf-8")
            else:
                raise ValueError(f"Invalid data type {self.value}, expected str data type.")

        hash_object = hashlib.sha256(input_bytes)
        return hash_object.hexdigest()

    async def get_data_filename(self, file_name: Optional[str] = None) -> Union[Path, str]:
        """
        Generates or retrieves a unique filename for the data file.
        """
        if self._file_path:
            return self._file_path

        if not self.data_on_disk():
            raise TypeError("Data is not stored on disk")

        if not self.data_sub_directory:
            raise RuntimeError("Data sub directory not set")

        ticks = int(time.time() * 1_000_000)
        results_path = self._memory.results_path
        file_name = file_name if file_name else str(ticks)

        if self._is_azure_storage_url(results_path):
            full_data_directory_path = results_path + self.data_sub_directory
            self._file_path = full_data_directory_path + f"/{file_name}.{self.file_extension}"
        else:
            full_data_directory_path = results_path + self.data_sub_directory
            await self._memory.results_storage_io.create_directory_if_not_exists(Path(full_data_directory_path))
            self._file_path = Path(full_data_directory_path, f"{file_name}.{self.file_extension}")

        return self._file_path

    @staticmethod
    def get_extension(file_path: str) -> str | None:
        """
        Get the file extension from the file path.
        """
        _, ext = os.path.splitext(file_path)
        return ext if ext else None

    @staticmethod
    def get_mime_type(file_path: str) -> str | None:
        """
        Get the MIME type of the file path.
        """
        mime_type, _ = guess_type(file_path)
        return mime_type

    def _is_azure_storage_url(self, path: str) -> bool:
        """
        Validates if the given path is an Azure Storage URL.

        Args:
            path (str): Path or URL to check.

        Returns:
            bool: True if the path is an Azure Blob Storage URL.
        """
        parsed = urlparse(path)
        return parsed.scheme in ("http", "https") and "blob.core.windows.net" in parsed.netloc


class TextDataTypeSerializer(DataTypeSerializer):
    def __init__(self, *, prompt_text: str, data_type: PromptDataType = "text"):
        self.data_type = data_type
        self.value = prompt_text

    def data_on_disk(self) -> bool:
        return False


class ErrorDataTypeSerializer(DataTypeSerializer):
    def __init__(self, *, prompt_text: str):
        self.data_type = "error"
        self.value = prompt_text

    def data_on_disk(self) -> bool:
        return False


class URLDataTypeSerializer(DataTypeSerializer):
    def __init__(self, *, category: str, prompt_text: str, extension: Optional[str] = None):
        self.data_type = "url"
        self.value = prompt_text
        self.data_sub_directory = f"/{category}/urls"
        self.file_extension = extension if extension else "txt"
        self.on_disk = not (prompt_text.startswith("http://") or prompt_text.startswith("https://"))

    def data_on_disk(self) -> bool:
        return self.on_disk


class ImagePathDataTypeSerializer(DataTypeSerializer):
    def __init__(self, *, category: str, prompt_text: Optional[str] = None, extension: Optional[str] = None):
        self.data_type = "image_path"
        self.data_sub_directory = f"/{category}/images"
        self.file_extension = extension if extension else "png"

        if prompt_text:
            self.value = prompt_text

    def data_on_disk(self) -> bool:
        return True


class AudioPathDataTypeSerializer(DataTypeSerializer):
    def __init__(
        self,
        *,
        category: str,
        prompt_text: Optional[str] = None,
        extension: Optional[str] = None,
    ):
        self.data_type = "audio_path"
        self.data_sub_directory = f"/{category}/audio"
        self.file_extension = extension if extension else "mp3"

        if prompt_text:
            self.value = prompt_text

    def data_on_disk(self) -> bool:
        return True


class VideoPathDataTypeSerializer(DataTypeSerializer):
    def __init__(
        self,
        *,
        category: str,
        prompt_text: Optional[str] = None,
        extension: Optional[str] = None,
    ):
        """
        Serializer for video data paths.

        Args:
            category (str): The category or context for the data.
            prompt_text (Optional[str]): The video path or identifier.
            extension (Optional[str]): The file extension, defaults to 'mp4'.
        """
        self.data_type = "video_path"
        self.data_sub_directory = f"/{category}/videos"
        self.file_extension = extension if extension else "mp4"

        if prompt_text:
            self.value = prompt_text

    def data_on_disk(self) -> bool:
        return True


class BinaryPathDataTypeSerializer(DataTypeSerializer):
    def __init__(
        self,
        *,
        category: str,
        prompt_text: Optional[str] = None,
        extension: Optional[str] = None,
    ):
        """
        Serializer for arbitrary binary data paths.

        This serializer handles generic binary data that doesn't fit into specific
        categories like images, audio, or video. Useful for XPIA attacks and
        storing files like PDFs, documents, or other binary formats.

        Args:
            category (str): The category or context for the data.
            prompt_text (Optional[str]): The binary file path or identifier.
            extension (Optional[str]): The file extension, defaults to 'bin'.
        """
        self.data_type = "binary_path"
        self.data_sub_directory = f"/{category}/binaries"
        self.file_extension = extension if extension else "bin"

        if prompt_text:
            self.value = prompt_text

    def data_on_disk(self) -> bool:
        return True
