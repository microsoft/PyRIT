# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Internal image utilities for the output module.

Used by pretty and markdown conversation printers to apply a Gaussian blur
to images before they are displayed to a reviewer (the ``blur_images`` flag).
"""

import io
import logging

logger = logging.getLogger(__name__)


def blur_image_bytes(*, image_bytes: bytes, radius: int = 20) -> bytes:
    """
    Apply a Gaussian blur to the given image bytes and return blurred PNG bytes.

    Args:
        image_bytes (bytes): The original encoded image bytes.
        radius (int): The Gaussian blur radius. Larger values blur more.
            Defaults to 20.

    Returns:
        bytes: The blurred image encoded as PNG. If blurring fails for any reason,
        returns the original ``image_bytes`` unchanged and logs a warning.
    """
    try:
        from PIL import Image, ImageFilter

        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            blurred = image.filter(ImageFilter.GaussianBlur(radius=radius))
            buffer = io.BytesIO()
            blurred.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception as exc:
        logger.warning(f"Failed to blur image (radius={radius}); returning original bytes. Error: {exc}")
        return image_bytes
