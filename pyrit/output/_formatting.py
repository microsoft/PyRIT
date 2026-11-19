# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from colorama import Fore, Style

from pyrit.common.text_helper import escape_control_characters


class _PrettyPrinterMixin:
    """Shared ANSI line formatting and report framing for pretty printers."""

    _enable_colors: bool
    _width: int

    def _render_boxed_header(self, title: str) -> str:
        """
        Render a centered title between separators.

        Args:
            title (str): The report title.

        Returns:
            str: The rendered header.
        """
        return "".join(
            [
                "\n",
                self._format_colored("=" * self._width, Fore.CYAN),
                self._format_colored(title.center(self._width), Style.BRIGHT, Fore.CYAN),
                self._format_colored("=" * self._width, Fore.CYAN),
            ]
        )

    def _render_section_header(self, title: str) -> str:
        """
        Render a section header with visual separation.

        Args:
            title (str): The section title.

        Returns:
            str: The rendered section header.
        """
        return "".join(
            [
                "\n",
                self._format_colored(f"▼ {title}", Style.BRIGHT, Fore.CYAN),
                self._format_colored("─" * self._width, Fore.CYAN),
            ]
        )

    def _render_footer(self) -> str:
        """
        Render the closing separator.

        Returns:
            str: The rendered footer.
        """
        return "".join(["\n", self._format_colored("=" * self._width, Fore.CYAN), "\n"])

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

    @staticmethod
    def _get_rate_color(rate: int) -> str:
        """
        Get color based on success rate.

        Args:
            rate (int): Success rate percentage (0-100).

        Returns:
            str: Colorama color constant.
        """
        if rate >= 75:
            return str(Fore.RED)
        if rate >= 50:
            return str(Fore.YELLOW)
        if rate >= 25:
            return str(Fore.CYAN)
        return str(Fore.GREEN)
