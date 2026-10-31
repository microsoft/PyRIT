# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import math
import string
from collections import Counter
from io import BytesIO
from pathlib import Path
from threading import get_ident
from unittest.mock import patch

import aiofiles
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from pyrit.converter import TextToImageConverter, get_converter_modalities
from pyrit.registry import ConverterRegistry


async def _read_image_async(path: str) -> Image.Image:
    async with aiofiles.open(path, "rb") as file:
        data = await file.read()
    with Image.open(BytesIO(data)) as image:
        assert image.format == "PNG"
        return image.convert("RGB")


def test_bundled_font_is_unmodified() -> None:
    data = TextToImageConverter._UGLY_GERRY_FONT_PATH.read_bytes()
    assert hashlib.sha256(data).hexdigest() == "38332905de7a8bcbda48038edd7cc5dfd26924b3991eebfc9f8ca84b0b35f1e4"
    font = ImageFont.truetype(BytesIO(data), size=64)
    assert font.getname() == ("Gerry", "Regular")
    for character in string.ascii_uppercase + "!',.?":
        assert font.getmask(character).getbbox() is not None


@pytest.mark.usefixtures("patch_central_database")
async def test_convert_async_renders_the_bundled_district_glyphs() -> None:
    converter = TextToImageConverter(font="ugly_gerry")
    result = await converter.convert_async(prompt="Hello, World!")
    with await _read_image_async(result.output_text) as actual:
        font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
        left, top, right, bottom = font.getbbox("HELLO, WORLD!")
        with Image.new("RGB", (right - left + 40, bottom - top + 40), "white") as expected:
            ImageDraw.Draw(expected).text((20 - left, 20 - top), "HELLO, WORLD!", fill="black", font=font)
            assert actual.size == expected.size
            assert actual.tobytes() == expected.tobytes()
        assert result.output_type == "image_path"
        assert ImageChops.difference(actual, Image.new("RGB", actual.size, "white")).getbbox() is not None


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("font", [None, "ugly_gerry"])
async def test_convert_async_uses_unique_files(concurrent: bool, font: str | None) -> None:
    converter = TextToImageConverter(font=font)
    if concurrent:
        results = await asyncio.gather(*(converter.convert_async(prompt="Same prompt") for _ in range(3)))
    else:
        results = [await converter.convert_async(prompt="Same prompt") for _ in range(3)]
    assert len({result.output_text for result in results}) == 3
    images = [await _read_image_async(result.output_text) for result in results]
    try:
        assert all(image.tobytes() == images[0].tobytes() for image in images)
    finally:
        for image in images:
            image.close()


@pytest.mark.usefixtures("patch_central_database")
async def test_convert_async_normalizes_case_and_line_endings() -> None:
    converter = TextToImageConverter(font="ugly_gerry")
    results = [
        await converter.convert_async(prompt=prompt)
        for prompt in ("hello\n\nworld!", "HELLO\r\n\r\nWORLD!", "Hello\r\rWorld!")
    ]
    images = [await _read_image_async(result.output_text) for result in results]
    try:
        assert all(image.tobytes() == images[0].tobytes() for image in images)
    finally:
        for image in images:
            image.close()


@pytest.mark.parametrize("prompt", ["", " ", "\n\r"])
async def test_convert_async_rejects_blank_prompt(prompt: str) -> None:
    with pytest.raises(ValueError, match="valid text"):
        await TextToImageConverter().convert_async(prompt=prompt)


@pytest.mark.parametrize("character", ["0", "9", ":", "-", "\t", "\u00e9", "\u00df", "\U0001f600", "\x00"])
async def test_convert_async_rejects_unsupported_characters(character: str) -> None:
    with pytest.raises(ValueError, match="does not support"):
        await TextToImageConverter(font="ugly_gerry").convert_async(prompt=f"Hello{character}")


async def test_convert_async_rejects_non_text_input() -> None:
    with pytest.raises(ValueError, match="Input type image_path not supported"):
        await TextToImageConverter().convert_async(prompt="test.png", input_type="image_path")


@pytest.mark.parametrize(
    "parameters",
    [
        {"font_size": 0},
        {"font_size": -1},
        {"font_size": 1.5},
        {"font_size": True},
        {"max_width": 0},
        {"max_width": 40, "padding": 20},
        {"padding": -1},
        {"color": (256, 0, 0)},
        {"background_color": (0, 0)},
        {"color": (255, 255, 255)},
    ],
)
def test_init_rejects_invalid_parameters(parameters: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TextToImageConverter(**parameters)  # type: ignore[arg-type]


def test_init_does_not_fall_back_when_bundled_font_is_missing(tmp_path: Path) -> None:
    with patch.object(TextToImageConverter, "_UGLY_GERRY_FONT_PATH", tmp_path / "missing.otf"):
        with pytest.raises(FileNotFoundError):
            TextToImageConverter(font="ugly_gerry")


def test_init_does_not_fall_back_when_bundled_font_is_corrupt(tmp_path: Path) -> None:
    font_path = tmp_path / "corrupt.otf"
    font_path.write_bytes(b"not a font")
    with patch.object(TextToImageConverter, "_UGLY_GERRY_FONT_PATH", font_path):
        with pytest.raises(OSError):
            TextToImageConverter(font="ugly_gerry")


@pytest.mark.parametrize(
    "text",
    ["W" * 50, "I" * 100, string.ascii_uppercase, "HI THERE\n\nWORLD!", "VV\nAA\nYY\nGG"],
)
@pytest.mark.parametrize("font_name", [None, "ugly_gerry"])
def test_wrapping_preserves_characters_and_fits_pixel_width(text: str, font_name: str | None) -> None:
    converter = TextToImageConverter(font=font_name, max_width=180)
    font = converter._load_font()
    lines = [line for line, _ in converter._iter_wrapped_lines(text=text, font=font)]
    assert "".join(lines) == text.replace("\n", "")
    assert all(font.getbbox(line)[2] - font.getbbox(line)[0] <= 140 for line in lines)
    with Image.open(BytesIO(converter._render_png(text))) as image:
        assert image.width <= 180
        assert image.height > 64
        with ImageChops.difference(image, Image.new("RGB", image.size, "white")) as ink:
            bbox = ink.getbbox()
            assert bbox is not None
            assert bbox[0] >= 20 and bbox[1] >= 20
            assert bbox[2] <= image.width - 20 and bbox[3] <= image.height - 20


def test_wrapping_measures_repeated_candidates_once() -> None:
    converter = TextToImageConverter(font="ugly_gerry")
    font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
    prompt = "A" * 10_000
    with patch.object(font, "getbbox", wraps=font.getbbox) as measure:
        wrapped = list(converter._iter_wrapped_lines(text=prompt, font=font))
    lines = [line for line, _ in wrapped]
    assert "".join(lines) == prompt
    assert lines[:-1] == ["A" * 14] * (len(lines) - 1)
    assert lines[-1] == "A" * 4
    counts = Counter(call.args[0] for call in measure.call_args_list)
    assert len(counts) == 15
    assert all(count == 1 for count in counts.values())


def test_wrapping_measurement_cache_is_bounded() -> None:
    converter = TextToImageConverter(font="ugly_gerry", max_width=122)
    font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
    with (
        patch.object(converter, "_MEASUREMENT_CACHE_SIZE", 2),
        patch.object(font, "getbbox", wraps=font.getbbox) as measure,
    ):
        lines = [line for line, _ in converter._iter_wrapped_lines(text="ABAB", font=font)]
    assert lines == ["A", "B", "A", "B"]
    counts = Counter(call.args[0] for call in measure.call_args_list)
    assert counts["A"] == 2
    assert counts["B"] == 2


def test_wrapping_measurement_cache_is_local_to_each_layout() -> None:
    converter = TextToImageConverter(font="ugly_gerry")
    font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
    with patch.object(font, "getbbox", wraps=font.getbbox) as measure:
        first = list(converter._iter_wrapped_lines(text="AAA", font=font))
        second = list(converter._iter_wrapped_lines(text="AAA", font=font))
    assert first == second
    counts = Counter(call.args[0] for call in measure.call_args_list)
    assert counts == {"A": 2, "AA": 2, "AAA": 2}


def test_rendering_reuses_measurements_for_completed_lines() -> None:
    converter = TextToImageConverter(font="ugly_gerry")
    font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
    with (
        patch.object(ImageFont, "truetype", return_value=font),
        patch.object(font, "getbbox", wraps=font.getbbox) as measure,
    ):
        converter._render_png("A" * 70)
    counts = Counter(call.args[0] for call in measure.call_args_list)
    assert len(counts) == 15
    assert all(count == 1 for count in counts.values())


def test_rendering_rejects_oversized_layout_before_consuming_all_lines() -> None:
    converter = TextToImageConverter(font="ugly_gerry")
    font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
    prompt = "A" * 10_000
    layout = converter._iter_wrapped_lines(text=prompt, font=font)
    with (
        patch.object(converter, "_iter_wrapped_lines", return_value=layout),
        patch.object(ImageFont, "truetype", return_value=font),
        patch.object(Image, "new", side_effect=AssertionError("Oversized images must not be allocated")),
    ):
        with pytest.raises(ValueError, match="exceeds 16 million pixels"):
            converter._render_png(prompt)
    remaining = list(layout)
    total_lines = math.ceil(len(prompt) / 14)
    assert 0 < len(remaining) < total_lines
    completed = total_lines - len(remaining)
    left, top, right, bottom = font.getbbox("A" * 14)
    width = right - left + 40
    line_height = bottom - top + 16
    assert width * (bottom - top + (completed - 2) * line_height + 40) <= converter._MAX_PIXELS
    assert width * (bottom - top + (completed - 1) * line_height + 40) > converter._MAX_PIXELS


@pytest.mark.parametrize("line_count", [1, 3])
@pytest.mark.parametrize("padding", [0, 20])
def test_rendering_preserves_the_exact_pixel_budget_boundary(line_count: int, padding: int) -> None:
    converter = TextToImageConverter(font="ugly_gerry", padding=padding)
    font = ImageFont.truetype(str(converter._UGLY_GERRY_FONT_PATH), size=64)
    left, top, right, bottom = font.getbbox("A")
    width = right - left + 2 * padding
    height = bottom - top + (line_count - 1) * (bottom - top + 16) + 2 * padding
    prompt = "\n".join(["A"] * line_count)
    with patch.object(converter, "_MAX_PIXELS", width * height):
        with Image.open(BytesIO(converter._render_png(prompt))) as image:
            assert image.size == (width, height)
    with patch.object(converter, "_MAX_PIXELS", width * height - 1):
        with pytest.raises(ValueError, match="exceeds 16 million pixels"):
            converter._render_png(prompt)


async def test_convert_async_rejects_width_smaller_than_one_glyph() -> None:
    with pytest.raises(ValueError, match="too small to render"):
        await TextToImageConverter(font="ugly_gerry", max_width=41).convert_async(prompt="W")


def test_rendering_keeps_custom_colors() -> None:
    converter = TextToImageConverter(color=(10, 20, 30), background_color=(240, 230, 220), padding=5)
    with Image.open(BytesIO(converter._render_png("HELLO"))) as image:
        assert image.getpixel((0, 0)) == (240, 230, 220)
        assert (10, 20, 30) in image.get_flattened_data()


async def test_convert_async_rejects_oversized_images_before_saving() -> None:
    converter = TextToImageConverter()
    with patch.object(converter, "_MAX_PIXELS", 1):
        with pytest.raises(ValueError, match="exceeds 16 million pixels"):
            await converter.convert_async(prompt="Hello")


@pytest.mark.usefixtures("patch_central_database")
async def test_convert_async_renders_off_the_event_loop() -> None:
    converter = TextToImageConverter()
    render = converter._render_png
    event_loop_thread = get_ident()
    worker_threads: list[int] = []

    def _render(text: str) -> bytes:
        worker_threads.append(get_ident())
        return render(text)

    with patch.object(converter, "_render_png", side_effect=_render):
        await converter.convert_async(prompt="Hello")
    assert worker_threads and all(thread != event_loop_thread for thread in worker_threads)


@pytest.mark.usefixtures("patch_central_database")
async def test_convert_async_propagates_storage_errors() -> None:
    converter = TextToImageConverter()
    with patch(
        "pyrit.converter.text_to_image_converter.data_serializer_factory", side_effect=OSError("storage failed")
    ):
        with pytest.raises(OSError, match="storage failed"):
            await converter.convert_async(prompt="Hello")


@pytest.mark.parametrize(
    "parameters",
    [
        {"font_size": 32},
        {"max_width": 600},
        {"padding": 0},
        {"color": (255, 0, 0)},
        {"background_color": (240, 240, 240)},
    ],
)
def test_identifier_includes_rendering_parameters(parameters: dict[str, object]) -> None:
    default = TextToImageConverter()
    configured = TextToImageConverter(**parameters)  # type: ignore[arg-type]
    assert configured.get_identifier().hash != default.get_identifier().hash
    for name, value in parameters.items():
        expected = list(value) if isinstance(value, tuple) else value
        assert configured.get_identifier().params[name] == expected


@pytest.mark.parametrize("font", [None, "default", "ugly_gerry", str(TextToImageConverter._UGLY_GERRY_FONT_PATH)])
def test_converter_is_discoverable_and_buildable(font: str | None) -> None:
    registry = ConverterRegistry()
    converter = registry.create_instance("TextToImageConverter", font=font, font_size=32)
    assert isinstance(converter, TextToImageConverter)
    assert converter.get_identifier().params["font_size"] == 32
    assert converter.get_identifier().params["font"] == ("default" if font is None else font)
    assert ("TextToImageConverter", ["text"], ["image_path"]) in get_converter_modalities()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("font", [None, "default"])
async def test_convert_async_default_font_preserves_case_and_supports_normal_text(font: str | None) -> None:
    prompt = "Mixed CASE: 123 - caf\u00e9"
    converter = TextToImageConverter(font=font)
    result = await converter.convert_async(prompt=prompt)
    pillow_font = ImageFont.load_default(size=64)
    left, top, right, bottom = pillow_font.getbbox(prompt)
    with (
        await _read_image_async(result.output_text) as actual,
        Image.new("RGB", (right - left + 40, bottom - top + 40), "white") as expected,
    ):
        ImageDraw.Draw(expected).text((20 - left, 20 - top), prompt, fill="black", font=pillow_font)
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
    assert result.output_type == "image_path"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("font", [None, "ugly_gerry"])
async def test_convert_async_normalizes_line_endings_for_each_font(font: str | None) -> None:
    converter = TextToImageConverter(font=font)
    results = [
        await converter.convert_async(prompt=prompt)
        for prompt in ("Hello\n\nWorld!", "Hello\r\n\r\nWorld!", "Hello\r\rWorld!")
    ]
    images = [await _read_image_async(result.output_text) for result in results]
    try:
        assert all(image.tobytes() == images[0].tobytes() for image in images)
    finally:
        for image in images:
            image.close()


@pytest.mark.usefixtures("patch_central_database")
async def test_convert_async_default_font_expands_tabs() -> None:
    converter = TextToImageConverter()
    result = await converter.convert_async(prompt="A\tB")
    with await _read_image_async(result.output_text) as actual:
        with Image.open(BytesIO(converter._render_png("A   B"))) as expected:
            assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("extension", [".ttf", ".otf", ".TTF", ".OTF"])
@pytest.mark.parametrize("as_string", [False, True])
@pytest.mark.usefixtures("patch_central_database")
async def test_convert_async_uses_local_font_without_gerry_restrictions(
    tmp_path: Path, extension: str, as_string: bool
) -> None:
    pillow_font = ImageFont.load_default(size=32)
    assert isinstance(pillow_font, ImageFont.FreeTypeFont)
    font_path = tmp_path / f"local{extension}"
    font_path.write_bytes(pillow_font.font_bytes)
    converter = TextToImageConverter(font=str(font_path) if as_string else font_path, font_size=32)
    prompt = "Mixed CASE: 123 - caf\u00e9"
    result = await converter.convert_async(prompt=prompt)
    left, top, right, bottom = pillow_font.getbbox(prompt)
    with (
        await _read_image_async(result.output_text) as actual,
        Image.new("RGB", (right - left + 40, bottom - top + 40), "white") as expected,
    ):
        ImageDraw.Draw(expected).text((20 - left, 20 - top), prompt, fill="black", font=pillow_font)
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
    assert converter.get_identifier().params["font"] == str(font_path)
    assert converter.get_identifier().params["font_sha256"] == hashlib.sha256(font_path.read_bytes()).hexdigest()


@pytest.mark.usefixtures("patch_central_database")
async def test_local_otf_does_not_apply_the_bundled_presets_validation() -> None:
    converter = TextToImageConverter(font=TextToImageConverter._UGLY_GERRY_FONT_PATH)
    result = await converter.convert_async(prompt="123")
    assert result.output_type == "image_path"


def test_init_does_not_fall_back_when_local_font_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        TextToImageConverter(font=tmp_path / "missing.ttf")


def test_init_does_not_fall_back_when_local_font_is_corrupt(tmp_path: Path) -> None:
    font_path = tmp_path / "corrupt.ttf"
    font_path.write_bytes(b"not a font")
    with pytest.raises(OSError):
        TextToImageConverter(font=font_path)


@pytest.mark.parametrize("font", ["", "unknown", "ugly-gerry", "font.woff"])
def test_init_rejects_unknown_presets_and_unsupported_font_files(font: str) -> None:
    with pytest.raises(ValueError, match="local .ttf or .otf path"):
        TextToImageConverter(font=font)


def test_default_font_does_not_require_the_bundled_font(tmp_path: Path) -> None:
    with patch.object(TextToImageConverter, "_UGLY_GERRY_FONT_PATH", tmp_path / "missing.otf"):
        with Image.open(BytesIO(TextToImageConverter()._render_png("Default 123"))) as image:
            assert image.format == "PNG"


def test_local_font_data_is_loaded_once_and_included_in_the_identifier(tmp_path: Path) -> None:
    font_path = tmp_path / "local.otf"
    font_path.write_bytes(TextToImageConverter._UGLY_GERRY_FONT_PATH.read_bytes())
    converter = TextToImageConverter(font=font_path)
    before = converter._render_png("Hello")
    original_hash = converter.get_identifier().hash
    pillow_font = ImageFont.load_default(size=64)
    assert isinstance(pillow_font, ImageFont.FreeTypeFont)
    font_path.write_bytes(pillow_font.font_bytes)
    replacement = TextToImageConverter(font=font_path)
    assert replacement.get_identifier().hash != original_hash
    assert replacement._render_png("Hello") != before
    font_path.unlink()
    assert converter._render_png("Hello") == before


def test_identifier_distinguishes_font_choices_and_normalizes_the_default_alias() -> None:
    default = TextToImageConverter().get_identifier()
    named_default = TextToImageConverter(font="default").get_identifier()
    bundled = TextToImageConverter(font="ugly_gerry").get_identifier()
    local = TextToImageConverter(font=TextToImageConverter._UGLY_GERRY_FONT_PATH).get_identifier()
    assert default.hash == named_default.hash
    assert len({default.hash, bundled.hash, local.hash}) == 3


@pytest.mark.parametrize("grapheme", ["e\u0301", "\U0001f469\u200d\U0001f4bb"])
def test_wrapping_keeps_grapheme_clusters_together(grapheme: str) -> None:
    font = ImageFont.load_default(size=64)
    assert isinstance(font, ImageFont.FreeTypeFont)
    left, _, right, _ = font.getbbox(grapheme)
    converter = TextToImageConverter(max_width=math.ceil(right - left), padding=0)
    lines = [line for line, _ in converter._iter_wrapped_lines(text=grapheme * 3, font=font)]
    assert lines == [grapheme] * 3
