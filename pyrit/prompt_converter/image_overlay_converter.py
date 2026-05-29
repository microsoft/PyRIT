# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
from io import BytesIO

from PIL import Image

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.models.data_type_serializer import DataTypeSerializer
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class ImageOverlayConverter(PromptConverter):
    """
    Composites a prompt image (overlay) onto a base image at a specified position.

    The base image is configured via the constructor, while the overlay image is
    provided as the prompt input. This is useful for creating many variations
    of a scenario by layering different images on top of a base image (e.g.,
    placing a CAPTCHA image over an open locket in a photo of hands).
    """

    SUPPORTED_INPUT_TYPES = ("image_path",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    def __init__(
        self,
        *,
        base_image: str,
        position: tuple[int, int] = (0, 0),
        overlay_size: tuple[int, int] | None = None,
        opacity: float = 1.0,
    ) -> None:
        """
        Initialize the converter with base image and placement parameters.

        Args:
            base_image (str): File path of the base image onto which overlays will be placed.
            position (tuple[int, int]): (x, y) pixel coordinates on the base image where
                the top-left corner of the overlay will be placed. Defaults to (0, 0).
            overlay_size (tuple[int, int] | None): Optional (width, height) to resize the
                overlay image before compositing. Defaults to None (use original size).
            opacity (float): Opacity of the overlay from 0.0 (fully transparent) to 1.0
                (fully opaque). Defaults to 1.0.

        Raises:
            ValueError: If ``base_image`` is empty, ``opacity`` is outside [0.0, 1.0],
                or ``overlay_size`` contains non-positive dimensions.
        """
        if not base_image:
            raise ValueError("Please provide a valid base_image path")
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Opacity must be between 0.0 and 1.0")
        if overlay_size is not None and (len(overlay_size) != 2 or overlay_size[0] <= 0 or overlay_size[1] <= 0):
            raise ValueError("overlay_size must be a tuple of two positive integers (width, height)")

        self._base_image = base_image
        self._position = position
        self._overlay_size = overlay_size
        self._opacity = opacity

        mime_type = DataTypeSerializer.get_mime_type(self._base_image) or "image/png"
        image_type = mime_type.split("/")[-1].lower()
        # PIL/Pillow uses "JPEG" as the save format; normalize the file extension to "jpg"
        # to match the convention used by other image converters.
        self._save_format = "jpeg" if image_type in ("jpg", "jpeg") else image_type
        self._file_extension = "jpg" if image_type in ("jpg", "jpeg") else image_type

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with overlay converter parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "base_image": self._base_image,
                "position": self._position,
                "overlay_size": self._overlay_size,
                "opacity": self._opacity,
            }
        )

    def _composite_images(self, *, base: Image.Image, overlay: Image.Image) -> Image.Image:
        """
        Composite the overlay image onto the base image.

        Preserves the alpha channel when the output format supports it (PNG, WEBP).
        For formats without alpha support (JPEG), the result is flattened onto a
        white background so transparent regions render predictably.

        Args:
            base (Image.Image): The base image.
            overlay (Image.Image): The overlay image to place on the base.

        Returns:
            Image.Image: The composited result image.
        """
        if self._overlay_size is not None:
            overlay = overlay.resize(self._overlay_size, Image.Resampling.LANCZOS)

        overlay = overlay.convert("RGBA")

        if self._opacity < 1.0:
            alpha = overlay.getchannel("A")
            alpha = alpha.point(lambda a: int(a * self._opacity))
            overlay.putalpha(alpha)

        overlay_w, overlay_h = overlay.size
        x, y = self._position
        if x + overlay_w <= 0 or y + overlay_h <= 0 or x >= base.width or y >= base.height:
            logger.warning(
                f"Overlay at position {self._position} with size ({overlay_w}, {overlay_h}) "
                f"falls entirely outside the base image ({base.width}x{base.height}); "
                "result will be unchanged from the base."
            )

        base = base.convert("RGBA")
        base.paste(overlay, self._position, mask=overlay)

        if self._save_format == "jpeg":
            background = Image.new("RGB", base.size, (255, 255, 255))
            background.paste(base, mask=base.split()[-1])
            return background
        return base

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Overlay the prompt image onto the configured base image.

        Args:
            prompt (str): The file path of the overlay image to place on the base.
            input_type (PromptDataType): The type of input data. Must be "image_path".

        Returns:
            ConverterResult: The result containing the path to the composited image.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        base_serializer = data_serializer_factory(
            category="prompt-memory-entries", value=self._base_image, data_type="image_path"
        )
        overlay_serializer = data_serializer_factory(
            category="prompt-memory-entries", value=prompt, data_type="image_path"
        )

        base_bytes = await base_serializer.read_data()
        overlay_bytes = await overlay_serializer.read_data()

        base_img = Image.open(BytesIO(base_bytes))
        overlay_img = Image.open(BytesIO(overlay_bytes))

        result_img = self._composite_images(base=base_img, overlay=overlay_img)

        image_bytes = BytesIO()
        result_img.save(image_bytes, format=self._save_format)
        image_str = base64.b64encode(image_bytes.getvalue()).decode("utf-8")

        output_serializer = data_serializer_factory(
            category="prompt-memory-entries", data_type="image_path", extension=self._file_extension
        )
        await output_serializer.save_b64_image(data=image_str)
        return ConverterResult(output_text=str(output_serializer.value), output_type="image_path")
