# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
import string
import textwrap
import warnings
from io import BytesIO
from typing import cast

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class AddImageTextConverter(PromptConverter):
    """
    Adds text to an image and wraps the text into multiple lines if necessary.

    Supports optional bounding box placement, text rotation, centering, and
    automatic font sizing to fit text within a specified region. When no
    bounding_box is provided, text is placed at (x_pos, y_pos) and wraps
    to the image width (original behavior).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    def __init__(
        self,
        *args: str,
        img_to_add: str = "",
        font_name: str = "helvetica.ttf",
        color: tuple[int, int, int] = (0, 0, 0),
        font_size: int = 15,
        x_pos: int = 10,
        y_pos: int = 10,
        bounding_box: tuple[int, int, int, int] | None = None,
        rotation: float = 0.0,
        center_text: bool = False,
        auto_font_size: bool = False,
        min_font_size: int = 10,
    ):
        """
        Initialize the converter with the image file path and text properties.

        Args:
            *args: Deprecated positional argument for img_to_add. Use img_to_add=... instead.
            img_to_add (str): File path of image to add text to.
            font_name (str): Path of font to use. Must be a TrueType font (.ttf). Defaults to "helvetica.ttf".
            color (tuple[int, int, int]): Color to print text in, using RGB values. Defaults to (0, 0, 0).
            font_size (int): Size of font to use. When auto_font_size is True, this is the maximum size.
                Defaults to 15.
            x_pos (int): X coordinate to place text in (ignored when bounding_box is set). Defaults to 10.
            y_pos (int): Y coordinate to place text in (ignored when bounding_box is set). Defaults to 10.
            bounding_box (tuple[int, int, int, int] | None): Optional (x1, y1, x2, y2) region to constrain
                text within. When set, text wraps within the box width and x_pos/y_pos are ignored.
                Defaults to None.
            rotation (float): Rotation angle in degrees for the text. Only used with bounding_box.
                Defaults to 0.0.
            center_text (bool): Whether to center text horizontally and vertically within the bounding box.
                Defaults to False.
            auto_font_size (bool): Whether to automatically shrink font size to fit text in the bounding box.
                Shrinks from font_size down to min_font_size. Defaults to False.
            min_font_size (int): Minimum font size when auto_font_size is True. Defaults to 10.

        Raises:
            TypeError: If more than one positional argument is passed, or if img_to_add
                is passed as both positional and keyword argument.
            ValueError: If img_to_add is empty, font_name doesn't end with ".ttf",
                or bounding_box coordinates are invalid.
        """
        if args:
            if len(args) > 1:
                raise TypeError(f"AddImageTextConverter() takes at most 1 positional argument, got {len(args)}")
            if img_to_add:
                raise TypeError("Cannot pass img_to_add as both positional and keyword argument")
            warnings.warn(
                "Passing 'img_to_add' as a positional argument is deprecated. "
                "Use img_to_add=... as a keyword argument. "
                "It will be keyword-only starting in version 0.13.0.",
                FutureWarning,
                stacklevel=2,
            )
            img_to_add = args[0]
        if not img_to_add:
            raise ValueError("Please provide valid image path")
        if not font_name.endswith(".ttf"):
            raise ValueError("The specified font must be a TrueType font with a .ttf extension")
        if bounding_box is not None:
            x1, y1, x2, y2 = bounding_box
            if x2 <= x1 or y2 <= y1:
                raise ValueError("bounding_box must have x2 > x1 and y2 > y1")
        self._img_to_add = img_to_add
        self._font_name = font_name
        self._font_size = font_size
        self._font_load_failed = False
        self._font = self._load_font()
        self._color = color
        self._x_pos = x_pos
        self._y_pos = y_pos
        self._bounding_box = bounding_box
        self._rotation = rotation
        self._center_text = center_text
        self._auto_font_size = auto_font_size
        self._min_font_size = min_font_size

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with image and text parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        params: dict[str, object] = {
            "img_to_add_path": str(self._img_to_add),
            "font_name": self._font_name,
            "color": self._color,
            "font_size": self._font_size,
            "x_pos": self._x_pos,
            "y_pos": self._y_pos,
        }
        if self._bounding_box:
            params["bounding_box"] = self._bounding_box
            params["rotation"] = self._rotation
            params["center_text"] = self._center_text
            params["auto_font_size"] = self._auto_font_size
            params["min_font_size"] = self._min_font_size
        return self._create_identifier(params=params)

    def _load_font(self) -> FreeTypeFont:
        """
        Load the font at self._font_size.

        Returns:
            FreeTypeFont: The loaded font object. Falls back to the default font on error.
        """
        return self._load_font_at_size(self._font_size)

    def _load_font_at_size(self, size: int) -> FreeTypeFont:
        """
        Load the font at a specific size.

        Args:
            size (int): The font size to load.

        Returns:
            FreeTypeFont: The loaded font object. Falls back to the default font on error.
        """
        if self._font_load_failed:
            return cast("FreeTypeFont", ImageFont.load_default())
        try:
            return ImageFont.truetype(self._font_name, size)
        except OSError:
            logger.warning(f"Cannot open font resource: {self._font_name}. Using default font.")
            self._font_load_failed = True
            return cast("FreeTypeFont", ImageFont.load_default())

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

    def _fit_text_to_box(self, *, text: str, box_width: int, box_height: int) -> tuple[FreeTypeFont, list[str]]:
        """
        Auto-size font from font_size down to min_font_size until text fits in the box.

        Args:
            text (str): The text to fit.
            box_width (int): The box width in pixels.
            box_height (int): The box height in pixels.

        Returns:
            tuple[FreeTypeFont, list[str]]: The chosen font and wrapped text lines.
        """
        usable_width = int(box_width * 0.95)
        usable_height = int(box_height * 0.95)

        for size in range(self._font_size, self._min_font_size - 1, -1):
            font = self._load_font_at_size(size)
            lines = self._wrap_text(text=text, font=font, max_width=usable_width)
            line_height = self._get_line_height(font=font)
            if len(lines) * line_height <= usable_height:
                return font, lines

        min_font = self._load_font_at_size(self._min_font_size)
        lines = self._wrap_text(text=text, font=min_font, max_width=usable_width)
        return min_font, lines

    def _render_text_in_bounding_box(self, *, image: Image.Image, text: str) -> Image.Image:
        """
        Render text within a bounding box with optional rotation and centering.

        Args:
            image (Image.Image): The base image to render text onto.
            text (str): The text to render.

        Returns:
            Image.Image: The image with text rendered in the bounding box.

        Raises:
            RuntimeError: If bounding_box is not set.
        """
        if self._bounding_box is None:
            raise RuntimeError("_render_text_in_bounding_box called without bounding_box")
        x1, y1, x2, y2 = self._bounding_box
        box_width = x2 - x1
        box_height = y2 - y1

        font, lines = self._resolve_font_and_lines(text=text, box_width=box_width, box_height=box_height)
        overlay = self._draw_text_overlay(lines=lines, font=font, box_width=box_width, box_height=box_height)
        return self._composite_overlay(image=image, overlay=overlay, x1=x1, y1=y1, x2=x2, y2=y2)

    def _resolve_font_and_lines(self, *, text: str, box_width: int, box_height: int) -> tuple[FreeTypeFont, list[str]]:
        """
        Choose font and wrap text lines based on auto_font_size setting.

        Args:
            text (str): The text to process.
            box_width (int): The box width in pixels.
            box_height (int): The box height in pixels.

        Returns:
            tuple[FreeTypeFont, list[str]]: The font and wrapped lines.
        """
        if self._auto_font_size:
            return self._fit_text_to_box(text=text, box_width=box_width, box_height=box_height)
        return self._font, self._wrap_text(text=text, font=self._font, max_width=box_width)

    def _draw_text_overlay(
        self, *, lines: list[str], font: FreeTypeFont, box_width: int, box_height: int
    ) -> Image.Image:
        """
        Draw text lines onto a transparent RGBA overlay image.

        Args:
            lines (list[str]): The text lines to draw.
            font (FreeTypeFont): The font to use.
            box_width (int): The overlay width.
            box_height (int): The overlay height.

        Returns:
            Image.Image: The RGBA overlay with rendered text.
        """
        overlay = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        fill_color = self._color + (255,)

        line_height = self._get_line_height(font=font)
        total_height = len(lines) * line_height
        y_start = (box_height - total_height) // 2 if self._center_text else 0

        for i, line in enumerate(lines):
            line_y = y_start + i * line_height
            if self._center_text:
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
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> Image.Image:
        """
        Optionally rotate the overlay and paste it onto the base image.

        Args:
            image (Image.Image): The base image.
            overlay (Image.Image): The text overlay.
            x1 (int): Left coordinate of the bounding box.
            y1 (int): Top coordinate of the bounding box.
            x2 (int): Right coordinate of the bounding box.
            y2 (int): Bottom coordinate of the bounding box.

        Returns:
            Image.Image: The composited image.
        """
        if self._rotation != 0:
            overlay = overlay.rotate(self._rotation, expand=True, resample=Image.Resampling.BICUBIC)
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

    def _add_text_to_image(self, text: str) -> Image.Image:
        """
        Add wrapped text to the image at ``self._img_to_add``.

        Args:
            text (str): The text to add to the image.

        Returns:
            Image.Image: The image with added text.

        Raises:
            ValueError: If ``text`` is empty.
        """
        if not text:
            raise ValueError("Please provide valid text value")

        image = Image.open(self._img_to_add)

        if self._bounding_box:
            return self._render_text_in_bounding_box(image=image, text=text)

        # Original behavior: place text at (x_pos, y_pos) and wrap to image width
        draw = ImageDraw.Draw(image)
        margin = 5
        max_width_pixels = image.size[0] - margin

        alphabet_letters = string.ascii_letters
        bbox = draw.textbbox((0, 0), alphabet_letters, font=self._font)
        avg_char_width = (bbox[2] - bbox[0]) / len(alphabet_letters)
        max_chars_per_line = int(max_width_pixels // avg_char_width)

        wrapped_text = textwrap.fill(text, width=max_chars_per_line)

        y_offset = float(self._y_pos)
        for line in wrapped_text.split("\n"):
            draw.text((self._x_pos, y_offset), line, font=self._font, fill=self._color)
            bbox = draw.textbbox((self._x_pos, y_offset), line, font=self._font)
            line_height = bbox[3] - bbox[1]
            y_offset += line_height

        return image

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by adding it as text to the image.

        Args:
            prompt (str): The text to be added to the image.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing path to the updated image.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        img_serializer = data_serializer_factory(
            category="prompt-memory-entries", value=self._img_to_add, data_type="image_path"
        )

        # Add text to the image
        updated_img = self._add_text_to_image(text=prompt)

        image_bytes = BytesIO()
        mime_type = img_serializer.get_mime_type(self._img_to_add)
        image_type = mime_type.split("/")[-1]
        updated_img.save(image_bytes, format=image_type)
        image_str = base64.b64encode(image_bytes.getvalue())
        # Save image as generated UUID filename
        await img_serializer.save_b64_image(data=image_str)
        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
