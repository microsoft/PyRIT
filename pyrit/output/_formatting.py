# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from colorama import Style

from pyrit.common.text_helper import escape_control_characters


class _PrettyPrinterMixin:
    """Shared ANSI line formatting for pretty printers."""

    _enable_colors: bool

    def _format_colored(self, text: str, *colors: str) -> str:
        """
        Format text with color codes if colors are enabled.

        Args:
            text (str): The text to format.
            *colors: Variable number of colorama color constants to apply.

        Returns:
            str: The formatted line with trailing newline.
        """
        # Escape the target's control characters before adding our own color codes.
        text = escape_control_characters(text)
        if self._enable_colors and colors:
            color_prefix = "".join(colors)
            return f"{color_prefix}{text}{Style.RESET_ALL}\n"
        return f"{text}\n"
