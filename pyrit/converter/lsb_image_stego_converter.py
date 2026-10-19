# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import math
from io import BytesIO
from pathlib import Path  # noqa: TC003 - registry annotation resolution
from typing import TYPE_CHECKING, Literal

from PIL import Image

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.memory import data_serializer_factory

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, PromptDataType

StegoChannelMode = Literal["RGB", "RGBA"]


def _validate_stego_params(*, channels: StegoChannelMode, delimiter: str) -> None:
    if channels not in ("RGB", "RGBA"):
        raise ValueError("channels must be either 'RGB' or 'RGBA'")
    if not isinstance(delimiter, str) or not delimiter:
        raise ValueError("delimiter must be a non-empty string")


class LSBImageStegoConverter(Converter):
    """
    Encode a text prompt into the Least Significant Bits (LSB) of a lossless PNG image.
    """

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("image_path",)
    _DEFAULT_DELIMITER = "====EOF===="
    _DEFAULT_MIN_DIMENSIONS = (64, 64)

    def __init__(
        self,
        *,
        carrier_image_path: str | Path | None = None,
        channels: StegoChannelMode = "RGBA",
        delimiter: str = _DEFAULT_DELIMITER,
        min_dimensions: tuple[int, int] = _DEFAULT_MIN_DIMENSIONS,
        background_color: tuple[int, int, int] | tuple[int, int, int, int] = (240, 244, 248, 255),
    ) -> None:
        """
        Initialize the LSB image steganography converter.

        Args:
            carrier_image_path (str | Path | None): Optional path to a cover image. When None, a lossless
                solid carrier image is generated and sized to fit the payload. Defaults to None.
            channels (Literal["RGB", "RGBA"]): Pixel channel mode used for LSB embedding. Defaults to "RGBA".
            delimiter (str): End-of-payload sentinel appended after the UTF-8 payload. Defaults to "====EOF====".
            min_dimensions (tuple[int, int]): Minimum (width, height) for synthetic carriers. Defaults to (64, 64).
            background_color (tuple[int, int, int] | tuple[int, int, int, int]): Base RGB/RGBA color for
                synthetic carrier images. Defaults to (240, 244, 248, 255).

        Raises:
            ValueError: If any parameter is invalid.
        """
        super().__init__()
        _validate_stego_params(channels=channels, delimiter=delimiter)
        if len(min_dimensions) != 2 or any(type(d) is not int or d < 1 for d in min_dimensions):
            raise ValueError("min_dimensions must be a (width, height) tuple with positive integers")
        if len(background_color) not in (3, 4) or any(
            type(c) is not int or not (0 <= c <= 255) for c in background_color
        ):
            raise ValueError("background_color must be an RGB or RGBA tuple with channel values in 0..255")

        self._carrier_image_path = str(carrier_image_path) if carrier_image_path is not None else None
        self._channels: StegoChannelMode = channels
        self._delimiter = delimiter
        self._min_dimensions = min_dimensions
        self._background_color: tuple[int, int, int, int] = (
            (background_color[0], background_color[1], background_color[2], 255)
            if len(background_color) == 3
            else (background_color[0], background_color[1], background_color[2], background_color[3])
        )

        self._base_image: Image.Image | None = None
        if self._carrier_image_path is not None:
            if not self._carrier_image_path.strip() or self._carrier_image_path == ".":
                raise ValueError("Please provide a valid carrier_image_path")
            with Image.open(self._carrier_image_path) as img:
                img.load()
                self._base_image = img.convert(self._channels).copy()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "carrier_image_path": self._carrier_image_path,
                "channels": self._channels,
                "delimiter": self._delimiter,
                "min_dimensions": self._min_dimensions,
                "background_color": self._background_color,
            }
        )

    def _prepare_carrier_image(self, required_bits: int) -> Image.Image:
        channels_per_pixel = 4 if self._channels == "RGBA" else 3
        if self._base_image is not None:
            capacity_bits = self._base_image.width * self._base_image.height * channels_per_pixel
            if required_bits > capacity_bits:
                raise ValueError(
                    f"Carrier image ({self._base_image.width}x{self._base_image.height}, "
                    f"{capacity_bits} bits capacity) is too small to hold payload of {required_bits} bits."
                )
            return self._base_image.copy()

        min_pixels = math.ceil(required_bits / channels_per_pixel)
        min_w, min_h = self._min_dimensions
        if min_w * min_h >= min_pixels:
            width, height = min_w, min_h
        else:
            width = max(min_w, math.ceil(math.sqrt(min_pixels)))
            height = max(min_h, math.ceil(min_pixels / width))

        color = self._background_color if self._channels == "RGBA" else self._background_color[:3]
        return Image.new(self._channels, (width, height), color=color)

    def _embed_lsb(self, prompt: str) -> Image.Image:
        payload_bytes = prompt.encode("utf-8")
        delimiter_bytes = self._delimiter.encode("utf-8")
        framed_bytes = payload_bytes + delimiter_bytes
        if framed_bytes.find(delimiter_bytes) != len(payload_bytes):
            raise ValueError(f"Prompt collides with delimiter {self._delimiter!r}; choose a different delimiter")

        carrier = self._prepare_carrier_image(required_bits=len(framed_bytes) * 8)
        raw_bytes = bytearray(carrier.tobytes())
        bit_index = 0
        for byte_val in framed_bytes:
            for shift in range(7, -1, -1):
                raw_bytes[bit_index] = (raw_bytes[bit_index] & 0xFE) | ((byte_val >> shift) & 1)
                bit_index += 1

        return Image.frombytes(self._channels, carrier.size, bytes(raw_bytes))

    def _encode_png(self, prompt: str) -> bytes:
        with self._embed_lsb(prompt=prompt) as stego_image, BytesIO() as image_bytes:
            stego_image.save(fp=image_bytes, format="PNG")
            return image_bytes.getvalue()

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given text prompt into a lossless PNG image with an LSB-encoded payload.

        Args:
            prompt (str): The text prompt to encode inside the PNG image.
            input_type (PromptDataType): The input data type. Defaults to "text".

        Returns:
            ConverterResult: Result containing the saved PNG file path with output_type="image_path".

        Raises:
            ValueError: If the input type is unsupported, the prompt is empty, or the carrier cannot hold the payload.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        if prompt.strip() == "":
            raise ValueError("Please provide valid text value")

        image_bytes = await asyncio.to_thread(self._encode_png, prompt)
        img_serializer = data_serializer_factory(
            category="prompt-memory-entries", data_type="image_path", extension="png"
        )
        await img_serializer.save_data_async(data=image_bytes)
        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")

    @classmethod
    def decode_image(
        cls,
        image_path: str | Path | BytesIO,
        *,
        channels: StegoChannelMode = "RGBA",
        delimiter: str = _DEFAULT_DELIMITER,
    ) -> str:
        """
        Decode and return the UTF-8 string hidden inside the LSBs of a PNG image.

        Args:
            image_path (str | Path | BytesIO): Path or byte stream of the stego PNG image.
            channels (Literal["RGB", "RGBA"]): Pixel channel mode used during encoding. Defaults to "RGBA".
            delimiter (str): End-of-payload sentinel string. Defaults to "====EOF====".

        Returns:
            str: The decoded UTF-8 text payload.

        Raises:
            ValueError: If parameters are invalid, the delimiter is missing, or the payload is not valid UTF-8.
        """
        _validate_stego_params(channels=channels, delimiter=delimiter)
        with Image.open(image_path) as img:
            raw_bytes = img.convert(channels).tobytes()

        delimiter_bytes = delimiter.encode("utf-8")
        delim_len = len(delimiter_bytes)
        extracted, current_byte, bit_count = bytearray(), 0, 0

        for channel_val in raw_bytes:
            current_byte = (current_byte << 1) | (channel_val & 1)
            bit_count += 1
            if bit_count == 8:
                extracted.append(current_byte)
                if extracted.endswith(delimiter_bytes):
                    try:
                        return bytes(extracted[:-delim_len]).decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError("Decoded LSB payload before delimiter is not valid UTF-8") from exc
                current_byte, bit_count = 0, 0

        raise ValueError(f"Delimiter {delimiter!r} not found in image LSB stream")


class LSBImageStegoDecodeConverter(Converter):
    """
    Decode a UTF-8 text payload from an LSB stego PNG image (`image_path -> text`).
    """

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("image_path",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    def __init__(
        self,
        *,
        channels: StegoChannelMode = "RGBA",
        delimiter: str = LSBImageStegoConverter._DEFAULT_DELIMITER,
    ) -> None:
        """
        Initialize the LSB image steganography decoder converter.

        Args:
            channels (Literal["RGB", "RGBA"]): Pixel channel mode used during encoding. Defaults to "RGBA".
            delimiter (str): End-of-payload sentinel string. Defaults to "====EOF====".

        Raises:
            ValueError: If `channels` or `delimiter` is invalid.
        """
        super().__init__()
        _validate_stego_params(channels=channels, delimiter=delimiter)
        self._channels: StegoChannelMode = channels
        self._delimiter = delimiter

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"channels": self._channels, "delimiter": self._delimiter})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Decode the hidden UTF-8 text payload from a stego PNG image path.

        Args:
            prompt (str): Path to the stego PNG image.
            input_type (PromptDataType): The input data type. Defaults to "image_path".

        Returns:
            ConverterResult: Result containing the decoded UTF-8 string with output_type="text".

        Raises:
            ValueError: If the input type is unsupported, the path is empty, or decoding fails.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        if prompt.strip() == "":
            raise ValueError("Please provide valid image_path value")

        img_serializer = data_serializer_factory(category="prompt-memory-entries", value=prompt, data_type="image_path")
        img_bytes = await img_serializer.read_data_async()
        decoded_text = await asyncio.to_thread(
            LSBImageStegoConverter.decode_image,
            BytesIO(img_bytes),
            channels=self._channels,
            delimiter=self._delimiter,
        )
        return ConverterResult(output_text=decoded_text, output_type="text")
