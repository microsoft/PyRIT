# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import typing
from typing import Any
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.scenario.core._attack_constructor_compatibility import ScorerOverridePolicy, _ConstructorCompatibilityHelper

if typing.TYPE_CHECKING:
    # The regression test binds this name only after constructing the helper.
    DeferredConfig = AttackScoringConfig


class _StubAttack:
    def __init__(
        self,
        *,
        objective_target: Any,
        attack_scoring_config: AttackScoringConfig | None = None,
    ) -> None:
        pass


class TestScorerPolicy:
    """Tests for scorer override policy logic."""

    def test_should_apply_returns_true_when_type_compatible(self):
        """Config passes through when the attack accepts base AttackScoringConfig."""
        helper = _ConstructorCompatibilityHelper(
            attack_class=_StubAttack, scorer_override_policy=ScorerOverridePolicy.WARN
        )
        config = MagicMock(spec=AttackScoringConfig)
        result = helper.should_apply_scoring_config(attack_scoring_config=config)
        assert result is True

    def test_should_apply_returns_false_when_param_not_accepted(self):
        """If the attack class doesn't accept attack_scoring_config, return False."""

        class _NoScoringAttack:
            def __init__(self, *, objective_target):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_NoScoringAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        config = MagicMock(spec=AttackScoringConfig)
        result = helper.should_apply_scoring_config(attack_scoring_config=config)
        assert result is False

    def test_should_apply_returns_false_when_type_incompatible_warn(self, caplog):
        """When annotation is narrowed and config doesn't match, WARN returns False and logs."""

        class _NarrowedScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedAttack:
            def __init__(self, *, objective_target, attack_scoring_config: _NarrowedScoringConfig | None = None):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_NarrowedAttack, scorer_override_policy=ScorerOverridePolicy.WARN
        )
        config = MagicMock(spec=AttackScoringConfig)
        result = helper.should_apply_scoring_config(attack_scoring_config=config)
        assert result is False
        assert "incompatible" in caplog.text

    def test_should_apply_raises_when_type_incompatible_raise_policy(self):
        """When annotation is narrowed and policy is RAISE, ValueError is raised."""

        class _NarrowedScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedAttack:
            def __init__(self, *, objective_target, attack_scoring_config: _NarrowedScoringConfig | None = None):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_NarrowedAttack, scorer_override_policy=ScorerOverridePolicy.RAISE
        )
        config = MagicMock(spec=AttackScoringConfig)
        with pytest.raises(ValueError, match="incompatible"):
            helper.should_apply_scoring_config(attack_scoring_config=config)

    def test_should_apply_accepts_subclass_of_narrowed_type(self):
        """A subclass of the narrowed annotation type should pass through."""

        class _NarrowedScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedAttack:
            def __init__(self, *, objective_target, attack_scoring_config: _NarrowedScoringConfig | None = None):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_NarrowedAttack, scorer_override_policy=ScorerOverridePolicy.RAISE
        )
        config = MagicMock(spec=_NarrowedScoringConfig)
        result = helper.should_apply_scoring_config(attack_scoring_config=config)
        assert result is True

    def test_apply_scorer_policy_skip_is_silent(self, caplog):
        helper = _ConstructorCompatibilityHelper(
            attack_class=_StubAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        helper._apply_scorer_policy("some incompatibility message")
        assert "some incompatibility message" not in caplog.text

    def test_apply_scorer_policy_warn_logs(self, caplog):
        helper = _ConstructorCompatibilityHelper(
            attack_class=_StubAttack, scorer_override_policy=ScorerOverridePolicy.WARN
        )
        helper._apply_scorer_policy("scorer mismatch detail")
        assert "scorer mismatch detail" in caplog.text

    def test_apply_scorer_policy_raise_raises(self):
        helper = _ConstructorCompatibilityHelper(
            attack_class=_StubAttack, scorer_override_policy=ScorerOverridePolicy.RAISE
        )
        with pytest.raises(ValueError, match="error detail"):
            helper._apply_scorer_policy("error detail")


class TestUnwrapOptional:
    """Tests for _ConstructorCompatibilityHelper._unwrap_optional static method."""

    def test_unwrap_union_with_none(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional(AttackScoringConfig | None)
        assert result is AttackScoringConfig

    def test_unwrap_plain_type(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional(AttackScoringConfig)
        assert result is AttackScoringConfig

    def test_unwrap_multi_union_returns_none(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional(int | str | None)
        assert result is None

    def test_unwrap_none_type_alone(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional(type(None))
        assert result is type(None)

    def test_unwrap_non_type_annotation_returns_none(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional("SomeForwardRef")
        assert result is None

    def test_unwrap_typing_optional_with_none(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional(typing.Optional[AttackScoringConfig])  # noqa: UP045
        assert result is AttackScoringConfig

    def test_unwrap_typing_union_multi_returns_none(self):
        result = _ConstructorCompatibilityHelper._unwrap_optional(typing.Union[int, str, None])  # noqa: UP007
        assert result is None


class TestAcceptedParams:
    """Tests for _ConstructorCompatibilityHelper.accepted_params."""

    def test_accepted_params_discovers_positional_and_keyword_args(self):
        class _ComplexAttack:
            def __init__(self, objective_target, *, attack_scoring_config=None, custom_kwarg=42):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_ComplexAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.accepted_params == {"objective_target", "attack_scoring_config", "custom_kwarg"}

    def test_accepted_params_ignores_self(self):
        class _SelfOnlyAttack:
            def __init__(self):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_SelfOnlyAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.accepted_params == set()


class TestGetScoringConfigType:
    """Tests for _ConstructorCompatibilityHelper.scoring_config_type."""

    def test_returns_none_when_no_scoring_config_param(self):
        class _NoScoringParamAttack:
            def __init__(self, *, objective_target: Any):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_NoScoringParamAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.scoring_config_type is None

    def test_returns_none_when_annotation_is_base_attack_scoring_config(self):
        class _BaseScoringAttack:
            def __init__(self, *, attack_scoring_config: AttackScoringConfig):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_BaseScoringAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.scoring_config_type is None

    def test_returns_none_when_annotation_is_optional_base_attack_scoring_config(self):
        class _OptionalBaseScoringAttack:
            def __init__(self, *, attack_scoring_config: AttackScoringConfig | None = None):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_OptionalBaseScoringAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.scoring_config_type is None

    def test_returns_subclass_when_annotation_is_narrowed_attack_scoring_config(self):
        class _CustomScoringConfig(AttackScoringConfig):
            pass

        class _NarrowedScoringAttack:
            def __init__(self, *, attack_scoring_config: _CustomScoringConfig):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_NarrowedScoringAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.scoring_config_type is _CustomScoringConfig

    def test_returns_none_when_annotation_is_not_attack_scoring_config_subclass(self):
        class _WrongAnnotationAttack:
            def __init__(self, *, objective_target, attack_scoring_config: int | None = None):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_WrongAnnotationAttack, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.scoring_config_type is None

    def test_returns_none_when_type_hints_fail_to_resolve(self, monkeypatch):
        class _StubWithFailingHints:
            def __init__(self, *, attack_scoring_config: AttackScoringConfig):
                pass

        def mock_get_type_hints(*args, **kwargs):
            raise NameError("Unresolvable type name")

        monkeypatch.setattr(typing, "get_type_hints", mock_get_type_hints)

        helper = _ConstructorCompatibilityHelper(
            attack_class=_StubWithFailingHints, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        assert helper.scoring_config_type is None

    def test_resolves_deferred_forward_ref_after_init(self):
        class _AttackWithDeferredRef:
            def __init__(self, *, attack_scoring_config: "DeferredConfig | None" = None):
                pass

        helper = _ConstructorCompatibilityHelper(
            attack_class=_AttackWithDeferredRef, scorer_override_policy=ScorerOverridePolicy.SKIP
        )
        # Initially unresolvable
        assert helper.scoring_config_type is None

        class _DeferredConfig(AttackScoringConfig):
            pass

        import sys

        mod_dict = sys.modules[_AttackWithDeferredRef.__module__].__dict__
        mod_dict["DeferredConfig"] = _DeferredConfig
        try:
            assert helper.scoring_config_type is _DeferredConfig
        finally:
            mod_dict.pop("DeferredConfig", None)
