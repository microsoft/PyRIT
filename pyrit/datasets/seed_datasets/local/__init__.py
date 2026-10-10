# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Local dataset loaders with automatic discovery.

Automatically discovers and registers all YAML dataset files from the seed_datasets directory.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.datasets.seed_datasets.local.jailbreak_dataset import _JailbreakTemplatesDataset
    from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "_JailbreakTemplatesDataset": "pyrit.datasets.seed_datasets.local.jailbreak_dataset",
    "_LocalDatasetLoader": "pyrit.datasets.seed_datasets.local.local_dataset_loader",
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
