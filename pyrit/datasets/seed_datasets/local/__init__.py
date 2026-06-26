# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Local dataset loaders with automatic discovery.

Automatically discovers and registers all YAML dataset files from the seed_datasets directory.
"""

from pyrit.datasets.seed_datasets.local.jailbreak_dataset import (
    _JailbreakTemplatesDataset,
)
from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader

__all__ = [
    "_JailbreakTemplatesDataset",
    "_LocalDatasetLoader",
]
