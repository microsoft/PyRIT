# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for pyrit.models.identifiers.identifier_projection."""

from pyrit.models.identifiers import (
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    TargetIdentifier,
    project_behavioral_identity,
)


class TestProjectBehavioralIdentity:
    """The behavioral projection is driven by each type's own field markers."""

    def test_drops_operational_target_params_and_applies_declared_fallback(self):
        """endpoint is Evaluate.Exclude; underlying_model_name falls back to model_name."""
        target = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://example.com",
                "model_name": "gpt-4o-deployment",
                "max_requests_per_minute": 60,
                "temperature": 0.7,
            },
        )

        result = project_behavioral_identity(target, identifier_type=TargetIdentifier)

        assert result.params == {"underlying_model_name": "gpt-4o-deployment", "temperature": 0.7}

    def test_excludes_children_the_parent_slot_marks_excluded(self):
        """AttackIdentifier.objective_scorer is Evaluate.Exclude."""
        attack = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack",
            children={
                "objective_scorer": ComponentIdentifier(
                    class_name="FloatScaleThresholdScorer",
                    class_module="pyrit.score",
                    params={"threshold": 0.1},
                ),
            },
        )
        technique = ComponentIdentifier(
            class_name="AttackTechnique",
            class_module="pyrit.scenario.core.attack_technique",
            children={"attack": attack},
        )

        result = project_behavioral_identity(technique, identifier_type=AttackTechniqueIdentifier)

        assert "objective_scorer" not in result.get_child_list("attack")[0].children

    def test_unwraps_wrapper_targets(self):
        """TargetIdentifier.targets is Evaluate.Unwrap."""
        inner = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "endpoint": "https://inner.example.com"},
        )
        wrapper = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target",
            params={"endpoint": "https://wrapper.example.com"},
            children={"targets": [inner]},
        )

        result = project_behavioral_identity(wrapper, identifier_type=TargetIdentifier)

        assert result.class_name == "OpenAIChatTarget"
        assert result.params == {"underlying_model_name": "gpt-4o"}

    def test_keeps_the_full_behavior_of_a_child_the_eval_hash_narrows(self):
        """AttackIdentifier.objective_target narrows to temperature for hashing only."""
        attack = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack",
            children={
                "objective_target": ComponentIdentifier(
                    class_name="OpenAIChatTarget",
                    class_module="pyrit.prompt_target",
                    params={
                        "endpoint": "https://example.com",
                        "underlying_model_name": "gpt-4o",
                        "temperature": 0.7,
                    },
                ),
            },
        )
        technique = ComponentIdentifier(
            class_name="AttackTechnique",
            class_module="pyrit.scenario.core.attack_technique",
            children={"attack": attack},
        )

        result = project_behavioral_identity(technique, identifier_type=AttackTechniqueIdentifier)

        projected_target = result.get_child_list("attack")[0].get_child_list("objective_target")[0]
        assert projected_target.params == {"underlying_model_name": "gpt-4o", "temperature": 0.7}

    def test_undeclared_child_slots_use_the_runtime_instance_type(self):
        """A target stored in an undeclared slot is still projected as a target."""
        technique = ComponentIdentifier(
            class_name="AttackTechnique",
            class_module="pyrit.scenario.core.attack_technique",
            children={
                "some_future_target": TargetIdentifier(
                    class_name="OpenAIChatTarget",
                    class_module="pyrit.prompt_target",
                    endpoint="https://example.com",
                    underlying_model_name="gpt-4o",
                ),
            },
        )

        result = project_behavioral_identity(technique, identifier_type=AttackTechniqueIdentifier)

        assert "endpoint" not in result.get_child_list("some_future_target")[0].params
