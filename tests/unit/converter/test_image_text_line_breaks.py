# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Text drawn onto images keeps its own line breaks."""

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from PIL import Image

from pyrit.converter import AddImageTextConverter, AddTextImageConverter, GridCompositeConverter
from pyrit.converter.base_image_text_converter import _BaseImageTextConverter

ConverterKind = Literal["image_text_fixed", "image_text_auto", "text_image", "grid"]


@pytest.fixture
def wide_image(tmp_path: Path) -> str:
    path = str(tmp_path / "wide.png")
    Image.new("RGB", (1600, 800), color=(200, 200, 200)).save(path)
    return path


@pytest.fixture
def innocuous_images(tmp_path: Path) -> list[str]:
    paths = []
    for index in range(3):
        path = str(tmp_path / f"innocuous_{index}.png")
        Image.new("RGB", (120, 90), color=(90, 90, 90)).save(path)
        paths.append(path)
    return paths


async def _drawn_lines_async(
    *, kind: ConverterKind, text: str, wide_image: str, innocuous_images: list[str]
) -> list[str]:
    with patch.object(
        _BaseImageTextConverter,
        "_draw_text_overlay",
        autospec=True,
        side_effect=_BaseImageTextConverter._draw_text_overlay,
    ) as draw:
        if kind == "image_text_fixed":
            await AddImageTextConverter(img_to_add=Path(wide_image)).convert_async(prompt=text, input_type="text")
        elif kind == "image_text_auto":
            converter = AddImageTextConverter(img_to_add=Path(wide_image), font_size=(10, 20))
            await converter.convert_async(prompt=text, input_type="text")
        elif kind == "text_image":
            await AddTextImageConverter(text_to_add=text).convert_async(prompt=wide_image, input_type="image_path")
        else:
            converter = GridCompositeConverter(innocuous_images=innocuous_images, tile_size=(1200, 600))
            await converter.convert_async(prompt=text, input_type="text")
    lines = draw.call_args.kwargs["lines"]
    assert isinstance(lines, list)
    return [str(line) for line in lines]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("kind", ["image_text_fixed", "image_text_auto", "text_image", "grid"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Step 1\nStep 2", ["Step 1", "Step 2"]),
        ("Step 1\r\nStep 2", ["Step 1", "Step 2"]),
        ("Intro\n\nDone", ["Intro", "", "Done"]),
        ("Just one line", ["Just one line"]),
    ],
)
async def test_line_breaks_are_drawn_as_lines_async(
    *, kind: ConverterKind, text: str, expected: list[str], wide_image: str, innocuous_images: list[str]
) -> None:
    lines = await _drawn_lines_async(kind=kind, text=text, wide_image=wide_image, innocuous_images=innocuous_images)

    assert lines == expected


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("kind", ["image_text_fixed", "text_image"])
async def test_long_lines_still_wrap_after_a_break_async(
    *, kind: ConverterKind, wide_image: str, innocuous_images: list[str]
) -> None:
    long_line = " ".join(["word"] * 400)

    lines = await _drawn_lines_async(
        kind=kind, text=f"Title\n{long_line}", wide_image=wide_image, innocuous_images=innocuous_images
    )

    assert lines[0] == "Title"
    assert len(lines) > 2
    assert " ".join(lines[1:]) == long_line
