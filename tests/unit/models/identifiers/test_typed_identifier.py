# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the strongly-typed identifier projections (ComponentIdentifier subclasses)."""

import pytest

from pyrit.models.identifiers import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    ConverterIdentifier,
    ScorerIdentifier,
    SeedIdentifier,
    TargetIdentifier,
)
from pyrit.models.parameter import ComponentType


def _target_identifier() -> ComponentIdentifier:
    """A representative target ComponentIdentifier."""
    return ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai.openai_chat_target",
        params={
            "endpoint": "https://example.openai.azure.com",
            "model_name": "gpt-4o",
            "underlying_model_name": "gpt-4o",
            "temperature": 0.7,
            "top_p": 0.9,
            "max_requests_per_minute": 60,
            "custom_thing": "keep-me",
        },
    )


def _converter_identifier() -> ComponentIdentifier:
    """A representative converter ComponentIdentifier."""
    return ComponentIdentifier(
        class_name="Base64Converter",
        class_module="pyrit.prompt_converter.base64_converter",
        params={
            "supported_input_types": ["text"],
            "supported_output_types": ["text"],
            "some_option": 3,
        },
    )


def _round_robin_identifier() -> ComponentIdentifier:
    """A composite target identifier with list-valued children."""
    inner_a = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai.openai_chat_target",
        params={"endpoint": "https://a", "model_name": "gpt-4o"},
    )
    inner_b = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai.openai_chat_target",
        params={"endpoint": "https://b", "model_name": "gpt-4o-mini"},
    )
    return ComponentIdentifier(
        class_name="RoundRobinTarget",
        class_module="pyrit.prompt_target.round_robin_target",
        params={"weights": [1, 1]},
        children={"targets": [inner_a, inner_b]},
    )


def _scorer_with_child_identifier() -> ComponentIdentifier:
    """A scorer-shaped identifier with a single (non-list) child."""
    child = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai.openai_chat_target",
        params={"endpoint": "https://c", "model_name": "gpt-4o", "temperature": 0.0},
    )
    return ComponentIdentifier(
        class_name="SelfAskScaleScorer",
        class_module="pyrit.score.self_ask_scale_scorer",
        params={"scorer_type": "float_scale", "some_param": "v"},
        children={"prompt_target": child},
    )


class TestPromoteInvariant:
    """The core round-trip guarantee: from_component_identifier() preserves identity and serialization."""

    @pytest.mark.parametrize(
        ("typed_cls", "factory"),
        [
            (TargetIdentifier, _target_identifier),
            (ConverterIdentifier, _converter_identifier),
            (TargetIdentifier, _round_robin_identifier),
            (ScorerIdentifier, _scorer_with_child_identifier),
        ],
    )
    def test_hash_preserved(self, typed_cls, factory):
        """promote(ci).hash == ci.hash."""
        ci = factory()
        assert typed_cls.from_component_identifier(ci).hash == ci.hash

    @pytest.mark.parametrize(
        ("typed_cls", "factory"),
        [
            (TargetIdentifier, _target_identifier),
            (ConverterIdentifier, _converter_identifier),
            (TargetIdentifier, _round_robin_identifier),
            (ScorerIdentifier, _scorer_with_child_identifier),
        ],
    )
    def test_full_structure_preserved(self, typed_cls, factory):
        """The full flat serialization round-trips byte-for-byte."""
        ci = factory()
        assert typed_cls.from_component_identifier(ci).model_dump() == ci.model_dump()

    @pytest.mark.parametrize(
        ("typed_cls", "factory"),
        [
            (TargetIdentifier, _target_identifier),
            (ConverterIdentifier, _converter_identifier),
            (TargetIdentifier, _round_robin_identifier),
            (ScorerIdentifier, _scorer_with_child_identifier),
        ],
    )
    def test_hash_recomputed_from_scratch_matches(self, typed_cls, factory):
        """
        Recomputing the hash from the projected params/children (not forwarding the
        stored hash) still equals the original — proves params are losslessly captured.
        """
        ci = factory()
        rebuilt = typed_cls.from_component_identifier(ci)
        recomputed = ComponentIdentifier(
            class_name=rebuilt.class_name,
            class_module=rebuilt.class_module,
            params=rebuilt.params,
            children=rebuilt.children,
        )
        assert recomputed.hash == ci.hash

    def test_promote_is_pass_through_for_same_type(self):
        td = TargetIdentifier.from_component_identifier(_target_identifier())
        assert TargetIdentifier.from_component_identifier(td) is td


class TestTargetIdentifier:
    """Promotion of well-known target params; capabilities are intentionally not projected."""

    def test_promoted_fields(self):
        td = TargetIdentifier.from_component_identifier(_target_identifier())
        assert td.endpoint == "https://example.openai.azure.com"
        assert td.model_name == "gpt-4o"
        assert td.underlying_model_name == "gpt-4o"
        assert td.temperature == 0.7
        assert td.top_p == 0.9
        assert td.max_requests_per_minute == 60

    def test_unknown_params_stay_in_params(self):
        td = TargetIdentifier.from_component_identifier(_target_identifier())
        assert td.params["custom_thing"] == "keep-me"
        # Promoted params are mirrored into params (so hashing/serialization is identical).
        assert td.params["endpoint"] == "https://example.openai.azure.com"

    def test_capabilities_not_projected_as_typed_field(self):
        # Capabilities describe a target but are deliberately not part of its
        # typed identity, so there is no ``capabilities`` field.
        assert "capabilities" not in TargetIdentifier.model_fields

    def test_inner_targets_typed_as_targets(self):
        td = TargetIdentifier.from_component_identifier(_round_robin_identifier())
        inner = td.targets
        assert isinstance(inner, list)
        assert all(isinstance(child, TargetIdentifier) for child in inner)
        assert inner[0].endpoint == "https://a"


class TestConverterIdentifier:
    """Promotion of converter input/output types."""

    def test_promoted_fields(self):
        cd = ConverterIdentifier.from_component_identifier(_converter_identifier())
        assert cd.supported_input_types == ["text"]
        assert cd.supported_output_types == ["text"]
        assert cd.params["some_option"] == 3

    def test_promoted_children_typed_per_field(self):
        target_child = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"endpoint": "https://obj", "model_name": "gpt-4o"},
        )
        sub_converter_child = ComponentIdentifier(
            class_name="Base64Converter",
            class_module="pyrit.prompt_converter.base64_converter",
            params={"supported_input_types": ["text"]},
        )
        ci = ComponentIdentifier(
            class_name="LLMGenericTextConverter",
            class_module="pyrit.prompt_converter.llm_generic_text_converter",
            params={},
            children={
                "converter_target": target_child,
                "sub_converter": sub_converter_child,
            },
        )
        cd = ConverterIdentifier.from_component_identifier(ci)
        assert isinstance(cd.converter_target, TargetIdentifier)
        assert cd.converter_target.endpoint == "https://obj"
        assert isinstance(cd.sub_converter, ConverterIdentifier)
        assert cd.hash == ci.hash


class TestScorerIdentifier:
    """Promotion of the scorer type discriminator and child target."""

    def test_promoted_fields(self):
        sd = ScorerIdentifier.from_component_identifier(_scorer_with_child_identifier())
        assert sd.scorer_type == "float_scale"
        assert isinstance(sd.prompt_target, TargetIdentifier)
        assert sd.prompt_target.endpoint == "https://c"


class TestComponentType:
    """Each leaf identifier self-reports its registry family; the base reports none."""

    def test_base_is_not_buildable(self):
        assert ComponentIdentifier.component_type is None

    @pytest.mark.parametrize(
        "identifier_type, expected",
        [
            (TargetIdentifier, ComponentType.TARGET),
            (ConverterIdentifier, ComponentType.CONVERTER),
            (ScorerIdentifier, ComponentType.SCORER),
        ],
    )
    def test_leaf_component_type(self, identifier_type, expected):
        assert identifier_type.component_type is expected

    def test_converter_reference_args_map_to_target(self):
        # converter_target (typed TargetIdentifier) and sub_converter (typed
        # ConverterIdentifier) are both Param.Include buildable references; the
        # Param.ClassAttr type lists are not.
        assert ConverterIdentifier.get_reference_component_types() == {
            "converter_target": ComponentType.TARGET,
            "sub_converter": ComponentType.CONVERTER,
        }

    def test_base_identifier_has_no_reference_args(self):
        assert ComponentIdentifier.get_reference_component_types() == {}


class TestClassAttributeValues:
    """``get_class_attribute_values`` reads Param.ClassAttr fields off a target class."""

    def test_reads_converter_supported_types(self):
        class _FakeConverter:
            SUPPORTED_INPUT_TYPES = ["text"]
            SUPPORTED_OUTPUT_TYPES = ["text", "image_path"]

        values = ConverterIdentifier.get_class_attribute_values(_FakeConverter)
        assert values == {
            "supported_input_types": ["text"],
            "supported_output_types": ["text", "image_path"],
        }

    def test_missing_attribute_maps_to_none(self):
        class _NoTypes:
            pass

        values = ConverterIdentifier.get_class_attribute_values(_NoTypes)
        assert values == {"supported_input_types": None, "supported_output_types": None}

    def test_base_identifier_has_no_class_attributes(self):
        assert ComponentIdentifier.get_class_attribute_values(object) == {}


class TestDirectConstruction:
    """Building a typed identifier by hand yields a valid ComponentIdentifier."""

    def test_hand_built_target(self):
        td = TargetIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            endpoint="https://hand",
            model_name="gpt-4o",
        )
        assert td.class_name == "OpenAIChatTarget"
        assert td.params["endpoint"] == "https://hand"
        assert td.params["model_name"] == "gpt-4o"
        assert td.hash is not None
        # A hand-built typed identifier hashes identically to the plain projection.
        plain = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"endpoint": "https://hand", "model_name": "gpt-4o"},
        )
        assert td.hash == plain.hash

    def test_none_promoted_fields_are_dropped(self):
        td = TargetIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            endpoint="https://hand",
        )
        assert "temperature" not in td.params
        assert "top_p" not in td.params

    def test_of_with_promoted_kwargs(self):
        class _Obj:
            pass

        sd = ScorerIdentifier.of(_Obj(), scorer_type="true_false")
        assert sd.scorer_type == "true_false"
        assert sd.params["scorer_type"] == "true_false"


class TestCompositeIdentifiers:
    """Attack / technique / atomic identifiers compose typed children."""

    def test_attack_identifier_children_typed(self):
        objective_target = TargetIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            endpoint="https://obj",
        )
        scorer = ScorerIdentifier(
            class_name="SelfAskScaleScorer",
            class_module="pyrit.score.self_ask_scale_scorer",
            scorer_type="float_scale",
        )
        attack = AttackIdentifier(
            class_name="RedTeamingAttack",
            class_module="pyrit.executor.attack.red_teaming",
            objective_target=objective_target,
            objective_scorer=scorer,
        )
        assert attack.children["objective_target"].hash == objective_target.hash
        assert attack.children["objective_scorer"].hash == scorer.hash

        rebuilt = AttackIdentifier.from_component_identifier(ComponentIdentifier.model_validate(attack.model_dump()))
        assert isinstance(rebuilt.objective_target, TargetIdentifier)
        assert isinstance(rebuilt.objective_scorer, ScorerIdentifier)
        assert rebuilt.hash == attack.hash

    def test_atomic_identifier_empty_seed_list_preserved(self):
        attack = AttackIdentifier(
            class_name="RedTeamingAttack",
            class_module="pyrit.executor.attack.red_teaming",
        )
        technique = AttackTechniqueIdentifier(
            class_name="AttackTechnique",
            class_module="pyrit.scenario.core.attack_technique",
            attack=attack,
        )
        atomic = AtomicAttackIdentifier(
            class_name="AtomicAttack",
            class_module="pyrit.scenario.core.atomic_attack",
            attack_technique=technique,
            seed_identifiers=[],
        )
        # An explicitly-set empty list is preserved in children (hash-affecting).
        assert atomic.children["seed_identifiers"] == []
        plain = ComponentIdentifier(
            class_name="AtomicAttack",
            class_module="pyrit.scenario.core.atomic_attack",
            children={"attack_technique": technique, "seed_identifiers": []},
        )
        assert atomic.hash == plain.hash

    def test_seed_identifier_promoted_fields(self):
        sid = SeedIdentifier(
            class_name="Seed",
            class_module="pyrit.models.seeds.seed",
            value="hello",
            value_sha256="abc",
            dataset_name="ds",
            is_general_technique=False,
        )
        assert sid.params == {
            "value": "hello",
            "value_sha256": "abc",
            "dataset_name": "ds",
            "is_general_technique": False,
        }
