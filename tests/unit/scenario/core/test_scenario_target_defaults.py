# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scoped adversarial defaults work for framework callers without registry mutation."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import RedTeamingAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.registry import TargetRegistry
from pyrit.scenario.core import (
    AttackTechniqueFactory,
    get_default_adversarial_target,
    get_default_scorer_target,
    override_default_adversarial_target,
    scenario_target_defaults,
)
from pyrit.scenario.scenarios.airt.scam import Scam
from pyrit.score import TrueFalseScorer


@pytest.mark.usefixtures("patch_central_database")
class TestScopedAdversarialDefault:
    def test_nested_scopes_restore_registry_default_and_leave_scorer_unchanged(self) -> None:
        registered, outer, inner, scorer = (MockPromptTarget() for _ in range(4))
        registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
        registry.instances.get.side_effect = {
            "adversarial_chat": registered,
            "objective_scorer_chat": scorer,
        }.get
        with patch.object(TargetRegistry, "get_registry_singleton", return_value=registry):
            assert get_default_adversarial_target() is registered
            with override_default_adversarial_target(outer):
                assert get_default_adversarial_target() is outer
                with override_default_adversarial_target(inner):
                    assert get_default_adversarial_target() is inner
                    assert get_default_scorer_target() is scorer
                assert get_default_adversarial_target() is outer
            assert get_default_adversarial_target() is registered
        registry.instances.register.assert_not_called()

    @pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
    def test_scope_restores_after_errors(self, error: type[BaseException]) -> None:
        outer, inner = MockPromptTarget(), MockPromptTarget()
        with override_default_adversarial_target(outer):
            with pytest.raises(error), override_default_adversarial_target(inner):
                raise error()
            assert get_default_adversarial_target() is outer

    async def test_concurrent_tasks_keep_distinct_defaults_async(self) -> None:
        outer, first, second = (MockPromptTarget() for _ in range(3))
        ready = asyncio.Event()
        arrived = 0

        async def resolve_async(target: PromptTarget) -> PromptTarget:
            nonlocal arrived
            with override_default_adversarial_target(target):
                arrived += 1
                if arrived == 2:
                    ready.set()
                await ready.wait()
                await asyncio.sleep(0)
                assert get_default_adversarial_target() is target
            assert get_default_adversarial_target() is outer
            return target

        with override_default_adversarial_target(outer):
            assert await asyncio.gather(resolve_async(first), resolve_async(second)) == [first, second]
            assert get_default_adversarial_target() is outer

    @pytest.mark.parametrize("invalid", [None, object(), "registered_name"])
    def test_invalid_types_rejected_without_replacing_outer_scope(self, invalid: object) -> None:
        target = MockPromptTarget()
        with override_default_adversarial_target(target):
            with (
                pytest.raises(ValueError, match="must be a PromptTarget"),
                override_default_adversarial_target(invalid),
            ):
                pytest.fail("Invalid target entered the scope")
            assert get_default_adversarial_target() is target

    def test_single_turn_target_rejected(self) -> None:
        target = MockPromptTarget()
        target.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
        with pytest.raises(ValueError, match="must support multi_turn"), override_default_adversarial_target(target):
            pytest.fail("Single-turn target entered the scope")

    def test_openai_fallback_retained_outside_scope(self) -> None:
        registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
        registry.instances.get.return_value = None
        target, fallback = MockPromptTarget(), MockPromptTarget()
        with (
            patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
            patch.object(scenario_target_defaults, "OpenAIChatTarget", return_value=fallback) as create_fallback,
        ):
            with override_default_adversarial_target(target):
                assert get_default_adversarial_target() is target
                create_fallback.assert_not_called()
            assert get_default_adversarial_target() is fallback
            create_fallback.assert_called_once_with(temperature=1.2)

    def test_scenario_constructor_prefers_explicit_target(self) -> None:
        scoped, explicit = MockPromptTarget(), MockPromptTarget()
        scorer = MagicMock(spec=TrueFalseScorer)
        with override_default_adversarial_target(scoped):
            default_scenario = Scam(objective_scorer=scorer)
            explicit_scenario = Scam(objective_scorer=scorer, adversarial_chat=explicit)
        assert default_scenario._adversarial_chat is scoped
        assert explicit_scenario._adversarial_chat is explicit

    @pytest.mark.parametrize("selection", ["scoped", "baked", "create"])
    def test_factory_explicit_targets_take_precedence(self, selection: str) -> None:
        scoped, explicit, objective = (MockPromptTarget() for _ in range(3))
        factory = AttackTechniqueFactory(
            name="scoped_test",
            attack_class=RedTeamingAttack,
            adversarial_chat=explicit if selection == "baked" else None,
        )
        with override_default_adversarial_target(scoped):
            technique = factory.create(
                objective_target=objective,
                attack_scoring_config=AttackScoringConfig(objective_scorer=MagicMock(spec=TrueFalseScorer)),
                adversarial_chat=explicit if selection == "create" else None,
            )
        expected = scoped if selection == "scoped" else explicit
        assert technique.attack._adversarial_chat is expected
