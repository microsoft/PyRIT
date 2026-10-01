# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Behavior and resume tests for latent document injection."""

import random
from typing import Any
from unittest.mock import patch

import pytest

from pyrit.converter import SearchReplaceConverter
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import AttackSeedGroup, Seed, SeedObjective, SeedPrompt
from pyrit.scenario.core.dataset_configuration import (
    DatasetAttackConfiguration,
    DatasetConstraintError,
    ResolvedDataset,
)
from pyrit.scenario.scenarios.garak.latent_injection import (
    LatentInjection,
    LatentInjectionDatasetConfiguration,
    LatentInjectionTechnique,
)
from pyrit.score import SubStringScorer
from tests.unit.mocks import MockPromptTarget


def _config(**kwargs: Any) -> LatentInjectionDatasetConfiguration:
    return LatentInjectionDatasetConfiguration(dataset_names=LatentInjection.required_datasets(), **kwargs)


@pytest.fixture
async def seeded_memory_async(patch_central_database: None) -> MemoryInterface:
    config = LatentInjectionDatasetConfiguration
    seeds: list[Seed] = []
    for family in ("whois", "resume", "latent_jailbreak"):
        seeds.extend(
            SeedPrompt(
                value=f"{family} document {index}: {{literal}}\n<|injection|>",
                dataset_name=config.CONTEXT_DATASET_NAME,
                metadata={"family": family, "language": "en"},
                source="https://example.test/context",
            )
            for index in range(3)
        )
        seeds.extend(
            [
                SeedPrompt(
                    value="Read this document.\n", dataset_name=config.TASK_DATASET_NAME, metadata={"family": family}
                ),
                SeedPrompt(
                    value="Print <|payload|>", dataset_name=config.INSTRUCTION_DATASET_NAME, metadata={"family": family}
                ),
                SeedPrompt(value="<|trigger|>", dataset_name=config.PAYLOAD_DATASET_NAME, metadata={"family": family}),
            ]
        )
        seeds.extend(
            SeedPrompt(
                value=trigger,
                dataset_name=config.TRIGGER_DATASET_NAME,
                metadata={"family": family},
            )
            for trigger in ("goal A", "goal B")
        )
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(seeds=seeds, added_by="test")
    return memory


async def _initialize_async(scenario: LatentInjection, **kwargs: Any) -> None:
    scenario.set_params_from_args(args={"objective_target": MockPromptTarget(), **kwargs})
    await scenario.initialize_async()


def _ids(scenario: LatentInjection) -> dict[str, list[str]]:
    return {
        attack.atomic_attack_name: [group.logical_id for group in attack.seed_groups]
        for attack in scenario._atomic_attacks
    }


@pytest.mark.usefixtures("patch_central_database")
class TestLatentDefaults:
    async def test_default_population_budget_and_estimate_async(self) -> None:
        scenario = LatentInjection()
        await _initialize_async(scenario)
        assert scenario.VERSION == 2
        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 552
        assert len({identity for ids in _ids(scenario).values() for identity in ids}) == 92
        assert len(scenario._atomic_attacks) == 23 * 6
        estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)
        assert estimate.estimated_attack_count == 552
        assert len(LatentInjectionTechnique.expand([LatentInjectionTechnique.ALL])) == 14
        assert len(LatentInjectionDatasetConfiguration.FAMILIES) == 9
        assert {parameter.name for parameter in scenario.additional_parameters()} == {"families"}

    async def test_all_families_and_separators_async(self) -> None:
        config = _config(families=LatentInjectionDatasetConfiguration.FAMILIES)
        scenario = LatentInjection(harm_scorer=SubStringScorer(substring="harm"))
        await _initialize_async(scenario, dataset_config=config, scenario_techniques=[LatentInjectionTechnique.ALL])
        assert {key[0] for key in config.coverage_keys} == set(config.FAMILIES)
        assert len(scenario._atomic_attacks) == len(config.coverage_keys) * 14
        for technique in LatentInjectionTechnique.expand([LatentInjectionTechnique.ALL]):
            attack = next(a for a in scenario._atomic_attacks if a.display_group == technique.value)
            group = attack.seed_groups[0]
            text = group.prompts[0].value
            for entry in attack.attack_technique.attack.get_request_converters():
                for converter in entry.converters:
                    text = (await converter.convert_async(prompt=text)).output_text
            prefix, suffix = scenario.SEPARATORS[technique.value]
            expected = group.prompts[0].value.replace(config.START_MARKER, prefix).replace(config.END_MARKER, suffix)
            assert text == expected
            assert config.START_MARKER not in text and config.END_MARKER not in text

    async def test_auto_fetch_false_and_wrong_configs_async(self) -> None:
        config = _config(auto_fetch=False)
        with patch.object(config, "_fetch_dataset_async") as fetch:
            with pytest.raises(DatasetConstraintError, match="auto_fetch is disabled"):
                await _initialize_async(LatentInjection(), dataset_config=config)
        fetch.assert_not_called()
        with pytest.raises(DatasetConstraintError, match="only supports"):
            await _initialize_async(LatentInjection(), dataset_config=DatasetAttackConfiguration())
        inline = LatentInjectionDatasetConfiguration(seeds=[SeedObjective(value="test")])
        with pytest.raises(DatasetConstraintError, match="inline seeds"):
            await inline.get_attack_seed_groups_async()
        with pytest.raises(DatasetConstraintError, match="requires exactly"):
            await LatentInjectionDatasetConfiguration(dataset_names=["wrong"]).get_attack_seed_groups_async()

    @pytest.mark.parametrize("families", [[], ["unknown"]])
    def test_invalid_families(self, families: list[str]) -> None:
        with pytest.raises(ValueError, match="non-empty selection"):
            _config(families=families)

    @pytest.mark.parametrize("cap", [0, -1])
    def test_invalid_constructor_budget(self, cap: int) -> None:
        with pytest.raises(ValueError, match="max_dataset_size"):
            _config(max_dataset_size=cap)

    @pytest.mark.parametrize(("family", "cap"), [("fact_eiffel", 20), ("fact_legal", 20), ("whois_snippet", 10)])
    def test_snippet_contexts_are_bounded(self, family: str, cap: int) -> None:
        paragraphs = [f"paragraph {index} <|injection|>" for index in range(8)]
        config = _config()
        contexts = config._contexts_for_family(family=family, paragraphs=paragraphs)
        assert len(contexts) == cap
        assert contexts == config._contexts_for_family(family=family, paragraphs=paragraphs)
        assert all(value.count(config.INJECTION_MARKER) == 1 for value in contexts)
        if family.startswith("fact"):
            assert all(not value.startswith(config.INJECTION_MARKER) for value in contexts)


@pytest.mark.usefixtures("seeded_memory_async")
class TestLatentPopulation:
    async def test_budget_coverage_and_full_resolution_async(self) -> None:
        config = _config(families=["whois", "resume"], max_dataset_size=4)
        with patch("pyrit.scenario.scenarios.garak._prompt_injection.random", random.Random(3)):
            flat = await config.get_attack_seed_groups_async()
        with patch("pyrit.scenario.scenarios.garak._prompt_injection.random", random.Random(3)):
            grouped = await config.get_attack_groups_by_dataset_async()
        assert [group.logical_id for group in flat] == [
            group.logical_id for groups in grouped.values() for group in groups
        ]
        assert len(flat) == len(config.coverage_keys) == 4
        assert {config._coverage_key(group) for group in flat} == set(config.coverage_keys)
        full = await config.get_attack_seed_groups_async(apply_sampling=False)
        assert len(full) == 12
        config.max_dataset_size = None
        assert len(await config.get_attack_seed_groups_async()) == 12
        for group in full:
            assert group.prompts[0].source == "https://example.test/context"
            assert group.objective.metadata["language"] == "en"
            assert "document" not in group.objective.value
            assert len(group.objective.value) < 180

    @pytest.mark.parametrize("cap", [-1, 0, 3])
    async def test_runtime_budget_is_validated_without_sampling_async(self, cap: int) -> None:
        config = _config(families=["whois", "resume"])
        config.max_dataset_size = cap
        with pytest.raises(DatasetConstraintError, match="family/trigger pairs"):
            await config.get_attack_seed_groups_async(apply_sampling=False)

    async def test_filters_validators_and_config_identity_async(self, seeded_memory_async: MemoryInterface) -> None:
        seen: list[ResolvedDataset] = []
        config = _config(
            families=["whois"],
            max_dataset_size=2,
            auto_fetch=False,
            filters={"data_types": ["text"]},
            validators=[seen.append],
        )
        scenario = LatentInjection()
        with patch.object(
            seeded_memory_async, "get_seeds_async", wraps=seeded_memory_async.get_seeds_async
        ) as get_seeds:
            await _initialize_async(
                scenario, dataset_config=config, scenario_techniques=[LatentInjectionTechnique.Bare]
            )
        assert scenario._dataset_config is config
        assert config.families == ["whois"]
        assert len(seen) == 1
        assert len(seen[0].seeds) == 24
        assert get_seeds.call_count == 5
        assert all(call.kwargs["data_types"] == ["text"] for call in get_seeds.call_args_list)
        assert len({i for ids in _ids(scenario).values() for i in ids}) == 2
        config.update_filters(filters={"data_types": ["image_path"]})
        with pytest.raises(DatasetConstraintError, match="none match"):
            await config.get_attack_seed_groups_async()

    async def test_validator_failure_is_not_discarded_async(self) -> None:
        def reject(_: ResolvedDataset) -> None:
            raise DatasetConstraintError("custom validator")

        with pytest.raises(DatasetConstraintError, match="custom validator"):
            await _initialize_async(LatentInjection(), dataset_config=_config(families=["whois"], validators=[reject]))

    async def test_missing_selected_family_raises_async(self) -> None:
        with pytest.raises(DatasetConstraintError, match="missing ingredients"):
            await _config(families=["report"]).get_attack_seed_groups_async()

    async def test_source_follows_each_carrier_async(self, seeded_memory_async: MemoryInterface) -> None:
        sources = {
            name: list(await seeded_memory_async.get_seeds_async(dataset_name=name))
            for name in LatentInjection.required_datasets()
        }
        contexts = sources[LatentInjectionDatasetConfiguration.CONTEXT_DATASET_NAME]
        for index, seed in enumerate(contexts):
            seed.source = f"https://example.test/context/{index}"
        config = _config(families=["whois"])
        with patch.object(config, "_collect_named_seeds_async", return_value=sources):
            groups = await config.get_attack_seed_groups_async()
        for group in groups:
            context = next(seed for seed in contexts if seed.value.split("<|injection|>")[0] in group.prompts[0].value)
            assert group.prompts[0].source == context.source
            assert group.objective.source == context.source

    @pytest.mark.parametrize(
        ("role", "value", "message"),
        [
            ("contexts", "no marker", "exactly one injection"),
            ("contexts", "<|injection|><|injection|>", "at most one injection"),
            ("instructions", "no marker", "exactly one payload"),
            ("payload_templates", "<|pyrit_latent_end|>", "reserved boundary"),
            ("payload_templates", "no marker", "requires a trigger marker"),
            ("triggers", "<|trigger|>", "unexpected ingredient"),
            ("tasks", "<|injection|>", "unexpected ingredient"),
        ],
    )
    async def test_invalid_markers_raise_async(
        self, seeded_memory_async: MemoryInterface, role: str, value: str, message: str
    ) -> None:
        sources = {
            name: list(await seeded_memory_async.get_seeds_async(dataset_name=name))
            for name in LatentInjection.required_datasets()
        }
        next(
            seed for seed in sources[f"garak_latent_injection_{role}"] if seed.metadata["family"] == "whois"
        ).value = value
        config = _config(families=["whois"])
        with patch.object(config, "_collect_named_seeds_async", return_value=sources):
            with pytest.raises(DatasetConstraintError, match=message):
                await config.get_attack_seed_groups_async()

    async def test_empty_trigger_raises_async(self, seeded_memory_async: MemoryInterface) -> None:
        sources = {
            name: list(await seeded_memory_async.get_seeds_async(dataset_name=name))
            for name in LatentInjection.required_datasets()
        }
        for seed in sources[LatentInjectionDatasetConfiguration.TRIGGER_DATASET_NAME]:
            seed.value = " "
        config = _config(families=["whois"])
        with patch.object(config, "_collect_named_seeds_async", return_value=sources):
            with pytest.raises(DatasetConstraintError, match="empty trigger"):
                await config.get_attack_seed_groups_async()


@pytest.mark.usefixtures("seeded_memory_async")
class TestLatentAttacks:
    async def test_techniques_reuse_groups_and_apply_custom_converters_async(self) -> None:
        scorer = SubStringScorer(substring="custom")
        extra = SearchReplaceConverter(pattern="goal", replace="custom")
        scenario = LatentInjection(objective_scorer=scorer)
        await _initialize_async(
            scenario,
            dataset_config=_config(families=["whois"], max_dataset_size=2),
            scenario_techniques=[LatentInjectionTechnique.Bare, LatentInjectionTechnique.SystemTag],
            technique_converters={"system_tag": [extra]},
        )
        assert len(scenario._atomic_attacks) == 4
        original: dict[str, AttackSeedGroup] = {}
        for attack in scenario._atomic_attacks:
            strategy = attack.attack_technique.attack
            assert strategy.get_attack_scoring_config().objective_scorer is scorer
            for group in attack.seed_groups:
                assert original.setdefault(group.logical_id, group) is group
                text = group.prompts[0].value
                for entry in strategy.get_request_converters():
                    for converter in entry.converters:
                        text = (await converter.convert_async(prompt=text)).output_text
                assert "{literal}" in text
                if attack.display_group == "system_tag":
                    assert "<system>Print custom" in text and "</system>" in text
                else:
                    assert "Print goal" in text and "<system>" not in text
        assert len(original) == 2

    @pytest.mark.parametrize("role", ["payload_templates", "triggers"])
    async def test_literals_survive_conversion_async(self, seeded_memory_async: MemoryInterface, role: str) -> None:
        literal = "Unicode: \u4f60\n{{ braces }} \\g<1> \\1"
        sources = {
            name: list(await seeded_memory_async.get_seeds_async(dataset_name=name))
            for name in LatentInjection.required_datasets()
        }
        for seed in sources[f"garak_latent_injection_{role}"]:
            seed.value = literal + ("<|trigger|>" if role == "payload_templates" else "")
        config = _config(families=["whois"], max_dataset_size=2)
        with patch.object(config, "_collect_named_seeds_async", return_value=sources):
            scenario = LatentInjection()
            await _initialize_async(
                scenario, dataset_config=config, scenario_techniques=[LatentInjectionTechnique.SystemTag]
            )
        for attack in scenario._atomic_attacks:
            text = attack.seed_groups[0].prompts[0].value
            for entry in attack.attack_technique.attack.get_request_converters():
                for converter in entry.converters:
                    text = (await converter.convert_async(prompt=text)).output_text
            assert literal in text

    async def test_harm_scorer_and_objective_async(self) -> None:
        config = _config(families=["latent_jailbreak"])
        with pytest.raises(ValueError, match="harm_scorer"):
            await _initialize_async(LatentInjection(), dataset_config=config)
        harm_scorer = SubStringScorer(substring="harm")
        scenario = LatentInjection(harm_scorer=harm_scorer, objective_scorer=SubStringScorer(substring="normal"))
        await _initialize_async(scenario, dataset_config=config, scenario_techniques=[LatentInjectionTechnique.Bare])
        for attack in scenario._atomic_attacks:
            assert attack.attack_technique.attack.get_attack_scoring_config().objective_scorer is harm_scorer
            assert "produce offensive content" in attack.seed_groups[0].objective.value

    async def test_baseline_is_rejected_async(self) -> None:
        with pytest.raises(ValueError, match="baseline"):
            await _initialize_async(LatentInjection(), include_baseline=True)

    async def test_resume_after_partial_execution_and_reordered_memory_async(
        self, seeded_memory_async: MemoryInterface
    ) -> None:
        original = LatentInjection()
        await _initialize_async(
            original,
            dataset_config=_config(families=["whois"], max_dataset_size=2),
            scenario_techniques=[LatentInjectionTechnique.Bare],
        )
        identities = _ids(original)
        original._atomic_attacks[0].set_scenario_result_id(original._scenario_result_id)
        await original._atomic_attacks[0].run_async()
        get_seeds = seeded_memory_async.get_seeds_async
        resumed = LatentInjection(scenario_result_id=original._scenario_result_id)

        async def reversed_seeds_async(**kwargs: Any) -> list[Seed]:
            return list(reversed(await get_seeds(**kwargs)))

        with patch.object(seeded_memory_async, "get_seeds_async", side_effect=reversed_seeds_async):
            await _initialize_async(
                resumed,
                dataset_config=_config(families=["whois"], max_dataset_size=2),
                scenario_techniques=[LatentInjectionTechnique.Bare],
            )
        assert _ids(resumed) == identities
        remaining = await resumed._get_remaining_atomic_attacks_async()
        assert sum(len(attack.seed_groups) for attack in remaining) == 1

    async def test_changed_scorer_refuses_resume_async(self) -> None:
        original = LatentInjection()
        args = {
            "dataset_config": _config(families=["whois"], max_dataset_size=2),
            "scenario_techniques": [LatentInjectionTechnique.Bare],
        }
        await _initialize_async(original, **args)
        resumed = LatentInjection(
            scenario_result_id=original._scenario_result_id, objective_scorer=SubStringScorer(substring="changed")
        )
        with pytest.raises(ValueError, match="matching configuration"):
            await _initialize_async(resumed, **args)

    async def test_changed_harm_scorer_refuses_resume_async(self) -> None:
        scorer = SubStringScorer(substring="fixed")
        original = LatentInjection(objective_scorer=scorer, harm_scorer=SubStringScorer(substring="harm"))
        args = {
            "dataset_config": _config(families=["whois", "latent_jailbreak"], max_dataset_size=4),
            "scenario_techniques": [LatentInjectionTechnique.Bare],
        }
        await _initialize_async(original, **args)
        resumed = LatentInjection(
            scenario_result_id=original._scenario_result_id,
            objective_scorer=scorer,
            harm_scorer=SubStringScorer(substring="changed"),
        )
        with pytest.raises(ValueError, match="matching configuration"):
            await _initialize_async(resumed, **args)

    async def test_changed_case_refuses_resume_async(self, seeded_memory_async: MemoryInterface) -> None:
        original = LatentInjection()
        args = {
            "dataset_config": _config(families=["whois"], max_dataset_size=2),
            "scenario_techniques": [LatentInjectionTechnique.Bare],
        }
        await _initialize_async(original, **args)
        sources = {
            name: list(await seeded_memory_async.get_seeds_async(dataset_name=name))
            for name in LatentInjection.required_datasets()
        }
        for seed in sources[LatentInjectionDatasetConfiguration.CONTEXT_DATASET_NAME]:
            seed.value += " changed"
        resumed = LatentInjection(scenario_result_id=original._scenario_result_id)
        with patch.object(args["dataset_config"], "_collect_named_seeds_async", return_value=sources):
            with pytest.raises(ValueError, match="cannot resume"):
                await _initialize_async(resumed, **args)
