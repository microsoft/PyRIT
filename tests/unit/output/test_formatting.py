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
