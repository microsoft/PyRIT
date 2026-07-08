# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the WebInjection scenario."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.garak import WebInjection, WebInjectionStrategy  # type: ignore[ty:unresolved-import]
from pyrit.score import (
    MarkdownInjectionScorer,
    TrueFalseCompositeScorer,
    TrueFalseScorer,
)
from pyrit.score.true_false.regex.xss_output_scorer import XSSOutputScorer


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def dataset_values():
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


@pytest.mark.usefixtures("patch_central_database")
class TestWebInjectionInitialization:
    def test_no_arg_instantiation(self):
        scenario = WebInjection()
        assert scenario.name == "WebInjection"
        assert scenario.VERSION == 1

    def test_default_objective_scorer_is_or_composite(self):
        scenario = WebInjection()
        assert isinstance(scenario._objective_scorer, TrueFalseCompositeScorer)

    def test_custom_objective_scorer_is_used(self):
        custom = MagicMock(spec=TrueFalseScorer)
        custom.get_identifier.return_value = _mock_id("CustomScorer")
        scenario = WebInjection(objective_scorer=custom)
        assert scenario._objective_scorer is custom

    def test_per_strategy_scorers_created(self):
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


class TestWebInjectionStrategyExpansion:
    def test_all_expands_to_eight(self):
        assert len(WebInjectionStrategy.get_all_strategies()) == 8

    def test_default_excludes_extended(self):
        default = {s.value for s in WebInjectionStrategy.expand({WebInjectionStrategy.DEFAULT})}
        assert "markdown_uri_image_exfil_extended" not in default
        assert "markdown_uri_non_image_exfil_extended" not in default
        assert "task_xss" in default
        assert "markdown_image_exfil" in default

    def test_exfil_aggregate(self):
        exfil = {s.value for s in WebInjectionStrategy.expand({WebInjectionStrategy.EXFIL})}
        assert "task_xss" not in exfil
        assert "markdown_xss" not in exfil
        assert len(exfil) == 6

    def test_xss_aggregate(self):
        xss = {s.value for s in WebInjectionStrategy.expand({WebInjectionStrategy.XSS})}
        assert xss == {"task_xss", "markdown_xss"}


@pytest.mark.usefixtures("patch_central_database")
class TestWebInjectionAtomicAttacks:
    async def test_atomic_attacks_one_per_strategy_plus_baseline(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values", return_value=dataset_values):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[WebInjectionStrategy.ALL],
                include_baseline=True,
            )
            attacks = scenario._atomic_attacks

        # 8 strategies + 1 baseline
        assert attacks[0].atomic_attack_name == "baseline"
        names = {a.atomic_attack_name for a in attacks}
        assert "baseline" in names
        assert "task_xss" in names
        assert "markdown_image_exfil" in names
        assert len([a for a in attacks if a.atomic_attack_name != "baseline"]) == 8

    async def test_no_baseline_when_disabled(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values", return_value=dataset_values):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[WebInjectionStrategy.XSS],
                include_baseline=False,
            )
            attacks = scenario._atomic_attacks

        assert all(a.atomic_attack_name != "baseline" for a in attacks)
        assert {a.atomic_attack_name for a in attacks} == {"task_xss", "markdown_xss"}

    async def test_seed_groups_pair_objective_and_prompt(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values", return_value=dataset_values):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[WebInjectionStrategy.MarkdownXSS],
                include_baseline=False,
            )
            attack = scenario._atomic_attacks[0]

        assert len(attack._seed_groups) > 0
        for group in attack._seed_groups:
            assert isinstance(group, SeedAttackGroup)
            assert isinstance(group.seeds[0], SeedObjective)
            assert isinstance(group.seeds[1], SeedPrompt)
            assert group.seeds[0].value.startswith("Exploit cross-site scripting via prompt injection")
            assert group.seeds[1].value in group.seeds[0].value

    async def test_exfil_strategy_uses_markdown_scorer(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values", return_value=dataset_values):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[WebInjectionStrategy.PlaygroundMarkdownExfil],
                include_baseline=False,
            )
            attack = scenario._atomic_attacks[0].attack_technique.attack

        assert isinstance(attack, PromptSendingAttack)
        scorer = attack._objective_scorer
        assert isinstance(scorer, MarkdownInjectionScorer)

    async def test_xss_strategy_uses_xss_scorer(self, mock_objective_target, dataset_values):
        scenario = WebInjection()
        with patch.object(WebInjection, "_load_dataset_values", return_value=dataset_values):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[WebInjectionStrategy.TaskXSS],
                include_baseline=False,
            )
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
        with patch.object(WebInjection, "_load_dataset_values", return_value=empty):
            with pytest.raises(ValueError):
                await scenario.initialize_async(
                    objective_target=mock_objective_target,
                    scenario_strategies=[WebInjectionStrategy.MarkdownImageExfil],
                )

    async def test_max_prompts_per_strategy_caps_output(self, mock_objective_target, dataset_values):
        scenario = WebInjection(max_prompts_per_strategy=3)
        with patch.object(WebInjection, "_load_dataset_values", return_value=dataset_values):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[WebInjectionStrategy.MarkdownURIImageExfilExtended],
                include_baseline=False,
            )
            attack = scenario._atomic_attacks[0]
        assert len(attack._seed_groups) == 3
