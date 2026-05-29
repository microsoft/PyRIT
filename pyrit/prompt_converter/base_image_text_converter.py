# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string
import textwrap

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont

from pyrit.prompt_converter.prompt_converter import PromptConverter


class _BaseImageTextConverter(PromptConverter):
    """
    Base class with shared text-on-image rendering utilities.

    Provides word wrapping, line height measurement, overlay drawing,
    and compositing used by both AddImageTextConverter and AddTextImageConverter.
    """

    _DEFAULT_MARGIN: int = 5

    def _wrap_text(self, *, text: str, font: FreeTypeFont, max_width: int) -> list[str]:
        """
        Word-wrap text to fit within max_width pixels.

        Args:
            text (str): The text to wrap.
            font (FreeTypeFont): The font used for measuring text width.
            max_width (int): The maximum width in pixels for each line.

        Returns:
            list[str]: The wrapped text lines.
        """
        temp_img = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(temp_img)
        bbox = draw.textbbox((0, 0), string.ascii_letters, font=font)
        avg_char_width = (bbox[2] - bbox[0]) / len(string.ascii_letters)
        max_chars = max(1, int(max_width / avg_char_width))
        wrapped = textwrap.fill(text, width=max_chars)
        return wrapped.split("\n")

    def _get_line_height(self, *, font: FreeTypeFont) -> int:
        """
        Get the line height in pixels for a given font.

        Args:
            font (FreeTypeFont): The font to measure.

        Returns:
            int: The line height in pixels.
        """
        temp_img = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(temp_img)
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return int(bbox[3] - bbox[1])

    def _draw_text_overlay(
        self,
        *,
        lines: list[str],
        font: FreeTypeFont,
        color: tuple[int, int, int],
        box_width: int,
        box_height: int,
        center_text: bool = False,
    ) -> Image.Image:
        """
        Draw text lines onto a transparent RGBA overlay image.

        Args:
            lines (list[str]): The text lines to draw.
            font (FreeTypeFont): The font to use.
            color (tuple[int, int, int]): RGB color for the text.
            box_width (int): The overlay width.
            box_height (int): The overlay height.
            center_text (bool): Whether to center text horizontally and vertically. Defaults to False.

        Returns:
            Image.Image: The RGBA overlay with rendered text.
        """
        overlay = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        fill_color = color + (255,)

        line_height = self._get_line_height(font=font)
        total_height = len(lines) * line_height
        y_start = (box_height - total_height) // 2 if center_text else 0

        for i, line in enumerate(lines):
            line_y = y_start + i * line_height
            if center_text:
                line_bbox = draw.textbbox((0, 0), line, font=font)
                line_x = (box_width - (line_bbox[2] - line_bbox[0])) // 2
            else:
                line_x = 0
            draw.text((line_x, line_y), line, font=font, fill=fill_color)

        return overlay

    def _composite_overlay(
        self,
        *,
        image: Image.Image,
        overlay: Image.Image,
        bounding_box: tuple[int, int, int, int],
        rotation: float = 0.0,
    ) -> Image.Image:
        """
        Optionally rotate the overlay and paste it onto the base image.

        Args:
            image (Image.Image): The base image.
            overlay (Image.Image): The text overlay.
            bounding_box (tuple[int, int, int, int]): The (x1, y1, x2, y2) region.
            rotation (float): Rotation angle in degrees. Defaults to 0.0.

        Returns:
            Image.Image: The composited image.
        """
        x1, y1, x2, y2 = bounding_box
        if rotation != 0:
            overlay = overlay.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            paste_x = center_x - overlay.width // 2
            paste_y = center_y - overlay.height // 2
        else:
            paste_x = x1
            paste_y = y1

        image = image.convert("RGBA")
        image.paste(overlay, (paste_x, paste_y), overlay)
        return image.convert("RGB")

    def _render_text_on_image(
        self,
        *,
        image: Image.Image,
        text: str,
        font: FreeTypeFont,
        color: tuple[int, int, int],
        bounding_box: tuple[int, int, int, int],
        center_text: bool = False,
        rotation: float = 0.0,
    ) -> Image.Image:
        """
        Render text within a bounding box on an image.

        Wraps text, draws it on a transparent overlay, and composites
        onto the base image with optional centering and rotation.

        Args:
            image (Image.Image): The base image to render text onto.
            text (str): The text to render.
            font (FreeTypeFont): The font to use.
            color (tuple[int, int, int]): RGB color for the text.
            bounding_box (tuple[int, int, int, int]): The (x1, y1, x2, y2) region.
            center_text (bool): Whether to center text in the bounding box. Defaults to False.
            rotation (float): Rotation angle in degrees. Defaults to 0.0.

        Returns:
            Image.Image: The image with text rendered in the bounding box.
        """
        x1, y1, x2, y2 = bounding_box
        box_width = x2 - x1
        box_height = y2 - y1

        lines = self._wrap_text(text=text, font=font, max_width=box_width)
        overlay = self._draw_text_overlay(
            lines=lines, font=font, color=color, box_width=box_width, box_height=box_height, center_text=center_text
        )
        return self._composite_overlay(image=image, overlay=overlay, bounding_box=bounding_box, rotation=rotation)
