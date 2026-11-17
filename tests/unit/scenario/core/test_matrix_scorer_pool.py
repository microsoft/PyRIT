# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Objective scorer pools in ``MatrixAtomicAttackBuilder``: per-cell selection, baselines, and policy."""

import logging
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack import AttackStrategy, PromptSendingAttack, TreeOfAttacksWithPruningAttack
from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
from pyrit.models import (
    AttackSeedGroup,
    ComponentIdentifier,
    MessageScorable,
    ScenarioRunPlanGroupKind,
    Scorable,
    Score,
    ScoringExpectation,
    SeedObjective,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import (
    AttackTechnique,
    AttackTechniqueFactory,
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    TechniqueRequirements,
)
from pyrit.scenario.core.matrix_atomic_attack_builder import MatrixAtomicAttackBuilder
from pyrit.score import (
    CompatibleScorers,
    ConflictingScorerRequirementsError,
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    MessageToolCallScorer,
    ScorerCandidate,
    ScorerRequirements,
    SubStringScorer,
)

HATE = "harm.hate"
VIOLENCE = "harm.violence"


def _factory(
    *,
    name: str,
    requirements: TechniqueRequirements | None = None,
    attack_class: type = PromptSendingAttack,
    scoring_config_type: type | None = None,
) -> MagicMock:
    factory = MagicMock(spec=AttackTechniqueFactory)
    factory.name = name
    factory.seed_technique = None
    factory.adversarial_chat = None
    factory.attack_class = attack_class
    factory.scoring_config_type = scoring_config_type
    factory.requirements = requirements or TechniqueRequirements()
    factory.resolve_adversarial_chat.return_value = None
    factory.create.side_effect = lambda **_: AttackTechnique(attack=MagicMock(spec=AttackStrategy))
    return factory


def _group(objective: str, *, tools: tuple[str, ...] = ()) -> AttackSeedGroup:
    conditions = (ToolsCalled(tools=tuple(ToolCallRequirement(name=t) for t in tools)),) if tools else ()
    return AttackSeedGroup(seeds=[SeedObjective(value=objective, conditions=conditions)])


def _pool() -> tuple[CompatibleScorers, SubStringScorer, SubStringScorer]:
    hate, violence = SubStringScorer(substring="hate"), SubStringScorer(substring="violence")
    return (
        CompatibleScorers(
            candidates=[
                ScorerCandidate(scorer=hate, evaluation=HATE),
                ScorerCandidate(scorer=violence, evaluation=VIOLENCE),
            ]
        ),
        hate,
        violence,
    )


def _builder(objective_scorer, *, policy=IncompatibleTechniquePolicy.SKIP, **kwargs) -> MatrixAtomicAttackBuilder:
    return MatrixAtomicAttackBuilder(
        objective_target=MagicMock(spec=PromptTarget),
        objective_scorer=objective_scorer,
        incompatible_technique_policy=policy,
        **kwargs,
    )


DATASETS = {
    "airt_hate": [_group("hate one"), _group("hate two")],
    "airt_violence": [_group("violence one")],
}
REQUIREMENTS = {
    "airt_hate": ScorerRequirements(evaluation=HATE),
    "airt_violence": ScorerRequirements(evaluation=VIOLENCE),
}


@pytest.mark.usefixtures("patch_central_database")
class TestPoolSelection:
    def test_each_dataset_uses_the_judge_bound_to_its_evaluation(self) -> None:
        pool, hate, violence = _pool()
        factory = _factory(name="prompt_sending")

        attacks = _builder(pool).build(
            technique_factories={"prompt_sending": factory},
            dataset_groups=DATASETS,
            dataset_scorer_requirements=REQUIREMENTS,
        )

        by_name = {attack.atomic_attack_name: attack for attack in attacks}
        assert by_name["prompt_sending_airt_hate"]._objective_scorer is hate
        assert by_name["prompt_sending_airt_violence"]._objective_scorer is violence
        configs = [call.kwargs["attack_scoring_config"].objective_scorer for call in factory.create.call_args_list]
        assert configs == [hate, violence]

        hate_binding = by_name["prompt_sending_airt_hate"].objective_scorer_binding
        assert hate_binding.candidate_index == 0
        assert hate_binding.evaluation == HATE
        assert hate_binding.required_evaluation == HATE
        assert hate_binding.scorer_hash == hate.get_identifier().hash
        assert len(hate_binding.expectation_fingerprints) == 2
        assert by_name["prompt_sending_airt_violence"].objective_scorer_binding.candidate_index == 1

    def test_inputs_without_a_requirement_take_the_first_compatible_candidate(self) -> None:
        pool, hate, _ = _pool()
        attacks = _builder(pool).build(
            technique_factories={"prompt_sending": _factory(name="prompt_sending")},
            dataset_groups={"misc": [_group("o1")]},
        )
        assert attacks[0]._objective_scorer is hate
        assert attacks[0].objective_scorer_binding.required_evaluation is None

    def test_conditions_steer_selection_to_a_scorer_that_can_judge_them(self) -> None:
        substring, tool_scorer = SubStringScorer(substring="x"), MessageToolCallScorer()
        pool = CompatibleScorers(candidates=[substring, tool_scorer])
        attacks = _builder(pool).build(
            technique_factories={"prompt_sending": _factory(name="prompt_sending")},
            dataset_groups={"plain": [_group("o1")], "agentic": [_group("o2", tools=("send_email",))]},
        )
        scorers = {attack.atomic_attack_name: attack._objective_scorer for attack in attacks}
        assert scorers == {"prompt_sending_plain": substring, "prompt_sending_agentic": tool_scorer}

    def test_technique_feedback_requirement_is_combined_with_the_dataset_requirement(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool, _, _ = _pool()
        ranking = _factory(
            name="tap",
            requirements=TechniqueRequirements(objective_scorer=ScorerRequirements(preserves_float_scale_value=True)),
        )
        with caplog.at_level(logging.WARNING):
            attacks = _builder(pool).build(
                technique_factories={"tap": ranking},
                dataset_groups=DATASETS,
                dataset_scorer_requirements=REQUIREMENTS,
            )
        assert attacks == []
        assert "float_feedback" in caplog.text

    def test_scenario_requirement_conflicting_with_a_dataset_raises(self) -> None:
        pool, _, _ = _pool()
        builder = _builder(pool, scorer_requirements=ScorerRequirements(evaluation=VIOLENCE))
        with pytest.raises(ConflictingScorerRequirementsError):
            builder.build(
                technique_factories={"prompt_sending": _factory(name="prompt_sending")},
                dataset_groups=DATASETS,
                dataset_scorer_requirements=REQUIREMENTS,
            )


@pytest.mark.usefixtures("patch_central_database")
class TestPoolPolicy:
    def test_skip_drops_only_the_cell_no_candidate_can_judge(self, caplog: pytest.LogCaptureFixture) -> None:
        pool, _, _ = _pool()
        requirements = {**REQUIREMENTS, "airt_violence": ScorerRequirements(evaluation="harm.violance")}
        with caplog.at_level(logging.WARNING):
            attacks = _builder(pool).build(
                technique_factories={"prompt_sending": _factory(name="prompt_sending")},
                dataset_groups=DATASETS,
                dataset_scorer_requirements=requirements,
            )
        assert [attack.atomic_attack_name for attack in attacks] == ["prompt_sending_airt_hate"]
        assert "dataset 'airt_violence'" in caplog.text
        assert "'harm.violance'" in caplog.text
        assert f"'{HATE}', '{VIOLENCE}'" in caplog.text

    def test_raise_names_the_cell_and_every_candidate_reason(self) -> None:
        pool, _, _ = _pool()
        requirements = {"airt_hate": ScorerRequirements(evaluation="harm.unknown")}
        with pytest.raises(IncompatibleTechniqueError) as excinfo:
            _builder(pool, policy=IncompatibleTechniquePolicy.RAISE).build(
                technique_factories={"prompt_sending": _factory(name="prompt_sending")},
                dataset_groups={"airt_hate": DATASETS["airt_hate"]},
                dataset_scorer_requirements=requirements,
            )
        message = str(excinfo.value)
        assert "Technique 'prompt_sending', dataset 'airt_hate'" in message
        assert "#0 SubStringScorer [harm.hate] (evaluation)" in message
        assert "#1 SubStringScorer [harm.violence] (evaluation)" in message

    def test_single_scorer_faces_the_same_checks(self) -> None:
        with pytest.raises(IncompatibleTechniqueError, match="does not support condition"):
            _builder(SubStringScorer(substring="x"), policy=IncompatibleTechniquePolicy.RAISE).build(
                technique_factories={"prompt_sending": _factory(name="prompt_sending")},
                dataset_groups={"agentic": [_group("o1", tools=("send_email",))]},
            )


@pytest.mark.usefixtures("patch_central_database")
class TestPoolBaselines:
    def test_each_population_is_compared_under_its_own_judge(self) -> None:
        pool, hate, violence = _pool()
        attacks = _builder(pool).build(
            technique_factories={"a": _factory(name="a"), "b": _factory(name="b")},
            dataset_groups=DATASETS,
            dataset_scorer_requirements=REQUIREMENTS,
            include_baseline=True,
        )
        baselines = [attack for attack in attacks if attack.group_kind is ScenarioRunPlanGroupKind.BASELINE]
        assert [b.atomic_attack_name for b in baselines] == ["baseline_airt_hate", "baseline_airt_violence"]
        assert [b._objective_scorer for b in baselines] == [hate, violence]
        assert baselines[0].seed_groups == DATASETS["airt_hate"]
        assert baselines[0].objective_scorer_binding == next(
            a.objective_scorer_binding for a in attacks if a.atomic_attack_name == "a_airt_hate"
        )
        # Two techniques with the same population and judge share one baseline.
        assert len(attacks) == 2 + 4

    def test_one_dataset_judged_two_ways_gets_two_numbered_baselines(self) -> None:
        plain, ranked = SubStringScorer(substring="a"), SubStringScorer(substring="b")
        ranked_requirements = ScorerRequirements(preserves_float_scale_value=True)
        ranked._preserves_float_scale_value = lambda: True  # type: ignore[method-assign]
        pool = CompatibleScorers(candidates=[plain, ranked])
        attacks = _builder(pool).build(
            technique_factories={
                "send": _factory(name="send"),
                "tap": _factory(name="tap", requirements=TechniqueRequirements(objective_scorer=ranked_requirements)),
            },
            dataset_groups={"d": [_group("o1")]},
            include_baseline=True,
        )
        baselines = [a for a in attacks if a.group_kind is ScenarioRunPlanGroupKind.BASELINE]
        assert [(b.atomic_attack_name, b._objective_scorer) for b in baselines] == [
            ("baseline_d__1", plain),
            ("baseline_d__2", ranked),
        ]

    def test_single_scorer_keeps_the_legacy_baseline(self) -> None:
        scorer = SubStringScorer(substring="x")
        attacks = _builder(scorer).build(
            technique_factories={"prompt_sending": _factory(name="prompt_sending")},
            dataset_groups=DATASETS,
            include_baseline=True,
        )
        assert [a.atomic_attack_name for a in attacks] == [
            "baseline",
            "prompt_sending_airt_hate",
            "prompt_sending_airt_violence",
        ]
        assert all(a.objective_scorer_binding is None for a in attacks)
        assert attacks[0].seed_groups == [*DATASETS["airt_hate"], *DATASETS["airt_violence"]]


class _Scale(FloatScaleScorer):
    SUPPORTED_SCORABLE_TYPES = (MessageScorable,)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []


@pytest.mark.usefixtures("patch_central_database")
class TestTapSelection:
    def _tap(self) -> MagicMock:
        return _factory(
            name="tap", attack_class=TreeOfAttacksWithPruningAttack, scoring_config_type=TAPAttackScoringConfig
        )

    def test_tap_gets_a_scorer_that_keeps_float_feedback_in_its_own_config_type(self) -> None:
        plain, ranked = SubStringScorer(substring="x"), FloatScaleThresholdScorer(scorer=_Scale(), threshold=0.7)
        tap = self._tap()
        attacks = _builder(CompatibleScorers(candidates=[plain, ranked])).build(
            technique_factories={"tap": tap}, dataset_groups={"d": [_group("o1")]}
        )
        assert attacks[0]._objective_scorer is ranked
        config = tap.create.call_args.kwargs["attack_scoring_config"]
        assert type(config) is TAPAttackScoringConfig
        assert config.objective_scorer is ranked

    def test_a_scenario_scorer_without_float_feedback_skips_tap_instead_of_being_replaced(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        tap = self._tap()
        with caplog.at_level(logging.WARNING):
            attacks = _builder(SubStringScorer(substring="x")).build(
                technique_factories={"tap": tap}, dataset_groups={"d": [_group("o1")]}
            )
        assert attacks == []
        tap.create.assert_not_called()
        assert "float_feedback" in caplog.text
