# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from colorama import Fore, Style

from pyrit.output._formatting import _PrettyPrinterMixin


class _TestPrettyPrinter(_PrettyPrinterMixin):
    def __init__(self, *, enable_colors: bool) -> None:
        self._enable_colors = enable_colors


@pytest.mark.parametrize(
    "enable_colors,colors,expected",
    [
        (True, (Style.BRIGHT, Fore.RED), f"{Style.BRIGHT}{Fore.RED}text{Style.RESET_ALL}\n"),
        (False, (Style.BRIGHT, Fore.RED), "text\n"),
        (True, (), "text\n"),
    ],
)
def test_format_colored_preserves_line_output(
    enable_colors: bool,
    colors: tuple[str, ...],
    expected: str,
) -> None:
    printer = _TestPrettyPrinter(enable_colors=enable_colors)

    assert printer._format_colored("text", *colors) == expected


@pytest.mark.parametrize("enable_colors", [True, False])
def test_format_colored_escapes_control_characters(enable_colors: bool) -> None:
    printer = _TestPrettyPrinter(enable_colors=enable_colors)
    colors = (Fore.RED,) if enable_colors else ()
    prefix = f"{Fore.RED}" if enable_colors else ""
    suffix = f"{Style.RESET_ALL}" if enable_colors else ""

    # Tabs and newlines are kept: multi-line text is formatted as a single block elsewhere.
    rendered = printer._format_colored("before \x1b]8;;https://example.com\x07\tafter\nnext", *colors)

    assert rendered == f"{prefix}before \\x1b]8;;https://example.com\\x07\tafter\nnext{suffix}\n"
