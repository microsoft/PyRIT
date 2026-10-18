# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from PIL import Image

from pyrit.converter import AddImageTextConverter


@pytest.mark.parametrize("font_size", [0, -1])
def test_add_image_text_converter_rejects_non_positive_fixed_font_size(tmp_path, font_size):
    image_path = tmp_path / "test.png"
    Image.new("RGB", (32, 32)).save(image_path)

    with pytest.raises(ValueError, match="font_size must be greater than 0"):
        AddImageTextConverter(img_to_add=str(image_path), font_size=font_size)
