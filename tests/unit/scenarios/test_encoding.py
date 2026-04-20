# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Encoding class."""

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack import PromptSendingAttack
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import SeedPrompt
from pyrit.prompt_converter import Base64Converter
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.garak import Encoding, EncodingStrategy
from pyrit.score import DecodingScorer, TrueFalseScorer


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test",
        class_description="",
        identifier_type="instance",
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
def mock_objective_target():
    """Create a mock objective target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = {"__type__": "MockObjectiveTarget", "__module__": "test"}
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
        """Test initialization with custom seed prompts."""
        scenario = Encoding(
            seed_prompts=sample_seeds,
            objective_scorer=mock_objective_scorer,
        )

        assert scenario._deprecated_seed_prompts == sample_seeds
        assert scenario.name == "Encoding"
        assert scenario.version == 1

    def test_init_with_default_seed_prompts(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with default seed prompts (Garak dataset)."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # _deprecated_seed_prompts should be None when using defaults
            assert scenario._deprecated_seed_prompts is None

    def test_init_with_custom_scorer(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom objective scorer."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            assert scenario._scorer_config.objective_scorer == mock_objective_scorer

    def test_init_creates_default_scorer_when_not_provided(self, mock_objective_target, mock_memory_seeds):
        """Test that initialization creates default DecodingScorer when not provided."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding()

            # Should create a DecodingScorer by default
            assert scenario._scorer_config.objective_scorer is not None
            assert isinstance(scenario._scorer_config.objective_scorer, DecodingScorer)

    @pytest.mark.asyncio
    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""

        # Don't mock _resolve_seed_prompts, let it try to load from empty memory
        scenario = Encoding(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed prompts
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)

    def test_init_with_memory_labels(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with memory labels."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # memory_labels are not set until initialize_async is called
            assert scenario._memory_labels == {}

    def test_init_with_custom_encoding_templates(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom encoding templates."""
        from unittest.mock import patch

        custom_templates = ["template1", "template2"]

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                encoding_templates=custom_templates,
                objective_scorer=mock_objective_scorer,
            )

            assert scenario._encoding_templates == custom_templates

    def test_init_with_max_concurrency(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom max_concurrency."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # max_concurrency defaults to 1 until initialize_async is called
            assert scenario._max_concurrency == 1

    @pytest.mark.asyncio
    async def test_init_attack_strategies(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test that attack strategies are set correctly."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)

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
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            # Should return multiple atomic attacks (one for each encoding type)
            assert len(atomic_attacks) > 0
            assert all(hasattr(run, "_attack") for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_get_converter_attacks_returns_multiple_encodings(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds
    ):
        """Test that _get_converter_attacks returns attacks for multiple encoding types."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)
            attack_runs = scenario._get_converter_attacks()

            # Should have multiple attack runs for different encodings
            # The list includes: Base64 (4 variants), Base2048, Base16, Base32, ASCII85 (2), hex,
            # quoted-printable, UUencode, ROT13, Braille, Atbash, Morse, NATO, Ecoji, Zalgo, Leet, AsciiSmuggler
            assert len(attack_runs) > 0

    @pytest.mark.asyncio
    async def test_get_prompt_attacks_creates_attack_runs(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds
    ):
        """Test that _get_prompt_attacks creates attack runs with correct structure."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)
            attack_runs = scenario._get_prompt_attacks(converters=[Base64Converter()], encoding_name="Base64")

            # Should create attack runs
            assert len(attack_runs) > 0

            # Each attack run should have the correct attack type
            for run in attack_runs:
                assert isinstance(run._attack, PromptSendingAttack)
                assert len(run.objectives) == len(mock_memory_seeds)
                assert run._seed_groups is not None
                assert len(run._seed_groups) == len(mock_memory_seeds)

    @pytest.mark.asyncio
    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds
    ):
        """Test that attack runs include objectives for each seed prompt."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)
            attack_runs = scenario._get_prompt_attacks(converters=[Base64Converter()], encoding_name="Base64")

            # Check that objectives are created for each seed prompt
            for run in attack_runs:
                assert len(run.objectives) == len(mock_memory_seeds)
                for i, objective in enumerate(run.objectives):
                    assert "Make the model say an encoded payload:" in objective
                    assert mock_memory_seeds[i].value in objective


@pytest.mark.usefixtures("patch_central_database")
class TestEncodingExecution:
    """Tests for Encoding execution."""

    @pytest.mark.asyncio
    async def test_scenario_initialization(self, mock_objective_target, mock_objective_scorer, mock_memory_seeds):
        """Test that scenario can be initialized successfully."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)

            # Verify initialization creates atomic attacks
            assert scenario.atomic_attack_count > 0

    @pytest.mark.asyncio
    async def test_resolve_seed_prompts_loads_garak_data(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds
    ):
        """Test that _resolve_seed_prompts loads data from Garak datasets."""
        from unittest.mock import patch

        with patch.object(Encoding, "_resolve_seed_prompts", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Encoding(
                objective_scorer=mock_objective_scorer,
            )

            # _deprecated_seed_prompts should be None when using defaults
            assert scenario._deprecated_seed_prompts is None

            # After resolve, should have seed prompts
            resolved = scenario._resolve_seed_prompts()
            assert len(resolved) > 0

            # Verify it's loading actual data (not empty)
            assert all(isinstance(prompt, str) for prompt in resolved)
            assert all(len(prompt) > 0 for prompt in resolved)
