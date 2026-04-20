# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Abstract base class for remote question-answering dataset loaders.

Sibling of `_RemoteDatasetLoader` with a different return contract: concrete
subclasses produce `QuestionAnsweringDataset` (structured multiple-choice),
not `SeedDataset`. The two ABCs share the URL-fetch / disk-cache helpers in
`pyrit.datasets._remote_fetch` but keep separate cache namespaces via each
subclass's `cache_subdir` attribute.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Literal, Optional

from pyrit.datasets._remote_fetch import fetch_with_cache
from pyrit.models import QuestionAnsweringDataset


class _RemoteQADatasetLoader(ABC):
    """
    Abstract base class for remote question-answering dataset loaders.

    Subclasses must implement:
    - `dataset_name` property: human-readable name.
    - `fetch_dataset()`: fetch and return a `QuestionAnsweringDataset`.

    Subclasses may override `cache_subdir` to isolate their on-disk cache
    under `DB_DATA_PATH/<cache_subdir>`.
    """

    cache_subdir: str = "question-answer-entries"

    @property
    @abstractmethod
    def dataset_name(self) -> str:
        """Return the human-readable name of the dataset."""

    @abstractmethod
    def fetch_dataset(self, *, cache: bool = True) -> QuestionAnsweringDataset:
        """Fetch the dataset and return as a `QuestionAnsweringDataset`."""

    def _fetch_from_url(
        self,
        *,
        source: str,
        source_type: Literal["public_url", "file"] = "public_url",
        file_type: Optional[str] = None,
        cache: bool = True,
    ) -> List[Dict[str, str]]:
        """
        Fetch raw examples from a URL or local file using this subclass's cache namespace.

        Returns:
            The parsed examples.
        """
        return fetch_with_cache(
            source=source,
            source_type=source_type,
            file_type=file_type,
            cache_subdir=self.cache_subdir,
            cache=cache,
        )
