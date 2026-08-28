# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Token smuggling converters that use Unicode-based techniques to hide, encode,
or obfuscate text content within prompts for security testing purposes.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.converter.token_smuggling.ascii_smuggler_converter import AsciiSmugglerConverter
    from pyrit.converter.token_smuggling.sneaky_bits_smuggler_converter import SneakyBitsSmugglerConverter
    from pyrit.converter.token_smuggling.variation_selector_smuggler_converter import VariationSelectorSmugglerConverter

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AsciiSmugglerConverter": "pyrit.converter.token_smuggling.ascii_smuggler_converter",
    "SneakyBitsSmugglerConverter": "pyrit.converter.token_smuggling.sneaky_bits_smuggler_converter",
    "VariationSelectorSmugglerConverter": "pyrit.converter.token_smuggling.variation_selector_smuggler_converter",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
