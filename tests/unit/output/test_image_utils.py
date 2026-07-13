# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io

from PIL import Image

from pyrit.output._image_utils import blur_image_bytes


def _make_image_bytes(*, color: tuple[int, int, int] = (255, 0, 0), size: tuple[int, int] = (32, 32)) -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_blur_image_bytes_returns_png_bytes():
    original = _make_image_bytes()
    blurred = blur_image_bytes(image_bytes=original, radius=5)

    assert isinstance(blurred, bytes)
    assert len(blurred) > 0
    with Image.open(io.BytesIO(blurred)) as img:
        assert img.format == "PNG"
        assert img.size == (32, 32)


def test_blur_image_bytes_changes_bytes_for_two_color_image():
    # A two-color image will definitely produce different bytes after blurring.
    image = Image.new("RGB", (32, 32), color=(255, 0, 0))
    for x in range(16):
        for y in range(32):
            image.putpixel((x, y), (0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    original = buffer.getvalue()

    blurred = blur_image_bytes(image_bytes=original, radius=10)

    assert blurred != original


def test_blur_image_bytes_invalid_input_returns_original():
    junk = b"not-an-image"
    result = blur_image_bytes(image_bytes=junk, radius=5)
    assert result == junk
