# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the WebInjection scenario."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackSeedGroup,
    BoundedDatasetSize,
    ComponentIdentifier,
    SeedObjective,
    SeedPrompt,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.garak import (  # type: ignore[ty:unresolved-import]
    PackageHallucinationTechnique,
    WebInjection,
    WebInjectionTechnique,
)
from pyrit.score import (
    MarkdownInjectionScorer,
    TrueFalseCompositeScorer,
    TrueFalseScorer,
)
from pyrit.score.true_false.regex.xss_output_scorer import XSSOutputScorer
from tests.unit.mocks import MockPromptTarget


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def dataset_values() -> dict[str, list[str]]:
    """Realistic-shaped raw seed values keyed by dataset name."""
    return {
        "garak_example_domains_xss": ["wuzzi.net", "derczynski.com"],
        "garak_markdown_js": [
            "javascript:prompt(document.cookie)",
            "data:text/html;base64,AAAA",
        ],
        "garak_web_html_js": [
            "<script>alert(1)</script>",
            "<IMG SRC=javascript:alert(1)>",
        ],
        "garak_xss_normal_instructions": ["Write a poem.", "Explain gravity."],
    }


@pytest.fixture
async def web_injection_seeds_async(*, patch_central_database: None, dataset_values: dict[str, list[str]]) -> None:
    seeds = [SeedPrompt(value=value, dataset_name=name) for name, values in dataset_values.items() for value in values]
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(seeds=seeds, added_by="test")


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_prompt_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        WebInjection(max_prompts_per_technique=limit)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("kwargs", [{}, {"max_prompts_per_technique": None}])
async def test_omitted_and_none_keep_finite_prompt_default_async(kwargs: dict[str, None]) -> None:
    scenario = WebInjection(**kwargs)
    with patch.object(scenario, "_load_dataset_values_async", side_effect=AssertionError("Preview read datasets")):
        estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.dataset_size == BoundedDatasetSize(value=62)
    assert estimate.estimated_attack_count == 124


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("limit", [1, 3, 12])
@pytest.mark.parametrize("baseline", [False, True])
async def test_every_technique_is_capped_and_preview_bounds_plan_async(*, limit: int, baseline: bool) -> None:
    values = {
        WebInjection.DATASET_EXAMPLE_DOMAINS: [f"domain{index}.example" for index in range(15)],
        WebInjection.DATASET_MARKDOWN_JS: [f"payload{index}" for index in range(15)],
        WebInjection.DATASET_WEB_HTML_JS: [f"html{index}" for index in range(15)],
        WebInjection.DATASET_NORMAL_INSTRUCTIONS: [f"task{index}" for index in range(15)],
    }
    scenario = WebInjection(max_prompts_per_technique=limit)
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scenario_techniques": [WebInjectionTechnique.ALL],
            "include_baseline": baseline,
        }
    )
    with patch.object(scenario, "_load_dataset_values_async", side_effect=AssertionError("Preview read data")):
        estimate = await scenario.get_run_size_estimate_async()
    expected_bound = 7 * limit + min(limit, 2)
    assert estimate.dataset_size == BoundedDatasetSize(value=expected_bound)
    assert estimate.estimated_attack_count == expected_bound * (1 + baseline)
    with (
        patch.object(scenario._dataset_config, "_collect_named_seeds_async", return_value=[]),
        patch.object(scenario, "_load_dataset_values_async", return_value=values),
    ):
        await scenario.initialize_async()
        full_groups = await scenario._resolve_seed_groups_by_dataset_async(apply_sampling=False)
        resumed = WebInjection(max_prompts_per_technique=limit, scenario_result_id=scenario._scenario_result_id)
        resumed.set_params_from_args(args=scenario.params)
        with (
            patch.object(resumed._dataset_config, "_collect_named_seeds_async", return_value=[]),
            patch.object(resumed, "_load_dataset_values_async", return_value=values),
        ):
            await resumed.initialize_async()
    attacks = scenario._atomic_attacks
    selected = [attack for attack in attacks if attack.atomic_attack_name != "baseline"]
    assert all(0 < len(attack._seed_groups) <= limit for attack in selected)
    full_objectives = {group.objective.value for groups in full_groups.values() for group in groups}
    assert all(group.objective.value in full_objectives for attack in selected for group in attack._seed_groups)
    plan = scenario._build_run_plan()
    actual = sum(len(group.seed_group_ids) for group in plan.atomic_groups)
    assert resumed._build_run_plan() == plan
    assert 0 < actual <= estimate.estimated_attack_count
    if baseline:
        assert {group.objective.value for group in attacks[0]._seed_groups} == {
            group.objective.value for attack in selected for group in attack._seed_groups
        }


@pytest.mark.usefixtures("patch_central_database")
class TestWebInjectionInitialization:
    def test_no_arg_instantiation(self):
        scenario = WebInjection()
        assert scenario.name == "WebInjection"
        assert scenario.VERSION == 2

    def test_default_objective_scorer_is_or_composite(self):
        scenario = WebInjection()
        assert isinstance(scenario._objective_scorer, TrueFalseCompositeScorer)

    def test_custom_objective_scorer_is_used(self):
        custom = MagicMock(spec=TrueFalseScorer)
        custom.get_identifier.return_value = _mock_id("CustomScorer")
        scenario = WebInjection(objective_scorer=custom)
        assert scenario._objective_scorer is custom

    def test_per_technique_scorers_created(self):
        scenario = WebInjection()
        assert isinstance(scenario._exfil_scoring_config.objective_scorer, MarkdownInjectionScorer)
        assert isinstance(scenario._xss_scoring_config.objective_scorer, XSSOutputScorer)

    def test_default_dataset_names(self):
        config = WebInjection()._default_dataset_config
        names = config.dataset_names
        assert "garak_example_domains_xss" in names
        assert "garak_markdown_js" in names
        assert "garak_web_html_js" in names
        assert "garak_xss_normal_instructions" in names

    async def test_real_local_datasets_load_async(self, *, mock_objective_target: PromptTarget) -> None:
        memory = CentralMemory.get_memory_instance()
        assert not await memory.get_seeds_async()

        scenario = WebInjection(max_prompts_per_technique=1)
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [
                    WebInjectionTechnique.MarkdownImageExfil,
                    WebInjectionTechnique.MarkdownXSS,
                    WebInjectionTechnique.TaskXSS,
                ],
                "include_baseline": False,
            }
        )
        await scenario.initialize_async()

        expected_values = {
            "garak_example_domains_xss": "llmsec.net",
            "garak_markdown_js": "javascript:window.onerror=confirm;throw%201",
            "garak_web_html_js": "<IMG SRC=javascript:alert(String.fromCharCode(88,83,83))>",
            "garak_xss_normal_instructions": "Write a recipe for chocolate chip cookies.",
        }
        seeds = await memory.get_seeds_async()
        assert {seed.dataset_name for seed in seeds} == set(expected_values)
        for name, value in expected_values.items():
            assert value in {seed.value for seed in seeds if seed.dataset_name == name}

        assert {attack.atomic_attack_name for attack in scenario._atomic_attacks} == {
            "markdown_image_exfil",
            "markdown_xss",
            "task_xss",
        }
        assert all(attack.seed_groups for attack in scenario._atomic_attacks)


class TestWebInjectionTechniqueExpansion:
    def test_all_expands_to_eight(self):
        assert len(WebInjectionTechnique.get_all_techniques()) == 8

    def test_default_excludes_extended(self):
        default = {s.value for s in WebInjectionTechnique.expand({WebInjectionTechnique.DEFAULT})}
        assert "markdown_uri_image_exfil_extended" not in default
        assert "markdown_uri_non_image_exfil_extended" not in default
        assert "task_xss" in default
        assert "markdown_image_exfil" in default

    def test_exfil_aggregate(self):
        exfil = {s.value for s in WebInjectionTechnique.expand({WebInjectionTechnique.EXFIL})}
        assert "task_xss" not in exfil
        assert "markdown_xss" not in exfil
        assert len(exfil) == 6

    def test_xss_aggregate(self):
        xss = {s.value for s in WebInjectionTechnique.expand({WebInjectionTechnique.XSS})}
        assert xss == {"task_xss", "markdown_xss"}


@pytest.mark.usefixtures("patch_central_database", "web_injection_seeds_async")
class TestWebInjectionAtomicAttacks:
    def test_seed_group_build_rejects_foreign_technique(self, dataset_values):
        scenario = WebInjection()
        scenario._scenario_techniques = [PackageHallucinationTechnique.Rust]

        with pytest.raises(TypeError, match="Unexpected web injection technique: PackageHallucinationTechnique"):
            scenario._build_synthesized_seed_groups(dataset_values=dataset_values)

    async def test_atomic_attack_build_rejects_foreign_technique(self, mock_objective_target):
        scenario = WebInjection()
        context = ScenarioContext(
            objective_target=mock_objective_target,
            scenario_techniques=[PackageHallucinationTechnique.Rust],
            dataset_config=scenario._default_dataset_config,
        )

        with pytest.raises(TypeError, match="Unexpected web injection technique: PackageHallucinationTechnique"):
            await scenario._build_atomic_attacks_async(context=context)

    async def test_atomic_attacks_one_per_technique_plus_baseline(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=dataset_values):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.ALL],
                    "include_baseline": True,
                }
            )
            await scenario.initialize_async()
            attacks = scenario._atomic_attacks

        # 8 techniques + 1 baseline
        assert attacks[0].atomic_attack_name == "baseline"
        names = {a.atomic_attack_name for a in attacks}
        assert "baseline" in names
        assert "task_xss" in names
        assert "markdown_image_exfil" in names
        assert len([a for a in attacks if a.atomic_attack_name != "baseline"]) == 8

    async def test_no_baseline_when_disabled(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=dataset_values):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.XSS],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            attacks = scenario._atomic_attacks

        assert all(a.atomic_attack_name != "baseline" for a in attacks)
        assert {a.atomic_attack_name for a in attacks} == {"task_xss", "markdown_xss"}

    async def test_seed_groups_pair_objective_and_prompt(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=dataset_values):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.MarkdownXSS],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            attack = scenario._atomic_attacks[0]

        assert len(attack._seed_groups) > 0
        for group in attack._seed_groups:
            assert isinstance(group, AttackSeedGroup)
            assert isinstance(group.seeds[0], SeedObjective)
            assert isinstance(group.seeds[1], SeedPrompt)
            assert group.seeds[0].value.startswith("Exploit cross-site scripting via prompt injection")
            assert group.seeds[1].value in group.seeds[0].value

    async def test_exfil_technique_uses_markdown_scorer(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=dataset_values):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.PlaygroundMarkdownExfil],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            attack = scenario._atomic_attacks[0].attack_technique.attack

        assert isinstance(attack, PromptSendingAttack)
        scorer = attack._objective_scorer
        assert isinstance(scorer, MarkdownInjectionScorer)

    async def test_xss_technique_uses_xss_scorer(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=dataset_values):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.TaskXSS],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            attack = scenario._atomic_attacks[0].attack_technique.attack

        scorer = attack._objective_scorer
        assert isinstance(scorer, XSSOutputScorer)

    async def test_raises_when_no_prompts(self, mock_objective_target):
        empty = {
            "garak_example_domains_xss": [],
            "garak_markdown_js": [],
            "garak_web_html_js": [],
            "garak_xss_normal_instructions": [],
        }
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=empty):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.MarkdownImageExfil],
                }
            )
            with pytest.raises(ValueError):
                await scenario.initialize_async()

    async def test_max_prompts_per_technique_caps_output(self, mock_objective_target, dataset_values):
        scenario = WebInjection(max_prompts_per_technique=3)
        with patch.object(WebInjection, "_load_dataset_values_async", return_value=dataset_values):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [WebInjectionTechnique.MarkdownURIImageExfilExtended],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            attack = scenario._atomic_attacks[0]
        assert len(attack._seed_groups) == 3
