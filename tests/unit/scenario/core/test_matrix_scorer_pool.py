# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Objective scorer pools in ``MatrixAtomicAttackBuilder``: per-cell selection, baselines, and policy."""

import logging
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack import AttackScoringConfig, AttackStrategy, PromptSendingAttack
from pyrit.models import (
    AttackSeedGroup,
    ScenarioRunPlanGroupKind,
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


@pytest.mark.usefixtures("patch_central_database")
class TestSelectionReport:
    def test_the_builder_reports_every_cell_including_skipped_ones(self) -> None:
        pool, _, _ = _pool()
        requirements = {**REQUIREMENTS, "airt_violence": ScorerRequirements(evaluation="harm.violance")}
        builder = _builder(pool)
        builder.build(
            technique_factories={"prompt_sending": _factory(name="prompt_sending")},
            dataset_groups=DATASETS,
            dataset_scorer_requirements=requirements,
        )

        report = builder.selection_report
        assert [
            (entry.technique, entry.dataset, entry.selected.evaluation if entry.selected else None)
            for entry in report.entries
        ] == [("prompt_sending", "airt_hate", HATE), ("prompt_sending", "airt_violence", None)]
        (skipped,) = report.skipped
        assert [verdict.reason_kind for verdict in skipped.candidates] == ["evaluation", "evaluation"]
        assert skipped.required_evaluation == "harm.violance"

    def test_each_build_reports_only_its_own_grid(self) -> None:
        pool, _, _ = _pool()
        builder = _builder(pool)
        for dataset in ("airt_hate", "airt_violence"):
            builder.build(
                technique_factories={"prompt_sending": _factory(name="prompt_sending")},
                dataset_groups={dataset: DATASETS[dataset]},
                dataset_scorer_requirements=REQUIREMENTS,
            )
        assert [entry.dataset for entry in builder.selection_report.entries] == ["airt_violence"]

    def test_a_cell_skipped_after_selection_is_reported_as_skipped(self) -> None:
        factory = _factory(name="narrow", scoring_config_type=_NarrowScoringConfig)
        builder = _builder(CompatibleScorers(candidates=[SubStringScorer(substring="other")]))
        builder.build(technique_factories={"narrow": factory}, dataset_groups={"d": [_group("o1")]})

        (entry,) = builder.selection_report.skipped
        assert entry.selected is None
        assert [(verdict.status, verdict.reason_kind) for verdict in entry.candidates] == [
            ("rejected", "scoring_config")
        ]
        assert entry.describe() == [
            "narrow / d: no compatible scorer, skipped",
            "  #0 SubStringScorer: scoring_config, the attack's scoring config type cannot carry this scorer.",
        ]


class _NarrowScoringConfig(AttackScoringConfig):
    """A config type an attack narrows to, accepting only substring scorers that look for 'ok'."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if getattr(self.objective_scorer, "_substring", None) != "ok":
            raise ValueError("This attack needs the 'ok' scorer.")


@pytest.mark.usefixtures("patch_central_database")
class TestNarrowedScoringConfig:
    def test_pool_selection_is_passed_in_the_config_type_the_attack_accepts(self) -> None:
        accepted = SubStringScorer(substring="ok")
        factory = _factory(name="narrow", scoring_config_type=_NarrowScoringConfig)

        attacks = _builder(CompatibleScorers(candidates=[accepted])).build(
            technique_factories={"narrow": factory}, dataset_groups={"d": [_group("o1")]}
        )

        config = factory.create.call_args.kwargs["attack_scoring_config"]
        assert type(config) is _NarrowScoringConfig
        assert config.objective_scorer is accepted
        assert attacks[0]._objective_scorer is accepted

    def test_skip_drops_a_cell_whose_attack_cannot_carry_the_selected_scorer(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        factory = _factory(name="narrow", scoring_config_type=_NarrowScoringConfig)
        with caplog.at_level(logging.WARNING):
            attacks = _builder(CompatibleScorers(candidates=[SubStringScorer(substring="other")])).build(
                technique_factories={"narrow": factory, "prompt_sending": _factory(name="prompt_sending")},
                dataset_groups={"d": [_group("o1")]},
                include_baseline=True,
            )

        # No binding or baseline names a scorer the narrowed attack would have replaced.
        factory.create.assert_not_called()
        assert [a.atomic_attack_name for a in attacks] == ["baseline_d", "prompt_sending_d"]
        assert "cannot carry the selected scorer" in caplog.text

    def test_raise_rejects_a_cell_whose_attack_cannot_carry_the_selected_scorer(self) -> None:
        factory = _factory(name="narrow", scoring_config_type=_NarrowScoringConfig)
        with pytest.raises(IncompatibleTechniqueError, match="Technique 'narrow', dataset 'd'.*cannot carry"):
            _builder(
                CompatibleScorers(candidates=[SubStringScorer(substring="other")]),
                policy=IncompatibleTechniquePolicy.RAISE,
            ).build(technique_factories={"narrow": factory}, dataset_groups={"d": [_group("o1")]})


@pytest.mark.usefixtures("patch_central_database")
class TestSingleScorerBaseline:
    DATASETS = {"plain": [_group("o1")], "agentic": [_group("o2", tools=("send_email",))]}

    def test_skip_keeps_only_datasets_the_scorer_can_judge(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            attacks = _builder(SubStringScorer(substring="x")).build(
                technique_factories={"prompt_sending": _factory(name="prompt_sending")},
                dataset_groups=self.DATASETS,
                include_baseline=True,
            )

        assert [a.atomic_attack_name for a in attacks] == ["baseline", "prompt_sending_plain"]
        assert attacks[0].seed_groups == self.DATASETS["plain"]
        assert "Baseline, dataset 'agentic'" in caplog.text

    def test_raise_names_the_baseline_dataset(self) -> None:
        with pytest.raises(IncompatibleTechniqueError, match="Baseline, dataset 'agentic'"):
            _builder(SubStringScorer(substring="x"), policy=IncompatibleTechniquePolicy.RAISE).build(
                technique_factories={}, dataset_groups=self.DATASETS, include_baseline=True
            )

    def test_dataset_requirements_apply_to_the_baseline(self) -> None:
        attacks = _builder(SubStringScorer(substring="x")).build(
            technique_factories={},
            dataset_groups=DATASETS,
            include_baseline=True,
            dataset_scorer_requirements={"airt_hate": ScorerRequirements(evaluation=HATE)},
        )
        assert attacks[0].seed_groups == DATASETS["airt_violence"]

    def test_a_dataset_rejected_only_by_a_technique_stays_in_the_baseline(self) -> None:
        ranked = _factory(
            name="ranked",
            requirements=TechniqueRequirements(objective_scorer=ScorerRequirements(preserves_float_scale_value=True)),
        )
        attacks = _builder(SubStringScorer(substring="x")).build(
            technique_factories={"ranked": ranked}, dataset_groups=DATASETS, include_baseline=True
        )
        assert [a.atomic_attack_name for a in attacks] == ["baseline"]
        assert attacks[0].seed_groups == [*DATASETS["airt_hate"], *DATASETS["airt_violence"]]

    def test_single_scorer_keeps_the_plain_config_for_a_narrowed_attack(self) -> None:
        factory = _factory(name="narrow", scoring_config_type=_NarrowScoringConfig)
        _builder(SubStringScorer(substring="other")).build(
            technique_factories={"narrow": factory}, dataset_groups={"d": [_group("o1")]}
        )
        assert type(factory.create.call_args.kwargs["attack_scoring_config"]) is AttackScoringConfig


class TestTechniqueConfigurationIdentity:
    """Scorer requirements that require nothing must not change a saved technique's fingerprint."""

    def test_empty_scorer_requirements_are_left_out_of_the_fingerprint(self) -> None:
        factory = AttackTechniqueFactory(name="plain", attack_class=PromptSendingAttack)
        assert "objective_scorer" not in factory._requirements_identity()
        assert set(factory._requirements_identity()) == {"objective_target", "seed_group"}

    def test_declared_scorer_requirements_change_the_fingerprint(self) -> None:
        plain = AttackTechniqueFactory(name="plain", attack_class=PromptSendingAttack)
        ranked = AttackTechniqueFactory(
            name="plain",
            attack_class=PromptSendingAttack,
            requirements=TechniqueRequirements(objective_scorer=ScorerRequirements(preserves_float_scale_value=True)),
        )
        assert ranked._requirements_identity()["objective_scorer"] == {
            "preserves_float_scale_value": True,
            "evaluation": None,
        }
        assert plain.get_configuration_identity() != ranked.get_configuration_identity()
