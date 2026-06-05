# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``blur_images`` flag across the pyrit.output module."""

import io
from pathlib import Path
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
    fake_serializer.read_data_async = AsyncMock(return_value=image_bytes)

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
    fake_serializer.read_data_async = AsyncMock(return_value=image_bytes)

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


def _resolved_chdir(monkeypatch, tmp_path: Path) -> Path:
    """``chdir`` into ``tmp_path`` and return the resolved path so callers can
    construct file paths that compare equal to ``Path.cwd()`` afterwards.

    macOS's ``/var`` -> ``/private/var`` symlink causes ``os.getcwd()`` (and
    therefore ``Path.cwd()``) to return the resolved form, so unresolved
    ``tmp_path`` children would otherwise fail ``relative_to`` lookups.
    """
    work_dir = tmp_path.resolve()
    monkeypatch.chdir(work_dir)
    return work_dir


# --- Markdown path ---


def test_markdown_writes_blurred_sibling_and_links_to_it(tmp_path, monkeypatch):
    work_dir = _resolved_chdir(monkeypatch, tmp_path)
    image_bytes = _make_image_bytes()
    image_path = work_dir / "img.png"
    image_path.write_bytes(image_bytes)

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)
    lines = printer._format_image_content(image_path=str(image_path))

    blurred_path = work_dir / "img_blurred.png"
    assert blurred_path.exists()
    assert blurred_path.read_bytes() != image_bytes

    assert len(lines) == 1
    assert lines[0] == "![Image](img_blurred.png)\n"


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


def test_markdown_default_does_not_blur(tmp_path, monkeypatch):
    work_dir = _resolved_chdir(monkeypatch, tmp_path)
    image_bytes = _make_image_bytes()
    image_path = work_dir / "img.png"
    image_path.write_bytes(image_bytes)

    printer = _ConcreteMarkdown()
    lines = printer._format_image_content(image_path=str(image_path))

    blurred_path = work_dir / "img_blurred.png"
    assert not blurred_path.exists()
    assert lines[0] == "![Image](img.png)\n"


def test_markdown_blur_failure_emits_text_link_to_original(tmp_path, monkeypatch, caplog):
    # Point at a path that does not exist — blurring should fail gracefully and emit
    # a text link to the original (NOT an inline image of the original).
    work_dir = _resolved_chdir(monkeypatch, tmp_path)
    bogus_path = str(work_dir / "does_not_exist.png")

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)
    lines = printer._format_image_content(image_path=bogus_path)

    assert lines[0] == "[image (blur failed — original)](does_not_exist.png)\n"
    # Crucially, no inline-image rendering of the unblurred original
    assert not lines[0].startswith("!")


def test_markdown_format_image_content_handles_cross_drive_path(tmp_path):
    """``Path.relative_to`` raises ValueError when the path is not under cwd (e.g.,
    on Windows when paths are on a different drive). The formatter must fall back
    to the absolute path instead of propagating the error."""
    image_path = str(tmp_path / "img.png")

    printer = _ConcreteMarkdown()
    with patch("pathlib.Path.relative_to", side_effect=ValueError("cross-drive")):
        lines = printer._format_image_content(image_path=image_path)

    expected = str(Path(image_path).resolve()).replace("\\", "/")
    assert lines[0] == f"![Image]({expected})\n"


def test_markdown_format_link_path_falls_back_to_absolute_when_outside_cwd(tmp_path, monkeypatch):
    """Paths that are not under cwd must render as absolute paths (with POSIX
    separators) — ``Path.relative_to`` raises ``ValueError`` in that case and
    ``_format_link_path`` falls back to ``Path.resolve()``.

    This is a deliberate behavior change from the previous ``os.path.relpath``
    implementation, which would have produced a ``../../...`` chain.
    """
    inside_cwd = tmp_path / "cwd"
    inside_cwd.mkdir()
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    outside_path = outside_dir / "img.png"
    outside_path.write_bytes(b"")

    monkeypatch.chdir(inside_cwd)

    link = MarkdownConversationPrinter._format_link_path(str(outside_path))

    expected = str(outside_path.resolve()).replace("\\", "/")
    assert link == expected
    # Never produces ".." dot-dot relative paths in the fallback branch.
    assert ".." not in link


# --- Helpers / wiring ---


def test_pretty_attack_result_memory_printer_forwards_blur_flag(patch_central_database):
    from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter

    printer = PrettyAttackResultMemoryPrinter(blur_images=True, blur_radius=7)
    assert printer._conversation_printer._blur_images is True
    assert printer._conversation_printer._blur_radius == 7


def test_markdown_attack_result_memory_printer_forwards_blur_flag(patch_central_database):
    from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter

    printer = MarkdownAttackResultMemoryPrinter(blur_images=True, blur_radius=9, blurred_dir="/tmp/blurred")
    assert printer._conversation_printer._blur_images is True
    assert printer._conversation_printer._blur_radius == 9
    assert printer._conversation_printer._blurred_dir == "/tmp/blurred"


# --- Round 2: configurable destination ---


def test_markdown_blurred_dir_redirects_output(tmp_path, monkeypatch):
    work_dir = _resolved_chdir(monkeypatch, tmp_path)
    image_bytes = _make_image_bytes()
    image_path = work_dir / "src" / "img.png"
    image_path.parent.mkdir()
    image_path.write_bytes(image_bytes)
    blurred_dir = work_dir / "blurred"

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5, blurred_dir=str(blurred_dir))
    lines = printer._format_image_content(image_path=str(image_path))

    blurred_path = blurred_dir / "img_blurred.png"
    assert blurred_path.exists()
    # Original directory must not contain the blurred copy
    assert not (image_path.parent / "img_blurred.png").exists()
    assert lines[0] == "![Image](blurred/img_blurred.png)\n"


# --- Round 2: atomic write ---


def test_markdown_atomic_write_leaves_no_temp_on_failure(tmp_path, monkeypatch):
    work_dir = _resolved_chdir(monkeypatch, tmp_path)
    image_bytes = _make_image_bytes()
    image_path = work_dir / "img.png"
    image_path.write_bytes(image_bytes)

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)

    # Force os.replace to fail; the temp file should be cleaned up and a text link
    # to the original returned.
    with patch("pyrit.output.conversation.markdown.os.replace", side_effect=OSError("boom")):
        lines = printer._format_image_content(image_path=str(image_path))

    assert lines[0] == "[image (blur failed — original)](img.png)\n"

    # No temp files left behind, no blurred file produced
    leftovers = [p.name for p in work_dir.iterdir() if p.name != "img.png"]
    assert leftovers == [], f"Unexpected leftover files: {leftovers}"


# --- Round 2: original not modified ---


def test_markdown_blur_does_not_modify_original(tmp_path):
    image_bytes = _make_image_bytes()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(image_bytes)
    original_mtime = image_path.stat().st_mtime_ns

    printer = _ConcreteMarkdown(blur_images=True, blur_radius=5)
    printer._format_image_content(image_path=str(image_path))

    assert image_path.read_bytes() == image_bytes
    assert image_path.stat().st_mtime_ns == original_mtime


# --- Round 2: end-to-end via output_attack_async ---


async def test_output_attack_async_forwards_blur_to_markdown_printer():
    from pyrit.output import helpers

    fake_printer = MagicMock()
    fake_printer.write_async = AsyncMock()
    with patch.object(helpers, "MarkdownAttackResultMemoryPrinter", return_value=fake_printer) as cls:
        await helpers.output_attack_async(
            result=MagicMock(),
            format="markdown",
            blur_images=True,
            blur_radius=11,
            blurred_dir="/tmp/x",
        )

    kwargs = cls.call_args.kwargs
    assert kwargs["blur_images"] is True
    assert kwargs["blur_radius"] == 11
    assert kwargs["blurred_dir"] == "/tmp/x"


async def test_output_attack_async_forwards_blur_to_pretty_printer():
    from pyrit.output import helpers

    fake_printer = MagicMock()
    fake_printer.write_async = AsyncMock()
    with patch.object(helpers, "PrettyAttackResultMemoryPrinter", return_value=fake_printer) as cls:
        await helpers.output_attack_async(
            result=MagicMock(),
            format="pretty",
            blur_images=True,
            blur_radius=11,
        )

    kwargs = cls.call_args.kwargs
    assert kwargs["blur_images"] is True
    assert kwargs["blur_radius"] == 11
