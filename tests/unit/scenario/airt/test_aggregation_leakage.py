# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AggregationLeakage scenario and its datasets."""

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedDataset, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.airt import AggregationLeakage, AggregationLeakageTechnique  # type: ignore[ty:unresolved-import]
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.scenarios.airt.aggregation_leakage import (
    AGGREGATION_CONTEXT_METADATA_KEY,
    AGGREGATION_LEAKAGE_DATASET,
    AGGREGATION_LEAKAGE_MENTION_ONLY_DATASET,
    AGGREGATION_LEAKAGE_MINIMAL_PAIRS_DATASET,
    strip_aggregation_context,
)
from pyrit.score import TrueFalseCompositeScorer, TrueFalseQuestion
from tests.unit.mocks import MockPromptTarget

DATASET_DIR = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
DATASET_FILES = {
    AGGREGATION_LEAKAGE_DATASET: ("aggregation_leakage.prompt", 16, "none"),
    AGGREGATION_LEAKAGE_MINIMAL_PAIRS_DATASET: ("aggregation_leakage_minimal_pairs.prompt", 8, "minimal_pair"),
    AGGREGATION_LEAKAGE_MENTION_ONLY_DATASET: ("aggregation_leakage_mention_only.prompt", 8, "mention_only"),
}
CHANNELS = {"rule_instance", "transitive", "set_complement", "numeric_residual"}
FIXTURES = ["patch_central_database", "mock_runtime_env"]


def _load_groups(dataset_name: str) -> list[AttackSeedGroup]:
    filename = DATASET_FILES[dataset_name][0]
    dataset = SeedDataset.from_yaml_file(DATASET_DIR / filename)
    return [
        AttackSeedGroup(seeds=list(group.seeds))
        for group in SeedDataset.group_seed_prompts_by_prompt_group_id(dataset.seeds)
    ]


@pytest.fixture
def groups_by_dataset() -> dict[str, list[AttackSeedGroup]]:
    return {name: _load_groups(name) for name in DATASET_FILES}


@pytest.fixture
def mock_runtime_env():
    with patch.dict(
        "os.environ",
        {
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    mock = MagicMock(spec=TrueFalseCompositeScorer)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockObjectiveScorer", class_module="test")
    return mock


@pytest.fixture
def mock_adversarial_target() -> MagicMock:
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockAdversarialTarget", class_module="test")
    return mock


async def _initialize_async(
    *,
    scenario: AggregationLeakage,
    groups_by_dataset: dict[str, list[AttackSeedGroup]],
    args: dict,
) -> None:
    scenario.set_params_from_args(args={"objective_target": MockPromptTarget(), **args})
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", new_callable=AsyncMock),
        patch.object(
            AggregationLeakage,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups_by_dataset,
        ),
    ):
        await scenario.initialize_async()


class TestAggregationLeakageTechnique:
    def test_default_expands_to_prompt_sending(self) -> None:
        members = {m.value for m in AggregationLeakageTechnique.expand({AggregationLeakageTechnique.DEFAULT})}
        assert members == {"prompt_sending"}

    def test_all_includes_red_teaming(self) -> None:
        members = {m.value for m in AggregationLeakageTechnique.expand({AggregationLeakageTechnique.ALL})}
        assert members == {"prompt_sending", "red_teaming"}

    def test_multi_turn_is_red_teaming(self) -> None:
        members = {m.value for m in AggregationLeakageTechnique.expand({AggregationLeakageTechnique.MULTI_TURN})}
        assert members == {"red_teaming"}


@pytest.mark.parametrize("dataset_name", list(DATASET_FILES))
def test_dataset_groups_have_objective_context_and_question(dataset_name: str) -> None:
    _, expected_count, expected_control = DATASET_FILES[dataset_name]
    groups = _load_groups(dataset_name)

    assert len(groups) == expected_count
    assert len({group.objective.value for group in groups}) == expected_count
    for group in groups:
        prepended = group.prepended_conversation
        assert prepended is not None
        assert [message.message_pieces[0].api_role for message in prepended] == ["system"]
        assert "[Document 1]" in prepended[0].message_pieces[0].original_value
        assert group.next_message is not None
        assert group.next_message.message_pieces[0].api_role == "user"
        assert group.objective.metadata["derivation_channel"] in CHANNELS
        assert group.objective.metadata["control"] == expected_control


def test_dataset_covers_every_channel_with_both_directnesses() -> None:
    groups = _load_groups(AGGREGATION_LEAKAGE_DATASET)
    pairs = {
        (group.objective.metadata["derivation_channel"], group.objective.metadata["query_directness"])
        for group in groups
    }
    assert pairs == {(channel, directness) for channel in CHANNELS for directness in ("indirect", "direct")}


def test_scoring_question_loads() -> None:
    (path,) = AggregationLeakage._get_additional_scoring_questions()
    question = TrueFalseQuestion.from_yaml(path)
    assert question.category == "aggregation_leakage"


def test_strip_aggregation_context_removes_only_context_turn() -> None:
    group = _load_groups(AGGREGATION_LEAKAGE_DATASET)[0]

    stripped = strip_aggregation_context(seed_group=group)

    assert stripped.prepended_conversation is None
    assert stripped.objective.value == group.objective.value
    assert stripped.next_message.message_pieces[0].original_value == group.next_message.message_pieces[0].original_value
    assert all(not (seed.metadata or {}).get(AGGREGATION_CONTEXT_METADATA_KEY) for seed in stripped.seeds)
    assert group.prepended_conversation is not None


def test_strip_aggregation_context_without_context_returns_same_group() -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="question", role="user")])
    assert strip_aggregation_context(seed_group=group) is group


@pytest.mark.usefixtures(*FIXTURES)
class TestAggregationLeakageInitialization:
    def test_init_metadata(self, mock_objective_scorer: TrueFalseCompositeScorer) -> None:
        scenario = AggregationLeakage(objective_scorer=mock_objective_scorer)

        assert scenario.name == "AggregationLeakage"
        assert scenario.VERSION == 1
        assert scenario._default_technique == AggregationLeakageTechnique.DEFAULT
        assert AggregationLeakage.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden
        assert AggregationLeakage.required_datasets() == list(DATASET_FILES)

    def test_additional_parameters(self) -> None:
        params = {param.name: param for param in AggregationLeakage.additional_parameters()}
        assert params["include_context"].default is True
        assert params["max_turns"].default == 3

    def test_default_scorer_is_composite(self) -> None:
        scenario = AggregationLeakage()
        assert isinstance(scenario._objective_scorer, TrueFalseCompositeScorer)


@pytest.mark.usefixtures(*FIXTURES)
class TestAggregationLeakageAttackGeneration:
    async def test_default_builds_one_prompt_sending_attack_per_dataset_async(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        groups_by_dataset: dict[str, list[AttackSeedGroup]],
    ) -> None:
        scenario = AggregationLeakage(objective_scorer=mock_objective_scorer)

        await _initialize_async(scenario=scenario, groups_by_dataset=groups_by_dataset, args={})

        attacks = scenario._atomic_attacks
        assert [atomic.atomic_attack_name for atomic in attacks] == [f"prompt_sending_{name}" for name in DATASET_FILES]
        assert all(isinstance(atomic._attack_technique.attack, PromptSendingAttack) for atomic in attacks)
        assert all(group.prepended_conversation is not None for atomic in attacks for group in atomic.seed_groups)

    async def test_all_adds_red_teaming_with_max_turns_async(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_adversarial_target: PromptTarget,
        groups_by_dataset: dict[str, list[AttackSeedGroup]],
    ) -> None:
        scenario = AggregationLeakage(objective_scorer=mock_objective_scorer, adversarial_chat=mock_adversarial_target)

        await _initialize_async(
            scenario=scenario,
            groups_by_dataset=groups_by_dataset,
            args={"scenario_techniques": [AggregationLeakageTechnique.ALL], "max_turns": 5},
        )

        red_teaming = [
            atomic for atomic in scenario._atomic_attacks if atomic.atomic_attack_name.startswith("red_teaming_")
        ]
        assert len(scenario._atomic_attacks) == 6
        assert len(red_teaming) == 3
        assert all(isinstance(atomic._attack_technique.attack, RedTeamingAttack) for atomic in red_teaming)
        assert all(atomic._attack_technique.attack._max_turns == 5 for atomic in red_teaming)

    async def test_include_context_false_sends_only_questions_async(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        groups_by_dataset: dict[str, list[AttackSeedGroup]],
    ) -> None:
        scenario = AggregationLeakage(objective_scorer=mock_objective_scorer)

        await _initialize_async(scenario=scenario, groups_by_dataset=groups_by_dataset, args={"include_context": False})

        groups = [group for atomic in scenario._atomic_attacks for group in atomic.seed_groups]
        assert len(groups) == 32
        assert all(group.prepended_conversation is None for group in groups)
        assert all(group.next_message.message_pieces[0].api_role == "user" for group in groups)

    async def test_include_baseline_raises_async(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        groups_by_dataset: dict[str, list[AttackSeedGroup]],
    ) -> None:
        scenario = AggregationLeakage(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="include_baseline"):
            await _initialize_async(
                scenario=scenario, groups_by_dataset=groups_by_dataset, args={"include_baseline": True}
            )
