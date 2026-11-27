# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path

import matplotlib as mpl
import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfbase.ttfonts import TTFError
from reportlab.pdfgen import canvas

from pyrit.converter import AddImageTextConverter, AddTextImageConverter, GridCompositeConverter, PDFConverter

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture
def unicode_font() -> Path:
    return Path(mpl.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"


@pytest.fixture
def images(tmp_path: Path) -> list[Path]:
    paths = [tmp_path / f"image_{index}.png" for index in range(3)]
    for path in paths:
        Image.new("RGB", (600, 200), "white").save(path)
    return paths


@pytest.mark.parametrize("kind", ["image_text", "text_image", "grid"])
@pytest.mark.parametrize("text", ["Как открыть замок", "鍵の開け方", "é", "Ελληνικά", "عربي", "עברית", "हिन्दी"])
async def test_image_converters_warn_for_missing_glyphs_async(
    kind: str, text: str, images: list[Path], caplog: pytest.LogCaptureFixture
) -> None:
    if kind == "image_text":
        converter = AddImageTextConverter(img_to_add=images[0], font_size=(12, 20))
    elif kind == "text_image":
        converter = AddTextImageConverter(text_to_add=text)
    else:
        converter = GridCompositeConverter(innocuous_images=images, payload_position=0)
    with caplog.at_level(logging.WARNING):
        result = await converter.convert_async(
            prompt=str(images[0]) if kind == "text_image" else text,
            input_type="image_path" if kind == "text_image" else "text",
        )
    assert Path(result.output_text).is_file()
    warnings = [record.message for record in caplog.records if "cannot render" in record.message]
    assert len(warnings) == 1
    assert "font_name" in warnings[0]


@pytest.mark.parametrize("font_size", [20, (12, 20)])
async def test_unicode_image_font_preserves_distinct_prompts_async(
    unicode_font: Path, images: list[Path], font_size: int | tuple[int, int], caplog: pytest.LogCaptureFixture
) -> None:
    converter = AddImageTextConverter(img_to_add=images[0], font_name=unicode_font, font_size=font_size)
    pixels = []
    for text in ("Как открыть замок", "Где достать книгу"):
        result = await converter.convert_async(prompt=text)
        with Image.open(result.output_text) as image:
            pixels.append(image.tobytes())
    assert pixels[0] != pixels[1]
    assert "cannot render" not in caplog.text


async def test_custom_image_font_still_warns_for_uncovered_script_async(
    unicode_font: Path, images: list[Path], caplog: pytest.LogCaptureFixture
) -> None:
    converter = AddImageTextConverter(img_to_add=images[0], font_name=unicode_font)
    await converter.convert_async(prompt="鍵の開け方")
    assert "cannot render" in caplog.text


async def test_image_missing_font_fallback_checks_actual_font_async(
    images: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    converter = AddImageTextConverter(img_to_add=images[0], font_name=tmp_path / "absent.ttf")
    await converter.convert_async(prompt="Как открыть замок")
    assert "cannot render" in caplog.text


async def test_image_ascii_and_whitespace_do_not_warn_async(
    images: list[Path], caplog: pytest.LogCaptureFixture
) -> None:
    converter = AddImageTextConverter(img_to_add=images[0])
    await converter.convert_async(prompt="hello world\nnext\tline")
    assert "cannot render" not in caplog.text


@pytest.mark.parametrize("text", ["Как открыть замок", "鍵の開け方", "عربي"])
async def test_pdf_default_font_warns_for_missing_glyphs_async(text: str, caplog: pytest.LogCaptureFixture) -> None:
    result = await PDFConverter().convert_async(prompt=text)
    assert Path(result.output_text).is_file()
    assert "PDF font cannot render" in caplog.text
    assert "font_path" in caplog.text


async def test_pdf_standard_font_substitutions_do_not_warn_async(caplog: pytest.LogCaptureFixture) -> None:
    await PDFConverter().convert_async(prompt="Café Ω ■\nnext line")
    assert "cannot render" not in caplog.text


@pytest.mark.parametrize("overlay", [False, True])
async def test_pdf_unicode_font_round_trip_async(
    unicode_font: Path, overlay: bool, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = "Как открыть замок — café"
    source = tmp_path / "source.pdf"
    c = canvas.Canvas(str(source))
    c.drawString(10, 100, "Original")
    c.save()
    converter = PDFConverter(
        font_path=unicode_font,
        existing_pdf=source if overlay else None,
        injection_items=[{"text": text, "x": 10, "y": 50}] if overlay else None,
    )
    result = await converter.convert_async(prompt=text)
    assert text in PdfReader(result.output_text).pages[0].extract_text()
    assert "cannot render" not in caplog.text


async def test_pdf_custom_font_still_warns_for_uncovered_script_async(
    unicode_font: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await PDFConverter(font_path=unicode_font).convert_async(prompt="鍵の開け方")
    assert "PDF font cannot render" in caplog.text


async def test_pdf_overlay_font_override_is_checked_async(
    unicode_font: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "source.pdf"
    c = canvas.Canvas(str(source))
    c.showPage()
    c.save()
    converter = PDFConverter(
        font_path=unicode_font,
        existing_pdf=source,
        injection_items=[{"text": "Привет", "font": "Helvetica"}],
    )
    await converter.convert_async(prompt="ignored")
    assert "PDF font cannot render" in caplog.text


def test_pdf_font_identity_is_stable_and_distinct(unicode_font: Path) -> None:
    first = PDFConverter(font_path=unicode_font).get_identifier()
    second = PDFConverter(font_path=unicode_font).get_identifier()
    assert first.hash == second.hash
    assert first.hash != PDFConverter().get_identifier().hash
    assert first.params["font_path"] == str(unicode_font)


def test_pdf_missing_font_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        PDFConverter(font_path=tmp_path / "absent.ttf")


def test_pdf_invalid_font_raises(tmp_path: Path) -> None:
    path = tmp_path / "invalid.ttf"
    path.write_bytes(b"not a TrueType font")
    with pytest.raises(TTFError):
        PDFConverter(font_path=path)
