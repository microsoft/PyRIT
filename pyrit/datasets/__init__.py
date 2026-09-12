# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Dataset fetching and loading utilities for red teaming and safety evaluation datasets."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.datasets.jailbreak.text_jailbreak import TextJailBreak
    from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
    from pyrit.datasets.seed_datasets.seed_metadata import (
        SeedDatasetFilter,
        SeedDatasetLoadTime,
        SeedDatasetMetadata,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "SeedDatasetFilter": "pyrit.datasets.seed_datasets.seed_metadata",
    "SeedDatasetMetadata": "pyrit.datasets.seed_datasets.seed_metadata",
    "SeedDatasetLoadTime": "pyrit.datasets.seed_datasets.seed_metadata",
    "SeedDatasetProvider": "pyrit.datasets.seed_datasets.seed_dataset_provider",
    "TextJailBreak": "pyrit.datasets.jailbreak.text_jailbreak",
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
