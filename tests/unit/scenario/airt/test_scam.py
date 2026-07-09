# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Scam class."""

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import ContextComplianceAttack, RedTeamingAttack, RolePlayAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedDataset, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.scenario import DatasetAttackConfiguration, DatasetConfiguration
from pyrit.scenario.scenarios.airt.scam import Scam, ScamTechnique
from pyrit.score import TrueFalseCompositeScorer

SEED_DATASETS_PATH = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
SEED_PROMPT_LIST = list(SeedDataset.from_yaml_file(SEED_DATASETS_PATH / "scams.prompt").get_values())


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
def mock_memory_seed_groups() -> list[SeedAttackGroup]:
    """Create mock seed groups that _get_default_seed_groups() would return."""
    return [SeedAttackGroup(seeds=[SeedObjective(value=prompt)]) for prompt in SEED_PROMPT_LIST]


@pytest.fixture
def mock_memory_seeds():
    """Create mock seeds (SeedObjective objects) from the seed prompt list."""
    return [SeedObjective(value=prompt) for prompt in SEED_PROMPT_LIST]


@pytest.fixture
def mock_dataset_config(mock_memory_seed_groups):
    """Create a mock dataset config that returns the seed groups."""
    seed_attack_groups = list(mock_memory_seed_groups)
    mock_config = MagicMock(spec=DatasetAttackConfiguration)
    mock_config.get_seed_attack_groups_async = AsyncMock(return_value=seed_attack_groups)
    mock_config.get_attack_groups_by_dataset_async = AsyncMock(return_value={"airt_scam": seed_attack_groups})
    mock_config.dataset_names = ["airt_scam"]
    return mock_config


@pytest.fixture
def single_turn_technique() -> ScamTechnique:
    return ScamTechnique.SINGLE_TURN


@pytest.fixture
def multi_turn_technique() -> ScamTechnique:
    return ScamTechnique.MULTI_TURN


@pytest.fixture
def scam_prompts() -> list[str]:
    return SEED_PROMPT_LIST


@pytest.fixture
def mock_runtime_env():
    with patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


@pytest.fixture
def mock_objective_target() -> PromptTarget:
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer() -> TrueFalseCompositeScorer:
    mock = MagicMock(spec=TrueFalseCompositeScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


@pytest.fixture
def mock_adversarial_target() -> PromptTarget:
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockAdversarialTarget")
    return mock


FIXTURES = ["patch_central_database", "mock_runtime_env"]


class TestScamTechniqueEnum:
    """Aggregate expansion for ScamTechnique (DEFAULT curation)."""

    def test_default_expands_to_single_turn_only(self):
        members = {m.value for m in ScamTechnique.expand({ScamTechnique.DEFAULT})}
        assert members == {"context_compliance", "role_play"}

    def test_default_excludes_persuasive_rta(self):
        members = {m.value for m in ScamTechnique.expand({ScamTechnique.DEFAULT})}
        assert "persuasive_rta" not in members

    def test_all_includes_persuasive_rta(self):
        members = {m.value for m in ScamTechnique.expand({ScamTechnique.ALL})}
        assert members == {"context_compliance", "role_play", "persuasive_rta"}

    def test_default_is_aggregate(self):
        assert "default" in ScamTechnique.get_aggregate_tags()
        assert ScamTechnique.DEFAULT in ScamTechnique.get_aggregate_techniques()


@pytest.mark.usefixtures(*FIXTURES)
class TestScamInitialization:
    """Tests for Scam initialization."""

    def test_init_with_default_objectives(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_memory_seed_groups: list[SeedAttackGroup],
    ) -> None:
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            assert scenario.name == "Scam"
            assert scenario.VERSION == 2

    def test_default_technique_is_default(self, mock_objective_scorer) -> None:
        scenario = Scam(objective_scorer=mock_objective_scorer)
        assert scenario._default_technique == ScamTechnique.DEFAULT

    def test_init_with_default_scorer(self, mock_memory_seed_groups) -> None:
        """Test initialization with default scorer."""
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam()
            assert scenario._objective_scorer_identifier

    def test_init_with_custom_scorer(self, *, mock_memory_seed_groups: list[SeedAttackGroup]) -> None:
        """Test initialization with custom scorer."""
        scorer = MagicMock(spec=TrueFalseCompositeScorer)

        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=scorer)
            assert isinstance(scenario._scorer_config, AttackScoringConfig)

    def test_init_default_adversarial_chat(
        self, *, mock_objective_scorer: TrueFalseCompositeScorer, mock_memory_seed_groups: list[SeedAttackGroup]
    ) -> None:
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            assert isinstance(scenario._adversarial_chat, OpenAIChatTarget)
            assert scenario._adversarial_chat._temperature == 1.2

    def test_init_with_adversarial_chat(
        self, *, mock_objective_scorer: TrueFalseCompositeScorer, mock_memory_seed_groups: list[SeedAttackGroup]
    ) -> None:
        adversarial_chat = MagicMock(OpenAIChatTarget)
        adversarial_chat.get_identifier.return_value = _mock_target_id("CustomAdversary")

        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(
                adversarial_chat=adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            assert scenario._adversarial_chat == adversarial_chat
            assert scenario._adversarial_config.target == adversarial_chat

    async def test_init_raises_exception_when_no_datasets_available_async(
        self, mock_objective_target, mock_objective_scorer
    ):
        """Test that initialization raises DatasetConstraintError when datasets are not available in memory."""
        from pyrit.scenario.core.dataset_configuration import DatasetConstraintError

        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = Scam(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups.
        # Neutralize the provider fetch so the empty-memory path raises loudly instead of fetching.
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(DatasetConstraintError, match="could not be loaded"):
                await scenario.initialize_async()


@pytest.mark.usefixtures(*FIXTURES)
class TestScamAttackGeneration:
    """Tests for Scam attack generation."""

    async def test_attack_generation_for_all(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """ALL runs every technique, including the multi-turn PersuasiveRedTeamingAttack."""
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [ScamTechnique.ALL],
                    "dataset_config": mock_dataset_config,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            atomic_attacks = scenario._atomic_attacks

            assert len(atomic_attacks) == 3
            attack_types = {type(run.attack_technique.attack) for run in atomic_attacks}
            assert attack_types == {ContextComplianceAttack, RolePlayAttack, RedTeamingAttack}

    async def test_default_run_yields_single_turn_only(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """No explicit techniques -> DEFAULT -> only the two single-turn techniques, no persuasive_rta."""
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            atomic_attacks = scenario._atomic_attacks

            assert len(atomic_attacks) == 2
            attack_types = {type(run.attack_technique.attack) for run in atomic_attacks}
            assert attack_types == {ContextComplianceAttack, RolePlayAttack}
            assert RedTeamingAttack not in attack_types

    async def test_attack_generation_for_singleturn_async(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        single_turn_technique: ScamTechnique,
        mock_dataset_config: DatasetConfiguration,
    ) -> None:
        """Test that the single turn technique attack generation works."""
        scenario = Scam(
            objective_scorer=mock_objective_scorer,
        )

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [single_turn_technique],
                "dataset_config": mock_dataset_config,
                "include_baseline": False,
            }
        )
        await scenario.initialize_async()
        atomic_attacks = scenario._atomic_attacks

        for run in atomic_attacks:
            assert isinstance(run.attack_technique.attack, (ContextComplianceAttack, RolePlayAttack))

    async def test_attack_generation_for_multiturn_async(
        self, mock_objective_target, mock_objective_scorer, multi_turn_technique, mock_dataset_config
    ):
        """Test that the multi turn attack generation works."""
        scenario = Scam(
            objective_scorer=mock_objective_scorer,
        )

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [multi_turn_technique],
                "dataset_config": mock_dataset_config,
                "include_baseline": False,
            }
        )
        await scenario.initialize_async()
        atomic_attacks = scenario._atomic_attacks

        for run in atomic_attacks:
            assert isinstance(run.attack_technique.attack, RedTeamingAttack)

    async def test_attack_runs_include_objectives_async(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_dataset_config: DatasetConfiguration,
        mock_memory_seeds,
    ) -> None:
        """Test that attack runs include objectives for each seed prompt."""
        scenario = Scam(
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

        for run in atomic_attacks:
            assert len(run.objectives) == len(mock_memory_seeds)
            for index, objective in enumerate(run.objectives):
                assert mock_memory_seeds[index].value in objective

    async def test_get_atomic_attacks_async_returns_attacks(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_dataset_config: DatasetConfiguration,
    ) -> None:
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        scenario = Scam(
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
        assert len(atomic_attacks) > 0
        assert all(run.attack_technique is not None for run in atomic_attacks)


@pytest.mark.usefixtures(*FIXTURES)
class TestScamMaxTurnsParameter:
    """Tests for the declared max_turns parameter (Stage 6 POC)."""

    def test_supported_parameters_declares_max_turns(self):
        """Scam exposes max_turns via supported_parameters."""
        params = Scam.supported_parameters()
        names = [p.name for p in params]
        assert "max_turns" in names

    async def test_max_turns_default_used_when_unset_async(
        self, mock_objective_target, mock_objective_scorer, multi_turn_technique, mock_dataset_config
    ):
        """When set_params_from_args isn't given max_turns, the declared default (5) is used."""
        scenario = Scam(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={})

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [multi_turn_technique],
                "dataset_config": mock_dataset_config,
                "include_baseline": False,
            }
        )
        await scenario.initialize_async()
        atomic_attacks = scenario._atomic_attacks

        for run in atomic_attacks:
            assert isinstance(run.attack_technique.attack, RedTeamingAttack)
            assert run.attack_technique.attack._max_turns == 5

    async def test_max_turns_override_flows_into_attack_async(
        self, mock_objective_target, mock_objective_scorer, multi_turn_technique, mock_dataset_config
    ):
        """A user-supplied max_turns overrides the default and reaches the underlying attack."""
        scenario = Scam(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={"max_turns": 10})

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [multi_turn_technique],
                "dataset_config": mock_dataset_config,
                "include_baseline": False,
                "max_turns": 10,
            }
        )
        await scenario.initialize_async()
        atomic_attacks = scenario._atomic_attacks

        for run in atomic_attacks:
            assert run.attack_technique.attack._max_turns == 10


@pytest.mark.usefixtures(*FIXTURES)
class TestScamLifecycle:
    """Tests for Scam lifecycle behavior."""

    async def test_initialize_async_with_max_concurrency(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_memory_seed_groups: list[SeedAttackGroup],
        mock_dataset_config,
    ) -> None:
        """Test initialization with custom max_concurrency."""
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "max_concurrency": 20,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            assert scenario._max_concurrency == 20

    async def test_initialize_async_with_memory_labels(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_memory_seed_groups: list[SeedAttackGroup],
        mock_dataset_config,
    ) -> None:
        """Test initialization with memory labels."""
        memory_labels = {"type": "scam", "category": "scenario"}

        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "memory_labels": memory_labels,
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestScamProperties:
    """Tests for Scam properties."""

    def test_scenario_version_is_set(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
    ) -> None:
        """Test that scenario version is properly set."""
        scenario = Scam(
            objective_scorer=mock_objective_scorer,
        )

        assert scenario.VERSION == 2

    async def test_no_target_duplication_async(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_memory_seed_groups: list[SeedAttackGroup],
        mock_dataset_config,
    ) -> None:
        """Test that all three targets (adversarial, object, scorer) are distinct."""
        with patch.object(
            Scam,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Scam()
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            objective_target = scenario._objective_target
            scorer_target = scenario._scorer_config.objective_scorer  # type: ignore[arg-type]
            adversarial_target = scenario._adversarial_chat

            assert objective_target != scorer_target
            assert objective_target != adversarial_target
            assert scorer_target != adversarial_target


@pytest.mark.usefixtures(*FIXTURES)
class TestScamBaselineUniformity:
    """ADO 9012 regression: baseline shares objectives with techniques under max_dataset_size."""

    async def test_one_resolution_call_baseline_matches_techniques(
        self, mock_objective_target, mock_objective_scorer, single_turn_technique
    ):
        from pyrit.models import SeedAttackGroup, SeedObjective

        seed_groups = [SeedAttackGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(10)]
        config = DatasetAttackConfiguration(seed_groups=seed_groups, max_dataset_size=3)

        first_sample = [("inline", group) for group in seed_groups[:3]]
        second_sample = [("inline", group) for group in seed_groups[5:8]]
        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample",
            side_effect=[first_sample, second_sample],
        ) as mock_sample:
            scenario = Scam(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [single_turn_technique],
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
