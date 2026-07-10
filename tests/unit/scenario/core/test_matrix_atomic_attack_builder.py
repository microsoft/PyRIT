# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``MatrixAtomicAttackBuilder`` and its module-level helpers.

The builder centralizes the technique × dataset (× optional adversarial target)
cross-product for scenarios whose attacks form such a grid. These tests pin the contract:

* cross-product cardinality across techniques, datasets, and the optional
  adversarial-target axis,
* default and custom ``atomic_attack_name`` / ``display_group`` derivation,
* ``factory.create`` adversarial-chat forwarding (and the ``AtomicAttack``
  ``adversarial_chat`` it stamps),
* seed-technique compatibility filtering (skip vs. subset), and
* optional baseline emission from the flattened seed groups.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import SeedAttackGroup, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    MatrixCombo,
    build_baseline_atomic_attack,
    build_matrix_atomic_attacks,
    resolve_technique_factories,
)
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import TrueFalseScorer


def _mock_factory(*, name: str, seed_technique=None, adversarial_chat=None) -> MagicMock:
    """Build a controllable ``AttackTechniqueFactory`` stand-in.

    ``create`` returns a fresh sentinel ``AttackTechnique`` so callers can assert
    on the (objective_target, scoring_config, adversarial_chat) kwargs it received.
    """
    factory = MagicMock(spec=AttackTechniqueFactory)
    factory.name = name
    factory.seed_technique = seed_technique
    factory.adversarial_chat = adversarial_chat
    factory.create.return_value = MagicMock(name=f"{name}_technique")
    return factory


def _seed_group(*, objective: str) -> SeedAttackGroup:
    return SeedAttackGroup(seeds=[SeedObjective(value=objective)])


def _builder() -> MatrixAtomicAttackBuilder:
    return MatrixAtomicAttackBuilder(
        objective_target=MagicMock(spec=PromptTarget),
        objective_scorer=MagicMock(spec=TrueFalseScorer),
        memory_labels={"op": "unit"},
    )


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixComboNaming:
    """Default ``atomic_attack_name`` / ``display_group`` helpers via ``MatrixCombo``."""

    def test_default_name_without_target(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
        )
        assert [a.atomic_attack_name for a in result] == ["tech_ds"]

    def test_default_name_with_target_axis(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            adversarial_targets=[("advA", MagicMock(spec=PromptTarget))],
        )
        assert [a.atomic_attack_name for a in result] == ["tech__advA_ds"]

    def test_default_display_group_is_technique(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
        )
        assert result[0].display_group == "tech"


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixBuildCrossProduct:
    """Cardinality and ordering of the produced cross-product."""

    def test_two_techniques_one_dataset_no_targets(self):
        builder = _builder()
        result = builder.build(
            technique_factories={
                "alpha": _mock_factory(name="alpha"),
                "beta": _mock_factory(name="beta"),
            },
            dataset_groups={"ds": [_seed_group(objective="o1")]},
        )
        assert [a.atomic_attack_name for a in result] == ["alpha_ds", "beta_ds"]

    def test_one_technique_two_targets_one_dataset(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            adversarial_targets=[
                ("advA", MagicMock(spec=PromptTarget)),
                ("advB", MagicMock(spec=PromptTarget)),
            ],
        )
        assert [a.atomic_attack_name for a in result] == ["tech__advA_ds", "tech__advB_ds"]

    def test_one_technique_one_target_two_datasets(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={
                "ds1": [_seed_group(objective="o1")],
                "ds2": [_seed_group(objective="o2")],
            },
            adversarial_targets=[("advA", MagicMock(spec=PromptTarget))],
        )
        assert [a.atomic_attack_name for a in result] == ["tech__advA_ds1", "tech__advA_ds2"]


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixAdversarialForwarding:
    """The adversarial-target axis must drive both ``factory.create`` and ``AtomicAttack``."""

    def test_create_called_with_adversarial_chat_per_target(self):
        builder = _builder()
        factory = _mock_factory(name="tech")
        target_a = MagicMock(spec=PromptTarget)
        target_b = MagicMock(spec=PromptTarget)
        builder.build(
            technique_factories={"tech": factory},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            adversarial_targets=[("advA", target_a), ("advB", target_b)],
        )
        assert factory.create.call_count == 2
        injected = {call.kwargs["adversarial_chat"] for call in factory.create.call_args_list}
        assert injected == {target_a, target_b}

    def test_atomic_attack_adversarial_chat_is_resolved_target(self):
        builder = _builder()
        target_a = MagicMock(spec=PromptTarget)
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            adversarial_targets=[("advA", target_a)],
        )
        assert result[0]._adversarial_chat is target_a

    def test_no_target_axis_uses_factory_adversarial_chat(self):
        builder = _builder()
        baked = MagicMock(spec=PromptTarget)
        factory = _mock_factory(name="tech", adversarial_chat=baked)
        result = builder.build(
            technique_factories={"tech": factory},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
        )
        # No adversarial_chat is forwarded into create() when the axis is collapsed.
        assert "adversarial_chat" not in factory.create.call_args.kwargs
        assert result[0]._adversarial_chat is baked


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixCustomCallbacks:
    """Custom ``name_fn`` / ``display_group_fn`` override the defaults."""

    def test_custom_name_and_display_group(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            adversarial_targets=[("advA", MagicMock(spec=PromptTarget))],
            name_fn=lambda combo: f"{combo.target_name}:{combo.technique_name}",
            display_group_fn=lambda combo: combo.target_name or "",
        )
        assert result[0].atomic_attack_name == "advA:tech"
        assert result[0].display_group == "advA"

    def test_callbacks_receive_full_combo(self):
        builder = _builder()
        seen: list[MatrixCombo] = []
        builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            adversarial_targets=[("advA", MagicMock(spec=PromptTarget))],
            name_fn=lambda combo: seen.append(combo) or "n",
        )
        assert seen == [MatrixCombo(technique_name="tech", dataset_name="ds", target_name="advA")]


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixSeedTechniqueFiltering:
    """``seed_technique`` gates which seed groups (and pairs) survive."""

    def test_incompatible_pair_is_skipped(self):
        builder = _builder()
        factory = _mock_factory(name="tech", seed_technique=MagicMock())
        with patch.object(SeedAttackGroup, "filter_compatible", return_value=[]):
            result = builder.build(
                technique_factories={"tech": factory},
                dataset_groups={"ds": [_seed_group(objective="o1")]},
            )
        assert result == []
        factory.create.assert_not_called()

    def test_partial_filter_keeps_subset(self):
        builder = _builder()
        factory = _mock_factory(name="tech", seed_technique=MagicMock())
        kept = _seed_group(objective="keep")
        dropped = _seed_group(objective="drop")
        with patch.object(SeedAttackGroup, "filter_compatible", return_value=[kept]):
            result = builder.build(
                technique_factories={"tech": factory},
                dataset_groups={"ds": [kept, dropped]},
            )
        assert len(result) == 1
        assert result[0]._seed_groups == [kept]

    def test_no_seed_technique_keeps_all_groups(self):
        builder = _builder()
        groups = [_seed_group(objective="a"), _seed_group(objective="b")]
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": groups},
        )
        assert result[0]._seed_groups == groups


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixBaseline:
    """Baseline emission prepends a single ``baseline`` attack over flattened seeds."""

    def test_baseline_prepended_when_requested(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            include_baseline=True,
        )
        assert result[0].atomic_attack_name == "baseline"
        assert [a.atomic_attack_name for a in result] == ["baseline", "tech_ds"]

    def test_baseline_omitted_by_default(self):
        builder = _builder()
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
        )
        assert all(a.atomic_attack_name != "baseline" for a in result)

    def test_baseline_flattens_all_datasets(self):
        builder = _builder()
        g1 = _seed_group(objective="o1")
        g2 = _seed_group(objective="o2")
        result = builder.build(
            technique_factories={"tech": _mock_factory(name="tech")},
            dataset_groups={"ds1": [g1], "ds2": [g2]},
            include_baseline=True,
        )
        assert result[0]._seed_groups == [g1, g2]


@pytest.mark.usefixtures("patch_central_database")
class TestBuildBaselineHelper:
    """The module-level ``build_baseline_atomic_attack`` helper."""

    def test_baseline_name_and_seed_groups(self):
        groups = [_seed_group(objective="o1")]
        baseline = build_baseline_atomic_attack(
            objective_target=MagicMock(spec=PromptTarget),
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            seed_groups=groups,
            memory_labels={"op": "unit"},
        )
        assert baseline.atomic_attack_name == "baseline"
        assert baseline._seed_groups == groups


@pytest.mark.usefixtures("patch_central_database")
class TestMatrixTechniqueConverters:
    """Per-technique ``technique_converters`` are appended via ``factory.create``."""

    def test_converters_forwarded_for_keyed_technique(self):
        from pyrit.prompt_converter import PromptConverter

        builder = _builder()
        factory = _mock_factory(name="tech")
        converter = MagicMock(spec=PromptConverter)
        builder.build(
            technique_factories={"tech": factory},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            technique_converters={"tech": [converter]},
        )
        extra = factory.create.call_args.kwargs["extra_request_converters"]
        assert extra is not None
        assert len(extra) == 1

    def test_unkeyed_technique_gets_no_converters(self):
        from pyrit.prompt_converter import PromptConverter

        builder = _builder()
        factory = _mock_factory(name="tech")
        builder.build(
            technique_factories={"tech": factory},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
            technique_converters={"other": [MagicMock(spec=PromptConverter)]},
        )
        assert factory.create.call_args.kwargs["extra_request_converters"] is None

    def test_no_converters_passes_none(self):
        builder = _builder()
        factory = _mock_factory(name="tech")
        builder.build(
            technique_factories={"tech": factory},
            dataset_groups={"ds": [_seed_group(objective="o1")]},
        )
        assert factory.create.call_args.kwargs["extra_request_converters"] is None


def _technique(value: str) -> SimpleNamespace:
    """A minimal technique stand-in exposing the ``.value`` the resolver reads."""
    return SimpleNamespace(value=value)


def _context(*, techniques, seed_groups_by_dataset=None) -> ScenarioContext:
    return ScenarioContext(
        objective_target=MagicMock(spec=PromptTarget),
        scenario_techniques=techniques,
        dataset_config=MagicMock(),
        memory_labels={"op": "unit"},
        seed_groups_by_dataset=seed_groups_by_dataset or {},
    )


def _patch_registry(factories: dict):
    registry = MagicMock()
    registry.get_factories_or_raise.return_value = factories
    return patch(
        "pyrit.registry.components.attack_technique_registry.AttackTechniqueRegistry.get_registry_singleton",
        return_value=registry,
    )


@pytest.mark.usefixtures("patch_central_database")
class TestResolveTechniqueFactories:
    """``resolve_technique_factories`` filters the registry to the selected techniques."""

    def test_keeps_only_selected_in_order(self):
        factories = {
            "alpha": _mock_factory(name="alpha"),
            "beta": _mock_factory(name="beta"),
            "gamma": _mock_factory(name="gamma"),
        }
        context = _context(techniques=[_technique("beta"), _technique("alpha")])
        with _patch_registry(factories):
            resolved = resolve_technique_factories(context=context)
        assert list(resolved.keys()) == ["beta", "alpha"]

    def test_drops_techniques_without_factory(self):
        factories = {"alpha": _mock_factory(name="alpha")}
        context = _context(techniques=[_technique("alpha"), _technique("missing")])
        with _patch_registry(factories):
            resolved = resolve_technique_factories(context=context)
        assert list(resolved.keys()) == ["alpha"]

    def test_extra_factories_merged_and_override_registry(self):
        registry_factories = {"alpha": _mock_factory(name="alpha")}
        local_alpha = _mock_factory(name="alpha")
        local_only = _mock_factory(name="local")
        context = _context(techniques=[_technique("alpha"), _technique("local")])
        with _patch_registry(registry_factories):
            resolved = resolve_technique_factories(
                context=context,
                extra_factories={"alpha": local_alpha, "local": local_only},
            )
        assert list(resolved.keys()) == ["alpha", "local"]
        assert resolved["alpha"] is local_alpha  # extra overrides the registry factory of the same name
        assert resolved["local"] is local_only  # local-only factory is selectable without global registration


@pytest.mark.usefixtures("patch_central_database")
class TestBuildMatrixAtomicAttacks:
    """``build_matrix_atomic_attacks`` wires the context into the builder in one call."""

    def test_builds_cross_product_grouped_by_technique(self):
        context = _context(
            techniques=[_technique("tech")],
            seed_groups_by_dataset={"ds": [_seed_group(objective="o1")]},
        )
        with _patch_registry({"tech": _mock_factory(name="tech")}):
            result = build_matrix_atomic_attacks(context=context, objective_scorer=MagicMock(spec=TrueFalseScorer))
        assert [a.atomic_attack_name for a in result] == ["tech_ds"]
        assert result[0].display_group == "tech"

    def test_custom_display_group_fn(self):
        context = _context(
            techniques=[_technique("tech")],
            seed_groups_by_dataset={"ds": [_seed_group(objective="o1")]},
        )
        with _patch_registry({"tech": _mock_factory(name="tech")}):
            result = build_matrix_atomic_attacks(
                context=context,
                objective_scorer=MagicMock(spec=TrueFalseScorer),
                display_group_fn=lambda combo: combo.dataset_name,
            )
        assert result[0].display_group == "ds"

    def test_no_baseline_emitted(self):
        context = _context(
            techniques=[_technique("tech")],
            seed_groups_by_dataset={"ds": [_seed_group(objective="o1")]},
        )
        with _patch_registry({"tech": _mock_factory(name="tech")}):
            result = build_matrix_atomic_attacks(context=context, objective_scorer=MagicMock(spec=TrueFalseScorer))
        assert all(a.atomic_attack_name != "baseline" for a in result)

    def test_technique_converters_forwarded(self):
        from pyrit.prompt_converter import PromptConverter

        context = _context(
            techniques=[_technique("tech")],
            seed_groups_by_dataset={"ds": [_seed_group(objective="o1")]},
        )
        factory = _mock_factory(name="tech")
        converter = MagicMock(spec=PromptConverter)
        with _patch_registry({"tech": factory}):
            build_matrix_atomic_attacks(
                context=context,
                objective_scorer=MagicMock(spec=TrueFalseScorer),
                technique_converters={"tech": [converter]},
            )
        extra = factory.create.call_args.kwargs["extra_request_converters"]
        assert extra is not None
        assert len(extra) == 1

    def test_extra_factories_used_for_selection(self):
        context = _context(
            techniques=[_technique("local")],
            seed_groups_by_dataset={"ds": [_seed_group(objective="o1")]},
        )
        # The selected technique exists only in extra_factories, not the registry.
        with _patch_registry({"other": _mock_factory(name="other")}):
            result = build_matrix_atomic_attacks(
                context=context,
                objective_scorer=MagicMock(spec=TrueFalseScorer),
                extra_factories={"local": _mock_factory(name="local")},
            )
        assert [a.atomic_attack_name for a in result] == ["local_ds"]
