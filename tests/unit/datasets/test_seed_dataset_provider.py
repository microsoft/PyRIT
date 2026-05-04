# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap
from dataclasses import fields as dc_fields
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader
from pyrit.datasets.seed_datasets.remote.darkbench_dataset import _DarkBenchDataset
from pyrit.datasets.seed_datasets.remote.harmbench_dataset import _HarmBenchDataset
from pyrit.datasets.seed_datasets.seed_metadata import (
    SeedDatasetFilter,
    SeedDatasetLoadTime,
    SeedDatasetMetadata,
)
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt


@pytest.fixture
def mock_harmbench_data():
    """Mock data for HarmBench dataset."""
    return [
        {
            "Behavior": "Test harmful behavior 1",
            "SemanticCategory": "chemical_biological",
        },
        {
            "Behavior": "Test harmful behavior 2",
            "SemanticCategory": "illegal_activity",
        },
    ]


@pytest.fixture
def mock_darkbench_data():
    """Mock data for DarkBench dataset."""
    return [
        {
            "Example": "Test dark pattern example 1",
            "Deceptive Pattern": "manipulative_design",
        },
        {
            "Example": "Test dark pattern example 2",
            "Deceptive Pattern": "forced_action",
        },
    ]


class TestSeedDatasetProvider:
    """Test the SeedDatasetProvider base class and registration."""

    def test_registration(self):
        """Test that subclasses are automatically registered."""

        # Define a dynamic class to avoid polluting registry permanently (though it will stay)
        class DynamicTestProvider(SeedDatasetProvider):
            @property
            def dataset_name(self):
                return "dynamic_test"

            async def fetch_dataset(self):
                return SeedDataset(seeds=[])

        providers = SeedDatasetProvider.get_all_providers()
        assert "DynamicTestProvider" in providers
        assert providers["DynamicTestProvider"] == DynamicTestProvider

    async def test_get_all_dataset_names(self):
        """Test getting all dataset names."""
        # Mock the registry to ensure deterministic results
        mock_provider_cls = MagicMock(__name__="TestProvider")
        mock_provider_instance = mock_provider_cls.return_value
        mock_provider_instance.dataset_name = "test_dataset"
        mock_provider_instance._parse_metadata = AsyncMock(return_value=None)

        with patch.dict(SeedDatasetProvider._registry, {"TestProvider": mock_provider_cls}, clear=True):
            names = await SeedDatasetProvider.get_all_dataset_names_async()
            assert names == ["test_dataset"]

    async def test_fetch_datasets_async(self):
        """Test fetching all datasets."""
        # Mock providers
        mock_provider1 = MagicMock(__name__="P1")
        mock_provider1.return_value.dataset_name = "d1"
        mock_provider1.return_value._parse_metadata = AsyncMock(return_value=None)
        mock_provider1.return_value.fetch_dataset = AsyncMock(
            return_value=SeedDataset(seeds=[SeedPrompt(value="p1", data_type="text")], dataset_name="d1")
        )

        mock_provider2 = MagicMock(__name__="P2")
        mock_provider2.return_value.dataset_name = "d2"
        mock_provider2.return_value._parse_metadata = AsyncMock(return_value=None)
        mock_provider2.return_value.fetch_dataset = AsyncMock(
            return_value=SeedDataset(seeds=[SeedPrompt(value="p2", data_type="text")], dataset_name="d2")
        )

        with patch.dict(SeedDatasetProvider._registry, {"P1": mock_provider1, "P2": mock_provider2}, clear=True):
            datasets = await SeedDatasetProvider.fetch_datasets_async()
            assert len(datasets) == 2

    async def test_fetch_datasets_async_with_filter(self):
        """Test fetching datasets with filter."""
        mock_provider1 = MagicMock(__name__="P1")
        mock_provider1.return_value.dataset_name = "d1"
        mock_provider1.return_value._parse_metadata = AsyncMock(return_value=None)
        mock_provider1.return_value.fetch_dataset = AsyncMock(
            return_value=SeedDataset(seeds=[SeedPrompt(value="p1", data_type="text")], dataset_name="d1")
        )

        mock_provider2 = MagicMock(__name__="P2")
        mock_provider2.return_value.dataset_name = "d2"
        mock_provider2.return_value._parse_metadata = AsyncMock(return_value=None)
        mock_provider2.return_value.fetch_dataset = AsyncMock(side_effect=Exception("Should not be called"))

        with patch.dict(SeedDatasetProvider._registry, {"P1": mock_provider1, "P2": mock_provider2}, clear=True):
            datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["d1"])
            assert len(datasets) == 1
            assert datasets[0].dataset_name == "d1"

    async def test_fetch_datasets_async_invalid_dataset_name(self):
        """Test that fetch_datasets_async raises ValueError for invalid dataset names."""
        mock_provider1 = MagicMock(__name__="P1")
        mock_provider1.return_value.dataset_name = "d1"
        mock_provider1.return_value._parse_metadata = AsyncMock(return_value=None)
        mock_provider1.return_value.fetch_dataset = AsyncMock(
            return_value=SeedDataset(seeds=[SeedPrompt(value="p1", data_type="text")], dataset_name="d1")
        )

        mock_provider2 = MagicMock(__name__="P2")
        mock_provider2.return_value.dataset_name = "d2"
        mock_provider2.return_value._parse_metadata = AsyncMock(return_value=None)
        mock_provider2.return_value.fetch_dataset = AsyncMock(
            return_value=SeedDataset(seeds=[SeedPrompt(value="p2", data_type="text")], dataset_name="d2")
        )

        with patch.dict(SeedDatasetProvider._registry, {"P1": mock_provider1, "P2": mock_provider2}, clear=True):
            # Test with single invalid name
            with pytest.raises(ValueError, match=r"Dataset\(s\) not found: \['nonexistent'\]"):
                await SeedDatasetProvider.fetch_datasets_async(dataset_names=["nonexistent"])

            # Test with mix of valid and invalid names
            with pytest.raises(ValueError, match=r"Dataset\(s\) not found: \['invalid1', 'invalid2'\]"):
                await SeedDatasetProvider.fetch_datasets_async(dataset_names=["d1", "invalid1", "invalid2"])


class TestHarmBenchDataset:
    """Test the HarmBench dataset loader."""

    async def test_fetch_dataset(self, mock_harmbench_data):
        """Test fetching HarmBench dataset."""
        loader = _HarmBenchDataset()

        with patch.object(loader, "_fetch_from_url", return_value=mock_harmbench_data):
            dataset = await loader.fetch_dataset()

            assert isinstance(dataset, SeedDataset)
            assert len(dataset.seeds) == 2
            assert all(isinstance(p, SeedObjective) for p in dataset.seeds)

            # Check first prompt
            first_prompt = dataset.seeds[0]
            assert first_prompt.value == "Test harmful behavior 1"
            assert first_prompt.data_type == "text"
            assert first_prompt.dataset_name == "harmbench"
            assert first_prompt.harm_categories == ["chemical_biological"]
            assert first_prompt.name == "HarmBench Examples"

    def test_dataset_name(self):
        """Test dataset_name property."""
        loader = _HarmBenchDataset()
        assert loader.dataset_name == "harmbench"

    async def test_fetch_dataset_missing_keys(self):
        """Test that missing required keys raise ValueError."""
        loader = _HarmBenchDataset()
        invalid_data = [{"Behavior": "Test"}]  # Missing SemanticCategory

        with patch.object(loader, "_fetch_from_url", return_value=invalid_data):
            with pytest.raises(ValueError, match="Missing keys in example"):
                await loader.fetch_dataset()

    async def test_fetch_dataset_with_custom_source(self, mock_harmbench_data):
        """Test fetching with custom source URL."""
        loader = _HarmBenchDataset(
            source="https://custom.example.com/data.csv",
            source_type="public_url",
        )

        with patch.object(loader, "_fetch_from_url", return_value=mock_harmbench_data) as mock_fetch:
            dataset = await loader.fetch_dataset(cache=False)

            assert len(dataset.seeds) == 2
            mock_fetch.assert_called_once()
            call_kwargs = mock_fetch.call_args.kwargs
            assert call_kwargs["source"] == "https://custom.example.com/data.csv"
            assert call_kwargs["source_type"] == "public_url"
            assert call_kwargs["cache"] is False


class TestDarkBenchDataset:
    """Test the DarkBench dataset loader."""

    async def test_fetch_dataset(self, mock_darkbench_data):
        """Test fetching DarkBench dataset."""
        loader = _DarkBenchDataset()

        with patch.object(loader, "_fetch_from_huggingface", return_value=mock_darkbench_data):
            dataset = await loader.fetch_dataset()

            assert isinstance(dataset, SeedDataset)
            assert len(dataset.seeds) == 2
            assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)

            # Check first prompt
            first_prompt = dataset.seeds[0]
            assert first_prompt.value == "Test dark pattern example 1"
            assert first_prompt.data_type == "text"
            assert first_prompt.dataset_name == "dark_bench"
            assert first_prompt.harm_categories == ["manipulative_design"]

    def test_dataset_name(self):
        """Test dataset_name property."""
        loader = _DarkBenchDataset()
        assert loader.dataset_name == "dark_bench"

    async def test_fetch_dataset_with_custom_config(self, mock_darkbench_data):
        """Test fetching with custom HuggingFace config."""
        loader = _DarkBenchDataset(
            dataset_name="custom/darkbench",
            config="custom_config",
            split="test",
        )

        with patch.object(loader, "_fetch_from_huggingface", return_value=mock_darkbench_data) as mock_fetch:
            dataset = await loader.fetch_dataset()

            assert len(dataset.seeds) == 2
            mock_fetch.assert_called_once()
            call_kwargs = mock_fetch.call_args.kwargs
            assert call_kwargs["dataset_name"] == "custom/darkbench"
            assert call_kwargs["config"] == "custom_config"
            assert call_kwargs["split"] == "test"


class TestMetadataParsingRemote:
    """Test metadata parsing and filter matching for remote providers."""

    async def test_parse_metadata_from_class_attrs(self):
        """Test _parse_metadata correctly extracts class-level metadata attributes."""
        loader = _HarmBenchDataset()
        metadata = await loader._parse_metadata()
        assert metadata is not None
        assert metadata.tags == {"default", "safety"}
        assert metadata.size == {"large"}
        assert metadata.modalities == {"text"}
        assert metadata.harm_categories == {"cybercrime", "illegal", "harmful", "chemical_biological", "harassment"}
        # source_type is not declared as a class attribute on HarmBench;
        # load_time inherits the UNINITIALIZED default from SeedDatasetProvider base class
        assert metadata.source_type is None
        assert metadata.load_time == {SeedDatasetLoadTime.UNINITIALIZED}

    def test_all_tag(self):
        """Filter with tags={'all'} matches any metadata."""
        metadata = SeedDatasetMetadata(tags={"safety"})
        filters = SeedDatasetFilter(tags={"all"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_tags(self):
        """Tag filter uses set intersection."""
        metadata = SeedDatasetMetadata(tags={"safety", "default"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata, dataset_filter=SeedDatasetFilter(tags={"safety"})
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata, dataset_filter=SeedDatasetFilter(tags={"unrelated"})
        )

    def test_sizes(self):
        """Size filter checks membership in the sizes list."""
        metadata = SeedDatasetMetadata(size={"large"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(size={"large", "huge"}),
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(size={"small"}),
        )

    def test_modalities(self):
        """Modality filter uses set intersection."""
        metadata = SeedDatasetMetadata(modalities={"text", "image"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(modalities={"text"}),
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(modalities={"audio"}),
        )

    def test_sources(self):
        """Source filter checks membership."""
        metadata = SeedDatasetMetadata(source_type={"remote"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(source_type={"remote"}),
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(source_type={"local"}),
        )

    def test_ranks(self):
        """Load time filter checks membership."""
        metadata = SeedDatasetMetadata(load_time={SeedDatasetLoadTime.FAST})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(load_time={SeedDatasetLoadTime.FAST}),
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(load_time={SeedDatasetLoadTime.SLOW}),
        )

    def test_harm_categories(self):
        """Harm category filter uses set intersection."""
        metadata = SeedDatasetMetadata(harm_categories={"violence", "cybercrime"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(harm_categories={"violence"}),
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(harm_categories={"unrelated"}),
        )

    def test_empty_filter(self):
        """Empty filter (all None) matches any metadata."""
        metadata = SeedDatasetMetadata(tags={"safety"}, size="large")
        filters = SeedDatasetFilter()
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_no_metadata(self):
        """Provider without metadata is skipped when filters are applied."""
        mock_provider_cls = MagicMock(__name__="NoProv")
        mock_provider_instance = mock_provider_cls.return_value
        mock_provider_instance.dataset_name = "no_metadata"
        mock_provider_instance._parse_metadata = AsyncMock(return_value=None)

        with patch.dict(SeedDatasetProvider._registry, {"NoProv": mock_provider_cls}, clear=True):
            names = await SeedDatasetProvider.get_all_dataset_names_async(filters=SeedDatasetFilter(tags={"safety"}))
            assert names == []


class TestStrictMatchFiltering:
    """Test strict_match behavior in SeedDatasetFilter."""

    def test_strict_tags_all_present_matches(self):
        """strict_match requires ALL filter tags to be present in metadata."""
        metadata = SeedDatasetMetadata(tags={"safety", "default", "curated"})
        filters = SeedDatasetFilter(tags={"safety", "default"}, strict_match=True)
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_tags_partial_overlap_fails(self):
        """strict_match rejects if metadata is missing any requested tag."""
        metadata = SeedDatasetMetadata(tags={"safety"})
        filters = SeedDatasetFilter(tags={"safety", "default"}, strict_match=True)
        assert not SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_nonstrict_tags_partial_overlap_passes(self):
        """Without strict_match, any tag overlap is sufficient."""
        metadata = SeedDatasetMetadata(tags={"safety"})
        filters = SeedDatasetFilter(tags={"safety", "default"}, strict_match=False)
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_harm_categories_all_present_matches(self):
        """strict_match requires ALL filter harm_categories present in metadata."""
        metadata = SeedDatasetMetadata(harm_categories={"violence", "cybercrime", "illegal"})
        filters = SeedDatasetFilter(harm_categories={"violence", "cybercrime"}, strict_match=True)
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_harm_categories_partial_fails(self):
        """strict_match rejects if metadata is missing any requested harm category."""
        metadata = SeedDatasetMetadata(harm_categories={"violence"})
        filters = SeedDatasetFilter(harm_categories={"violence", "cybercrime"}, strict_match=True)
        assert not SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_modalities_all_present_matches(self):
        """strict_match requires ALL filter modalities present in metadata."""
        metadata = SeedDatasetMetadata(modalities={"text", "image", "audio"})
        filters = SeedDatasetFilter(modalities={"text", "image"}, strict_match=True)
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_modalities_partial_fails(self):
        """strict_match rejects if metadata is missing any requested modality."""
        metadata = SeedDatasetMetadata(modalities={"text"})
        filters = SeedDatasetFilter(modalities={"text", "image"}, strict_match=True)
        assert not SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_size_unchanged(self):
        """strict_match doesn't change size behavior — still membership check."""
        metadata = SeedDatasetMetadata(size={"large"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(size={"large"}, strict_match=True),
        )
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(size={"small"}, strict_match=True),
        )

    def test_strict_cross_axis_and(self):
        """strict_match with multiple axes: all must match."""
        metadata = SeedDatasetMetadata(
            tags={"safety", "default"},
            size="large",
            harm_categories={"violence", "cybercrime"},
        )
        # Both axes satisfied
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(
                tags={"safety"},
                harm_categories={"violence"},
                strict_match=True,
            ),
        )
        # harm_categories axis fails (missing "illegal")
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(
                tags={"safety"},
                harm_categories={"violence", "illegal"},
                strict_match=True,
            ),
        )

    def test_strict_all_tag_still_bypasses(self):
        """tags={'all'} still bypasses everything even with strict_match."""
        metadata = SeedDatasetMetadata(tags={"safety"})
        filters = SeedDatasetFilter(tags={"all"}, strict_match=True)
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    def test_strict_default_plus_other_tags_requires_both(self):
        """With strict_match, 'default' is a normal tag — all must be present."""
        metadata = SeedDatasetMetadata(tags={"default", "safety"})
        # Both present → match
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(tags={"default", "safety"}, strict_match=True),
        )
        # Missing "curated" → reject
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(tags={"default", "safety", "curated"}, strict_match=True),
        )

    def test_nonstrict_default_is_shortcut(self):
        """Without strict_match, 'default' in filter tags is a shortcut match."""
        # Dataset has "default" tag → matches even without other filter tags present
        metadata = SeedDatasetMetadata(tags={"default"})
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(tags={"default", "nonexistent"}),
        )

    def test_strict_default_without_tag_on_dataset_fails(self):
        """With strict_match, dataset must actually have 'default' in tags."""
        metadata = SeedDatasetMetadata(tags={"default", "safety"}, load_time=SeedDatasetLoadTime.FAST)
        # Without strict, "default" shortcut matches because metadata has "default" tag
        assert SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(tags={"default", "curated"}),
        )
        # With strict, ALL filter tags must be in metadata — "curated" is missing
        assert not SeedDatasetProvider._match_filter_to_metadata(
            metadata=metadata,
            dataset_filter=SeedDatasetFilter(tags={"default", "curated"}, strict_match=True),
        )


class TestFilterValidation:
    """Test that invalid or contradictory filter configurations are caught early."""

    def test_all_with_strict_match_warns(self, caplog):
        """'all' + strict_match logs a warning since strict has no effect."""
        SeedDatasetFilter(tags={"all"}, strict_match=True)
        assert "strict_match has no effect" in caplog.text

    def test_all_with_other_tags_warns(self, caplog):
        """'all' combined with other tags logs a warning."""
        SeedDatasetFilter(tags={"all", "safety"})
        assert "other tags will be ignored" in caplog.text

    def test_all_with_other_fields_warns(self, caplog):
        """'all' combined with size/modality/etc logs a warning."""
        SeedDatasetFilter(tags={"all"}, size={"large"})
        assert "other fields will be ignored" in caplog.text

    def test_all_alone_no_warning(self, caplog):
        """'all' by itself does not warn."""
        SeedDatasetFilter(tags={"all"})
        assert caplog.text == ""

    def test_all_bypasses_match_filter_entirely(self):
        """'all' returns True from _match_filter regardless of metadata content."""
        # Metadata with no overlap to any filter field
        metadata = SeedDatasetMetadata(
            tags={"unrelated"},
            size="tiny",
            modalities={"audio"},
            harm_categories={"nothing"},
        )
        # Filter that would normally reject everything about this metadata
        filters = SeedDatasetFilter(
            tags={"all"},
            size={"huge"},
            modalities={"text"},
            harm_categories={"violence"},
            strict_match=True,
        )
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_all_includes_datasets_without_metadata(self):
        """'all' in get_all_dataset_names_async includes providers with no metadata."""
        mock_cls = MagicMock(__name__="BareProv")
        mock_cls.return_value.dataset_name = "bare"
        mock_cls.return_value._parse_metadata = AsyncMock(return_value=None)

        with patch.dict(SeedDatasetProvider._registry, {"Bare": mock_cls}, clear=True):
            # Without 'all', bare datasets are skipped
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"safety"}),
            )
            assert names == []

            # With 'all', bare datasets are included
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"all"}),
            )
            assert names == ["bare"]

    async def test_all_skips_match_filter_call(self):
        """'all' in get_all_dataset_names_async doesn't call _match_filter at all."""
        mock_cls = MagicMock(__name__="Prov")
        mock_cls.return_value.dataset_name = "test"
        mock_cls.return_value._parse_metadata = AsyncMock(return_value=None)

        with (
            patch.dict(SeedDatasetProvider._registry, {"P": mock_cls}, clear=True),
            patch.object(SeedDatasetProvider, "_match_filter_to_metadata") as mock_match,
        ):
            await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"all"}),
            )
            mock_match.assert_not_called()


class TestMetadataParsingLocal:
    """Test metadata parsing and filter matching for local YAML providers."""

    def _make_loader(self, yaml_path):
        """Create a _LocalDatasetLoader bypassing SeedDataset pre-loading."""
        loader = _LocalDatasetLoader.__new__(_LocalDatasetLoader)
        loader.file_path = yaml_path
        loader._dataset_name = yaml_path.stem
        return loader

    def _write_yaml(self, tmp_path, name, content):
        """Write a .prompt YAML file and return its path."""
        path = tmp_path / f"{name}.prompt"
        path.write_text(content)
        return path

    async def test_parse_metadata_extracts_fields(self, tmp_path):
        """Test _parse_metadata correctly extracts metadata fields from YAML."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                harm_categories:
                  - violence
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        assert metadata.harm_categories == {"violence"}

    async def test_all_tag(self, tmp_path):
        """Filter with tags={'all'} matches regardless of metadata types."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                tags:
                  - safety
                harm_categories:
                  - violence
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(tags={"all"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_tags(self, tmp_path):
        """YAML produces tags as list; set intersection in _match_filter expects a set."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                tags:
                  - safety
                  - default
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(tags={"safety"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_sizes(self, tmp_path):
        """YAML produces size as string; _match_filter compares against enum values."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                size: large
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(size={"large"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_modalities(self, tmp_path):
        """YAML produces modalities as list of strings; _match_filter uses enum values."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                modalities:
                  - text
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(modalities={"text"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_sources(self, tmp_path):
        """YAML produces source_type as string; _match_filter compares against enum values."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                source_type: remote
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(source_type={"remote"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_ranks(self, tmp_path):
        """YAML produces load_time as string; _match_filter compares against enum values."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                load_time: fast
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(load_time={SeedDatasetLoadTime.FAST})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_harm_categories(self, tmp_path):
        """Both YAML and filter use list[str], so intersection works correctly."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                harm_categories:
                  - violence
                  - cybercrime
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter(harm_categories={"violence"})
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_empty_filter(self, tmp_path):
        """Empty filter matches everything."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                harm_categories:
                  - violence
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is not None
        filters = SeedDatasetFilter()
        assert SeedDatasetProvider._match_filter_to_metadata(metadata=metadata, dataset_filter=filters)

    async def test_no_metadata(self, tmp_path):
        """YAML without any metadata fields returns None from _parse_metadata."""
        yaml_path = self._write_yaml(
            tmp_path,
            "test",
            textwrap.dedent("""\
                dataset_name: test
                seeds:
                  - value: test prompt
                    data_type: text
            """),
        )
        loader = self._make_loader(yaml_path)
        metadata = await loader._parse_metadata()
        assert metadata is None


class TestLocalDatasetMetadataCollisions:
    """
    Regression tests that scan every real .prompt file under seed_datasets/local
    to verify _parse_metadata does not crash from field-name collisions between
    the YAML schema and SeedDatasetMetadata.

    The previous `source` field collision (URLs parsed as SeedDatasetSourceType)
    is the motivating example.
    """

    @staticmethod
    def _get_local_prompt_files() -> list:
        """Collect all .prompt and .yaml files under the local datasets directory."""
        local_dir = Path(__file__).resolve().parents[3] / "pyrit" / "datasets" / "seed_datasets" / "local"
        return sorted(local_dir.glob("**/*.prompt")) + sorted(local_dir.glob("**/*.yaml"))

    @pytest.mark.parametrize("prompt_file", _get_local_prompt_files.__func__(), ids=lambda p: p.stem)
    async def test_parse_metadata_does_not_crash(self, prompt_file):
        """_parse_metadata must not raise on any real local dataset file."""
        loader = _LocalDatasetLoader.__new__(_LocalDatasetLoader)
        loader.file_path = prompt_file
        loader._dataset_name = prompt_file.stem

        metadata = await loader._parse_metadata()
        # metadata can be None (no matching fields) or a valid SeedDatasetMetadata
        if metadata is not None:
            assert isinstance(metadata, SeedDatasetMetadata)

    @pytest.mark.parametrize("prompt_file", _get_local_prompt_files.__func__(), ids=lambda p: p.stem)
    def test_no_yaml_key_shadows_metadata_field_with_wrong_type(self, prompt_file):
        """
        If a YAML top-level key matches a SeedDatasetMetadata field name, the
        coerced value must be the correct type (enum, set, list) — not a raw
        string or other primitive that would silently break filtering.
        """
        with open(prompt_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            return

        metadata_field_names = {fld.name for fld in dc_fields(SeedDatasetMetadata)}
        overlapping_keys = metadata_field_names & data.keys()

        if not overlapping_keys:
            return

        # Coerce and construct — must not raise
        loader = _LocalDatasetLoader.__new__(_LocalDatasetLoader)
        loader.file_path = prompt_file
        loader._dataset_name = prompt_file.stem

        raw = {k: data[k] for k in overlapping_keys}
        coerced = SeedDatasetMetadata._coerce_metadata_values(raw_metadata=raw)
        metadata = SeedDatasetMetadata(**coerced)

        # Verify coerced types match expectations
        expected_types = {
            "tags": (set, type(None)),
            "size": (set, type(None)),
            "modalities": (set, type(None)),
            "source_type": (set, type(None)),
            "load_time": (set, type(None)),
            "harm_categories": (set, type(None)),
        }
        for key in overlapping_keys:
            value = getattr(metadata, key)
            valid_types = expected_types.get(key)
            if valid_types:
                assert isinstance(value, valid_types), (
                    f"Field '{key}' in {prompt_file.name} has type {type(value).__name__}, "
                    f"expected one of {valid_types}"
                )
