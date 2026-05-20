# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``blur_images`` flag across the pyrit.output module."""

import io
import os
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from pyrit.models import MessagePiece, Score
from pyrit.output.conversation.markdown import MarkdownConversationPrinter
from pyrit.output.conversation.pretty import PrettyConversationMemoryPrinter


class _ConcreteMarkdown(MarkdownConversationPrinter):
    async def _get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        return []


def _make_image_bytes(*, multicolor: bool = True) -> bytes:
    image = Image.new("RGB", (32, 32), color=(0, 200, 0))
    if multicolor:
        for x in range(16):
            for y in range(32):
                image.putpixel((x, y), (200, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --- Pretty path ---


async def test_pretty_blurs_image_bytes_before_display(tmp_path, patch_central_database):
    image_bytes = _make_image_bytes()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(image_bytes)

    printer = PrettyConversationMemoryPrinter(blur_images=True, blur_radius=5)

    piece = MessagePiece(
        role="assistant",
        original_value=str(image_path),
        converted_value=str(image_path),
        converted_value_data_type="image_path",
    )

    fake_serializer = AsyncMock()
    fake_serializer.read_data = AsyncMock(return_value=image_bytes)

    with (
        patch("pyrit.common.notebook_utils.is_in_ipython_session", return_value=True),
        patch(
            "pyrit.models.data_type_serializer.ImagePathDataTypeSerializer",
            return_value=fake_serializer,
        ),
        patch(
            "pyrit.output._image_utils.blur_image_bytes",
            return_value=b"blurred-bytes",
        ) as mock_blur,
        patch.dict("sys.modules", {"IPython": MagicMock(), "IPython.display": MagicMock()}),
    ):
        import sys

        ipython_display = sys.modules["IPython.display"]
        await printer._display_image_async(piece)

    mock_blur.assert_called_once()
    assert mock_blur.call_args.kwargs["image_bytes"] == image_bytes
    assert mock_blur.call_args.kwargs["radius"] == 5
    ipython_display.Image.assert_called_once_with(data=b"blurred-bytes")


async def test_pretty_does_not_blur_by_default(tmp_path, patch_central_database):
    image_bytes = _make_image_bytes()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(image_bytes)

    printer = PrettyConversationMemoryPrinter()

    piece = MessagePiece(
        role="assistant",
        original_value=str(image_path),
        converted_value=str(image_path),
        converted_value_data_type="image_path",
    )

    fake_serializer = AsyncMock()
    fake_serializer.read_data = AsyncMock(return_value=image_bytes)

    with (
        patch("pyrit.common.notebook_utils.is_in_ipython_session", return_value=True),
        patch(
            "pyrit.models.data_type_serializer.ImagePathDataTypeSerializer",
            return_value=fake_serializer,
        ),
        patch(
            "pyrit.output._image_utils.blur_image_bytes",
            return_value=b"blurred-bytes",
        ) as mock_blur,
        patch.dict("sys.modules", {"IPython": MagicMock(), "IPython.display": MagicMock()}),
    ):
        import sys

        ipython_display = sys.modules["IPython.display"]
        await printer._display_image_async(piece)

    mock_blur.assert_not_called()
    ipython_display.Image.assert_called_once_with(data=image_bytes)


# --- Markdown path ---


def test_markdown_writes_blurred_sibling_and_links_to_it(tmp_path):
    image_bytes = _make_image_bytes()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(image_bytes)

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)
    lines = printer._format_image_content(image_path=str(image_path))

    blurred_path = tmp_path / "img_blurred.png"
    assert blurred_path.exists()
    assert blurred_path.read_bytes() != image_bytes

    assert len(lines) == 1
    expected_rel = os.path.relpath(str(blurred_path)).replace("\\", "/")
    assert lines[0] == f"![Image]({expected_rel})\n"


def test_markdown_blur_is_idempotent(tmp_path):
    image_bytes = _make_image_bytes()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(image_bytes)

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)
    printer._format_image_content(image_path=str(image_path))
    blurred_path = tmp_path / "img_blurred.png"
    first_bytes = blurred_path.read_bytes()
    first_mtime = blurred_path.stat().st_mtime_ns

    printer._format_image_content(image_path=str(image_path))
    assert blurred_path.read_bytes() == first_bytes
    # Existing file is reused — not rewritten
    assert blurred_path.stat().st_mtime_ns == first_mtime


def test_markdown_default_does_not_blur(tmp_path):
    image_bytes = _make_image_bytes()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(image_bytes)

    printer = _ConcreteMarkdown()
    lines = printer._format_image_content(image_path=str(image_path))

    blurred_path = tmp_path / "img_blurred.png"
    assert not blurred_path.exists()
    expected_rel = os.path.relpath(str(image_path)).replace("\\", "/")
    assert lines[0] == f"![Image]({expected_rel})\n"


def test_markdown_blur_failure_falls_back_to_original(tmp_path, caplog):
    # Point at a path that does not exist — blurring should fail gracefully.
    bogus_path = str(tmp_path / "does_not_exist.png")

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)
    lines = printer._format_image_content(image_path=bogus_path)

    expected_rel = os.path.relpath(bogus_path).replace("\\", "/")
    assert lines[0] == f"![Image]({expected_rel})\n"


# --- Helpers / wiring ---


def test_pretty_attack_result_memory_printer_forwards_blur_flag(patch_central_database):
    from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter

    printer = PrettyAttackResultMemoryPrinter(blur_images=True, blur_radius=7)
    assert printer._conversation_printer._blur_images is True
    assert printer._conversation_printer._blur_radius == 7


def test_markdown_attack_result_memory_printer_forwards_blur_flag(patch_central_database):
    from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter

    printer = MarkdownAttackResultMemoryPrinter(blur_images=True, blur_radius=9)
    assert printer._conversation_printer._blur_images is True
    assert printer._conversation_printer._blur_radius == 9
