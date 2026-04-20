# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.executors.question_answer.remote_qa_dataset_loader import (
    _RemoteQADatasetLoader,
)
from pyrit.models import QuestionAnsweringDataset


class _FakeQAContents(_RemoteQADatasetLoader):
    """Minimal concrete subclass used for ABC contract tests."""

    cache_subdir = "unit-test-qa-cache"

    @property
    def dataset_name(self) -> str:
        return "fake-qa"

    def fetch_dataset(self, *, cache: bool = True) -> QuestionAnsweringDataset:
        return QuestionAnsweringDataset(name="fake-qa", questions=[])


class TestRemoteQADatasetLoader:
    def test_cannot_instantiate_abc_directly(self):
        with pytest.raises(TypeError):
            _RemoteQADatasetLoader()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self):
        loader = _FakeQAContents()
        assert loader.dataset_name == "fake-qa"
        assert loader.cache_subdir == "unit-test-qa-cache"

    def test_default_cache_subdir_on_subclass_without_override(self):
        class _NoOverride(_RemoteQADatasetLoader):
            @property
            def dataset_name(self) -> str:
                return "no-override"

            def fetch_dataset(self, *, cache: bool = True) -> QuestionAnsweringDataset:
                return QuestionAnsweringDataset(name="no-override", questions=[])

        assert _NoOverride().cache_subdir == "question-answer-entries"

    def test_fetch_from_url_delegates_to_shared_helper(self):
        """`_fetch_from_url` must forward subclass's cache_subdir and args to `fetch_with_cache`."""
        loader = _FakeQAContents()
        with patch("pyrit.datasets.executors.question_answer.remote_qa_dataset_loader.fetch_with_cache") as mock_fetch:
            mock_fetch.return_value = [{"ok": True}]
            result = loader._fetch_from_url(source="https://example.com/x.jsonl", file_type="jsonl", cache=False)

        assert result == [{"ok": True}]
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["source"] == "https://example.com/x.jsonl"
        assert kwargs["file_type"] == "jsonl"
        assert kwargs["cache_subdir"] == "unit-test-qa-cache"
        assert kwargs["cache"] is False
        assert kwargs["source_type"] == "public_url"

    def test_does_not_register_with_seed_dataset_provider(self):
        """_RemoteQADatasetLoader must not pollute SeedDatasetProvider's registry."""
        from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider

        registered = SeedDatasetProvider.get_all_providers()
        assert "_FakeQAContents" not in registered
        assert "_RemoteQADatasetLoader" not in registered
