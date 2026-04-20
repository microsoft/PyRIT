# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.identifiers import (
    AtomicAttackEvaluationIdentifier,
    ComponentIdentifier,
    build_atomic_attack_identifier,
    build_seed_identifier,
    compute_eval_hash,
)
from pyrit.models.seeds.seed_prompt import SeedPrompt


class _FakeSeedGroup:
    """Minimal stub for SeedGroup with a seeds list."""

    def __init__(self, *, seeds: list):
        self.seeds = seeds


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

_ATTACK_MODULE = "pyrit.executor.attack.single_turn.prompt_sending"
_TARGET_MODULE = "pyrit.prompt_target.openai.openai_chat_target"


def _make_target(*, params: dict | None = None) -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module=_TARGET_MODULE,
        params=params or {},
    )


def _make_attack(
    *,
    class_name: str = "PromptSendingAttack",
    children: dict | None = None,
) -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name=class_name,
        class_module=_ATTACK_MODULE,
        children=children or {},
    )


# =========================================================================
# build_seed_identifier
# =========================================================================


class TestBuildSeedIdentifier:
    """Tests for build_seed_identifier."""

    def test_returns_component_identifier(self):
        seed = SeedPrompt(value="hello", value_sha256="abc123", dataset_name="test_ds", name="seed1")
        result = build_seed_identifier(seed)
        assert isinstance(result, ComponentIdentifier)

    def test_captures_class_name(self):
        seed = SeedPrompt(value="hello", value_sha256="abc123")
        assert build_seed_identifier(seed).class_name == "SeedPrompt"

    def test_includes_value_and_sha256_and_dataset(self):
        seed = SeedPrompt(value="hello", value_sha256="abc", dataset_name="my_dataset")
        result = build_seed_identifier(seed)
        assert result.params["value"] == "hello"
        assert result.params["value_sha256"] == "abc"
        assert result.params["dataset_name"] == "my_dataset"

    def test_includes_is_general_technique_true(self):
        seed = SeedPrompt(value="hello", value_sha256="abc", is_general_technique=True)
        result = build_seed_identifier(seed)
        assert result.params["is_general_technique"] is True

    def test_includes_is_general_technique_false(self):
        seed = SeedPrompt(value="hello", value_sha256="abc", is_general_technique=False)
        result = build_seed_identifier(seed)
        assert result.params["is_general_technique"] is False

    def test_none_values_present_in_params(self):
        seed = SeedPrompt(value="hello")
        seed.value_sha256 = None
        seed.dataset_name = None
        result = build_seed_identifier(seed)
        assert "value_sha256" in result.params
        assert result.params["value_sha256"] is None
        assert "dataset_name" in result.params
        assert result.params["dataset_name"] is None

    def test_deterministic_hash(self):
        seed1 = SeedPrompt(value="hello", value_sha256="abc123", dataset_name="ds")
        seed2 = SeedPrompt(value="hello", value_sha256="abc123", dataset_name="ds")
        assert build_seed_identifier(seed1).hash == build_seed_identifier(seed2).hash

    def test_different_content_different_hash(self):
        seed1 = SeedPrompt(value="hello", value_sha256="abc123")
        seed2 = SeedPrompt(value="world", value_sha256="def456")
        assert build_seed_identifier(seed1).hash != build_seed_identifier(seed2).hash


# =========================================================================
# build_atomic_attack_identifier
# =========================================================================


class TestBuildAtomicAttackIdentifier:
    """Tests for build_atomic_attack_identifier."""

    def test_returns_component_identifier(self):
        result = build_atomic_attack_identifier(attack_identifier=_make_attack())
        assert isinstance(result, ComponentIdentifier)

    def test_class_name_is_atomic_attack(self):
        result = build_atomic_attack_identifier(attack_identifier=_make_attack())
        assert result.class_name == "AtomicAttack"

    def test_class_module_is_correct(self):
        result = build_atomic_attack_identifier(attack_identifier=_make_attack())
        assert result.class_module == "pyrit.scenario.core.atomic_attack"

    def test_attack_technique_child_is_present(self):
        attack_id = _make_attack()
        result = build_atomic_attack_identifier(attack_identifier=attack_id)
        technique = result.children["attack_technique"]
        assert technique.class_name == "AttackTechnique"
        assert technique.children["attack"] == attack_id

    def test_no_seed_group_empty_seed_identifiers(self):
        result = build_atomic_attack_identifier(attack_identifier=_make_attack())
        assert result.children["seed_identifiers"] == []

    def test_empty_seed_group_empty_seed_identifiers(self):
        result = build_atomic_attack_identifier(attack_identifier=_make_attack(), seed_group=_FakeSeedGroup(seeds=[]))
        assert result.children["seed_identifiers"] == []

    def test_includes_all_seeds(self):
        general_seed = SeedPrompt(value="technique", value_sha256="abc", is_general_technique=True)
        non_general_seed = SeedPrompt(value="objective", value_sha256="def", is_general_technique=False)
        result = build_atomic_attack_identifier(
            attack_identifier=_make_attack(),
            seed_group=_FakeSeedGroup(seeds=[general_seed, non_general_seed]),
        )
        seed_ids = result.children["seed_identifiers"]
        assert len(seed_ids) == 2
        assert seed_ids[0].params.get("value_sha256") == "abc"
        assert seed_ids[0].params.get("is_general_technique") is True
        assert seed_ids[1].params.get("value_sha256") == "def"
        assert seed_ids[1].params.get("is_general_technique") is False

    def test_multiple_seeds(self):
        seed1 = SeedPrompt(value="tech1", value_sha256="aaa", is_general_technique=True)
        seed2 = SeedPrompt(value="tech2", value_sha256="bbb", is_general_technique=True)
        result = build_atomic_attack_identifier(
            attack_identifier=_make_attack(),
            seed_group=_FakeSeedGroup(seeds=[seed1, seed2]),
        )
        assert len(result.children["seed_identifiers"]) == 2

    def test_deterministic_hash(self):
        attack_id = _make_attack()
        seed = SeedPrompt(value="technique", value_sha256="abc", is_general_technique=True)
        r1 = build_atomic_attack_identifier(attack_identifier=attack_id, seed_group=_FakeSeedGroup(seeds=[seed]))
        r2 = build_atomic_attack_identifier(attack_identifier=attack_id, seed_group=_FakeSeedGroup(seeds=[seed]))
        assert r1.hash == r2.hash

    def test_different_seeds_different_hash(self):
        attack_id = _make_attack()
        seed1 = SeedPrompt(value="tech1", value_sha256="aaa", is_general_technique=True)
        seed2 = SeedPrompt(value="tech2", value_sha256="bbb", is_general_technique=True)
        r1 = build_atomic_attack_identifier(attack_identifier=attack_id, seed_group=_FakeSeedGroup(seeds=[seed1]))
        r2 = build_atomic_attack_identifier(attack_identifier=attack_id, seed_group=_FakeSeedGroup(seeds=[seed2]))
        assert r1.hash != r2.hash

    def test_different_attacks_different_hash(self):
        r1 = build_atomic_attack_identifier(attack_identifier=_make_attack(class_name="PromptSendingAttack"))
        r2 = build_atomic_attack_identifier(attack_identifier=_make_attack(class_name="CrescendoAttack"))
        assert r1.hash != r2.hash

    def test_serialization_round_trip(self):
        seed = SeedPrompt(value="technique", value_sha256="abc", is_general_technique=True, dataset_name="ds")
        original = build_atomic_attack_identifier(
            attack_identifier=_make_attack(),
            seed_group=_FakeSeedGroup(seeds=[seed]),
        )
        restored = ComponentIdentifier.from_dict(original.to_dict())
        assert restored.hash == original.hash


# =========================================================================
# AtomicAttackEvaluationIdentifier
# =========================================================================


class TestAtomicAttackEvaluationIdentifier:
    """Tests for AtomicAttackEvaluationIdentifier."""

    # -- ClassVar constants ------------------------------------------------

    def test_objective_target_rule(self):
        rule = AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES["objective_target"]
        assert rule.included_params == frozenset({"temperature"})
        assert not rule.exclude

    def test_adversarial_chat_rule(self):
        rule = AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES["adversarial_chat"]
        assert rule.included_params == frozenset({"model_name", "temperature", "top_p"})
        assert not rule.exclude

    def test_scorer_only_keys_absent(self):
        """Scorer-specific keys should not appear in attack rules."""
        assert "prompt_target" not in AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES
        assert "converter_target" not in AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES

    def test_objective_scorer_excluded(self):
        rule = AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES["objective_scorer"]
        assert rule.exclude is True

    def test_seed_identifiers_rule(self):
        rule = AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES["seed_identifiers"]
        assert rule.exclude is True

    # -- Basic properties --------------------------------------------------

    def test_identifier_property_returns_original(self):
        composite = build_atomic_attack_identifier(attack_identifier=_make_attack())
        identity = AtomicAttackEvaluationIdentifier(composite)
        assert identity.identifier is composite

    def test_eval_hash_is_64_char_hex(self):
        composite = build_atomic_attack_identifier(attack_identifier=_make_attack())
        identity = AtomicAttackEvaluationIdentifier(composite)
        assert isinstance(identity.eval_hash, str) and len(identity.eval_hash) == 64

    # -- Consistency with free functions -----------------------------------

    def test_eval_hash_matches_compute_eval_hash_with_rules(self):
        composite = build_atomic_attack_identifier(
            attack_identifier=_make_attack(children={"objective_target": _make_target(params={"temperature": 0.5})})
        )
        identity = AtomicAttackEvaluationIdentifier(composite)
        expected = compute_eval_hash(
            composite,
            child_eval_rules=AtomicAttackEvaluationIdentifier.CHILD_EVAL_RULES,
        )
        assert identity.eval_hash == expected

    # -- objective_target filtering ----------------------------------------

    def test_objective_target_operational_params_ignored(self):
        """Same temperature, different endpoint/model -> same eval hash."""
        t1 = _make_target(params={"model_name": "gpt-4o", "endpoint": "https://a.com", "temperature": 0.7})
        t2 = _make_target(params={"model_name": "gpt-3.5", "endpoint": "https://b.com", "temperature": 0.7})
        c1 = build_atomic_attack_identifier(attack_identifier=_make_attack(children={"objective_target": t1}))
        c2 = build_atomic_attack_identifier(attack_identifier=_make_attack(children={"objective_target": t2}))
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_objective_target_different_temperature_different_hash(self):
        t1 = _make_target(params={"temperature": 0.7})
        t2 = _make_target(params={"temperature": 0.0})
        c1 = build_atomic_attack_identifier(attack_identifier=_make_attack(children={"objective_target": t1}))
        c2 = build_atomic_attack_identifier(attack_identifier=_make_attack(children={"objective_target": t2}))
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash != AtomicAttackEvaluationIdentifier(c2).eval_hash

    # -- adversarial_chat filtering ----------------------------------------

    def test_adversarial_chat_model_name_affects_hash(self):
        """model_name IS in the adversarial_chat allowlist."""
        chat1 = ComponentIdentifier(class_name="Chat", class_module="m", params={"model_name": "gpt-4o"})
        chat2 = ComponentIdentifier(class_name="Chat", class_module="m", params={"model_name": "gpt-3.5"})
        a1 = _make_attack(children={"adversarial_chat": chat1})
        a2 = _make_attack(children={"adversarial_chat": chat2})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash != AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_adversarial_chat_endpoint_ignored(self):
        """endpoint is NOT in the adversarial_chat allowlist."""
        chat1 = ComponentIdentifier(
            class_name="Chat",
            class_module="m",
            params={"model_name": "gpt-4o", "endpoint": "https://a.com"},
        )
        chat2 = ComponentIdentifier(
            class_name="Chat",
            class_module="m",
            params={"model_name": "gpt-4o", "endpoint": "https://b.com"},
        )
        a1 = _make_attack(children={"adversarial_chat": chat1})
        a2 = _make_attack(children={"adversarial_chat": chat2})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    # -- objective_scorer exclusion ----------------------------------------

    def test_objective_scorer_excluded_from_eval_hash(self):
        """Different objective_scorers must produce the same eval hash."""
        scorer1 = ComponentIdentifier(
            class_name="TrueFalseScorer", class_module="pyrit.score", params={"threshold": 0.5}
        )
        scorer2 = ComponentIdentifier(
            class_name="TrueFalseScorer", class_module="pyrit.score", params={"threshold": 0.9}
        )
        a1 = _make_attack(children={"objective_scorer": scorer1})
        a2 = _make_attack(children={"objective_scorer": scorer2})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_objective_scorer_presence_vs_absence_same_hash(self):
        """Having or not having an objective_scorer must not change the eval hash."""
        scorer = ComponentIdentifier(
            class_name="TrueFalseScorer", class_module="pyrit.score", params={"threshold": 0.5}
        )
        a_with = _make_attack(children={"objective_scorer": scorer})
        a_without = _make_attack()
        c1 = build_atomic_attack_identifier(attack_identifier=a_with)
        c2 = build_atomic_attack_identifier(attack_identifier=a_without)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    # -- Converters (non-target, fully included) ---------------------------

    def test_different_request_converters_different_hash(self):
        conv1 = ComponentIdentifier(class_name="Base64Converter", class_module="pyrit.prompt_converter")
        conv2 = ComponentIdentifier(class_name="ROT13Converter", class_module="pyrit.prompt_converter")
        a1 = _make_attack(children={"request_converters": [conv1]})
        a2 = _make_attack(children={"request_converters": [conv2]})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash != AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_same_request_converters_same_hash(self):
        conv = ComponentIdentifier(class_name="Base64Converter", class_module="pyrit.prompt_converter")
        a1 = _make_attack(children={"request_converters": [conv]})
        a2 = _make_attack(children={"request_converters": [conv]})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_response_converters_contribute(self):
        conv1 = ComponentIdentifier(class_name="Base64Converter", class_module="pyrit.prompt_converter")
        conv2 = ComponentIdentifier(class_name="ROT13Converter", class_module="pyrit.prompt_converter")
        a1 = _make_attack(children={"response_converters": [conv1]})
        a2 = _make_attack(children={"response_converters": [conv2]})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash != AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_converters_contribute_while_target_endpoint_ignored(self):
        """Converters fully contribute even when objective_target operational params are stripped."""
        t1 = _make_target(params={"model_name": "gpt-4o", "endpoint": "https://a.com"})
        t2 = _make_target(params={"model_name": "gpt-4o", "endpoint": "https://b.com"})
        conv = ComponentIdentifier(class_name="Base64Converter", class_module="pyrit.prompt_converter")
        a1 = _make_attack(children={"objective_target": t1, "request_converters": [conv]})
        a2 = _make_attack(children={"objective_target": t2, "request_converters": [conv]})
        c1 = build_atomic_attack_identifier(attack_identifier=a1)
        c2 = build_atomic_attack_identifier(attack_identifier=a2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    # -- Seeds and technique_seeds (eval hash uses technique_seeds, excludes seeds) ---

    def test_different_technique_seeds_different_eval_hash(self):
        attack_id = _make_attack()
        seed1 = SeedPrompt(value="tech1", value_sha256="aaa", is_general_technique=True)
        seed2 = SeedPrompt(value="tech2", value_sha256="bbb", is_general_technique=True)
        technique1 = ComponentIdentifier(
            class_name="AttackTechnique",
            class_module="pyrit.scenario.core.attack_technique",
            children={"attack": attack_id, "technique_seeds": [build_seed_identifier(seed1)]},
        )
        technique2 = ComponentIdentifier(
            class_name="AttackTechnique",
            class_module="pyrit.scenario.core.attack_technique",
            children={"attack": attack_id, "technique_seeds": [build_seed_identifier(seed2)]},
        )
        c1 = build_atomic_attack_identifier(technique_identifier=technique1)
        c2 = build_atomic_attack_identifier(technique_identifier=technique2)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash != AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_seeds_in_seed_group_ignored_in_eval_hash(self):
        """Different seeds in seed_group (traceability) should not affect eval hash."""
        attack_id = _make_attack()
        non_general_1 = SeedPrompt(value="obj1", value_sha256="xxx", is_general_technique=False)
        non_general_2 = SeedPrompt(value="obj2", value_sha256="yyy", is_general_technique=False)
        c1 = build_atomic_attack_identifier(
            attack_identifier=attack_id,
            seed_group=_FakeSeedGroup(seeds=[non_general_1]),
        )
        c2 = build_atomic_attack_identifier(
            attack_identifier=attack_id,
            seed_group=_FakeSeedGroup(seeds=[non_general_2]),
        )
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    def test_general_technique_seeds_in_seed_group_ignored_in_eval_hash(self):
        """Even general technique seeds in seed_group are excluded from eval hash."""
        attack_id = _make_attack()
        general_seed = SeedPrompt(value="technique", value_sha256="abc", is_general_technique=True)
        c_with = build_atomic_attack_identifier(
            attack_identifier=attack_id,
            seed_group=_FakeSeedGroup(seeds=[general_seed]),
        )
        c_without = build_atomic_attack_identifier(
            attack_identifier=attack_id,
        )
        assert (
            AtomicAttackEvaluationIdentifier(c_with).eval_hash == AtomicAttackEvaluationIdentifier(c_without).eval_hash
        )

    def test_identifier_hash_differs_with_different_seeds(self):
        """The full identifier hash SHOULD differ when seeds differ (even though eval hash doesn't)."""
        attack_id = _make_attack()
        non_general_1 = SeedPrompt(value="obj1", value_sha256="xxx", is_general_technique=False)
        non_general_2 = SeedPrompt(value="obj2", value_sha256="yyy", is_general_technique=False)
        c1 = build_atomic_attack_identifier(
            attack_identifier=attack_id,
            seed_group=_FakeSeedGroup(seeds=[non_general_1]),
        )
        c2 = build_atomic_attack_identifier(
            attack_identifier=attack_id,
            seed_group=_FakeSeedGroup(seeds=[non_general_2]),
        )
        # Full identifier hash should differ (all seeds contribute to hash)
        assert c1.hash != c2.hash
        # But eval hash should be the same (seeds excluded)
        assert AtomicAttackEvaluationIdentifier(c1).eval_hash == AtomicAttackEvaluationIdentifier(c2).eval_hash

    # -- Full composite scenario -------------------------------------------

    def test_full_composite_eval_hash(self):
        """End-to-end: builds a realistic composite and verifies eval hash consistency."""
        target = _make_target(params={"model_name": "gpt-4o", "temperature": 0.7, "endpoint": "https://a.com"})
        chat = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module=_TARGET_MODULE,
            params={"model_name": "gpt-4o", "temperature": 0.5, "top_p": 0.9, "endpoint": "https://b.com"},
        )
        scorer = ComponentIdentifier(
            class_name="TrueFalseScorer", class_module="pyrit.score", params={"threshold": 0.8}
        )
        converter = ComponentIdentifier(class_name="Base64Converter", class_module="pyrit.prompt_converter")
        seed = SeedPrompt(value="technique", value_sha256="abc", is_general_technique=True)

        attack_id = _make_attack(
            children={
                "objective_target": target,
                "adversarial_chat": chat,
                "objective_scorer": scorer,
                "request_converters": [converter],
            }
        )
        composite = build_atomic_attack_identifier(
            attack_identifier=attack_id,
            seed_group=_FakeSeedGroup(seeds=[seed]),
        )

        identity = AtomicAttackEvaluationIdentifier(composite)

        # Changing only endpoint on target should NOT change hash
        target2 = _make_target(params={"model_name": "gpt-4o", "temperature": 0.7, "endpoint": "https://other.com"})
        attack_id2 = _make_attack(
            children={
                "objective_target": target2,
                "adversarial_chat": chat,
                "objective_scorer": scorer,
                "request_converters": [converter],
            }
        )
        composite2 = build_atomic_attack_identifier(
            attack_identifier=attack_id2,
            seed_group=_FakeSeedGroup(seeds=[seed]),
        )
        assert identity.eval_hash == AtomicAttackEvaluationIdentifier(composite2).eval_hash

        # Changing scorer should NOT change hash (scorer is ignored)
        scorer2 = ComponentIdentifier(
            class_name="FloatScaleScorer", class_module="pyrit.score", params={"threshold": 0.1}
        )
        attack_id3 = _make_attack(
            children={
                "objective_target": target,
                "adversarial_chat": chat,
                "objective_scorer": scorer2,
                "request_converters": [converter],
            }
        )
        composite3 = build_atomic_attack_identifier(
            attack_identifier=attack_id3,
            seed_group=_FakeSeedGroup(seeds=[seed]),
        )
        assert identity.eval_hash == AtomicAttackEvaluationIdentifier(composite3).eval_hash
