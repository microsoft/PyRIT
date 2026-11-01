# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import math
import string
from collections.abc import Iterator
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import regex
from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from pyrit.converter.base_image_text_converter import _BaseImageTextConverter
from pyrit.converter.converter import ConverterResult
from pyrit.memory import data_serializer_factory
from pyrit.models import ComponentIdentifier, PromptDataType


class TextToImageConverter(_BaseImageTextConverter):
    """
    Render text as a PNG using Pillow's default font, a bundled font, or a local font file.

    Text retains its case, tabs expand to four-column stops, and lines wrap at
    grapheme boundaries to fit the maximum image width. Glyph coverage depends on
    the selected font. The bundled ``ugly_gerry`` option instead renders ASCII
    letters as capitals and rejects characters outside its limited repertoire.
    No font installation, network access, or model is required for bundled fonts.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)
    _UGLY_GERRY_FONT_PATH = Path(__file__).parent / "fonts" / "Gerry.otf"
    _UGLY_GERRY_CHARACTERS = frozenset(string.ascii_letters + " \n\r!',.?")
    _MAX_PIXELS = 16_000_000
    _MEASUREMENT_CACHE_SIZE = 1024

    def __init__(
        self,
        *,
        font: str | Path | None = None,
        font_size: int = 64,
        max_width: int = 1200,
        padding: int = 20,
        color: tuple[int, int, int] = (0, 0, 0),
        background_color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        """
        Initialize the local font renderer.

        Args:
            font (str | Path | None): ``None`` or ``default`` uses Pillow's built-in font;
                ``ugly_gerry`` selects the bundled district-shaped font. Otherwise,
                provide a local ``.ttf`` or ``.otf`` file. Defaults to None.
            font_size (int): Font size in pixels. Defaults to 64.
            max_width (int): Maximum image width, including padding. Defaults to 1200.
            padding (int): Blank border in pixels. Defaults to 20.
            color (tuple[int, int, int]): Text RGB color. Defaults to black.
            background_color (tuple[int, int, int]): Background RGB color. Defaults to white.

        Raises:
            ValueError: If sizes or colors are invalid, or the colors are identical.
            OSError: If a selected font is missing or cannot be loaded. No fallback is used.
        """
        super().__init__()
        for name, value, minimum in (("font_size", font_size, 1), ("max_width", max_width, 1), ("padding", padding, 0)):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
        if max_width <= 2 * padding:
            raise ValueError("max_width must be greater than twice padding")
        self._validate_color(color)
        self._validate_color(background_color)
        if color == background_color:
            raise ValueError("color and background_color must differ so the text is visible")
        self._font_size = font_size
        self._max_width = max_width
        self._padding = padding
        self._color = color
        self._background_color = background_color
        self._font_name = "default" if font is None else str(font)
        self._font_data: bytes | None = None
        if isinstance(font, str) and font == "ugly_gerry":
            self._font_data = self._UGLY_GERRY_FONT_PATH.read_bytes()
        elif font is not None and not (isinstance(font, str) and font == "default"):
            font_path = Path(font)
            if font_path.suffix.lower() not in (".ttf", ".otf"):
                raise ValueError("font must be 'default', 'ugly_gerry', or a local .ttf or .otf path")
            self._font_data = font_path.read_bytes()
        self._load_font()

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Render a prompt and save a uniquely named PNG through PyRIT's image serializer.

        Args:
            prompt (str): Text to render. Choose a font that covers the prompt's characters.
            input_type (PromptDataType): Must be ``text``.

        Returns:
            ConverterResult: The generated PNG path with output type ``image_path``.

        Raises:
            ValueError: If the input type or characters are unsupported, the prompt is blank,
                a character cannot fit the width, or the image would exceed 16 million pixels.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type {input_type} not supported")
        if not prompt.strip():
            raise ValueError("Please provide valid text value")
        text = prompt.replace("\r\n", "\n").replace("\r", "\n")
        if self._font_name == "ugly_gerry":
            unsupported = set(prompt) - self._UGLY_GERRY_CHARACTERS
            if unsupported:
                raise ValueError(f"Ugly Gerry does not support these characters: {sorted(unsupported)!r}")
            text = text.upper()
        else:
            text = text.expandtabs(4)
        image_bytes = await asyncio.to_thread(self._render_png, text)
        serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path", extension="png")
        await serializer.save_data_async(data=image_bytes)
        return ConverterResult(output_text=str(serializer.value), output_type="image_path")

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "font": self._font_name,
                "font_sha256": hashlib.sha256(self._font_data).hexdigest() if self._font_data is not None else None,
                "font_size": self._font_size,
                "max_width": self._max_width,
                "padding": self._padding,
                "color": self._color,
                "background_color": self._background_color,
            }
        )

    def _load_font(self) -> FreeTypeFont:
        if self._font_data is not None:
            return ImageFont.truetype(BytesIO(self._font_data), size=self._font_size)
        font = ImageFont.load_default(size=self._font_size)
        if not isinstance(font, FreeTypeFont):
            raise RuntimeError("TextToImageConverter requires Pillow with FreeType support")
        return font

    def _iter_wrapped_lines(
        self, *, text: str, font: FreeTypeFont
    ) -> Iterator[tuple[str, tuple[float, float, float, float]]]:
        measure = lru_cache(maxsize=self._MEASUREMENT_CACHE_SIZE)(font.getbbox)
        width = self._max_width - 2 * self._padding
        for paragraph in text.split("\n"):
            line = ""
            for match in regex.finditer(r"\X", paragraph):
                character: str = match.group()
                candidate = line + character
                left, _, right, _ = measure(candidate)
                if right - left > width:
                    if not line:
                        raise ValueError(f"max_width is too small to render character {character!r}")
                    yield line, measure(line)
                    line = character
                    left, _, right, _ = measure(line)
                    if right - left > width:
                        raise ValueError(f"max_width is too small to render character {character!r}")
                else:
                    line = candidate
            yield line, measure(line)

    def _get_image_size(self, *, text_width: float, text_height: float, line_count: int) -> tuple[int, int]:
        line_height = max(1, text_height) + self._font_size // 4
        width = max(1, math.ceil(text_width)) + 2 * self._padding
        height = math.ceil(max(1, text_height) + (line_count - 1) * line_height) + 2 * self._padding
        if width * height > self._MAX_PIXELS:
            raise ValueError("Rendered image exceeds 16 million pixels; shorten the prompt or reduce font_size")
        return width, height

    def _render_png(self, text: str) -> bytes:
        font = self._load_font()
        lines: list[str] = []
        bounds: list[tuple[float, float, float, float]] = []
        top, bottom, text_width = math.inf, -math.inf, 0.0
        for line, box in self._iter_wrapped_lines(text=text, font=font):
            lines.append(line)
            bounds.append(box)
            top = min(top, box[1])
            bottom = max(bottom, box[3])
            text_width = max(text_width, box[2] - box[0])
            # Completed-line bounds are a lower bound on the final image's dimensions.
            self._get_image_size(text_width=text_width, text_height=bottom - top, line_count=len(lines))
        width, height = self._get_image_size(text_width=text_width, text_height=bottom - top, line_count=len(lines))
        line_height = max(1, bottom - top) + self._font_size // 4
        with Image.new("RGB", (width, height), self._background_color) as image, BytesIO() as output:
            draw = ImageDraw.Draw(image)
            for index, (line, box) in enumerate(zip(lines, bounds, strict=True)):
                draw.text(
                    (self._padding - box[0], self._padding - top + index * line_height),
                    line,
                    font=font,
                    fill=self._color,
                )
            image.save(output, format="PNG")
            return output.getvalue()
