# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import patch

import pytest
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import (
    AttackExecutor,
    AttackScoringConfig,
    AttackStrategy,
    CrescendoAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.compound import SequentialAttack, SequentialChildAttack
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AdversarialPrompts,
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackSeedGroup,
    JsonResponseConfig,
    Message,
    MessagePiece,
    ScenarioRunPlanGroupKind,
    Score,
    SeedObjective,
    SeedPrompt,
    get_common_json_schema,
)
from pyrit.scenario import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.matrix_atomic_attack_builder import MatrixAtomicAttackBuilder
from pyrit.scenario.core.technique_requirements import IncompatibleTechniqueError, IncompatibleTechniquePolicy
from pyrit.score import SubStringScorer
from pyrit.setup.initializers.techniques.core import get_technique_factories


def _group(
    *, instructions: str = "A customer who needs the return policy.", include_user: bool = False
) -> AttackSeedGroup:
    prompt = SeedPrompt(
        value="Objective: {{ objective }}\n{{ user_instructions }}",
        data_type="text",
        parameters=["objective", "user_instructions"],
        response_json_schema=get_common_json_schema("adversarial_chat"),
    ).bind_template_parameters(user_instructions=instructions)
    seeds = [SeedObjective(value="Explain the published return policy.")]
    if include_user:
        seeds.append(SeedPrompt(value="What is the return period?", data_type="text", role="user"))
    return AttackSeedGroup(seeds=seeds, adversarial_prompts=AdversarialPrompts(system_prompt=prompt))


def _factory(adversarial_chat: MockPromptTarget) -> AttackTechniqueFactory:
    return AttackTechniqueFactory(
        name="policy_conversation",
        attack_class=RedTeamingAttack,
        adversarial_chat=adversarial_chat,
        attack_kwargs={"max_turns": 3},
    )


class _PolicyScorer(SubStringScorer):
    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        scores = await super()._score_piece_async(message_piece, objective=objective)
        scores[0].score_rationale = "Ask for the published policy without private order details."
        return scores


@pytest.mark.usefixtures("patch_central_database")
class TestGroupAdversarialPrompts:
    @pytest.mark.parametrize("group_kind", [ScenarioRunPlanGroupKind.ATTACK, ScenarioRunPlanGroupKind.ADAPTIVE])
    @pytest.mark.parametrize("binding", ["unbound", "different", "changed"])
    def test_atomic_boundary_rejects_unbound_or_mismatched_prompts(
        self, *, group_kind: ScenarioRunPlanGroupKind, binding: str
    ) -> None:
        source = _group()
        bound_group = None
        if binding == "different":
            bound_group = _group(instructions="A different role.")
        elif binding == "changed":
            bound_group = source
        technique = _factory(MockPromptTarget()).create(
            objective_target=MockPromptTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="policy")),
            seed_group=bound_group,
        )
        if binding == "changed":
            source.adversarial_prompts.system_prompt.value += "\nChanged instructions."
            source.objective.metadata.update(source.adversarial_prompts.to_metadata())
        with pytest.raises(IncompatibleTechniqueError, match="adversarial prompts.*bound"):
            AtomicAttack(
                atomic_attack_name="unbound",
                attack_technique=technique,
                seed_groups=[source],
                group_kind=group_kind,
            )

    def test_compound_binding_retains_configured_child_identity(self) -> None:
        source = _group()
        target = MockPromptTarget()
        child = _factory(MockPromptTarget()).create(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="policy")),
            seed_group=source,
        )
        sequence = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(
                    strategy=child.attack, seed_group=source, technique_identifier=child.get_identifier()
                )
            ],
        )
        unbound = AttackTechnique(attack=sequence)
        bound = AttackTechnique(attack=sequence, adversarial_prompts=source.adversarial_prompts)
        assert bound.get_identifier() == unbound.get_identifier()
        atomic = AtomicAttack(atomic_attack_name="compound", attack_technique=bound, seed_groups=[source])
        assert atomic.seed_groups == [source]

    def test_bound_technique_identity_includes_the_response_contract(self) -> None:
        group = _group()
        factory = _factory(MockPromptTarget())
        target = MockPromptTarget()
        scoring = AttackScoringConfig(objective_scorer=SubStringScorer(substring="policy"))
        first = factory.create(objective_target=target, attack_scoring_config=scoring, seed_group=group)
        group.adversarial_prompts.system_prompt.response_json_schema = {
            **get_common_json_schema("adversarial_chat"),
            "description": "A distinct response contract.",
        }
        group.objective.metadata.update(group.adversarial_prompts.to_metadata())
        second = factory.create(objective_target=target, attack_scoring_config=scoring, seed_group=group)

        assert first.get_identifier().hash != second.get_identifier().hash
        assert (
            AtomicAttackEvaluationIdentifier(
                AtomicAttackIdentifier.build(technique_identifier=first.get_identifier())
            ).eval_hash
            != AtomicAttackEvaluationIdentifier(
                AtomicAttackIdentifier.build(technique_identifier=second.get_identifier())
            ).eval_hash
        )

    @pytest.mark.parametrize("slot", ["first_message", "feedback_template"])
    @pytest.mark.parametrize("attack_class", [CrescendoAttack, TreeOfAttacksWithPruningAttack, RedTeamingAttack])
    def test_named_user_prompt_slots_match_attack_consumption(
        self, *, slot: str, attack_class: type[AttackStrategy]
    ) -> None:
        prompt = SeedPrompt(value="Ask about the public return policy.", data_type="text")
        group = AttackSeedGroup(
            seeds=[SeedObjective(value="Explain the return policy.")],
            adversarial_prompts=AdversarialPrompts(**{slot: prompt}),
        )
        factory = AttackTechniqueFactory(name="slot_contract", attack_class=attack_class)
        if attack_class is not RedTeamingAttack:
            with patch.object(attack_class, "__init__") as constructor:
                with pytest.raises(IncompatibleTechniqueError, match=slot):
                    factory.create(
                        objective_target=MockPromptTarget(),
                        attack_scoring_config=AttackScoringConfig(),
                        seed_group=group,
                    )
                constructor.assert_not_called()
        else:
            technique = factory.create(
                objective_target=MockPromptTarget(),
                adversarial_chat=MockPromptTarget(),
                attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="policy")),
                seed_group=group,
            )
            config = technique.attack.get_attack_adversarial_config()
            effective = config.first_message if slot == "first_message" else config.adversarial_prompt_template
            assert effective.value == prompt.value

    @pytest.mark.parametrize("policy", list(IncompatibleTechniquePolicy))
    @pytest.mark.parametrize("first_message_schema", [True, False])
    @pytest.mark.parametrize("replacement_system", [False, True])
    def test_default_system_schema_conflicts_are_checked_before_construction(
        self, *, policy: IncompatibleTechniquePolicy, first_message_schema: bool, replacement_system: bool
    ) -> None:
        group = AttackSeedGroup(
            seeds=[SeedObjective(value="Explain the return policy.")],
            adversarial_prompts=AdversarialPrompts(
                system_prompt=(
                    SeedPrompt(value="Achieve {{ objective }}", parameters=["objective"])
                    if replacement_system
                    else None
                ),
                first_message=SeedPrompt(
                    value="Ask about returns.",
                    response_json_schema=get_common_json_schema("adversarial_chat") if first_message_schema else None,
                ),
            ),
        )
        conflict = first_message_schema and not replacement_system
        ordinary = AttackSeedGroup(seeds=[SeedObjective(value="Describe delivery options.")])
        factory = _factory(MockPromptTarget())
        builder = MatrixAtomicAttackBuilder(
            objective_target=MockPromptTarget(),
            objective_scorer=SubStringScorer(substring="policy"),
            incompatible_technique_policy=policy,
        )
        with patch.object(factory, "create", wraps=factory.create) as create:
            if conflict and policy is IncompatibleTechniquePolicy.RAISE:
                with pytest.raises(IncompatibleTechniqueError, match="response_json_schema"):
                    builder.build(
                        technique_factories={"rta": factory},
                        dataset_groups={"support": [group, ordinary]},
                        include_baseline=True,
                    )
                create.assert_not_called()
            else:
                atomics = builder.build(
                    technique_factories={"rta": factory},
                    dataset_groups={"support": [group, ordinary]},
                    include_baseline=True,
                )
                assert len(atomics) == (2 if conflict else 3)
                assert atomics[0].seed_groups == [group, ordinary]
                assert [source for atomic in atomics[1:] for source in atomic.seed_groups] == (
                    [ordinary] if conflict else [group, ordinary]
                )
                assert create.call_count == (1 if conflict else 2)

    @pytest.mark.parametrize("policy", list(IncompatibleTechniquePolicy))
    @pytest.mark.parametrize("include_user", [True, False])
    def test_core_matrix_checks_group_inputs_before_construction(
        self,
        *,
        policy: IncompatibleTechniquePolicy,
        include_user: bool,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        groups = list(
            make_assert_result(config=make_assert_config(group_count=2, include_user_prompt=include_user)).seed_groups
        )
        catalog = {factory.name: factory for factory in get_technique_factories()}
        factories = {name: catalog[name] for name in ("code_attack", "tap", "red_teaming")}
        builder = MatrixAtomicAttackBuilder(
            objective_target=MockPromptTarget(),
            objective_scorer=SubStringScorer(substring="30 days"),
            incompatible_technique_policy=policy,
        )
        with (
            patch(
                "pyrit.scenario.core.attack_technique_factory.get_default_adversarial_target",
                return_value=MockPromptTarget(),
            ),
            patch.object(factories["code_attack"], "create", wraps=factories["code_attack"].create) as code,
            patch.object(factories["tap"], "create", wraps=factories["tap"].create) as tap,
            patch.object(factories["red_teaming"], "create", wraps=factories["red_teaming"].create) as red_teaming,
        ):
            if policy is IncompatibleTechniquePolicy.RAISE:
                with pytest.raises(IncompatibleTechniqueError, match="code_attack.*does not accept"):
                    builder.build(
                        technique_factories=factories, dataset_groups={"assert": groups}, include_baseline=True
                    )
                red_teaming.assert_not_called()
            else:
                atomics = builder.build(
                    technique_factories=factories, dataset_groups={"assert": groups}, include_baseline=True
                )
                assert len(atomics) == 3
                assert atomics[0].atomic_attack_name == "baseline"
                assert atomics[0].seed_groups == groups
                assert [atomic.technique_name for atomic in atomics[1:]] == ["red_teaming", "red_teaming"]
                assert [atomic.seed_groups[0] for atomic in atomics[1:]] == groups
                assert red_teaming.call_count == 2
                summaries = [
                    record.getMessage() for record in caplog.records if "skipped 2 seed group" in record.getMessage()
                ]
                assert len(summaries) == 2
                assert any("code_attack" in summary for summary in summaries)
                assert any("tap" in summary and "desired_prefix" in summary for summary in summaries)
            code.assert_not_called()
            tap.assert_not_called()

    def test_factory_binds_named_inputs_to_fresh_constructor_configs(self) -> None:
        adversarial_chat = MockPromptTarget()
        objective_target = MockPromptTarget()
        scorer = SubStringScorer(substring="30 days")
        scoring = AttackScoringConfig(objective_scorer=scorer)
        group = _group()
        assert group.adversarial_prompts is not None
        prompts = group.adversarial_prompts
        prompts = AdversarialPrompts(
            system_prompt=prompts.system_prompt,
            first_message=SeedPrompt(value="Begin {{ objective }}", data_type="text", parameters=["objective"]),
            feedback_template=SeedPrompt(
                value="Continue {{ objective }}: {{ feedback_text }}",
                data_type="text",
                parameters=["objective", "feedback_text"],
            ),
        )
        group.objective.metadata.pop(AdversarialPrompts.METADATA_KEY)
        group = AttackSeedGroup(seeds=group.seeds, adversarial_prompts=prompts)
        factory = _factory(adversarial_chat)
        first = factory.create(objective_target=objective_target, attack_scoring_config=scoring, seed_group=group)
        second = factory.create(objective_target=objective_target, attack_scoring_config=scoring, seed_group=group)
        assert first.attack is not second.attack
        first_config = first.attack.get_attack_adversarial_config()
        second_config = second.attack.get_attack_adversarial_config()
        assert first_config is not None and second_config is not None
        assert first_config.target is second_config.target is adversarial_chat
        assert isinstance(first_config.system_prompt, SeedPrompt)
        assert isinstance(second_config.system_prompt, SeedPrompt)
        assert first_config.system_prompt is not second_config.system_prompt
        assert first_config.system_prompt is not prompts.system_prompt
        assert first_config.first_message.value == "Begin {{ objective }}"
        assert first_config.adversarial_prompt_template.value == "Continue {{ objective }}: {{ feedback_text }}"
        assert first_config.system_prompt.response_json_schema == get_common_json_schema("adversarial_chat")
        first_config.system_prompt.value = "Changed on the first attack."
        assert second_config.system_prompt.value == prompts.system_prompt.value
        assert first.attack.get_attack_scoring_config().objective_scorer is scorer

    def test_factory_rejects_unsupported_group_inputs(self) -> None:
        factory = AttackTechniqueFactory(name="single", attack_class=PromptSendingAttack)
        with pytest.raises(ValueError, match="does not accept group adversarial prompts"):
            factory.create(
                objective_target=MockPromptTarget(),
                attack_scoring_config=AttackScoringConfig(),
                seed_group=_group(),
            )

    @pytest.mark.parametrize("baked", [True, False])
    def test_factory_rejects_competing_prompt_sources(self, baked: bool) -> None:
        target = MockPromptTarget()
        factory = (
            AttackTechniqueFactory(
                name="custom",
                attack_class=RedTeamingAttack,
                adversarial_chat=target,
                adversarial_system_prompt="A custom role for {{ objective }}",
            )
            if baked
            else _factory(target)
        )
        with pytest.raises(ValueError, match="custom adversarial prompt|not both"):
            factory.create(
                objective_target=MockPromptTarget(),
                attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="30 days")),
                seed_group=_group(),
                adversarial_system_prompt=None if baked else "An explicit role for {{ objective }}",
            )

    @pytest.mark.parametrize("attack_class", [RedTeamingAttack, CrescendoAttack, TreeOfAttacksWithPruningAttack])
    def test_factory_validates_the_group_prompt_contract(self, attack_class: type[AttackStrategy]) -> None:
        group = AttackSeedGroup(
            seeds=[SeedObjective(value="goal")],
            adversarial_prompts=AdversarialPrompts(system_prompt=SeedPrompt(value="No objective parameter.")),
        )
        factory = AttackTechniqueFactory(name="missing_parameters", attack_class=attack_class)
        with patch.object(attack_class, "__init__") as constructor:
            with pytest.raises(IncompatibleTechniqueError, match="missing required parameters"):
                factory.create(
                    objective_target=MockPromptTarget(),
                    attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="yes")),
                    seed_group=group,
                )
            constructor.assert_not_called()

    def test_matrix_uses_stable_names_and_independent_attacks(self) -> None:
        objective_target = MockPromptTarget()
        adversarial_chat = MockPromptTarget()
        groups = [_group(instructions="A new customer."), _group(instructions="A returning customer.")]
        builder = MatrixAtomicAttackBuilder(
            objective_target=objective_target, objective_scorer=SubStringScorer(substring="30 days")
        )
        factory = _factory(adversarial_chat)
        first = builder.build(technique_factories={"rta": factory}, dataset_groups={"support": groups})
        second = builder.build(technique_factories={"rta": factory}, dataset_groups={"support": groups})
        assert len(first) == 2
        assert len({attack.atomic_attack_name for attack in first}) == 2
        assert [attack.atomic_attack_name for attack in first] == [attack.atomic_attack_name for attack in second]
        assert {attack.display_group for attack in first} == {"rta"}
        assert first[0].attack_technique.attack is not first[1].attack_technique.attack
        for atomic, group in zip(first, groups, strict=True):
            config = atomic.attack_technique.attack.get_attack_adversarial_config()
            assert config is not None and isinstance(config.system_prompt, SeedPrompt)
            assert config.target is adversarial_chat
            assert group.adversarial_prompts is not None
            assert config.system_prompt.value == group.adversarial_prompts.system_prompt.value

    @pytest.mark.parametrize("same_prompts", [False, True])
    def test_repeated_objectives_and_baselines_have_distinct_execution_units(self, same_prompts: bool) -> None:
        groups = [
            _group(),
            _group(instructions="A customer who needs the return policy." if same_prompts else "A new user."),
        ]
        builder = MatrixAtomicAttackBuilder(
            objective_target=MockPromptTarget(), objective_scorer=SubStringScorer(substring="30 days")
        )
        results = builder.build(
            technique_factories={"rta": _factory(MockPromptTarget())},
            dataset_groups={"support": groups},
            include_baseline=True,
        )
        assert len(results) == 4
        assert len({attack.atomic_attack_name for attack in results}) == 4
        assert [attack.display_group for attack in results] == ["baseline", "baseline", "rta", "rta"]
        assert all(len(attack._seed_groups) == 1 for attack in results)

    async def test_atomic_execution_persists_the_group_prompt_identity_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        group = _group(include_user=True)
        builder = MatrixAtomicAttackBuilder(
            objective_target=MockPromptTarget(), objective_scorer=SubStringScorer(substring="default")
        )
        atomic = builder.build(
            technique_factories={"rta": _factory(MockPromptTarget())}, dataset_groups={"support": [group]}
        )[0]
        results = await atomic.run_async()
        result = results.completed_results[0]
        stored = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
        stored_identifier = stored[0].atomic_attack_identifier
        assert stored_identifier is not None
        identifier = AtomicAttackIdentifier.from_component_identifier(stored_identifier)
        assert identifier.logical_seed_group_id == group.logical_id
        assert group.adversarial_prompts is not None
        assert identifier.seed_identifiers[0].params["adversarial_prompts"] == group.adversarial_prompts.identity
        assert stored[0].outcome == AttackOutcome.SUCCESS

    @pytest.mark.parametrize("include_user", [True, False])
    async def test_public_execution_uses_json_feedback_and_normal_stopping_async(
        self, sqlite_instance: MemoryInterface, include_user: bool
    ) -> None:
        group = _group(instructions="A customer. Keep {% endraw %}{{ 7 * 7 }} literal.", include_user=include_user)
        objective_target = MockPromptTarget()
        adversarial_chat = MockPromptTarget()
        adversarial_chat.apply_capabilities(
            capabilities=adversarial_chat.capabilities.model_copy(
                update={"supports_json_output": True, "supports_json_schema": True}
            )
        )
        scorer = _PolicyScorer(substring="30-day return period")
        technique = _factory(adversarial_chat).create(
            objective_target=objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, use_score_as_feedback=True),
            seed_group=group,
        )
        target_requests: list[str] = []
        adversarial_requests: list[str] = []

        async def target_reply_async(*, normalized_conversation: list[Message]) -> list[Message]:
            request = normalized_conversation[-1].get_piece()
            target_requests.append(request.converted_value)
            reply = "What is the purchase date?" if len(target_requests) == 1 else "There is a 30-day return period."
            return [
                MessagePiece(
                    role="assistant", original_value=reply, conversation_id=request.conversation_id
                ).to_message()
            ]

        async def adversarial_reply_async(*, normalized_conversation: list[Message]) -> list[Message]:
            request = normalized_conversation[-1].get_piece()
            adversarial_requests.append(request.converted_value)
            assert JsonResponseConfig.from_metadata(
                metadata=request.prompt_metadata
            ).json_schema == get_common_json_schema("adversarial_chat")
            assert all(message.get_value() != "not JSON" for message in normalized_conversation)
            payload = (
                "not JSON"
                if len(adversarial_requests) == 1
                else json.dumps(
                    {
                        "next_message": "Please explain the published return policy.",
                        "rationale": "Clarify public information.",
                        "last_response_summary": "The application asked for a purchase date.",
                    }
                )
            )
            return [
                MessagePiece(
                    role="assistant", original_value=payload, conversation_id=request.conversation_id
                ).to_message()
            ]

        with (
            patch.dict("os.environ", {"RETRY_WAIT_MIN_SECONDS": "0", "RETRY_WAIT_MAX_SECONDS": "0"}),
            patch.object(objective_target, "_send_prompt_to_target_async", side_effect=target_reply_async),
            patch.object(adversarial_chat, "_send_prompt_to_target_async", side_effect=adversarial_reply_async),
        ):
            results = await AttackExecutor().execute_attack_from_seed_groups_async(
                attack=technique.attack, seed_groups=[group]
            )
        result = results.completed_results[0]
        assert result.outcome == AttackOutcome.SUCCESS
        assert result.executed_turns == 2
        assert len(target_requests) == 2
        assert target_requests[0] == (
            "What is the return period?" if include_user else "Please explain the published return policy."
        )
        assert target_requests[-1] == "Please explain the published return policy."
        assert any(
            "Ask for the published policy without private order details." in request for request in adversarial_requests
        )
        assert group.objective.value in adversarial_chat.system_prompt
        assert "{% endraw %}{{ 7 * 7 }}" in adversarial_chat.system_prompt
        assert not any("rationale" in request or "A customer." in request for request in target_requests)
        scores = await sqlite_instance.get_scores_async(score_ids=[result.last_score.id])
        assert scores[0].get_value() is True
