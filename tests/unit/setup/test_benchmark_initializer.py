# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for BenchmarkInitializer."""

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.setup.initializers import BenchmarkInitializer
from pyrit.setup.initializers.benchmark import DEFAULT_ADVERSARIAL_TAG_QUERY
from pyrit.setup.initializers.components.targets import TargetInitializerTags


@pytest.fixture(autouse=True)
def reset_registries():
    """Reset technique and target registries between tests."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()


def _register_adversarial_target(*, name: str) -> PromptTarget:
    """Register a mock adversarial-tagged target and return the instance."""
    target = MagicMock(spec=PromptTarget)
    target.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.register_instance(target, name=name, tags=[TargetInitializerTags.ADVERSARIAL.value])
    return target


class TestBenchmarkInitializerBasic:
    """Class metadata tests."""

    def test_can_be_created(self):
        init = BenchmarkInitializer()
        assert init is not None

    def test_required_env_vars_is_empty(self):
        """Initializer takes no required env vars; discovery happens via TargetRegistry."""
        init = BenchmarkInitializer()
        assert init.required_env_vars == []

    def test_supported_parameters_declares_target_names(self):
        init = BenchmarkInitializer()
        names = [p.name for p in init.supported_parameters]
        assert "target_names" in names

    def test_default_adversarial_tag_query_matches_adversarial_only(self):
        """The default discovery query is exactly ``TagQuery.all("adversarial")``."""
        assert DEFAULT_ADVERSARIAL_TAG_QUERY.matches({"adversarial"})
        assert not DEFAULT_ADVERSARIAL_TAG_QUERY.matches({"default"})
        assert not DEFAULT_ADVERSARIAL_TAG_QUERY.matches(set())


class TestBenchmarkInitializerFanOut:
    """Tests for the fan-out registration behavior."""

    async def test_fans_out_one_spec_per_target_per_adversarial_technique(self):
        """N targets * M adversarial-capable techniques = N*M fanned specs in the attack registry."""
        _register_adversarial_target(name="adv_a")
        _register_adversarial_target(name="adv_b")

        init = BenchmarkInitializer()
        await init.initialize_async()

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        fanned = attack_registry.get_by_tag(tag="benchmark_fanout")
        assert len(fanned) > 0
        assert len(fanned) % 2 == 0, "Expected an even count: every adversarial technique fanned across both targets"

        fanned_names = {entry.name for entry in fanned}
        for name in fanned_names:
            assert "__" in name, f"Fanned spec name '{name}' missing '__' separator"

    async def test_fanned_spec_names_use_source_double_underscore_target(self):
        """Spec naming contract: ``f'{source_spec.name}__{target_name}'``."""
        _register_adversarial_target(name="adv_single")

        init = BenchmarkInitializer()
        await init.initialize_async()

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        fanned = attack_registry.get_by_tag(tag="model:adv_single")
        assert len(fanned) > 0
        for entry in fanned:
            assert entry.name.endswith("__adv_single")
            source_name = entry.name.split("__", 1)[0]
            assert source_name and "__" not in source_name

    async def test_fanned_specs_carry_benchmark_and_model_tags(self):
        """Each fanned spec is tagged ``benchmark_fanout`` plus ``f'model:{name}'``."""
        _register_adversarial_target(name="adv_a")
        _register_adversarial_target(name="adv_b")

        init = BenchmarkInitializer()
        await init.initialize_async()

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        for entry in attack_registry.get_by_tag(tag="benchmark_fanout"):
            assert "benchmark_fanout" in entry.tags
            model_tags = [tag for tag in entry.tags if tag.startswith("model:")]
            assert len(model_tags) == 1, f"Expected exactly one model:* tag on {entry.name}, got {model_tags}"
            assert model_tags[0] in ("model:adv_a", "model:adv_b")

    async def test_registration_is_idempotent_across_re_init(self):
        """Re-running initialize_async produces the same registry state (per-name idempotent)."""
        _register_adversarial_target(name="adv_a")

        init = BenchmarkInitializer()
        await init.initialize_async()
        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        first_count = len(attack_registry.get_by_tag(tag="benchmark_fanout"))

        await init.initialize_async()
        second_count = len(attack_registry.get_by_tag(tag="benchmark_fanout"))

        assert first_count == second_count


class TestBenchmarkInitializerTargetNamesNarrowing:
    """Tests for the optional ``target_names`` parameter."""

    async def test_target_names_narrows_to_subset(self):
        """When ``target_names`` is set, only those entries are fanned."""
        _register_adversarial_target(name="adv_a")
        _register_adversarial_target(name="adv_b")
        _register_adversarial_target(name="adv_c")

        init = BenchmarkInitializer()
        init.params = {"target_names": ["adv_a", "adv_c"]}
        await init.initialize_async()

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        model_b_specs = attack_registry.get_by_tag(tag="model:adv_b")
        assert model_b_specs == []

        model_a_specs = attack_registry.get_by_tag(tag="model:adv_a")
        model_c_specs = attack_registry.get_by_tag(tag="model:adv_c")
        assert len(model_a_specs) > 0
        assert len(model_c_specs) > 0

    async def test_target_names_unknown_raises_with_discovered_list(self):
        """Unknown ``target_names`` raise ``ValueError`` naming both the unknowns and the discovered set."""
        _register_adversarial_target(name="adv_a")

        init = BenchmarkInitializer()
        init.params = {"target_names": ["nonexistent"]}

        with pytest.raises(ValueError, match=r"nonexistent.*adv_a"):
            await init.initialize_async()

    async def test_empty_target_names_param_falls_back_to_default_query(self):
        """An empty ``target_names`` list is treated as "no narrowing" (same as omitting it)."""
        _register_adversarial_target(name="adv_a")

        init = BenchmarkInitializer()
        init.params = {"target_names": []}
        await init.initialize_async()

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        assert len(attack_registry.get_by_tag(tag="model:adv_a")) > 0


class TestBenchmarkInitializerErrorMessages:
    """Tests for the actionable error message on empty discovery."""

    async def test_no_adversarial_targets_raises_with_actionable_message(self):
        """``ValueError`` must name ``ADVERSARIAL_CHAT_*`` env vars and the ``TargetInitializer`` dependency."""
        init = BenchmarkInitializer()
        with pytest.raises(ValueError) as exc_info:
            await init.initialize_async()

        msg = str(exc_info.value)
        assert "ADVERSARIAL_CHAT_" in msg
        assert "TargetInitializer" in msg
