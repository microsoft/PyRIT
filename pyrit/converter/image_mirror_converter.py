# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from PIL import Image

from pyrit.converter.base_image_to_image_converter import BaseImageToImageConverter
from pyrit.models import ComponentIdentifier

logger = logging.getLogger(__name__)


class ImageMirrorConverter(BaseImageToImageConverter):
    """
    Mirrors an image horizontally or vertically.

    This converter uses PIL's Image.transpose to flip an image left-to-right
    (``horizontal``) or top-to-bottom (``vertical``). Mirrored text is harder for
    OCR-based content filters to read, while a multimodal model may still read it,
    which makes mirroring a useful perturbation for typographic image prompts.
    The output has the same size as the input.

    References:
        https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.transpose
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        direction: Literal["horizontal", "vertical"] = "horizontal",
    ) -> None:
        """
        Initialize the converter with the specified mirror direction and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            direction (Literal["horizontal", "vertical"]): The mirroring direction.
                "horizontal" flips the image left to right, "vertical" flips it
                top to bottom. Defaults to "horizontal".

        Raises:
            ValueError: If unsupported output format is specified, or if the direction
                is not "horizontal" or "vertical".
        """
        if direction not in ("horizontal", "vertical"):
            raise ValueError(f"Direction must be 'horizontal' or 'vertical', got {direction!r}")
        self._direction = direction
        super().__init__(output_format=output_format)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format and direction parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "direction": self._direction,
            },
        )

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Mirror the image in the configured direction.

        Args:
            image (PIL.Image.Image): The image to mirror.

        Returns:
            PIL.Image.Image: The mirrored image, with the same size as the input.
        """
        method = Image.Transpose.FLIP_LEFT_RIGHT if self._direction == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM
        return image.transpose(method)
