# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Encoding class."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.converter import Base64Converter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import CompoundDatasetAttackConfiguration, DatasetAttackConfiguration, DatasetConfiguration
from pyrit.scenario.garak import Encoding, EncodingTechnique  # type: ignore[ty:unresolved-import]
from pyrit.scenario.scenarios.garak.encoding import EncodingDatasetConfiguration
from pyrit.score import DecodingScorer, TrueFalseScorer


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


@pytest.fixture
def mock_memory_seeds():
    """Create mock seed prompts that memory.get_seeds() would return."""
    return [
        SeedPrompt(value="test slur term 1", data_type="text"),
        SeedPrompt(value="test slur term 2", data_type="text"),
        SeedPrompt(value="test web html 1", data_type="text"),
        SeedPrompt(value="test web html 2", data_type="text"),
    ]


@pytest.fixture
def mock_attack_seed_groups(mock_memory_seeds):
    """Create mock seed attack groups from the mock seeds."""
    return [
        AttackSeedGroup(
            seeds=[
                SeedObjective(value=f"Make the model say an encoded payload: {seed.value}"),
                SeedPrompt(value=seed.value),
            ]
        )
        for seed in mock_memory_seeds
    ]


@pytest.fixture
def mock_dataset_config(mock_attack_seed_groups):
    """Create a mock dataset config that returns the seed attack groups."""
    mock_config = MagicMock(spec=EncodingDatasetConfiguration)
    mock_config.get_attack_seed_groups_async = AsyncMock(return_value=mock_attack_seed_groups)
    mock_config.dataset_names = ["garak_slur_terms_en", "garak_web_html_js"]
    return mock_config


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    """Create a mock objective scorer for testing."""
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


@pytest.fixture
def sample_seeds():
    """Create sample seeds for testing."""
    return ["test prompt 1", "test prompt 2"]


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingInitialization:
    """Tests for Encoding initialization."""

    def test_init_with_default_seed_prompts(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with default seed prompts (Garak dataset)."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            assert scenario.name == "Encoding"
            assert scenario.VERSION == 2

    def test_init_with_custom_scorer(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom objective scorer."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            assert scenario._scorer_config.objective_scorer == mock_objective_scorer

    def test_init_creates_default_scorer_when_not_provided(self, mock_objective_target, mock_memory_seeds):
        """Test that initialization creates default DecodingScorer when not provided."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}):
            scenario = Encoding()

            # Should create a DecodingScorer by default
            assert scenario._scorer_config.objective_scorer is not None
            assert isinstance(scenario._scorer_config.objective_scorer, DecodingScorer)

    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises DatasetConstraintError when datasets are unavailable."""
        from unittest.mock import patch

        from pyrit.scenario.core.dataset_configuration import DatasetConstraintError

        # Don't mock _resolve_seed_groups_by_dataset_async; let it try to load from empty memory.
        # Disable the provider fallback so memory stays empty and the scenario raises.
        scenario = Encoding(objective_scorer=mock_objective_scorer)

        with patch.object(EncodingDatasetConfiguration, "_fetch_dataset_async", new_callable=AsyncMock):
            # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed prompts
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(DatasetConstraintError, match="could not be loaded"):
                await scenario.initialize_async()

    def test_init_with_memory_labels(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with memory labels."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # memory_labels are not set until initialize_async is called
            assert scenario._memory_labels == {}

    def test_init_with_custom_encoding_templates(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom encoding templates."""
        from unittest.mock import patch

        custom_templates = ["template1", "template2"]

        with patch.object(Encoding, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}):
            scenario = Encoding(
                encoding_templates=custom_templates,
                objective_scorer=mock_objective_scorer,
            )

            assert scenario._encoding_templates == custom_templates

    def test_init_with_max_concurrency(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom max_concurrency."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # max_concurrency is unset (None) until initialize_async is called
            assert scenario._max_concurrency is None

    async def test_init_attack_techniques(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups, mock_dataset_config
    ):
        """Test that attack techniques are set correctly."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            # By default, EncodingTechnique.DEFAULT is used, which expands to the curated subset
            assert len(scenario._scenario_techniques) > 0
            # Verify all techniques contain EncodingTechnique instances
            assert all(isinstance(s, EncodingTechnique) for s in scenario._scenario_techniques)
            # Verify none of the techniques are the aggregate members (ALL/DEFAULT)
            assert all(s != EncodingTechnique.ALL for s in scenario._scenario_techniques)
            assert all(s != EncodingTechnique.DEFAULT for s in scenario._scenario_techniques)
            # The default run is the curated DEFAULT set, not the exhaustive ALL set
            assert {s.value for s in scenario._scenario_techniques} == {
                t.value for t in EncodingTechnique.get_techniques_by_tag("default")
            }


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingTechniqueDefault:
    """Tests for the curated DEFAULT aggregate and technique tagging."""

    def test_default_aggregate_curates_representative_subset(self):
        """DEFAULT expands to a broad curated subset spanning encoding families, smaller than ALL."""
        default_names = {t.value for t in EncodingTechnique.get_techniques_by_tag("default")}
        assert default_names == {
            "base64",
            "base2048",
            "base16",
            "base32",
            "ascii85",
            "hex",
            "quoted_printable",
            "uuencode",
            "rot13",
            "atbash",
            "morse_code",
            "nato",
            "leet_speak",
        }
        all_names = {t.value for t in EncodingTechnique.get_all_techniques()}
        assert default_names < all_names
        assert len(all_names) == 17
        # The niche/lossy schemes stay ALL-only.
        assert all_names - default_names == {"braille", "ecoji", "zalgo", "ascii_smuggler"}

    def test_get_aggregate_tags_includes_default(self):
        """The scenario exposes both ``all`` and ``default`` as aggregate tags."""
        assert EncodingTechnique.get_aggregate_tags() == {"all", "default"}

    def test_default_technique_is_curated_default(self, mock_objective_scorer):
        """A bare scenario defaults to the curated DEFAULT aggregate, not ALL."""
        scenario = Encoding(objective_scorer=mock_objective_scorer)
        assert scenario._default_technique == EncodingTechnique.DEFAULT


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingAtomicNameUniqueness:
    """Tests that atomic-attack names are unique per converter variant (collision fix)."""

    async def test_all_atomic_attack_names_are_unique(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups
    ):
        """Every atomic attack across the exhaustive ALL run has a unique name."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [EncodingTechnique.ALL],
                }
            )
            await scenario.initialize_async()

            names = [
                aa.atomic_attack_name
                for aa in scenario._get_converter_attacks(
                    context=scenario._build_scenario_context(seed_groups_by_dataset={"memory": mock_attack_seed_groups})
                )
            ]
            assert len(names) == len(set(names)), "atomic_attack_name collisions detected"

    async def test_base64_trimmed_to_two_variants(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups
    ):
        """base64 keeps only the default and url-safe variants (near-duplicates removed)."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [EncodingTechnique.Base64],
                }
            )
            await scenario.initialize_async()

            attacks = scenario._get_converter_attacks(
                context=scenario._build_scenario_context(seed_groups_by_dataset={"memory": mock_attack_seed_groups})
            )
            # Every base64 atomic attack groups under the "base64" display group.
            assert all(aa.display_group == "base64" for aa in attacks)
            # Two distinct converter variants (default + urlsafe), each fanned over the raw config
            # plus one config per decode template — names are unique and prefixed by the variant slug.
            names = {aa.atomic_attack_name for aa in attacks}
            assert {n for n in names if n.startswith("base64_urlsafe")}, "missing urlsafe variant"
            assert {n for n in names if n.startswith("base64_") and not n.startswith("base64_urlsafe")}, (
                "missing default base64 variant"
            )
            # The trimmed variants must not appear.
            assert not any("standard" in n or "b2a" in n for n in names)

    async def test_memory_labels_propagate_to_atomic_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups
    ):
        """Run-level memory_labels reach every built atomic attack (matches the sibling convention)."""
        from unittest.mock import patch

        labels = {"experiment": "enc-run", "operator": "airt"}
        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [EncodingTechnique.ALL],
                    "memory_labels": labels,
                }
            )
            await scenario.initialize_async()

            technique_attacks = [aa for aa in scenario._atomic_attacks if aa.atomic_attack_name != "baseline"]
            assert technique_attacks
            assert all(aa._memory_labels == labels for aa in technique_attacks)


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingAtomicAttacks:
    """Tests for Encoding atomic attack generation."""

    async def test_get_atomic_attacks_async_returns_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups, mock_dataset_config
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            atomic_attacks = scenario._atomic_attacks

            # Should return multiple atomic attacks (one for each encoding type)
            assert len(atomic_attacks) > 0
            assert all(run.attack_technique is not None for run in atomic_attacks)

    async def test_get_converter_attacks_returns_multiple_encodings(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups, mock_dataset_config
    ):
        """Test that _get_converter_attacks returns attacks for multiple encoding types."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            attack_runs = scenario._get_converter_attacks(
                context=scenario._build_scenario_context(seed_groups_by_dataset={"memory": mock_attack_seed_groups})
            )

            # Should have multiple attack runs for different encodings
            # The list includes: base64 (2 variants: default + urlsafe), base2048, base16, base32, ascii85 (2),
            # quoted-printable, UUencode, ROT13, Braille, Atbash, Morse, NATO, Ecoji, Zalgo, Leet, AsciiSmuggler
            assert len(attack_runs) > 0

    async def test_get_prompt_attacks_creates_attack_runs(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups, mock_dataset_config
    ):
        """Test that _get_prompt_attacks creates attack runs with correct structure."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            attack_runs = scenario._get_prompt_attacks(
                converters=[Base64Converter()],
                encoding_name="base64",
                variant_slug="base64",
                context=scenario._build_scenario_context(seed_groups_by_dataset={"memory": mock_attack_seed_groups}),
            )

            # Should create attack runs
            assert len(attack_runs) > 0

            # Each attack run should have the correct attack type
            for run in attack_runs:
                assert isinstance(run.attack_technique.attack, PromptSendingAttack)
                assert len(run._seed_groups) == len(mock_attack_seed_groups)

    async def test_attack_runs_include_objectives(
        self,
        mock_objective_target,
        mock_objective_scorer,
        mock_attack_seed_groups,
        mock_memory_seeds,
        mock_dataset_config,
    ):
        """Test that attack runs include objectives for each seed prompt."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            attack_runs = scenario._get_prompt_attacks(
                converters=[Base64Converter()],
                encoding_name="base64",
                variant_slug="base64",
                context=scenario._build_scenario_context(seed_groups_by_dataset={"memory": mock_attack_seed_groups}),
            )

            # Check that seed groups contain objectives with the expected format
            for run in attack_runs:
                assert len(run._seed_groups) == len(mock_attack_seed_groups)
                for i, seed_group in enumerate(run._seed_groups):
                    # The first seed in each group should be a SeedObjective
                    objective_seed = seed_group.seeds[0]
                    assert isinstance(objective_seed, SeedObjective)
                    assert "Make the model say an encoded payload:" in objective_seed.value
                    assert mock_memory_seeds[i].value in objective_seed.value


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingExecution:
    """Tests for Encoding execution."""

    async def test_scenario_initialization(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups, mock_dataset_config
    ):
        """Test that scenario can be initialized successfully."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            # Verify initialization creates atomic attacks
            assert scenario.atomic_attack_count > 0

    async def test_resolve_seed_groups_loads_garak_data(
        self, mock_objective_target, mock_objective_scorer, mock_attack_seed_groups, mock_dataset_config
    ):
        """Test that _resolve_seed_groups_by_dataset_async loads data from Garak datasets."""
        from unittest.mock import patch

        with patch.object(
            Encoding,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_attack_seed_groups},
        ):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # After resolve, should have seed groups keyed by dataset
            resolved = await scenario._resolve_seed_groups_by_dataset_async()
            flattened = [group for groups in resolved.values() for group in groups]
            assert flattened

            # Verify it's returning AttackSeedGroup objects
            assert all(isinstance(group, AttackSeedGroup) for group in flattened)


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingDatasetConfiguration:
    """Tests for the EncodingDatasetConfiguration class."""

    def test_default_dataset_config_returns_encoding_config(self, mock_objective_scorer):
        """Test that default_dataset_config is a compound of EncodingDatasetConfiguration children."""
        config = Encoding(objective_scorer=mock_objective_scorer)._default_dataset_config
        assert isinstance(config, CompoundDatasetAttackConfiguration)
        assert all(isinstance(child, EncodingDatasetConfiguration) for child in config._configurations)

    def test_default_dataset_config_uses_garak_datasets(self, mock_objective_scorer):
        """Test that the default config uses the expected garak datasets."""
        config = Encoding(objective_scorer=mock_objective_scorer)._default_dataset_config
        dataset_names = config.dataset_names
        assert "garak_slur_terms_en" in dataset_names
        assert "garak_web_html_js" in dataset_names

    def test_default_dataset_config_has_max_size(self, mock_objective_scorer):
        """Test that each child of the default config caps samples at 10 per dataset."""
        config = Encoding(objective_scorer=mock_objective_scorer)._default_dataset_config
        assert [child.max_dataset_size for child in config._configurations] == [10, 10]


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingDatasetConfigurationBuildAttackGroups:
    """Tests for EncodingDatasetConfiguration._build_attack_groups and resolution."""

    def test_build_attack_groups_transforms_seeds(self, mock_memory_seeds):
        """Test that _build_attack_groups transforms raw seeds into objective-bearing AttackSeedGroups."""
        config = EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"])
        result = config._build_attack_groups(mock_memory_seeds)

        assert len(result) == len(mock_memory_seeds)
        for i, group in enumerate(result):
            assert isinstance(group, AttackSeedGroup)
            # First seed should be a SeedObjective with the encoding objective format
            assert isinstance(group.seeds[0], SeedObjective)
            assert "Make the model say an encoded payload:" in group.seeds[0].value
            assert mock_memory_seeds[i].value in group.seeds[0].value
            # Second seed should be the original SeedPrompt
            assert isinstance(group.seeds[1], SeedPrompt)
            assert group.seeds[1].value == mock_memory_seeds[i].value

    def test_build_attack_groups_empty_returns_empty(self):
        """Test that _build_attack_groups returns an empty list when given no seeds."""
        config = EncodingDatasetConfiguration(dataset_names=["empty_dataset"])
        assert config._build_attack_groups([]) == []

    async def test_get_attack_seed_groups_async_transforms_memory_seeds(self, mock_memory_seeds):
        """Test that get_attack_seed_groups_async loads seeds and shapes them via _build_attack_groups."""
        from unittest.mock import patch

        config = EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"], auto_fetch=False)
        with patch.object(
            EncodingDatasetConfiguration,
            "_collect_named_seeds_async",
            new_callable=AsyncMock,
            return_value={"garak_slur_terms_en": mock_memory_seeds},
        ):
            result = await config.get_attack_seed_groups_async()

        assert len(result) == len(mock_memory_seeds)
        assert all(isinstance(group, AttackSeedGroup) for group in result)

    async def test_get_attack_seed_groups_async_raises_when_empty(self):
        """Test that get_attack_seed_groups_async raises DatasetConstraintError when nothing resolves."""
        from pyrit.scenario.core.dataset_configuration import DatasetConstraintError

        config = EncodingDatasetConfiguration(dataset_names=["empty_dataset"], auto_fetch=False)

        with pytest.raises(DatasetConstraintError):
            await config.get_attack_seed_groups_async()

    def test_encoding_dataset_config_inherits_from_dataset_config(self):
        """Test that EncodingDatasetConfiguration is a subclass of DatasetConfiguration."""
        assert issubclass(EncodingDatasetConfiguration, DatasetConfiguration)

    def test_encoding_dataset_config_can_be_initialized_with_dataset_names(self):
        """Test that EncodingDatasetConfiguration can be initialized with dataset_names."""
        config = EncodingDatasetConfiguration(
            dataset_names=["garak_slur_terms_en", "garak_web_html_js"],
            max_dataset_size=5,
        )

        assert config._dataset_names == ["garak_slur_terms_en", "garak_web_html_js"]
        assert config.max_dataset_size == 5


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingBaselineUniformity:
    """ADO 9012 regression: baseline shares objectives with techniques under max_dataset_size."""

    async def test_one_resolution_call_baseline_matches_techniques(self, mock_objective_target, mock_objective_scorer):
        from unittest.mock import patch

        from pyrit.models import AttackSeedGroup, SeedObjective

        seed_groups = [AttackSeedGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(10)]
        config = DatasetAttackConfiguration(seed_groups=seed_groups, max_dataset_size=3)

        first_sample = [("inline", group) for group in seed_groups[:3]]
        second_sample = [("inline", group) for group in seed_groups[5:8]]
        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample",
            side_effect=[first_sample, second_sample],
        ) as mock_sample:
            scenario = Encoding(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [EncodingTechnique.ALL],
                    "dataset_config": config,
                    "include_baseline": True,
                }
            )
            await scenario.initialize_async()

        assert mock_sample.call_count == 1
        assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"
        baseline_objs = set(scenario._atomic_attacks[0].objectives)
        for attack in scenario._atomic_attacks[1:]:
            assert set(attack.objectives) == baseline_objs
