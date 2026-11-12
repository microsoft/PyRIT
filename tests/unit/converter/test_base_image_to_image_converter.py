# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import threading
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.converter import ImageColorSaturationConverter, ImageResizingConverter, ImageRotationConverter
from pyrit.converter.base_image_to_image_converter import BaseImageToImageConverter


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), color=(125, 125, 125)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "converter",
    [ImageColorSaturationConverter(), ImageResizingConverter(scale_factor=0.5), ImageRotationConverter(angle=90)],
    ids=["saturation", "resizing", "rotation"],
)
async def test_image_transformation_runs_off_the_event_loop(converter: BaseImageToImageConverter) -> None:
    loop_thread_id = threading.get_ident()
    transform_thread_ids: list[int] = []
    transform_image = converter._transform_image

    def recording_transform(image: Image.Image, original_format: str) -> tuple[BytesIO, str]:
        transform_thread_ids.append(threading.get_ident())
        return transform_image(image, original_format)

    with (
        patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory") as mock_factory,
        patch.object(converter, "_transform_image", side_effect=recording_transform),
    ):
        mock_serializer = AsyncMock()
        mock_serializer.read_data_async.return_value = _png_bytes()
        mock_serializer.value = "converted.png"
        mock_factory.return_value = mock_serializer

        result = await converter.convert_async(prompt="input.png", input_type="image_path")

    assert result.output_text == "converted.png"
    mock_serializer.save_b64_image_async.assert_awaited_once()
    assert len(transform_thread_ids) == 1
    assert transform_thread_ids[0] != loop_thread_id
