# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechniqueFactory class."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.converter import Base64Converter, ROT13Converter
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import AttackTechniqueSeedGroup, ComponentIdentifier, Identifiable, SeedPrompt
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory, ScorerOverridePolicy


def _make_seed_technique() -> AttackTechniqueSeedGroup:
    return AttackTechniqueSeedGroup(
        seeds=[
            SeedPrompt(value="technique1", data_type="text", is_general_technique=True),
        ]
    )


class _StubAttack:
    """
    Minimal stub that mimics an AttackStrategy constructor signature.

    We use a plain class rather than a real AttackStrategy subclass to keep
    the unit tests fast and free of heavyweight base-class initialization.
    ``inspect.signature`` sees the same keyword-only parameters that the
    factory's ``_validate_kwargs`` expects.
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        attack_scoring_config: AttackScoringConfig | None = None,
        attack_converter_config: AttackConverterConfig | None = None,
        max_turns: int = 5,
    ) -> None:
        self.objective_target = objective_target
        self.attack_scoring_config = attack_scoring_config
        self.attack_converter_config = attack_converter_config
        self.max_turns = max_turns

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(
            class_name="_StubAttack",
            class_module="tests.unit.scenario.test_attack_technique_factory",
        )


class TestFactoryInit:
    """Tests for AttackTechniqueFactory construction and validation."""

    def test_init_defaults(self):
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)

        assert factory.attack_class is _StubAttack
        assert factory.seed_technique is None

    def test_init_stores_seed_technique(self):
        seeds = _make_seed_technique()
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack, seed_technique=seeds)

        assert factory.seed_technique is seeds

    def test_validate_kwargs_accepts_valid_params(self):
        """All valid kwarg names should pass without error."""
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10, "attack_scoring_config": None},
        )
        assert factory.attack_class is _StubAttack

    def test_validate_kwargs_rejects_unknown_params(self):
        """Typo or nonexistent kwarg should raise TypeError immediately."""
        with pytest.raises(TypeError, match="Invalid kwargs.*max_turn"):
            AttackTechniqueFactory(
                name="test",
                attack_class=_StubAttack,
                attack_kwargs={"max_turn": 10},  # typo: should be max_turns
            )

    def test_validate_kwargs_rejects_objective_target(self):
        """objective_target must not be in attack_kwargs."""
        target = MagicMock(spec=PromptTarget)
        with pytest.raises(ValueError, match="objective_target must not be in attack_kwargs"):
            AttackTechniqueFactory(
                name="test",
                attack_class=_StubAttack,
                attack_kwargs={"objective_target": target},
            )

    def test_validate_kwargs_rejects_multiple_invalid(self):
        """Multiple bad kwargs should all be reported."""
        with pytest.raises(TypeError, match="Invalid kwargs"):
            AttackTechniqueFactory(
                name="test",
                attack_class=_StubAttack,
                attack_kwargs={"bad_param_1": 1, "bad_param_2": 2},
            )

    def test_validate_kwargs_rejects_var_keyword_constructor(self):
        """Constructors with **kwargs prevent parameter validation and should be rejected."""

        class _KwargsAttack:
            def __init__(self, **kwargs):
                pass

        with pytest.raises(TypeError, match="accepts \\*\\*kwargs.*parameter validation"):
            AttackTechniqueFactory(name="test", attack_class=_KwargsAttack)

    def test_validate_kwargs_rejects_var_keyword_even_with_named_params(self):
        """Mixed named params + **kwargs should still be rejected."""

        class _MixedAttack:
            def __init__(self, *, objective_target, max_turns: int = 5, **extra):
                pass

        with pytest.raises(TypeError, match="accepts \\*\\*kwargs"):
            AttackTechniqueFactory(
                name="test",
                attack_class=_MixedAttack,
                attack_kwargs={"max_turns": 10},
            )

    def test_validate_kwargs_works_with_real_attack_class(self):
        """
        Validate that inspect.signature correctly sees through @apply_defaults
        and functools.wraps on a real AttackStrategy subclass.
        """
        # PromptSendingAttack uses @apply_defaults — factory should see its real params
        factory = AttackTechniqueFactory(name="test", attack_class=PromptSendingAttack)
        assert factory.attack_class is PromptSendingAttack

    def test_validate_kwargs_rejects_invalid_param_on_real_attack_class(self):
        """A typo kwarg should be caught even through @apply_defaults."""
        with pytest.raises(TypeError, match="Invalid kwargs.*nonexistent_param"):
            AttackTechniqueFactory(
                name="test",
                attack_class=PromptSendingAttack,
                attack_kwargs={"nonexistent_param": 42},
            )


class TestFactoryCreate:
    """Tests for AttackTechniqueFactory.create()."""

    def _scoring(self) -> AttackScoringConfig:
        return MagicMock(spec=AttackScoringConfig)

    def test_create_produces_attack_technique(self):
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)
        target = MagicMock(spec=PromptTarget)

        technique = factory.create(objective_target=target, attack_scoring_config=self._scoring())

        assert isinstance(technique, AttackTechnique)
        assert isinstance(technique.attack, _StubAttack)
        assert technique.attack.objective_target is target

    def test_create_passes_frozen_kwargs(self):
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 42},
        )
        target = MagicMock(spec=PromptTarget)

        technique = factory.create(objective_target=target, attack_scoring_config=self._scoring())

        assert technique.attack.max_turns == 42

    def test_create_passes_scoring_config(self):
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)
        target = MagicMock(spec=PromptTarget)
        scoring = MagicMock(spec=AttackScoringConfig)

        technique = factory.create(objective_target=target, attack_scoring_config=scoring)

        assert technique.attack.attack_scoring_config is scoring

    def test_create_overrides_frozen_scoring_config(self):
        """Create-time scoring config should override the frozen one."""
        frozen_scoring = MagicMock(spec=AttackScoringConfig)
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"attack_scoring_config": frozen_scoring},
        )
        target = MagicMock(spec=PromptTarget)
        override_scoring = MagicMock(spec=AttackScoringConfig)

        technique = factory.create(objective_target=target, attack_scoring_config=override_scoring)

        assert technique.attack.attack_scoring_config is override_scoring
        assert technique.attack.attack_scoring_config is not frozen_scoring

    def test_create_preserves_seed_technique(self):
        seeds = _make_seed_technique()
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack, seed_technique=seeds)
        target = MagicMock(spec=PromptTarget)

        technique = factory.create(objective_target=target, attack_scoring_config=self._scoring())

        assert technique.seed_technique is seeds

    def test_create_produces_independent_instances(self):
        """Two create() calls should produce fully independent attack instances."""
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10},
        )
        target1 = MagicMock(spec=PromptTarget)
        target2 = MagicMock(spec=PromptTarget)
        scoring = self._scoring()

        technique1 = factory.create(objective_target=target1, attack_scoring_config=scoring)
        technique2 = factory.create(objective_target=target2, attack_scoring_config=scoring)

        assert technique1.attack is not technique2.attack
        assert technique1.attack.objective_target is target1
        assert technique2.attack.objective_target is target2

    def test_create_shares_kwargs_values(self):
        """Factory uses shallow copy — mutable values inside kwargs are shared (by design)."""
        mutable_list = [1, 2, 3]

        class _ListAttack:
            def __init__(self, *, objective_target, attack_scoring_config=None, items: list | None = None):
                self.objective_target = objective_target
                self.items = items

            def get_identifier(self):
                return ComponentIdentifier(class_name="_ListAttack", class_module="test")

        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_ListAttack,
            attack_kwargs={"items": mutable_list},
        )
        target = MagicMock(spec=PromptTarget)

        technique1 = factory.create(objective_target=target, attack_scoring_config=self._scoring())
        assert technique1.attack.items == [1, 2, 3]

        # Mutating the original list is visible to future creates (shallow copy)
        mutable_list.append(999)
        technique2 = factory.create(objective_target=target, attack_scoring_config=self._scoring())
        assert technique2.attack.items == [1, 2, 3, 999]

    def test_create_without_optional_configs_omits_them(self):
        """When optional configs are None, adversarial and converter should not be passed."""
        unset = object()

        class _SentinelAttack:
            def __init__(
                self,
                *,
                objective_target,
                attack_scoring_config,
                attack_adversarial_config=unset,
                attack_converter_config=unset,
            ):
                self.objective_target = objective_target
                self.adversarial_was_passed = attack_adversarial_config is not unset
                self.converter_was_passed = attack_converter_config is not unset

            def get_identifier(self):
                return ComponentIdentifier(class_name="_SentinelAttack", class_module="test")

        factory = AttackTechniqueFactory(name="test", attack_class=_SentinelAttack, uses_adversarial=False)
        target = MagicMock(spec=PromptTarget)
        technique = factory.create(objective_target=target, attack_scoring_config=self._scoring())

        assert not technique.attack.adversarial_was_passed
        assert not technique.attack.converter_was_passed

    def test_create_appends_extra_request_converters_without_baked(self):
        """``extra_request_converters`` become the request converters when none are baked."""
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)
        target = MagicMock(spec=PromptTarget)
        extra = ConverterConfiguration.from_converters(converters=[Base64Converter()])

        technique = factory.create(
            objective_target=target,
            attack_scoring_config=self._scoring(),
            extra_request_converters=extra,
        )

        cfg = technique.attack.attack_converter_config
        assert cfg.request_converters == extra
        assert cfg.response_converters == []

    def test_create_appends_extra_request_converters_on_top_of_baked(self):
        """``extra_request_converters`` are appended after baked request converters; responses are preserved."""
        baked_request = ConverterConfiguration.from_converters(converters=[Base64Converter()])
        baked_response = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
        baked = AttackConverterConfig(request_converters=baked_request, response_converters=baked_response)
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"attack_converter_config": baked},
        )
        target = MagicMock(spec=PromptTarget)
        extra = ConverterConfiguration.from_converters(converters=[Base64Converter()])

        technique = factory.create(
            objective_target=target,
            attack_scoring_config=self._scoring(),
            extra_request_converters=extra,
        )

        cfg = technique.attack.attack_converter_config
        assert cfg.request_converters == baked_request + extra
        assert cfg.response_converters == baked_response

    def test_create_extra_request_converters_skipped_when_unsupported(self):
        """Attacks that don't accept ``attack_converter_config`` silently ignore extras."""

        class _NoConverterAttack:
            def __init__(self, *, objective_target, attack_scoring_config=None):
                self.objective_target = objective_target

            def get_identifier(self):
                return ComponentIdentifier(class_name="_NoConverterAttack", class_module="test")

        factory = AttackTechniqueFactory(name="test", attack_class=_NoConverterAttack, uses_adversarial=False)
        target = MagicMock(spec=PromptTarget)
        extra = ConverterConfiguration.from_converters(converters=[Base64Converter()])

        technique = factory.create(
            objective_target=target,
            attack_scoring_config=self._scoring(),
            extra_request_converters=extra,
        )

        assert isinstance(technique, AttackTechnique)


class TestFactoryIdentifier:
    """Tests for AttackTechniqueFactory._build_identifier()."""

    def test_identifier_includes_attack_class_name(self):
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)

        identifier = factory.get_identifier()

        assert isinstance(identifier, ComponentIdentifier)
        assert identifier.class_name == "AttackTechniqueFactory"
        assert identifier.params["attack_class"] == "_StubAttack"

    def test_identifier_includes_kwargs_with_values(self):
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10, "attack_scoring_config": None},
        )

        identifier = factory.get_identifier()

        assert identifier.params["kwargs"] == {"attack_scoring_config": None, "max_turns": 10}

    def test_identifier_empty_kwargs(self):
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)

        identifier = factory.get_identifier()

        assert identifier.params["kwargs"] == {}

    def test_same_keys_different_values_produce_different_hashes(self):
        """Two factories with max_turns=5 vs max_turns=50 must have different hashes."""
        factory1 = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 5},
        )
        factory2 = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 50},
        )

        assert factory1.get_identifier().hash != factory2.get_identifier().hash

    def test_different_kwargs_keys_produce_different_hashes(self):
        factory1 = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10},
        )
        factory2 = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10, "attack_scoring_config": None},
        )

        assert factory1.get_identifier().hash != factory2.get_identifier().hash

    def test_identifier_serializes_identifiable_values(self):
        """Identifiable objects in kwargs should contribute their hash to the identifier."""
        expected_id = ComponentIdentifier(
            class_name="MockConfig",
            class_module="test",
            params={"key": "value"},
        )
        mock_identifiable = MagicMock(spec=Identifiable)
        mock_identifiable.get_identifier.return_value = expected_id

        class _IdentifiableParamAttack:
            def __init__(self, *, objective_target, config=None):
                pass

            def get_identifier(self):
                return ComponentIdentifier(class_name="_IdentifiableParamAttack", class_module="test")

        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_IdentifiableParamAttack,
            attack_kwargs={"config": mock_identifiable},
        )

        identifier = factory.get_identifier()
        config_value = identifier.params["kwargs"]["config"]
        # Should be the hash string from the identifiable, not the object itself
        assert isinstance(config_value, str)
        assert config_value == expected_id.hash

    def test_identifier_is_cached(self):
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)

        first = factory.get_identifier()
        second = factory.get_identifier()

        assert first is second

    def test_seed_technique_included_in_identifier(self):
        """A factory with seed_technique should have technique_seeds children."""
        seed_technique = _make_seed_technique()
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack, seed_technique=seed_technique)

        identifier = factory.get_identifier()

        assert "technique_seeds" in identifier.children
        assert len(identifier.children["technique_seeds"]) == 1

    def test_no_seed_technique_means_no_children(self):
        """A factory without seed_technique should have no technique_seeds children."""
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)

        identifier = factory.get_identifier()

        assert "technique_seeds" not in identifier.children

    def test_different_seed_techniques_produce_different_hashes(self):
        """Two factories differing only by seed_technique must have different hashes."""
        seed1 = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="technique_a", data_type="text", is_general_technique=True)],
        )
        seed2 = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="technique_b", data_type="text", is_general_technique=True)],
        )
        factory1 = AttackTechniqueFactory(name="test", attack_class=_StubAttack, seed_technique=seed1)
        factory2 = AttackTechniqueFactory(name="test", attack_class=_StubAttack, seed_technique=seed2)

        assert factory1.get_identifier().hash != factory2.get_identifier().hash


class TestScorerPolicy:
    """Tests for scorer override policy logic (_should_apply_scoring_config, _apply_scorer_policy)."""

    def test_should_apply_returns_true_when_type_compatible(self):
        """Config passes through when the attack accepts base AttackScoringConfig."""
        factory = AttackTechniqueFactory(name="test", attack_class=_StubAttack)
        config = MagicMock(spec=AttackScoringConfig)

        result = factory._should_apply_scoring_config(
            attack_scoring_config=config,
            accepted_params=factory._get_accepted_params(),
        )

        assert result is True

    def test_should_apply_returns_false_when_param_not_accepted(self):
        """If the attack class doesn't accept attack_scoring_config, return False."""

        class _NoScoringAttack:
            def __init__(self, *, objective_target):
                pass

            def get_identifier(self):
                return ComponentIdentifier(class_name="_NoScoringAttack", class_module="test")

        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_NoScoringAttack,
            scorer_override_policy=ScorerOverridePolicy.SKIP,
        )
        config = MagicMock(spec=AttackScoringConfig)

        result = factory._should_apply_scoring_config(
            attack_scoring_config=config,
            accepted_params=factory._get_accepted_params(),
        )

        assert result is False

    def test_should_apply_returns_false_when_type_incompatible_warn(self, caplog):
        """When annotation is narrowed and config doesn't match, WARN returns False and logs."""

        class _NarrowedScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedAttack:
            def __init__(self, *, objective_target, attack_scoring_config: _NarrowedScoringConfig | None = None):
                pass

            def get_identifier(self):
                return ComponentIdentifier(class_name="_NarrowedAttack", class_module="test")

        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_NarrowedAttack,
            scorer_override_policy=ScorerOverridePolicy.WARN,
        )
        config = MagicMock(spec=AttackScoringConfig)

        result = factory._should_apply_scoring_config(
            attack_scoring_config=config,
            accepted_params=factory._get_accepted_params(),
        )

        assert result is False
        assert "incompatible" in caplog.text

    def test_should_apply_raises_when_type_incompatible_raise_policy(self):
        """When annotation is narrowed and policy is RAISE, ValueError is raised."""

        class _NarrowedScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedAttack:
            def __init__(self, *, objective_target, attack_scoring_config: _NarrowedScoringConfig | None = None):
                pass

            def get_identifier(self):
                return ComponentIdentifier(class_name="_NarrowedAttack", class_module="test")

        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_NarrowedAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )
        config = MagicMock(spec=AttackScoringConfig)

        with pytest.raises(ValueError, match="incompatible"):
            factory._should_apply_scoring_config(
                attack_scoring_config=config,
                accepted_params=factory._get_accepted_params(),
            )

    def test_should_apply_accepts_subclass_of_narrowed_type(self):
        """A subclass of the narrowed annotation type should pass through."""

        class _NarrowedScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedAttack:
            def __init__(self, *, objective_target, attack_scoring_config: _NarrowedScoringConfig | None = None):
                pass

            def get_identifier(self):
                return ComponentIdentifier(class_name="_NarrowedAttack", class_module="test")

        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_NarrowedAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )
        config = MagicMock(spec=_NarrowedScoringConfig)

        result = factory._should_apply_scoring_config(
            attack_scoring_config=config,
            accepted_params=factory._get_accepted_params(),
        )

        assert result is True

    def test_apply_scorer_policy_skip_is_silent(self, caplog):
        """SKIP policy should not log or raise."""
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            scorer_override_policy=ScorerOverridePolicy.SKIP,
        )

        factory._apply_scorer_policy("some incompatibility message")

        assert "some incompatibility message" not in caplog.text

    def test_apply_scorer_policy_warn_logs(self, caplog):
        """WARN policy should log a warning."""
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            scorer_override_policy=ScorerOverridePolicy.WARN,
        )

        factory._apply_scorer_policy("scorer mismatch detail")

        assert "scorer mismatch detail" in caplog.text

    def test_apply_scorer_policy_raise_raises(self):
        """RAISE policy should raise ValueError with the message."""
        factory = AttackTechniqueFactory(
            name="test",
            attack_class=_StubAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        with pytest.raises(ValueError, match="error detail"):
            factory._apply_scorer_policy("error detail")


class TestCustomAdversarialPrompt:
    """Tests for the adversarial_system_prompt / adversarial_seed_prompt params."""

    class _AdversarialAttack:
        def __init__(self, *, objective_target, attack_scoring_config=None, attack_adversarial_config=None):
            self.objective_target = objective_target
            self.attack_scoring_config = attack_scoring_config
            self.attack_adversarial_config = attack_adversarial_config

        def get_identifier(self):
            return ComponentIdentifier(class_name="_AdversarialAttack", class_module="test")

    @staticmethod
    def _scoring():
        return MagicMock(spec=AttackScoringConfig)

    def test_custom_prompt_implies_uses_adversarial(self):
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=_StubAttack,
            adversarial_system_prompt="custom {{ objective }}",
        )
        assert factory.uses_adversarial is True

    def test_custom_seed_prompt_implies_uses_adversarial(self):
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=_StubAttack,
            adversarial_seed_prompt=SeedPrompt(value="hi {{ objective }}", data_type="text", parameters=["objective"]),
        )
        assert factory.uses_adversarial is True

    def test_custom_prompt_with_baked_chat_coexist(self):
        """A baked adversarial_chat and custom prompts can be combined freely."""
        target = MagicMock(spec=PromptTarget)
        seed = SeedPrompt(value="hi {{ objective }}", data_type="text", parameters=["objective"])
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_chat=target,
            adversarial_system_prompt="sys {{ objective }}",
            adversarial_seed_prompt=seed,
        )
        technique = factory.create(objective_target=MagicMock(spec=PromptTarget), attack_scoring_config=self._scoring())
        config = technique.attack.attack_adversarial_config
        assert config.target is target
        assert config.system_prompt == "sys {{ objective }}"
        assert config.first_message is seed

    def test_adversarial_chat_implies_uses_adversarial(self):
        target = MagicMock(spec=PromptTarget)
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=_StubAttack,
            adversarial_chat=target,
        )
        assert factory.uses_adversarial is True
        assert factory.adversarial_chat is target

    def test_adversarial_chat_used_as_default_target(self):
        """When no override is given, the baked adversarial_chat is used (no lazy default)."""
        target = MagicMock(spec=PromptTarget)
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_chat=target,
        )
        with patch(
            "pyrit.scenario.core.attack_technique_factory.get_default_adversarial_target",
        ) as mock_default:
            technique = factory.create(
                objective_target=MagicMock(spec=PromptTarget), attack_scoring_config=self._scoring()
            )
        mock_default.assert_not_called()
        assert technique.attack.attack_adversarial_config.target is target

    def test_create_adversarial_chat_conflicts_with_baked_raises(self):
        """create() must not supply an adversarial_chat when the factory baked one."""
        baked = MagicMock(spec=PromptTarget)
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_chat=baked,
        )
        with pytest.raises(ValueError, match="already baked"):
            factory.create(
                objective_target=MagicMock(spec=PromptTarget),
                attack_scoring_config=self._scoring(),
                adversarial_chat=MagicMock(spec=PromptTarget),
            )

    def test_adversarial_chat_with_uses_adversarial_false_raises(self):
        with pytest.raises(ValueError, match="uses_adversarial=False"):
            AttackTechniqueFactory(
                name="durian",
                attack_class=_StubAttack,
                adversarial_chat=MagicMock(spec=PromptTarget),
                uses_adversarial=False,
            )

    def test_custom_prompt_with_uses_adversarial_false_raises(self):
        with pytest.raises(ValueError, match="uses_adversarial=False"):
            AttackTechniqueFactory(
                name="durian",
                attack_class=_StubAttack,
                adversarial_system_prompt="custom {{ objective }}",
                uses_adversarial=False,
            )

    def test_lazy_resolution_attaches_custom_prompts(self):
        seed = SeedPrompt(value="durian {{ objective }}", data_type="text", parameters=["objective"])
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_system_prompt="durian sys {{ objective }}",
            adversarial_seed_prompt=seed,
        )
        fallback = MagicMock(spec=PromptTarget)
        with patch(
            "pyrit.scenario.core.attack_technique_factory.get_default_adversarial_target",
            return_value=fallback,
        ):
            technique = factory.create(
                objective_target=MagicMock(spec=PromptTarget), attack_scoring_config=self._scoring()
            )

        config = technique.attack.attack_adversarial_config
        assert config.target is fallback
        assert config.system_prompt == "durian sys {{ objective }}"
        assert config.first_message is seed

    def test_create_adversarial_chat_is_combined_with_custom_prompts(self):
        seed = SeedPrompt(value="durian {{ objective }}", data_type="text", parameters=["objective"])
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_system_prompt="durian sys {{ objective }}",
            adversarial_seed_prompt=seed,
        )
        create_target = MagicMock(spec=PromptTarget)

        technique = factory.create(
            objective_target=MagicMock(spec=PromptTarget),
            attack_scoring_config=self._scoring(),
            adversarial_chat=create_target,
        )

        config = technique.attack.attack_adversarial_config
        # The create-time target is used; the technique keeps its custom prompts.
        assert config.target is create_target
        assert config.system_prompt == "durian sys {{ objective }}"
        assert config.first_message is seed

    def test_create_adversarial_chat_used_as_target(self):
        """A create-time adversarial_chat fills the lazy slot (no default resolution)."""
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
        )
        create_target = MagicMock(spec=PromptTarget)
        with patch(
            "pyrit.scenario.core.attack_technique_factory.get_default_adversarial_target",
        ) as mock_default:
            technique = factory.create(
                objective_target=MagicMock(spec=PromptTarget),
                attack_scoring_config=self._scoring(),
                adversarial_chat=create_target,
            )
        mock_default.assert_not_called()
        assert technique.attack.attack_adversarial_config.target is create_target

    def test_identifier_distinguishes_custom_system_prompt(self):
        f1 = AttackTechniqueFactory(
            name="durian", attack_class=self._AdversarialAttack, adversarial_system_prompt="a {{ objective }}"
        )
        f2 = AttackTechniqueFactory(
            name="durian", attack_class=self._AdversarialAttack, adversarial_system_prompt="b {{ objective }}"
        )
        assert f1.get_identifier().hash != f2.get_identifier().hash

    def test_identifier_distinguishes_custom_seed_prompt_object(self):
        """A SeedPrompt adversarial_seed_prompt is serialized by value, so different prompts differ."""
        f1 = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_seed_prompt=SeedPrompt(value="a {{ objective }}", data_type="text", parameters=["objective"]),
        )
        f2 = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_seed_prompt=SeedPrompt(value="b {{ objective }}", data_type="text", parameters=["objective"]),
        )
        assert f1.get_identifier().hash != f2.get_identifier().hash

    def test_create_custom_prompt_conflicts_with_baked_raises(self):
        """create() must not supply adversarial prompts when the factory baked a custom one."""
        factory = AttackTechniqueFactory(
            name="durian",
            attack_class=self._AdversarialAttack,
            adversarial_system_prompt="baked {{ objective }}",
        )
        with pytest.raises(ValueError, match="custom adversarial prompt is already baked"):
            factory.create(
                objective_target=MagicMock(spec=PromptTarget),
                attack_scoring_config=self._scoring(),
                adversarial_system_prompt="create-time {{ objective }}",
            )


class TestUnwrapOptional:
    """Tests for AttackTechniqueFactory._unwrap_optional static method."""

    def test_unwrap_union_with_none(self):
        """X | None should unwrap to X."""
        result = AttackTechniqueFactory._unwrap_optional(AttackScoringConfig | None)
        assert result is AttackScoringConfig

    def test_unwrap_plain_type(self):
        """A bare type (no Optional wrapping) returns itself."""
        result = AttackTechniqueFactory._unwrap_optional(AttackScoringConfig)
        assert result is AttackScoringConfig

    def test_unwrap_multi_union_returns_none(self):
        """Union of more than one non-None type returns None (ambiguous)."""
        result = AttackTechniqueFactory._unwrap_optional(int | str | None)
        assert result is None

    def test_unwrap_none_type_alone(self):
        """NoneType alone is a plain type — returns itself."""
        result = AttackTechniqueFactory._unwrap_optional(type(None))
        assert result is type(None)

    def test_unwrap_non_type_annotation_returns_none(self):
        """A non-type annotation (e.g., string forward ref) returns None."""
        result = AttackTechniqueFactory._unwrap_optional("SomeForwardRef")
        assert result is None
