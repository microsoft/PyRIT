# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import string
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from pyrit.converter.converter import Converter

logger = logging.getLogger(__name__)


class _BaseImageTextConverter(Converter):
    """
    Base class with shared text-on-image rendering utilities.

    Provides validation, font loading, word wrapping, overlay drawing, and
    compositing used by image text converters.
    """

    _font_name: str | None
    _font_load_failed: bool
    _font_size_min: int
    _font_size_max: int
    _auto_font_size: bool

    @staticmethod
    def _validate_font_name(font_name: Path | None) -> None:
        """
        Validate that a configured font uses the TrueType extension.

        Args:
            font_name (Path | None): The optional font path to validate.

        Raises:
            ValueError: If the font path does not use the ``.ttf`` extension.
        """
        if font_name is not None and Path(font_name).suffix.lower() != ".ttf":
            raise ValueError("The specified font must be a TrueType font with a .ttf extension")

    @staticmethod
    def _validate_color(color: object) -> None:
        """
        Validate an RGB color tuple.

        Args:
            color (object): The value to validate.

        Raises:
            ValueError: If the value is not a three-integer RGB tuple in the range 0 through 255.
        """
        if (
            not isinstance(color, tuple)
            or len(color) != 3
            or not all(isinstance(channel, int) and 0 <= channel <= 255 for channel in color)
        ):
            raise ValueError("color must be a tuple of three integers between 0 and 255")

    def _extract_font_size(self, font_size: int | tuple[int, int]) -> None:
        """
        Parse a fixed font size or range into shared internal fields.

        Args:
            font_size (int | tuple[int, int]): Fixed size or (min, max) range.

        Raises:
            ValueError: If the fixed size is not positive or the tuple range is invalid.
        """
        if isinstance(font_size, tuple):
            if len(font_size) != 2 or font_size[0] > font_size[1] or font_size[0] < 1:
                raise ValueError("font_size tuple must be (min, max) with 1 <= min <= max")
            self._font_size_min = font_size[0]
            self._font_size_max = font_size[1]
            self._auto_font_size = True
        else:
            if font_size < 1:
                raise ValueError("font_size must be greater than 0")
            self._font_size_min = font_size
            self._font_size_max = font_size
            self._auto_font_size = False

    def _load_font_at_size(self, size: int) -> FreeTypeFont:
        """
        Load the configured font at a specific size.

        Args:
            size (int): The font size to load.

        Returns:
            FreeTypeFont: The loaded font, falling back to Pillow's built-in font on error.
        """
        if self._font_load_failed:
            return cast("FreeTypeFont", ImageFont.load_default(size=size))
        try:
            return ImageFont.truetype(self._font_name, size)  # type: ignore[ty:invalid-argument-type]
        except OSError:
            logger.warning(f"Cannot open font resource: {self._font_name}. Using Pillow built-in default font.")
            self._font_load_failed = True
            return cast("FreeTypeFont", ImageFont.load_default(size=size))

    def _fit_font_to_box(
        self,
        *,
        text: str,
        font_loader: Callable[[int], FreeTypeFont],
        min_size: int,
        max_size: int,
        box_width: int,
        box_height: int,
    ) -> tuple[FreeTypeFont, list[str]]:
        """
        Shrink the font from ``max_size`` down to ``min_size`` until the wrapped text fits the box.

        Args:
            text (str): The text to fit.
            font_loader (Callable[[int], FreeTypeFont]): Returns a font for a given point size.
            min_size (int): The smallest font size to try.
            max_size (int): The largest font size to try.
            box_width (int): The box width in pixels.
            box_height (int): The box height in pixels.

        Returns:
            tuple[FreeTypeFont, list[str]]: The chosen font and wrapped text lines. Falls back to
            ``min_size`` and logs a warning when the text overflows even then, so long text is
            never silently clipped.
        """
        usable_width = int(box_width * 0.95)
        usable_height = int(box_height * 0.95)

        for size in range(max_size, min_size - 1, -1):
            font = font_loader(size)
            lines = self._wrap_text(text=text, font=font, max_width=usable_width)
            if len(lines) * self._get_line_height(font=font) <= usable_height:
                return font, lines

        min_font = font_loader(min_size)
        lines = self._wrap_text(text=text, font=min_font, max_width=usable_width)
        logger.warning(
            f"Text does not fit in box ({box_width}x{box_height}) even at minimum font size "
            f"{min_size}. Text may be clipped."
        )
        return min_font, lines

    _DEFAULT_MARGIN: int = 5

    def _wrap_text(self, *, text: str, font: FreeTypeFont, max_width: int) -> list[str]:
        """
        Word-wrap text to fit within max_width pixels.

        Args:
            text (str): The text to wrap.
            font (FreeTypeFont): The font used for measuring text width.
            max_width (int): The maximum width in pixels for each line.

        Returns:
            list[str]: The wrapped text lines.
        """
        temp_img = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(temp_img)
        bbox = draw.textbbox((0, 0), string.ascii_letters, font=font)
        avg_char_width = (bbox[2] - bbox[0]) / len(string.ascii_letters)
        max_chars = max(1, int(max_width / avg_char_width))
        wrapped = textwrap.fill(text, width=max_chars)
        return wrapped.split("\n")

    def _get_line_height(self, *, font: FreeTypeFont) -> int:
        """
        Get the line height in pixels for a given font.

        Args:
            font (FreeTypeFont): The font to measure.

        Returns:
            int: The line height in pixels.
        """
        temp_img = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(temp_img)
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return int(bbox[3] - bbox[1])

    def _draw_text_overlay(
        self,
        *,
        lines: list[str],
        font: FreeTypeFont,
        color: tuple[int, int, int],
        box_width: int,
        box_height: int,
        center_text: bool = False,
    ) -> Image.Image:
        """
        Draw text lines onto a transparent RGBA overlay image.

        Args:
            lines (list[str]): The text lines to draw.
            font (FreeTypeFont): The font to use.
            color (tuple[int, int, int]): RGB color for the text.
            box_width (int): The overlay width.
            box_height (int): The overlay height.
            center_text (bool): Whether to center text horizontally and vertically. Defaults to False.

        Returns:
            Image.Image: The RGBA overlay with rendered text.
        """
        overlay = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        fill_color = color + (255,)

        line_height = self._get_line_height(font=font)
        total_height = len(lines) * line_height
        y_start = (box_height - total_height) // 2 if center_text else 0

        for i, line in enumerate(lines):
            line_y = y_start + i * line_height
            if center_text:
                line_bbox = draw.textbbox((0, 0), line, font=font)
                line_x = (box_width - (line_bbox[2] - line_bbox[0])) // 2
            else:
                line_x = 0
            draw.text((line_x, line_y), line, font=font, fill=fill_color)

        return overlay

    def _composite_overlay(
        self,
        *,
        image: Image.Image,
        overlay: Image.Image,
        bounding_box: tuple[int, int, int, int],
        rotation: float = 0.0,
    ) -> Image.Image:
        """
        Optionally rotate the overlay and paste it onto the base image.

        Args:
            image (Image.Image): The base image.
            overlay (Image.Image): The text overlay.
            bounding_box (tuple[int, int, int, int]): The (x1, y1, x2, y2) region.
            rotation (float): Rotation angle in degrees. Defaults to 0.0.

        Returns:
            Image.Image: The composited image.
        """
        x1, y1, x2, y2 = bounding_box
        if rotation != 0:
            overlay = overlay.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            paste_x = center_x - overlay.width // 2
            paste_y = center_y - overlay.height // 2
        else:
            paste_x = x1
            paste_y = y1

        image = image.convert("RGBA")
        image.paste(overlay, (paste_x, paste_y), overlay)
        return image.convert("RGB")

    def _render_text_on_image(
        self,
        *,
        image: Image.Image,
        text: str,
        font: FreeTypeFont,
        color: tuple[int, int, int],
        bounding_box: tuple[int, int, int, int],
        center_text: bool = False,
        rotation: float = 0.0,
    ) -> Image.Image:
        """
        Render text within a bounding box on an image.

        Wraps text, draws it on a transparent overlay, and composites
        onto the base image with optional centering and rotation.

        Args:
            image (Image.Image): The base image to render text onto.
            text (str): The text to render.
            font (FreeTypeFont): The font to use.
            color (tuple[int, int, int]): RGB color for the text.
            bounding_box (tuple[int, int, int, int]): The (x1, y1, x2, y2) region.
            center_text (bool): Whether to center text in the bounding box. Defaults to False.
            rotation (float): Rotation angle in degrees. Defaults to 0.0.

        Returns:
            Image.Image: The image with text rendered in the bounding box.
        """
        x1, y1, x2, y2 = bounding_box
        box_width = x2 - x1
        box_height = y2 - y1

        lines = self._wrap_text(text=text, font=font, max_width=box_width)
        overlay = self._draw_text_overlay(
            lines=lines, font=font, color=color, box_width=box_width, box_height=box_height, center_text=center_text
        )
        return self._composite_overlay(image=image, overlay=overlay, bounding_box=bounding_box, rotation=rotation)
