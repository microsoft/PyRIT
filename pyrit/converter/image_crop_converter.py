# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import math
from typing import Literal

from PIL import Image

from pyrit.converter.base_image_to_image_converter import BaseImageToImageConverter
from pyrit.models import ComponentIdentifier

logger = logging.getLogger(__name__)


class ImageCropConverter(BaseImageToImageConverter):
    """
    Crops an image to a rectangular region.

    The crop box is given as fractions of the image width and height, so one
    configuration works for images of any size. The defaults keep the central
    80% in each dimension. Cropping removes part of an image, such as surrounding
    context or part of an embedded text, which makes it useful for checking
    whether a target's answer or safety decision depends on seeing the whole image.

    References:
        https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.crop
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        left: float = 0.1,
        top: float = 0.1,
        right: float = 0.9,
        bottom: float = 0.9,
    ) -> None:
        """
        Initialize the converter with the specified crop box and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            left (float): Left edge of the crop box as a fraction of the image width.
            top (float): Top edge of the crop box as a fraction of the image height.
            right (float): Right edge of the crop box as a fraction of the image width.
            bottom (float): Bottom edge of the crop box as a fraction of the image height.
                The fractions must be finite and satisfy ``0 <= left < right <= 1``
                and ``0 <= top < bottom <= 1``. The defaults keep the central 80%
                in each dimension.

        Raises:
            ValueError: If unsupported output format is specified, or if the crop
                fractions are non-finite or do not form a valid box.
        """
        for name, value in (("left", left), ("top", top), ("right", right), ("bottom", bottom)):
            if not math.isfinite(value):
                raise ValueError(f"Crop fraction '{name}' must be finite, got {value}")
        if not 0 <= left < right <= 1:
            raise ValueError(f"Crop fractions must satisfy 0 <= left < right <= 1, got left={left}, right={right}")
        if not 0 <= top < bottom <= 1:
            raise ValueError(f"Crop fractions must satisfy 0 <= top < bottom <= 1, got top={top}, bottom={bottom}")
        self._left = left
        self._top = top
        self._right = right
        self._bottom = bottom
        super().__init__(output_format=output_format)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format and crop fraction parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "left": self._left,
                "top": self._top,
                "right": self._right,
                "bottom": self._bottom,
            },
        )

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Crop the image to the configured fractional box.

        Args:
            image (PIL.Image.Image): The image to crop.

        Returns:
            PIL.Image.Image: The cropped image, at least 1x1 pixel.

        Raises:
            ValueError: If the crop box rounds to an empty region for this image size.
        """
        box = (
            int(image.width * self._left),
            int(image.height * self._top),
            int(image.width * self._right),
            int(image.height * self._bottom),
        )
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            raise ValueError(
                f"Crop box {box} is empty for an image of size {image.size}; "
                "the cropped image must be at least 1x1 pixel"
            )
        return image.crop(box)
