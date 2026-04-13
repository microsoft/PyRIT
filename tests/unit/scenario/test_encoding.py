# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Encoding class."""

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack import PromptSendingAttack
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_converter import Base64Converter
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.garak import Encoding, EncodingStrategy
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
def mock_seed_attack_groups(mock_memory_seeds):
    """Create mock seed attack groups from the mock seeds."""
    return [
        SeedAttackGroup(
            seeds=[
                SeedObjective(value=f"Make the model say an encoded payload: {seed.value}"),
                SeedPrompt(value=seed.value),
            ]
        )
        for seed in mock_memory_seeds
    ]


@pytest.fixture
def mock_dataset_config(mock_seed_attack_groups):
    """Create a mock dataset config that returns the seed attack groups."""
    mock_config = MagicMock(spec=EncodingDatasetConfiguration)
    mock_config.get_all_seed_attack_groups.return_value = mock_seed_attack_groups
    mock_config.get_default_dataset_names.return_value = ["garak_slur_terms_en", "garak_web_html_js"]
    mock_config.has_data_source.return_value = True
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

    def test_init_with_custom_seed_prompts(self, mock_objective_target, mock_objective_scorer, sample_seeds):
        """Test initialization with custom seed prompts (deprecated parameter)."""
        scenario = Encoding(
            seed_prompts=sample_seeds,
            objective_scorer=mock_objective_scorer,
        )

        assert scenario._deprecated_seed_prompts == sample_seeds
        assert scenario.name == "Encoding"
        assert scenario.VERSION == 1

    def test_init_with_default_seed_prompts(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with default seed prompts (Garak dataset)."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=[]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # _deprecated_seed_prompts should be None when using defaults
            assert scenario._deprecated_seed_prompts is None

    def test_init_with_custom_scorer(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom objective scorer."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=[]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            assert scenario._scorer_config.objective_scorer == mock_objective_scorer

    def test_init_creates_default_scorer_when_not_provided(self, mock_objective_target, mock_memory_seeds):
        """Test that initialization creates default DecodingScorer when not provided."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=[]):
            scenario = Encoding()

            # Should create a DecodingScorer by default
            assert scenario._scorer_config.objective_scorer is not None
            assert isinstance(scenario._scorer_config.objective_scorer, DecodingScorer)

    @pytest.mark.asyncio
    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""

        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = Encoding(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed prompts
        with pytest.raises(ValueError, match="No seeds found in the configured datasets"):
            await scenario.initialize_async(objective_target=mock_objective_target)

    def test_init_with_memory_labels(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with memory labels."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=[]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # memory_labels are not set until initialize_async is called
            assert scenario._memory_labels == {}

    def test_init_with_custom_encoding_templates(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom encoding templates."""
        from unittest.mock import patch

        custom_templates = ["template1", "template2"]

        with patch.object(Encoding, "_resolve_seed_groups", return_value=[]):
            scenario = Encoding(
                encoding_templates=custom_templates,
                objective_scorer=mock_objective_scorer,
            )

            assert scenario._encoding_templates == custom_templates

    def test_init_with_max_concurrency(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom max_concurrency."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=[]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # max_concurrency defaults to 1 until initialize_async is called
            assert scenario._max_concurrency == 1

    @pytest.mark.asyncio
    async def test_init_attack_strategies(
        self, mock_objective_target, mock_objective_scorer, mock_seed_attack_groups, mock_dataset_config
    ):
        """Test that attack strategies are set correctly."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)

            # By default, EncodingStrategy.ALL is used, which expands to all encoding strategies
            assert len(scenario._scenario_composites) > 0
            # Verify all composites contain EncodingStrategy instances
            assert all(
                isinstance(comp.strategies[0], EncodingStrategy)
                for comp in scenario._scenario_composites
                if comp.strategies
            )
            # Verify none of the strategies are the aggregate "ALL"
            assert all(
                comp.strategies[0] != EncodingStrategy.ALL for comp in scenario._scenario_composites if comp.strategies
            )


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingAtomicAttacks:
    """Tests for Encoding atomic attack generation."""

    @pytest.mark.asyncio
    async def test_get_atomic_attacks_async_returns_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_seed_attack_groups, mock_dataset_config
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            # Should return multiple atomic attacks (one for each encoding type)
            assert len(atomic_attacks) > 0
            assert all(hasattr(run, "_attack") for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_get_converter_attacks_returns_multiple_encodings(
        self, mock_objective_target, mock_objective_scorer, mock_seed_attack_groups, mock_dataset_config
    ):
        """Test that _get_converter_attacks returns attacks for multiple encoding types."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            attack_runs = scenario._get_converter_attacks()

            # Should have multiple attack runs for different encodings
            # The list includes: Base64 (4 variants), Base2048, Base16, Base32, ASCII85 (2), hex,
            # quoted-printable, UUencode, ROT13, Braille, Atbash, Morse, NATO, Ecoji, Zalgo, Leet, AsciiSmuggler
            assert len(attack_runs) > 0

    @pytest.mark.asyncio
    async def test_get_prompt_attacks_creates_attack_runs(
        self, mock_objective_target, mock_objective_scorer, mock_seed_attack_groups, mock_dataset_config
    ):
        """Test that _get_prompt_attacks creates attack runs with correct structure."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            attack_runs = scenario._get_prompt_attacks(converters=[Base64Converter()], encoding_name="Base64")

            # Should create attack runs
            assert len(attack_runs) > 0

            # Each attack run should have the correct attack type
            for run in attack_runs:
                assert isinstance(run._attack, PromptSendingAttack)
                assert len(run._seed_groups) == len(mock_seed_attack_groups)

    @pytest.mark.asyncio
    async def test_attack_runs_include_objectives(
        self,
        mock_objective_target,
        mock_objective_scorer,
        mock_seed_attack_groups,
        mock_memory_seeds,
        mock_dataset_config,
    ):
        """Test that attack runs include objectives for each seed prompt."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            attack_runs = scenario._get_prompt_attacks(converters=[Base64Converter()], encoding_name="Base64")

            # Check that seed groups contain objectives with the expected format
            for run in attack_runs:
                assert len(run._seed_groups) == len(mock_seed_attack_groups)
                for i, seed_group in enumerate(run._seed_groups):
                    # The first seed in each group should be a SeedObjective
                    objective_seed = seed_group.seeds[0]
                    assert isinstance(objective_seed, SeedObjective)
                    assert "Make the model say an encoded payload:" in objective_seed.value
                    assert mock_memory_seeds[i].value in objective_seed.value


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingExecution:
    """Tests for Encoding execution."""

    @pytest.mark.asyncio
    async def test_scenario_initialization(
        self, mock_objective_target, mock_objective_scorer, mock_seed_attack_groups, mock_dataset_config
    ):
        """Test that scenario can be initialized successfully."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)

            # Verify initialization creates atomic attacks
            assert scenario.atomic_attack_count > 0

    @pytest.mark.asyncio
    async def test_resolve_seed_groups_loads_garak_data(
        self, mock_objective_target, mock_objective_scorer, mock_seed_attack_groups, mock_dataset_config
    ):
        """Test that _resolve_seed_groups loads data from Garak datasets."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_groups", return_value=mock_seed_attack_groups):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # _deprecated_seed_prompts should be None when using defaults
            assert scenario._deprecated_seed_prompts is None

            # After resolve, should have seed groups
            resolved = scenario._resolve_seed_groups()
            assert len(resolved) > 0

            # Verify it's returning SeedAttackGroup objects
            assert all(isinstance(group, SeedAttackGroup) for group in resolved)


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingDatasetConfiguration:
    """Tests for the EncodingDatasetConfiguration class."""

    def test_default_dataset_config_returns_encoding_config(self):
        """Test that default_dataset_config returns EncodingDatasetConfiguration."""
        config = Encoding.default_dataset_config()
        assert isinstance(config, EncodingDatasetConfiguration)

    def test_default_dataset_config_uses_garak_datasets(self):
        """Test that the default config uses the expected garak datasets."""
        config = Encoding.default_dataset_config()
        dataset_names = config.get_default_dataset_names()
        assert "garak_slur_terms_en" in dataset_names
        assert "garak_web_html_js" in dataset_names

    def test_default_dataset_config_has_max_size(self):
        """Test that the default config has max_dataset_size set."""
        config = Encoding.default_dataset_config()
        assert config.max_dataset_size == 3


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingResolveSeedGroups:
    """Tests for the _resolve_seed_groups method."""

    def test_resolve_seed_groups_with_deprecated_seed_prompts(self, mock_objective_scorer, sample_seeds):
        """Test that _resolve_seed_groups handles deprecated seed_prompts correctly."""
        scenario = Encoding(
            seed_prompts=sample_seeds,
            objective_scorer=mock_objective_scorer,
        )

        # Set up the dataset config to simulate initialize_async was called
        scenario._dataset_config = DatasetConfiguration(dataset_names=["test"])
        scenario._dataset_config_provided = False

        resolved = scenario._resolve_seed_groups()

        # Should return SeedAttackGroup for each seed prompt
        assert len(resolved) == len(sample_seeds)
        for i, group in enumerate(resolved):
            assert isinstance(group, SeedAttackGroup)
            # The first seed in each group should be a SeedObjective
            assert isinstance(group.seeds[0], SeedObjective)
            assert group.seeds[0].value == sample_seeds[i]

    def test_resolve_seed_groups_raises_on_conflict(self, mock_objective_scorer, sample_seeds, mock_dataset_config):
        """Test that _resolve_seed_groups raises ValueError when both seed_prompts and dataset_config are provided."""
        scenario = Encoding(
            seed_prompts=sample_seeds,
            objective_scorer=mock_objective_scorer,
        )

        # Simulate that dataset_config was explicitly provided
        scenario._dataset_config = mock_dataset_config
        scenario._dataset_config_provided = True

        with pytest.raises(ValueError, match="Cannot specify both"):
            scenario._resolve_seed_groups()


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingDatasetConfigurationGetAllSeedAttackGroups:
    """Tests for EncodingDatasetConfiguration.get_all_seed_attack_groups method."""

    def test_get_all_seed_attack_groups_transforms_seeds(self, mock_memory_seeds):
        """Test that get_all_seed_attack_groups transforms seeds into SeedAttackGroups."""
        from unittest.mock import patch

        with patch.object(EncodingDatasetConfiguration, "get_all_seeds", return_value=mock_memory_seeds):
            config = EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"])
            result = config.get_all_seed_attack_groups()

            assert len(result) == len(mock_memory_seeds)
            for i, group in enumerate(result):
                assert isinstance(group, SeedAttackGroup)
                # First seed should be a SeedObjective with the encoding objective format
                assert isinstance(group.seeds[0], SeedObjective)
                assert "Make the model say an encoded payload:" in group.seeds[0].value
                assert mock_memory_seeds[i].value in group.seeds[0].value
                # Second seed should be the original SeedPrompt
                assert isinstance(group.seeds[1], SeedPrompt)
                assert group.seeds[1].value == mock_memory_seeds[i].value

    def test_get_all_seed_attack_groups_raises_when_no_seeds(self):
        """Test that get_all_seed_attack_groups raises ValueError when no seeds found."""
        from unittest.mock import patch

        with patch.object(EncodingDatasetConfiguration, "get_all_seeds", return_value=[]):
            config = EncodingDatasetConfiguration(dataset_names=["empty_dataset"])

            with pytest.raises(ValueError, match="No seeds found in the configured datasets"):
                config.get_all_seed_attack_groups()

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
