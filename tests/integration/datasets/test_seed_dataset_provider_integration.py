# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader
from pyrit.datasets.seed_datasets.remote import _VLSUMultimodalDataset
from pyrit.datasets.seed_datasets.seed_metadata import (
    SeedDatasetFilter,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


def get_dataset_providers():
    """Helper to get all registered providers for parameterization."""
    providers = SeedDatasetProvider.get_all_providers()
    return [(name, cls) for name, cls in providers.items()]


class TestSeedDatasetProviderIntegration:
    """Integration tests for SeedDatasetProvider."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,provider_cls", get_dataset_providers())
    async def test_fetch_dataset_integration(self, name, provider_cls):
        """
        Integration test to verify that a specific registered dataset can be fetched.

        This test is parameterized to run for each registered provider.
        It verifies that:
        1. The dataset can be downloaded/loaded without error
        2. The result is a SeedDataset
        3. The dataset is not empty (has seeds)
        """
        logger.info(f"Testing provider: {name}")

        try:
            # Use max_examples for slow providers that fetch many remote images
            provider = provider_cls(max_examples=6) if provider_cls == _VLSUMultimodalDataset else provider_cls()
            dataset = await provider.fetch_dataset(cache=False)

            assert isinstance(dataset, SeedDataset), f"{name} did not return a SeedDataset"
            assert len(dataset.seeds) > 0, f"{name} returned an empty dataset"
            assert dataset.dataset_name, f"{name} has no dataset_name"

            # Verify seeds have required fields
            for seed in dataset.seeds:
                assert seed.value, f"Seed in {name} has no value"
                assert seed.dataset_name == dataset.dataset_name, (
                    f"Seed dataset_name mismatch in {name}: {seed.dataset_name} != {dataset.dataset_name}"
                )

            logger.info(f"Successfully verified {name} with {len(dataset.seeds)} seeds")

        except Exception as e:
            pytest.fail(f"Failed to fetch dataset from {name}: {str(e)}")


class TestRemoteFilteringIntegration:
    """
    Integration test for remote dataset filtering.

    Uses a mocked remote provider with class-level metadata attributes to
    validate the full flow: metadata population, filter matching, and
    get_all_dataset_names output.
    """

    def _make_remote_provider_cls(
        self,
        *,
        name: str,
        tags: set,
        size: str,
        modalities: list,
        harm_categories: list,
    ) -> type:
        """Build a minimal concrete SeedDatasetProvider with class-level metadata."""
        from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader

        captured_name = name

        async def _fetch_dataset(self, *, cache=True):
            return SeedDataset(
                seeds=[SeedPrompt(value="x", data_type="text")],
                dataset_name=captured_name,
            )

        attrs = {
            "tags": tags,
            "size": size,
            "modalities": modalities,
            "harm_categories": harm_categories,
            "should_register": False,
            "__module__": __name__,
            # Concrete implementations satisfy ABC requirements
            "dataset_name": property(lambda self: captured_name),
            "fetch_dataset": _fetch_dataset,
            "_fetch_from_url": lambda self, **kw: [],
        }

        return type(f"_Mock_{name}", (_RemoteDatasetLoader,), attrs)

    @pytest.mark.asyncio
    async def test_filter_matches_correct_remote_provider(self):
        """Filter by size returns only providers that match."""
        large_cls = self._make_remote_provider_cls(
            name="large_ds",
            tags={"default"},
            size="large",
            modalities=["text"],
            harm_categories=["violence"],
        )
        small_cls = self._make_remote_provider_cls(
            name="small_ds",
            tags={"default"},
            size="small",
            modalities=["text"],
            harm_categories=["cybercrime"],
        )

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Large": large_cls, "Small": small_cls},
            clear=True,
        ):
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(size={"large"}),
            )
            assert names == ["large_ds"]

    @pytest.mark.asyncio
    async def test_filter_all_tag_returns_everything(self):
        """tags={'all'} bypasses filtering and returns every provider."""
        cls1 = self._make_remote_provider_cls(
            name="ds_a",
            tags={"safety"},
            size="tiny",
            modalities=["text"],
            harm_categories=[],
        )
        cls2 = self._make_remote_provider_cls(
            name="ds_b",
            tags={"custom"},
            size="huge",
            modalities=["image"],
            harm_categories=["violence"],
        )

        with patch.dict(
            SeedDatasetProvider._registry,
            {"A": cls1, "B": cls2},
            clear=True,
        ):
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"all"}),
            )
            assert sorted(names) == ["ds_a", "ds_b"]

    @pytest.mark.asyncio
    async def test_multi_axis_filter(self):
        """Multiple filter axes are ANDed together."""
        cls1 = self._make_remote_provider_cls(
            name="text_large",
            tags={"default"},
            size="large",
            modalities=["text"],
            harm_categories=["violence"],
        )
        cls2 = self._make_remote_provider_cls(
            name="image_large",
            tags={"default"},
            size="large",
            modalities=["image"],
            harm_categories=["violence"],
        )

        with patch.dict(
            SeedDatasetProvider._registry,
            {"TL": cls1, "IL": cls2},
            clear=True,
        ):
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(
                    size={"large"},
                    modalities={"text"},
                ),
            )
            assert names == ["text_large"]


class TestLocalFilteringIntegration:
    """
    Integration test for local dataset filtering.

    Creates real YAML prompt files on disk, registers them as local providers,
    and validates the full flow through get_all_dataset_names with filters.
    """

    @staticmethod
    def _make_local_cls(yaml_path: Path) -> type:
        """Build a dynamic local provider class for a YAML file."""

        def make_init(path: Path):
            def init_fn(self):
                _LocalDatasetLoader.__init__(self, file_path=path)

            return init_fn

        return type(
            f"LocalTest_{yaml_path.stem}",
            (_LocalDatasetLoader,),
            {"__init__": make_init(yaml_path), "should_register": False, "__module__": __name__},
        )

    @pytest.mark.asyncio
    async def test_local_filter_by_size(self, tmp_path):
        """Local YAML with size metadata is correctly coerced and filtered."""
        large_yaml = tmp_path / "large_ds.prompt"
        large_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: large_local
                size: large
                harm_categories:
                  - violence
                seeds:
                  - value: test
                    data_type: text
            """)
        )
        small_yaml = tmp_path / "small_ds.prompt"
        small_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: small_local
                size: small
                harm_categories:
                  - cybercrime
                seeds:
                  - value: test
                    data_type: text
            """)
        )

        large_cls = self._make_local_cls(large_yaml)
        small_cls = self._make_local_cls(small_yaml)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Large": large_cls, "Small": small_cls},
            clear=True,
        ):
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(size={"large"}),
            )
            # dataset_name falls back to file stem when SeedDataset.from_yaml_file
            # rejects extra keys like "size" during __init__ pre-loading
            assert names == ["large_ds"]

    @pytest.mark.asyncio
    async def test_local_filter_by_tags(self, tmp_path):
        """Local YAML tags (list) are coerced to set for intersection."""
        yaml_path = tmp_path / "tagged.prompt"
        yaml_path.write_text(
            textwrap.dedent("""\
                dataset_name: tagged_local
                tags:
                  - safety
                  - default
                harm_categories:
                  - violence
                seeds:
                  - value: test
                    data_type: text
            """)
        )
        cls = self._make_local_cls(yaml_path)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Tagged": cls},
            clear=True,
        ):
            # dataset_name falls back to file stem ("tagged") when
            # SeedDataset.from_yaml_file rejects extra keys like "tags"
            matched = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"safety"}),
            )
            assert matched == ["tagged"]

            not_matched = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"unrelated"}),
            )
            assert not_matched == []

    @pytest.mark.asyncio
    async def test_local_no_metadata_skipped(self, tmp_path):
        """Local YAML without metadata fields is skipped when filters are provided."""
        yaml_path = tmp_path / "bare.prompt"
        yaml_path.write_text(
            textwrap.dedent("""\
                dataset_name: bare_local
                seeds:
                  - value: test
                    data_type: text
            """)
        )
        cls = self._make_local_cls(yaml_path)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Bare": cls},
            clear=True,
        ):
            # Without filters, the dataset is included
            all_names = await SeedDatasetProvider.get_all_dataset_names_async()
            assert "bare_local" in all_names

            # With filters, it's skipped (no metadata to match against)
            filtered = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"safety"}),
            )
            assert filtered == []


class TestEndToEndLocalDatasetWorkflow:
    """
    End-to-end integration test that simulates a user workflow:
    1. Write a temporary local dataset to disk
    2. Register it into the provider registry
    3. Discover it via filtered get_all_dataset_names
    4. Fetch the actual dataset by name
    5. Verify metadata is accessible and correct
    """

    @staticmethod
    def _make_local_cls(yaml_path: Path) -> type:
        """Build a dynamic local provider class for a YAML file."""

        def make_init(path: Path):
            def init_fn(self):
                _LocalDatasetLoader.__init__(self, file_path=path)

            return init_fn

        return type(
            f"LocalTest_{yaml_path.stem}",
            (_LocalDatasetLoader,),
            {"__init__": make_init(yaml_path), "should_register": False, "__module__": __name__},
        )

    @pytest.mark.asyncio
    async def test_user_discovers_and_fetches_filtered_dataset(self, tmp_path):
        """
        Simulate a user who wants small text datasets about cybercrime:
        1. Filter returns the matching dataset name
        2. Fetch by that name succeeds and returns correct seeds
        3. Metadata on the provider is accessible and consistent
        """
        # --- Step 0: Write two datasets to disk ---
        matching_yaml = tmp_path / "cyber_small.prompt"
        matching_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: cyber_prompts
                harm_categories:
                  - cybercrime
                seeds:
                  - value: Explain phishing techniques
                    data_type: text
                  - value: Describe SQL injection
                    data_type: text
            """)
        )
        unrelated_yaml = tmp_path / "violence_large.prompt"
        unrelated_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: violence_prompts
                harm_categories:
                  - violence
                seeds:
                  - value: Describe a violent scenario
                    data_type: text
            """)
        )

        matching_cls = self._make_local_cls(matching_yaml)
        unrelated_cls = self._make_local_cls(unrelated_yaml)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Cyber": matching_cls, "Violence": unrelated_cls},
            clear=True,
        ):
            # --- Step 1: User filters by harm_categories ---
            names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(harm_categories={"cybercrime"}),
            )
            assert len(names) == 1
            dataset_name = names[0]

            # --- Step 2: User fetches the dataset by name ---
            datasets = await SeedDatasetProvider.fetch_datasets_async(
                dataset_names=[dataset_name],
            )
            assert len(datasets) == 1
            dataset = datasets[0]
            assert len(dataset.seeds) == 2
            assert dataset.seeds[0].value == "Explain phishing techniques"
            assert dataset.seeds[1].value == "Describe SQL injection"

            # --- Step 3: User inspects metadata ---
            provider = matching_cls()
            metadata = await provider._parse_metadata()
            assert metadata is not None
            assert metadata.harm_categories == {"cybercrime"}

    @pytest.mark.asyncio
    async def test_user_fetches_unfiltered(self, tmp_path):
        """
        Without filters, get_all_dataset_names returns everything,
        and fetch_datasets_async retrieves all of them.
        """
        ds1 = tmp_path / "ds_one.prompt"
        ds1.write_text(
            textwrap.dedent("""\
                dataset_name: dataset_one
                seeds:
                  - value: prompt one
                    data_type: text
            """)
        )
        ds2 = tmp_path / "ds_two.prompt"
        ds2.write_text(
            textwrap.dedent("""\
                dataset_name: dataset_two
                seeds:
                  - value: prompt two
                    data_type: text
            """)
        )

        cls1 = self._make_local_cls(ds1)
        cls2 = self._make_local_cls(ds2)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"One": cls1, "Two": cls2},
            clear=True,
        ):
            names = await SeedDatasetProvider.get_all_dataset_names_async()
            assert len(names) == 2

            datasets = await SeedDatasetProvider.fetch_datasets_async()
            assert len(datasets) == 2
            fetched_names = sorted(d.dataset_name for d in datasets)
            assert fetched_names == ["dataset_one", "dataset_two"]


class TestAllTagBypassIntegration:
    """
    Integration tests for the tags={'all'} bypass pattern.

    The 'all' tag is a special escape hatch that returns every registered
    dataset regardless of metadata presence or other filter axes.
    """

    @staticmethod
    def _make_local_cls(yaml_path: Path) -> type:
        """Build a dynamic local provider class for a YAML file."""

        def make_init(path: Path):
            def init_fn(self):
                _LocalDatasetLoader.__init__(self, file_path=path)

            return init_fn

        return type(
            f"LocalTest_{yaml_path.stem}",
            (_LocalDatasetLoader,),
            {"__init__": make_init(yaml_path), "should_register": False, "__module__": __name__},
        )

    @pytest.mark.asyncio
    async def test_all_tag_includes_datasets_without_metadata(self, tmp_path):
        """
        A dataset whose YAML has no metadata fields at all is normally
        skipped when filters are present. tags={'all'} overrides that.
        """
        bare_yaml = tmp_path / "bare.prompt"
        bare_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: bare_dataset
                seeds:
                  - value: bare prompt
                    data_type: text
            """)
        )
        cls = self._make_local_cls(bare_yaml)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Bare": cls},
            clear=True,
        ):
            # Normal filter skips it
            filtered = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"safety"}),
            )
            assert filtered == []

            # 'all' includes it
            all_names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"all"}),
            )
            assert "bare_dataset" in all_names

    @pytest.mark.asyncio
    async def test_all_tag_ignores_other_filter_axes(self, tmp_path):
        """
        tags={'all'} returns everything even when other filter axes
        would exclude datasets.
        """
        small_yaml = tmp_path / "small.prompt"
        small_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: small_dataset
                size: small
                harm_categories:
                  - cybercrime
                seeds:
                  - value: small prompt
                    data_type: text
            """)
        )
        cls = self._make_local_cls(small_yaml)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Small": cls},
            clear=True,
        ):
            # Size filter alone would exclude it
            size_filtered = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(size={"large"}),
            )
            assert size_filtered == []

            # 'all' tag overrides the size filter
            all_names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"all"}, size={"large"}),
            )
            assert "small" in all_names

    @pytest.mark.asyncio
    async def test_all_tag_with_mixed_metadata_and_bare_datasets(self, tmp_path):
        """
        With a mix of metadata-rich and metadata-bare datasets,
        tags={'all'} returns all of them.
        """
        rich_yaml = tmp_path / "rich.prompt"
        rich_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: rich_dataset
                harm_categories:
                  - violence
                tags:
                  - safety
                seeds:
                  - value: rich prompt
                    data_type: text
            """)
        )
        bare_yaml = tmp_path / "bare.prompt"
        bare_yaml.write_text(
            textwrap.dedent("""\
                dataset_name: bare_dataset
                seeds:
                  - value: bare prompt
                    data_type: text
            """)
        )

        rich_cls = self._make_local_cls(rich_yaml)
        bare_cls = self._make_local_cls(bare_yaml)

        with patch.dict(
            SeedDatasetProvider._registry,
            {"Rich": rich_cls, "Bare": bare_cls},
            clear=True,
        ):
            all_names = await SeedDatasetProvider.get_all_dataset_names_async(
                filters=SeedDatasetFilter(tags={"all"}),
            )
            assert len(all_names) == 2
            assert "bare_dataset" in all_names


class TestHarmbenchMetadataInScenario:
    """
    Regression tests verifying that metadata changes don't break the dataset
    loading flow for scenarios that use datasets with explicit metadata.

    RedTeamAgent is the primary scenario that uses harmbench — the only remote
    dataset with class-level metadata (tags, size, modalities, harm_categories).
    These tests verify the full pipeline: metadata parsing → dataset loading →
    memory storage → scenario initialization.
    """

    @pytest.mark.asyncio
    async def test_harmbench_metadata_parses_correctly(self):
        """HarmBench's class-level metadata is correctly parsed into sets."""
        from pyrit.datasets.seed_datasets.remote.harmbench_dataset import _HarmBenchDataset

        loader = _HarmBenchDataset()
        metadata = await loader._parse_metadata()

        assert metadata is not None
        assert isinstance(metadata.tags, set)
        assert "default" in metadata.tags
        assert "safety" in metadata.tags
        assert metadata.size == {"large"}
        assert metadata.modalities == {"text"}
        assert isinstance(metadata.harm_categories, set)
        assert "cybercrime" in metadata.harm_categories

    @pytest.mark.asyncio
    async def test_harmbench_discoverable_via_filter(self):
        """HarmBench can be found via tag and harm_category filters."""
        names_by_safety = await SeedDatasetProvider.get_all_dataset_names_async(
            filters=SeedDatasetFilter(tags={"safety"}),
        )
        assert "harmbench" in names_by_safety

        names_by_harm = await SeedDatasetProvider.get_all_dataset_names_async(
            filters=SeedDatasetFilter(harm_categories={"cybercrime"}),
        )
        assert "harmbench" in names_by_harm

    @pytest.mark.asyncio
    async def test_harmbench_loads_and_stores_in_memory(self, sqlite_instance):
        """HarmBench can be fetched and stored in memory for scenario use."""
        datasets = await SeedDatasetProvider.fetch_datasets_async(
            dataset_names=["harmbench"],
        )
        assert len(datasets) == 1
        assert datasets[0].dataset_name == "harmbench"
        assert len(datasets[0].seeds) > 0

        await sqlite_instance.add_seed_datasets_to_memory_async(
            datasets=datasets,
            added_by="test",
        )

        # Verify seeds are queryable from memory (this is what scenarios do)
        seed_groups = sqlite_instance.get_seed_groups(dataset_name="harmbench")
        assert seed_groups is not None
        assert len(list(seed_groups)) > 0

    @pytest.mark.asyncio
    async def test_red_team_agent_initializes_with_harmbench(self, sqlite_instance):
        """
        RedTeamAgent can initialize with harmbench dataset loaded in memory.

        This is the critical regression test: if metadata changes break the
        parsing/coercion/filtering pipeline, this test will fail during
        scenario initialization when it tries to load seed groups from memory.
        """
        from unittest.mock import MagicMock

        from pyrit.executor.attack.core.attack_config import AttackScoringConfig
        from pyrit.prompt_target import TextTarget
        from pyrit.scenario.scenarios.foundry.red_team_agent import (
            FoundryStrategy,
            RedTeamAgent,
        )
        from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

        # Load harmbench into memory
        datasets = await SeedDatasetProvider.fetch_datasets_async(
            dataset_names=["harmbench"],
        )
        await sqlite_instance.add_seed_datasets_to_memory_async(
            datasets=datasets,
            added_by="test",
        )

        # Mock scorer to avoid Azure dependency
        mock_scorer = MagicMock(spec=TrueFalseScorer)
        mock_scorer.get_identifier.return_value = {"__type__": "MockScorer"}

        target = TextTarget()
        rta = RedTeamAgent(
            adversarial_chat=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_scorer),
            include_baseline=False,
        )

        # This is the critical call — it loads seed groups from memory
        # and builds atomic attacks. If metadata broke the pipeline,
        # this would raise ValueError about missing seed_groups.
        await rta.initialize_async(
            objective_target=target,
            max_concurrency=1,
            scenario_strategies=[FoundryStrategy.Base64],
        )

        # Verify the scenario got objectives from harmbench
        attacks = rta._atomic_attacks
        assert len(attacks) > 0
        for attack in attacks:
            assert len(attack.objectives) > 0
