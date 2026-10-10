# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Prompt normalization components for standardizing and converting prompts.

This module provides tools for normalizing prompts before sending them to targets,
including converter configurations and request handling.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.prompt_normalizer.converter_configuration import ConverterConfiguration
    from pyrit.prompt_normalizer.json_retry import send_json_with_retry_async
    from pyrit.prompt_normalizer.normalizer_request import NormalizerRequest
    from pyrit.prompt_normalizer.prompt_normalizer import PromptNormalizer

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "NormalizerRequest": "pyrit.prompt_normalizer.normalizer_request",
    "ConverterConfiguration": "pyrit.prompt_normalizer.converter_configuration",
    "PromptNormalizer": "pyrit.prompt_normalizer.prompt_normalizer",
    "send_json_with_retry_async": "pyrit.prompt_normalizer.json_retry",
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
