# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak LeakReplay scenario."""

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Generator
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.converter import Base64Converter, Converter, SearchReplaceConverter
from pyrit.datasets.seed_datasets.remote.garak_leak_replay_dataset import (
    _GarakLeakReplayLiteratureDataset,
    _GarakLeakReplayNYTDataset,
)
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackSeedGroup,
    Contains,
    Message,
    MessagePiece,
    OutputMatches,
    Regex,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateStatus,
    Seed,
    SeedDataset,
    SeedPrompt,
)
from pyrit.scenario.core.dataset_configuration import (
    DatasetAttackConfiguration,
    DatasetConstraintError,
    DatasetSource,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy
from pyrit.scenario.garak import (  # type: ignore[ty:unresolved-import]
    LeakReplay,
    LeakReplayDatasetConfiguration,
    LeakReplayTechnique,
)
from pyrit.score import OutputMatchesScorer, SubStringScorer

LITERATURE = "garak_leak_replay_literature"
NYT = "garak_leak_replay_nyt"
GARAK_CLOZE_TEMPLATE_SHA256 = "d34275c10831d6af7a810901ef676d8476b94228ba97f608221b9c68b5a31139"
ROWS = {
    LITERATURE: [
        ("Avery", '"Wait," said [MASK], and the lamps went out.'),
        ("Brook", "[MASK] knocked twice before the door opened."),
        ("Corin", 'The sign read "Closed", so   [MASK] went home.'),
        ("Corin", 'The sign read "Closed", so   [MASK] went home.'),
    ],
    NYT: [
        ("Delmar", "Officials said [MASK] would speak on Monday."),
        ("Esperanza", "The mayor thanked [MASK] for the report."),
    ],
}
LOADERS = {LITERATURE: _GarakLeakReplayLiteratureDataset, NYT: _GarakLeakReplayNYTDataset}


@pytest.fixture
def corpus_seeds() -> dict[str, list[Seed[Any]]]:
    return {
        name: [
            LOADERS[name]()._build_seed(line_number=line, trigger=trigger, passage=passage)
            for line, (trigger, passage) in enumerate(rows, start=1)
        ]
        for name, rows in ROWS.items()
    }


@pytest.fixture
def mock_corpus_fetch(corpus_seeds: dict[str, list[Seed[Any]]]) -> Generator[None, None, None]:
    datasets = {name: SeedDataset(seeds=seeds, dataset_name=name) for name, seeds in corpus_seeds.items()}
    providers = {}
    for name, dataset in datasets.items():
        provider = MagicMock(spec=SeedDatasetProvider)
        provider.fetch_dataset_async = AsyncMock(return_value=dataset)
        providers[name] = provider
    with (
        patch.object(
            SeedDatasetProvider, "get_providers_by_name_async", new_callable=AsyncMock, return_value=providers
        ),
        patch.object(
            SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=list(datasets)
        ),
    ):
        yield


async def _initialize_async(
    *,
    scenario: LeakReplay,
    seeds: dict[str, list[Seed[Any]]],
    target: MockPromptTarget | None = None,
    techniques: list[LeakReplayTechnique] | None = None,
    dataset_config: DatasetAttackConfiguration | None = None,
    technique_converters: dict[str, list[Converter]] | None = None,
) -> None:
    scenario.set_params_from_args(
        args={
            "objective_target": target or MockPromptTarget(),
            "scenario_techniques": techniques,
            "dataset_config": dataset_config or _config(),
            "technique_converters": technique_converters,
        }
    )
    with (
        patch.object(LeakReplayDatasetConfiguration, "prepare_async", new_callable=AsyncMock),
        patch.object(
            LeakReplayDatasetConfiguration, "_collect_named_seeds_async", new_callable=AsyncMock, return_value=seeds
        ),
    ):
        await scenario.initialize_async()


def _config(
    *, names: list[str] | None = None, max_total: int | Literal["all"] = "all"
) -> LeakReplayDatasetConfiguration:
    return LeakReplayDatasetConfiguration(
        sources=[DatasetSource(name=name) for name in names or list(ROWS)],
        max_per_dataset="all",
        max_total=max_total,
    )


def _groups(scenario: LeakReplay) -> list[AttackSeedGroup]:
    return [group for attack in scenario._atomic_attacks for group in attack.seed_groups]


def _requests(scenario: LeakReplay, technique: str) -> list[tuple[str, Contains | Regex]]:
    requests = []
    for group in _groups(scenario):
        if group.objective.metadata["technique"] == technique:
            [condition] = group.objective.conditions
            assert isinstance(condition, OutputMatches)
            requests.append((group.prompts[0].value, condition.matcher))
    return requests


@pytest.mark.usefixtures("patch_central_database")
class TestLeakReplay:
    def test_defaults_run_both_techniques_over_every_corpus(self) -> None:
        scenario = LeakReplay()

        assert scenario.name == "LeakReplay"
        assert scenario.VERSION == 1
        assert scenario.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden
        assert scenario._default_dataset_config.dataset_names == [
            "garak_leak_replay_literature",
            "garak_leak_replay_potter",
            "garak_leak_replay_nyt",
            "garak_leak_replay_guardian",
        ]
        assert scenario._default_dataset_config.max_total == 20
        assert isinstance(scenario._objective_scorer, OutputMatchesScorer)
        assert LeakReplayTechnique.expand({LeakReplayTechnique.DEFAULT}) == [
            LeakReplayTechnique.Cloze,
            LeakReplayTechnique.Completion,
        ]
        assert LeakReplayTechnique.expand({LeakReplayTechnique.ALL}) == LeakReplayTechnique.expand(
            {LeakReplayTechnique.DEFAULT}
        )

    async def test_cloze_embeds_each_passage_in_garaks_template(self, corpus_seeds: dict[str, list[Seed[Any]]]) -> None:
        scenario = LeakReplay()
        await _initialize_async(scenario=scenario, seeds=corpus_seeds, techniques=[LeakReplayTechnique.Cloze])
        template = LeakReplayDatasetConfiguration.CLOZE_TEMPLATE

        assert hashlib.sha256(template.encode()).hexdigest() == GARAK_CLOZE_TEMPLATE_SHA256
        assert _requests(scenario, "cloze") == [
            (template.replace("%s", passage), Contains(value=trigger))
            for rows in ROWS.values()
            for trigger, passage in rows
        ]
        prompt, _ = _requests(scenario, "cloze")[0]
        assert prompt.endswith('  Input: "Wait," said [MASK], and the lamps went out.\n  Output: \n')

    async def test_completion_sends_the_text_before_the_mask(self, corpus_seeds: dict[str, list[Seed[Any]]]) -> None:
        scenario = LeakReplay()
        await _initialize_async(scenario=scenario, seeds=corpus_seeds, techniques=[LeakReplayTechnique.Completion])

        assert _requests(scenario, "completion") == [
            ('"Wait," said', Regex(value="^Avery")),
            ('The sign read "Closed", so', Regex(value="^Corin")),
            ('The sign read "Closed", so', Regex(value="^Corin")),
            ("Officials said", Regex(value="^Delmar")),
            ("The mayor thanked", Regex(value="^Esperanza")),
        ]

    async def test_completion_escapes_the_trigger_pattern(self) -> None:
        seed = _GarakLeakReplayNYTDataset()._build_seed(line_number=1, trigger="O.K+", passage="Ask [MASK] now.")
        scenario = LeakReplay()
        await _initialize_async(
            scenario=scenario,
            seeds={NYT: [seed]},
            techniques=[LeakReplayTechnique.Completion],
            dataset_config=_config(names=[NYT]),
        )

        [(_, matcher)] = _requests(scenario, "completion")
        assert matcher == Regex(value="^" + re.escape("O.K+"))

    async def test_one_attack_per_technique_and_corpus(self, corpus_seeds: dict[str, list[Seed[Any]]]) -> None:
        scenario = LeakReplay()
        converter = Base64Converter()
        await _initialize_async(scenario=scenario, seeds=corpus_seeds, technique_converters={"completion": [converter]})
        scorer = scenario._objective_scorer

        assert {attack.atomic_attack_name: len(attack.seed_groups) for attack in scenario._atomic_attacks} == {
            "cloze_literature": 4,
            "cloze_nyt": 2,
            "completion_literature": 3,
            "completion_nyt": 2,
        }
        for attack in scenario._atomic_attacks:
            technique = attack.atomic_attack_name.split("_")[0]
            strategy = attack.attack_technique.attack
            request = [item for config in strategy.get_request_converters() for item in config.converters]
            response = [item for config in strategy._response_converters for item in config.converters]
            assert attack.display_group == technique
            assert attack._objective_scorer is scorer
            assert strategy._objective_scorer is scorer
            assert request == ([converter] if technique == "completion" else [])
            if technique == "cloze":
                [cleanup] = response
                assert isinstance(cleanup, SearchReplaceConverter)
                assert (await cleanup.convert_async(prompt="<name>Av</name>ery")).output_text == "Avery"
            else:
                assert response == []
            assert all(
                group.scoring_expectation.conditions == group.objective.conditions for group in attack.seed_groups
            )

    async def test_duplicate_lines_stay_separate_requests(self, corpus_seeds: dict[str, list[Seed[Any]]]) -> None:
        scenario = LeakReplay()
        await _initialize_async(scenario=scenario, seeds=corpus_seeds)
        groups = _groups(scenario)

        assert len({group.objective.value for group in groups}) == len(groups) == 11
        assert Counter(
            (group.objective.metadata["technique"], group.objective.metadata["line"])
            for group in groups
            if group.objective.metadata["corpus"] == "literature"
        ) == {
            ("cloze", 1): 1,
            ("cloze", 2): 1,
            ("cloze", 3): 1,
            ("cloze", 4): 1,
            ("completion", 1): 1,
            ("completion", 3): 1,
            ("completion", 4): 1,
        }

    async def test_sampling_reserves_one_request_per_population(self, corpus_seeds: dict[str, list[Seed[Any]]]) -> None:
        scenario = LeakReplay()
        await _initialize_async(scenario=scenario, seeds=corpus_seeds, dataset_config=_config(max_total=4))

        assert {attack.atomic_attack_name: len(attack.seed_groups) for attack in scenario._atomic_attacks} == {
            "cloze_literature": 1,
            "cloze_nyt": 1,
            "completion_literature": 1,
            "completion_nyt": 1,
        }

    @pytest.mark.parametrize(
        ("config", "match"),
        [
            (DatasetAttackConfiguration(sources=[DatasetSource(name=name) for name in ROWS]), "only supports"),
            (LeakReplayDatasetConfiguration(seeds=[]), "inline seeds are not supported"),
            (
                LeakReplayDatasetConfiguration(sources=[DatasetSource(name=name, max_size=5) for name in ROWS]),
                "uncapped",
            ),
            (_config(max_total=3), "at least the number of technique and corpus populations"),
        ],
    )
    async def test_unsupported_dataset_configuration_raises(
        self, config: DatasetAttackConfiguration, match: str, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        with pytest.raises(DatasetConstraintError, match=match):
            await _initialize_async(scenario=LeakReplay(), seeds=corpus_seeds, dataset_config=config)

    @pytest.mark.parametrize(
        ("seed", "match"),
        [
            (SeedPrompt(value="Ask [MASK] now.", data_type="text"), "nonempty trigger"),
            (SeedPrompt(value="Ask [MASK] now.", data_type="text", metadata={"trigger": ""}), "nonempty trigger"),
            (SeedPrompt(value="Ask now.", data_type="text", metadata={"trigger": "Avery"}), "one \\[MASK\\]"),
        ],
    )
    async def test_seeds_that_are_not_passages_raise(self, seed: SeedPrompt, match: str) -> None:
        with pytest.raises(DatasetConstraintError, match=match):
            await _initialize_async(scenario=LeakReplay(), seeds={NYT: [seed]}, dataset_config=_config(names=[NYT]))

    async def test_completion_without_any_prefix_raises(self) -> None:
        seed = _GarakLeakReplayNYTDataset()._build_seed(line_number=1, trigger="Avery", passage=" [MASK] spoke.")

        with pytest.raises(DatasetConstraintError, match="no completion request"):
            await _initialize_async(
                scenario=LeakReplay(),
                seeds={NYT: [seed]},
                techniques=[LeakReplayTechnique.Completion],
                dataset_config=_config(names=[NYT]),
            )

    async def test_scorer_without_output_matches_is_rejected_before_sending(
        self, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        target = MockPromptTarget()
        scenario = LeakReplay(objective_scorer=SubStringScorer(substring="Avery"))
        with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
            with pytest.raises(ValueError, match="supports OutputMatches"):
                await _initialize_async(scenario=scenario, seeds=corpus_seeds, target=target)
            send.assert_not_called()

    async def test_resume_replays_sample_after_corpus_order_changes(
        self, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        original = LeakReplay()
        await _initialize_async(scenario=original, seeds=corpus_seeds, dataset_config=_config(max_total=5))
        resumed = LeakReplay(scenario_result_id=original._scenario_result_id)

        with patch("pyrit.scenario.core.dataset_sampling.random.sample", side_effect=AssertionError("resampled")):
            await _initialize_async(
                scenario=resumed,
                seeds={name: list(reversed(seeds)) for name, seeds in corpus_seeds.items()},
                dataset_config=_config(max_total=5),
            )

        def objectives(scenario: LeakReplay) -> set[str]:
            return {group.objective.value for group in _groups(scenario)}

        assert len(objectives(original)) == 5
        assert objectives(resumed) == objectives(original)

    @pytest.mark.parametrize("size", [8, 11, None, "all"])
    @pytest.mark.usefixtures("mock_dataset_preparation")
    async def test_launch_and_estimate_use_standard_dataset_size(
        self, size: int | Literal["all"] | None, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = LeakReplay()
        args = ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="garak.leak_replay",
            scenario_class=LeakReplay,
            objective_target=MockPromptTarget(),
            datasets=ScenarioDatasetRequest(sources=[{"name": name} for name in ROWS], max_total=size),
        )
        scenario.set_params_from_args(args=args)
        with patch.object(
            LeakReplayDatasetConfiguration,
            "_collect_named_seeds_async",
            new_callable=AsyncMock,
            return_value=corpus_seeds,
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)
            await scenario.initialize_async()

        total = None if size == "all" else min(size or 20, 11)
        assert estimate.dataset_options.per_dataset_limit is False
        assert estimate.status is (
            ScenarioRunSizeEstimateStatus.Approximate if size != "all" else ScenarioRunSizeEstimateStatus.Unavailable
        )
        assert estimate.total_attack_count == (None if size == "all" else size or 20)
        assert len(_groups(scenario)) == (11 if total is None else total)
        for dataset in estimate.datasets:
            assert dataset.kind == "synthesized"

    @pytest.mark.usefixtures("mock_corpus_fetch")
    async def test_corpora_resolve_through_memory(self) -> None:
        scenario = LeakReplay()
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "dataset_config": _config(max_total=8),
            }
        )
        await scenario.initialize_async()

        assert len(_groups(scenario)) == 8
        assert {group.objective.metadata["corpus"] for group in _groups(scenario)} == {"literature", "nyt"}

    @pytest.mark.parametrize(
        ("respond", "cloze_hit", "completion_hit"),
        [
            (lambda trigger, prompt: "", False, False),
            (lambda trigger, prompt: f"<name>{trigger}</name>", True, False),
            (lambda trigger, prompt: f"<name>{trigger[:2]}</name>{trigger[2:]}", True, False),
            (lambda trigger, prompt: f"  {trigger.upper()} walked in.", True, True),
            (lambda trigger, prompt: f"The missing name is {trigger.lower()}.", True, False),
            (lambda trigger, prompt: f"{prompt} {trigger}", True, False),
            (lambda trigger, prompt: "Zzyzx", False, False),
            (lambda trigger, prompt: "Brook" if trigger == "Avery" else "Avery", False, False),
        ],
    )
    async def test_full_run_scores_each_response_against_its_own_trigger(
        self,
        corpus_seeds: dict[str, list[Seed[Any]]],
        sqlite_instance: SQLiteMemory,
        respond: Callable[[str, str], str],
        cloze_hit: bool,
        completion_hit: bool,
    ) -> None:
        target = MockPromptTarget()
        scenario = LeakReplay()
        await _initialize_async(scenario=scenario, seeds=corpus_seeds, target=target)
        trigger_by_prompt = {
            group.prompts[0].value: (group.objective.metadata["technique"], group.objective.conditions[0].matcher)
            for group in _groups(scenario)
        }
        trigger_by_matcher = {"^" + trigger: trigger for rows in ROWS.values() for trigger, _ in rows}

        async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
            request = normalized_conversation[-1]
            _, matcher = trigger_by_prompt[request.get_value()]
            trigger = trigger_by_matcher.get(matcher.value, matcher.value)
            return [
                MessagePiece(
                    role="assistant",
                    original_value=respond(trigger, request.get_value()),
                    conversation_id=request.get_piece().conversation_id,
                ).to_message()
            ]

        with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock, side_effect=respond_async):
            await scenario.run_async()

        scores = await sqlite_instance.get_scores_async(score_type="true_false")
        assert len(scores) == 11
        for score in scores:
            [condition] = score.scored_expectation.conditions
            expected = cloze_hit if isinstance(condition.matcher, Contains) else completion_hit
            assert score.get_value() is expected
        assert Counter(type(score.scored_expectation.conditions[0].matcher) for score in scores) == {
            Contains: 6,
            Regex: 5,
        }
