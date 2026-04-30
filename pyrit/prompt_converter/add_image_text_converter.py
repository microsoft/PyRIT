# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
import warnings
from io import BytesIO
from typing import cast

from PIL import Image, ImageFont
from PIL.ImageFont import FreeTypeFont

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.base_image_text_converter import _BaseImageTextConverter
from pyrit.prompt_converter.prompt_converter import ConverterResult

logger = logging.getLogger(__name__)

_UNSET = object()


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
        *args: str,
        img_to_add: str = "",
        font_name: str = "helvetica.ttf",
        color: tuple[int, int, int] = (0, 0, 0),
        font_size: int | tuple[int, int] = 15,
        x_pos: int = _UNSET,  # type: ignore[ty:invalid-assignment, ty:invalid-parameter-default]
        y_pos: int = _UNSET,  # type: ignore[ty:invalid-assignment, ty:invalid-parameter-default]
        bounding_box: tuple[int, int, int, int] | None = None,
        rotation: float = 0.0,
        center_text: bool = False,
    ) -> None:
        """
        Initialize the converter with the image file path and text properties.

        Args:
            *args: Deprecated positional argument for img_to_add. Use img_to_add=... instead.
                Will be removed in version 0.15.0.
            img_to_add (str): File path of image to add text to.
            font_name (str): Path of font to use. Must be a TrueType font (.ttf). Defaults to "helvetica.ttf".
            color (tuple[int, int, int]): Color to print text in, using RGB values. Defaults to (0, 0, 0).
            font_size (int | tuple[int, int]): Font size as a fixed int, or a (min, max) tuple for automatic
                sizing that shrinks from max down to min to fit text in the bounding box. Defaults to 15.
            x_pos (int): Deprecated. Use bounding_box instead. Will be removed in version 0.15.0.
            y_pos (int): Deprecated. Use bounding_box instead. Will be removed in version 0.15.0.
            bounding_box (tuple[int, int, int, int] | None): Optional (x1, y1, x2, y2) region to constrain
                text within. When not set, the full image is used with a default margin.
                Defaults to None.
            rotation (float): Rotation angle in degrees for the text. Defaults to 0.0.
            center_text (bool): Whether to center text horizontally and vertically within the bounding box.
                Defaults to False.

        Raises:
            TypeError: If more than one positional argument is passed, or if img_to_add
                is passed as both positional and keyword argument.
            ValueError: If img_to_add is empty, font_name doesn't end with ".ttf",
                font_size tuple is invalid, bounding_box coordinates are invalid,
                or x_pos/y_pos are used together with bounding_box.
        """
        if args:
            if len(args) > 1:
                raise TypeError(f"AddImageTextConverter takes at most 1 positional argument, got {len(args)}")
            if img_to_add:
                raise TypeError("Cannot pass img_to_add as both positional and keyword argument")
            warnings.warn(
                "Passing 'img_to_add' as a positional argument is deprecated. "
                "Use img_to_add=... as a keyword argument. "
                "It will be keyword-only starting in version 0.15.0.",
                FutureWarning,
                stacklevel=2,
            )
            img_to_add = args[0]
        if x_pos is not _UNSET or y_pos is not _UNSET:
            if bounding_box is not None:
                raise ValueError(
                    "Cannot pass x_pos/y_pos together with bounding_box. Use bounding_box=(x, y, x2, y2) instead."
                )
            warnings.warn(
                "x_pos and y_pos are deprecated. Use bounding_box=(x, y, x2, y2) instead. "
                "They will be removed in version 0.15.0.",
                FutureWarning,
                stacklevel=2,
            )
        # Resolve defaults after deprecation check
        if x_pos is _UNSET:
            x_pos = 10
        if y_pos is _UNSET:
            y_pos = 10
        if not img_to_add:
            raise ValueError("Please provide valid image path")
        if not font_name.endswith(".ttf"):
            raise ValueError("The specified font must be a TrueType font with a .ttf extension")
        self._extract_font_size(font_size)
        if bounding_box is not None:
            x1, y1, x2, y2 = bounding_box
            if x2 <= x1 or y2 <= y1:
                raise ValueError("bounding_box must have x2 > x1 and y2 > y1")
        self._img_to_add = img_to_add
        self._font_name = font_name
        self._font_size = self._font_size_max
        self._font_load_failed = False
        self._font = self._load_font()
        self._color = color
        self._x_pos = x_pos
        self._y_pos = y_pos
        self._bounding_box = bounding_box
        self._rotation = rotation
        self._center_text = center_text

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

    def _extract_font_size(self, font_size: int | tuple[int, int]) -> None:
        """
        Parse font_size into internal min/max/auto fields.

        Args:
            font_size (int | tuple[int, int]): Fixed size or (min, max) range.

        Raises:
            ValueError: If font_size tuple is invalid.
        """
        if isinstance(font_size, tuple):
            if len(font_size) != 2 or font_size[0] > font_size[1] or font_size[0] < 1:
                raise ValueError("font_size tuple must be (min, max) with 1 <= min <= max")
            self._font_size_min = font_size[0]
            self._font_size_max = font_size[1]
            self._auto_font_size = True
        else:
            self._font_size_min = font_size
            self._font_size_max = font_size
            self._auto_font_size = False

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
            FreeTypeFont: The loaded font object. Falls back to Pillow's built-in default font on error.
        """
        if self._font_load_failed:
            return cast("FreeTypeFont", ImageFont.load_default(size=size))
        try:
            return ImageFont.truetype(self._font_name, size)
        except OSError:
            logger.warning(f"Cannot open font resource: {self._font_name}. Using Pillow built-in default font.")
            self._font_load_failed = True
            return cast("FreeTypeFont", ImageFont.load_default(size=size))

    def _fit_text_to_box(self, *, text: str, box_width: int, box_height: int) -> tuple[FreeTypeFont, list[str]]:
        """
        Auto-size font from font_size_max down to font_size_min until text fits in the box.

        Args:
            text (str): The text to fit.
            box_width (int): The box width in pixels.
            box_height (int): The box height in pixels.

        Returns:
            tuple[FreeTypeFont, list[str]]: The chosen font and wrapped text lines.
        """
        usable_width = int(box_width * 0.95)
        usable_height = int(box_height * 0.95)

        for size in range(self._font_size_max, self._font_size_min - 1, -1):
            font = self._load_font_at_size(size)
            lines = self._wrap_text(text=text, font=font, max_width=usable_width)
            line_height = self._get_line_height(font=font)
            if len(lines) * line_height <= usable_height:
                return font, lines

        min_font = self._load_font_at_size(self._font_size_min)
        lines = self._wrap_text(text=text, font=min_font, max_width=usable_width)
        logger.warning(
            f"Text does not fit in bounding box ({box_width}x{box_height}) even at minimum font size "
            f"{self._font_size_min}. Text may be clipped."
        )
        return min_font, lines

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

        image = Image.open(self._img_to_add)

        if self._bounding_box:
            bounding_box = self._bounding_box
        else:
            # Default to full image with margin to preserve backward-compatible behavior
            margin = self._DEFAULT_MARGIN
            bounding_box = (self._x_pos, self._y_pos, image.width - margin, image.height - margin)

        if self._auto_font_size:
            x1, y1, x2, y2 = bounding_box
            font, lines = self._fit_text_to_box(text=text, box_width=x2 - x1, box_height=y2 - y1)
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
        mime_type = img_serializer.get_mime_type(self._img_to_add) or "image/png"
        image_type = mime_type.split("/")[-1]
        updated_img.save(image_bytes, format=image_type)
        image_str = base64.b64encode(image_bytes.getvalue())
        # Save image as generated UUID filename
        await img_serializer.save_b64_image(data=image_str)
        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
