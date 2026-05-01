# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import hashlib
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


class AddTextImageConverter(_BaseImageTextConverter):
    """
    Adds a string to an image and wraps the text into multiple lines if necessary.

    This class is similar to :class:`AddImageTextConverter` except
    we pass in text as an argument to the constructor as opposed to an image file path.
    """

    SUPPORTED_INPUT_TYPES = ("image_path",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    def __init__(
        self,
        *args: str,
        text_to_add: str = "",
        font_name: str = "helvetica.ttf",
        color: tuple[int, int, int] = (0, 0, 0),
        font_size: int = 15,
        x_pos: int = 10,
        y_pos: int = 10,
    ) -> None:
        """
        Initialize the converter with the text and text properties.

        Args:
            *args: Deprecated positional argument for text_to_add. Use text_to_add=... instead.
                Will be removed in version 0.15.0.
            text_to_add (str): Text to add to an image.
            font_name (str): Path of font to use. Must be a TrueType font (.ttf). Defaults to "helvetica.ttf".
            color (tuple): Color to print text in, using RGB values. Defaults to (0, 0, 0).
            font_size (int): Size of font to use. Defaults to 15.
            x_pos (int): X coordinate to place text in (0 is left most). Defaults to 10.
            y_pos (int): Y coordinate to place text in (0 is upper most). Defaults to 10.

        Raises:
            TypeError: If more than one positional argument is passed, or if text_to_add
                is passed as both positional and keyword argument.
            ValueError: If ``text_to_add`` is empty, or if ``font_name`` does not end with ".ttf".
        """
        if args:
            if len(args) > 1:
                raise TypeError(f"AddTextImageConverter takes at most 1 positional argument, got {len(args)}")
            if text_to_add:
                raise TypeError("Cannot pass text_to_add as both positional and keyword argument")
            warnings.warn(
                "Passing 'text_to_add' as a positional argument is deprecated. "
                "Use text_to_add=... as a keyword argument. "
                "It will be keyword-only starting in version 0.15.0.",
                FutureWarning,
                stacklevel=2,
            )
            text_to_add = args[0]
        if text_to_add.strip() == "":
            raise ValueError("Please provide valid text_to_add value")
        if not font_name.endswith(".ttf"):
            raise ValueError("The specified font must be a TrueType font with a .ttf extension")
        self._text_to_add = text_to_add
        self._font_name = font_name
        self._font_size = font_size
        self._font = self._load_font()
        self._color = color
        self._x_pos = x_pos
        self._y_pos = y_pos

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with text and image parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        text_hash = hashlib.sha256(self._text_to_add.encode("utf-8")).hexdigest()[:16]
        return self._create_identifier(
            params={
                "text_to_add_hash": text_hash,
                "font_name": self._font_name,
                "color": self._color,
                "font_size": self._font_size,
                "x_pos": self._x_pos,
                "y_pos": self._y_pos,
            },
        )

    def _load_font(self) -> FreeTypeFont:
        """
        Load the font for a given font name and font size.

        Returns:
            FreeTypeFont: The loaded font object. Falls back to Pillow's built-in default font on error.
        """
        try:
            return ImageFont.truetype(self._font_name, self._font_size)
        except OSError:
            logger.warning(f"Cannot open font resource: {self._font_name}. Using Pillow built-in default font.")
            return cast("FreeTypeFont", ImageFont.load_default(size=self._font_size))

    def _add_text_to_image(self, image: Image.Image) -> Image.Image:
        """
        Add wrapped text to the image.

        Args:
            image (Image.Image): The image to which text will be added.

        Returns:
            Image.Image: The image with added text.
        """
        margin = self._DEFAULT_MARGIN
        bounding_box = (self._x_pos, self._y_pos, image.width - margin, image.height - margin)

        return self._render_text_on_image(
            image=image,
            text=self._text_to_add,
            font=self._font,
            color=self._color,
            bounding_box=bounding_box,
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Convert the given prompt (image) by adding text to it.

        Args:
            prompt (str): The image file path to which text will be added.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing path to the updated image.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        img_serializer = data_serializer_factory(category="prompt-memory-entries", value=prompt, data_type="image_path")

        # Open the image
        original_img_bytes = await img_serializer.read_data()
        original_img = Image.open(BytesIO(original_img_bytes))

        # Add text to the image
        updated_img = self._add_text_to_image(image=original_img)

        image_bytes = BytesIO()
        mime_type = img_serializer.get_mime_type(prompt) or "image/png"
        image_type = mime_type.split("/")[-1]
        updated_img.save(image_bytes, format=image_type)
        image_str = base64.b64encode(image_bytes.getvalue()).decode("utf-8")
        # Save image as generated UUID filename
        await img_serializer.save_b64_image(data=image_str)
        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
