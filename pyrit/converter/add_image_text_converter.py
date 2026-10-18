# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import math
from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL.ImageFont import FreeTypeFont

from pyrit.common import get_mime_type
from pyrit.converter.base_image_text_converter import _BaseImageTextConverter
from pyrit.converter.converter import ConverterResult
from pyrit.memory import data_serializer_factory
from pyrit.models import ComponentIdentifier, PromptDataType


class AddImageTextConverter(_BaseImageTextConverter):
    """
    Adds text to an image and wraps the text into multiple lines if necessary.

    Supports optional bounding box placement, text rotation, centering, and
    automatic font sizing to fit text within a specified region. When no
    bounding_box is provided, the full image is used as the bounding box.

    Font size can be a fixed int or a (min, max) tuple for automatic sizing
    that shrinks from max down to min to fit text within the bounding box.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    _DEFAULT_MARGIN = 5

    def __init__(
        self,
        *,
        img_to_add: Path,
        font_name: Path | None = None,
        color: tuple[int, int, int] = (0, 0, 0),
        font_size: int | tuple[int, int] = 15,
        bounding_box: tuple[int, int, int, int] | None = None,
        rotation: float = 0.0,
        center_text: bool = False,
    ) -> None:
        """
        Initialize the converter with the image file path and text properties.

        Args:
            img_to_add (Path): File path of image to add text to.
            font_name (Path | None): Path of font to use. Must be a TrueType font (.ttf).
                Defaults to None which uses Pillow's built-in default font.
            color (tuple[int, int, int]): Color to print text in, using RGB values. Defaults to (0, 0, 0).
            font_size (int | tuple[int, int]): Font size as a fixed int, or a (min, max) tuple for automatic
                sizing that shrinks from max down to min to fit text in the bounding box. Defaults to 15.
            bounding_box (tuple[int, int, int, int] | None): Optional (x1, y1, x2, y2) region to constrain
                text within. When not set, the full image is used with a default margin.
                Defaults to None.
            rotation (float): Rotation angle in degrees for the text. Must be finite. Defaults to 0.0.
            center_text (bool): Whether to center text horizontally and vertically within the bounding box.
                Defaults to False.

        Raises:
            ValueError: If img_to_add is empty, font_name doesn't end with ".ttf",
                color is not a valid RGB tuple, font_size is invalid, bounding_box
                coordinates are invalid, or rotation is non-finite.
        """
        if not img_to_add:
            raise ValueError("Please provide valid image path")
        self._validate_font_name(font_name)
        self._validate_color(color)
        self._extract_font_size(font_size)
        if bounding_box is not None:
            x1, y1, x2, y2 = bounding_box
            if x2 <= x1 or y2 <= y1:
                raise ValueError("bounding_box must have x2 > x1 and y2 > y1")
        if not math.isfinite(rotation):
            raise ValueError(f"rotation must be finite, got {rotation}")
        self._img_to_add = str(img_to_add)
        self._font_name = str(font_name) if font_name is not None else None
        self._font_size = self._font_size_max
        self._font_load_failed = font_name is None
        self._font = self._load_font()
        self._color = color
        self._bounding_box = bounding_box
        self._rotation = rotation
        self._center_text = center_text
        # Load the base image once at construction time so the hot path (`_add_text_to_image`)
        # doesn't do file I/O or image decode on each `convert_async` call.
        with Image.open(self._img_to_add) as img:
            img.load()
            self._base_image = img.copy()

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
            "font_size_min": self._font_size_min,
            "font_size_max": self._font_size_max,
        }
        if self._bounding_box:
            params["bounding_box"] = self._bounding_box
        params["rotation"] = self._rotation
        params["center_text"] = self._center_text
        return self._create_identifier(params=params)

    def _load_font(self) -> FreeTypeFont:
        """
        Load the font at self._font_size.

        Returns:
            FreeTypeFont: The loaded font object. Falls back to the default font on error.
        """
        return self._load_font_at_size(self._font_size)

    def _add_text_to_image(self, text: str) -> Image.Image:
        """
        Add wrapped text to the image at `self._img_to_add`.

        Args:
            text (str): The text to add to the image.

        Returns:
            Image.Image: The image with added text.

        Raises:
            ValueError: If ``text`` is empty.
        """
        if not text:
            raise ValueError("Please provide valid text value")

        image = self._base_image.copy()

        if self._bounding_box:
            bounding_box = self._bounding_box
        else:
            # Default to full image with margin to preserve backward-compatible behavior
            margin = self._DEFAULT_MARGIN
            bounding_box = (10, 10, image.width - margin, image.height - margin)

        if self._auto_font_size:
            x1, y1, x2, y2 = bounding_box
            font, lines = self._fit_font_to_box(
                text=text,
                font_loader=self._load_font_at_size,
                min_size=self._font_size_min,
                max_size=self._font_size_max,
                box_width=x2 - x1,
                box_height=y2 - y1,
            )
            overlay = self._draw_text_overlay(
                lines=lines,
                font=font,
                color=self._color,
                box_width=x2 - x1,
                box_height=y2 - y1,
                center_text=self._center_text,
            )
            return self._composite_overlay(
                image=image, overlay=overlay, bounding_box=bounding_box, rotation=self._rotation
            )

        return self._render_text_on_image(
            image=image,
            text=text,
            font=self._font,
            color=self._color,
            bounding_box=bounding_box,
            center_text=self._center_text,
            rotation=self._rotation,
        )

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
        mime_type = get_mime_type(self._img_to_add) or "image/png"
        image_type = mime_type.split("/")[-1]
        updated_img.save(image_bytes, format=image_type)
        image_str = base64.b64encode(image_bytes.getvalue())
        # Save image as generated UUID filename
        await img_serializer.save_b64_image_async(data=image_str)
        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
