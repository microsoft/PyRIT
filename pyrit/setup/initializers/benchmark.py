# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Benchmark initializer that fans adversarial-capable scenario techniques across
adversarial targets discovered in ``TargetRegistry``.

This is the entry point for bootstrapping an ``AdversarialBenchmark`` trial.
It queries ``TargetRegistry`` for entries tagged ``ADVERSARIAL`` (via
``TagQuery.all("adversarial")``), then for every adversarial-capable
technique in ``SCENARIO_TECHNIQUES`` builds one fanned
``AttackTechniqueSpec`` per discovered target. Each fanned spec binds the
live target onto ``adversarial_chat`` and is registered into
``AttackTechniqueRegistry`` tagged ``["benchmark_fanout", f"model:{name}"]``
so the benchmark scenario can discover them via tag query in a later commit.

The ``target_names`` parameter (optional, settable from ``.pyrit_conf``)
narrows the fan-out to a specific subset of adversarial targets by registry
name. Unknown names raise ``ValueError``.

Discovery returning no adversarial-tagged targets raises ``ValueError`` with
an actionable message pointing at the ``ADVERSARIAL_CHAT_*`` env vars and
the ``TargetInitializer`` dependency.
"""

import dataclasses
import logging

from pyrit.common.parameter import Parameter
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
    AttackTechniqueSpec,
)
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES, _spec_needs_adversarial
from pyrit.setup.initializers.components.targets import TargetInitializerTags
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


#: Default discovery query used when no ``target_names`` override is provided.
#: Resolves every ``TargetRegistry`` entry tagged ``ADVERSARIAL`` (which today
#: includes ``adversarial_chat`` plus the ``ADVERSARIAL_CHAT_{SINGLETURN,
#: MULTITURN,REASONING}`` variants — all set by ``TargetInitializer``).
DEFAULT_ADVERSARIAL_TAG_QUERY: TagQuery = TagQuery.all(TargetInitializerTags.ADVERSARIAL.value)


class BenchmarkInitializer(PyRITInitializer):
    """
    Fan adversarial-capable scenario techniques across discovered adversarial targets.

    For every ``AttackTechniqueSpec`` in ``SCENARIO_TECHNIQUES`` that uses an
    adversarial chat target (multi-turn attacks plus crescendo-style
    simulated conversations), this initializer registers one variant per
    discovered adversarial target with the target bound onto
    ``adversarial_chat``. Variants are named
    ``f"{source_spec.name}__{target_name}"`` (e.g. ``red_teaming__adversarial_chat_singleturn``)
    and carry the additional strategy tags ``"benchmark_fanout"`` and
    ``f"model:{target_name}"`` so the benchmark scenario can query the
    registry by tag in a later commit.

    Parameters (declared via :attr:`supported_parameters`):

    * ``target_names`` (``list[str]``, optional): Narrow fan-out to a
      specific subset of adversarial targets by registry name. When omitted,
      every target matching :data:`DEFAULT_ADVERSARIAL_TAG_QUERY` is used.

    Raises (at ``initialize_async``):

    * ``ValueError`` — no adversarial-tagged targets are registered. The
      error names the ``ADVERSARIAL_CHAT_*`` env vars to set and the
      ``TargetInitializer`` dependency.
    * ``ValueError`` — any name in ``target_names`` does not match a
      discovered adversarial-tagged target. The error lists discovered names.

    Prerequisites: ``TargetInitializer`` must have run first so adversarial
    env-driven targets are present in ``TargetRegistry``. Registering the
    base scenario-technique catalog (``ScenarioTechniqueInitializer`` or an
    equivalent caller of ``register_scenario_techniques``) is also expected
    if users will select non-benchmark strategies in the same session;
    ``BenchmarkInitializer`` itself only registers the fanned variants.
    Per-name idempotent via ``AttackTechniqueRegistry.register_from_specs``:
    running the initializer twice with the same registry state is a no-op.
    """

    @property
    def supported_parameters(self) -> list[Parameter]:
        """Declare the optional ``target_names`` narrowing parameter."""
        return [
            Parameter(
                name="target_names",
                description=(
                    "Optional list of adversarial target registry names to narrow benchmark fan-out. "
                    'When omitted, every target matching TagQuery.all("adversarial") is used.'
                ),
                default=None,
                param_type=list[str],
            ),
        ]

    async def initialize_async(self) -> None:
        """
        Discover adversarial targets and register fanned specs into the technique registry.

        Raises:
            ValueError: If no adversarial-tagged targets are registered in
                ``TargetRegistry``, or if ``self.params['target_names']``
                contains a name not in the discovered set.
        """
        target_registry = TargetRegistry.get_registry_singleton()
        discovered_entries = target_registry.get_by_tag_query(query=DEFAULT_ADVERSARIAL_TAG_QUERY)
        if not discovered_entries:
            raise ValueError(
                "BenchmarkInitializer: no adversarial-tagged targets registered in TargetRegistry. "
                "Set ADVERSARIAL_CHAT_* env vars (see .env_example) and ensure TargetInitializer runs "
                "before BenchmarkInitializer (e.g. via .pyrit_conf initializer ordering)."
            )

        selected_entries = self._narrow_by_target_names(discovered_entries=discovered_entries)

        fanned_specs = self._build_fanned_specs(target_entries=selected_entries)

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        attack_registry.register_from_specs(fanned_specs)

        logger.info(
            "BenchmarkInitializer: registered %d fanned spec(s) across %d adversarial target(s): %s",
            len(fanned_specs),
            len(selected_entries),
            ", ".join(entry.name for entry in selected_entries),
        )

    def _narrow_by_target_names(self, *, discovered_entries: list) -> list:
        """
        Filter ``discovered_entries`` to the names in ``self.params['target_names']``, if set.

        Args:
            discovered_entries: The full set of adversarial-tagged registry entries.

        Returns:
            list: ``discovered_entries`` unchanged when no ``target_names`` param
            is set, otherwise the subset whose ``name`` is in the requested set.

        Raises:
            ValueError: If any name in ``self.params['target_names']`` is not
                present in ``discovered_entries``.
        """
        target_names_param = self.params.get("target_names")
        if not target_names_param:
            return discovered_entries

        requested = set(target_names_param)
        discovered_names = {entry.name for entry in discovered_entries}
        unknown = requested - discovered_names
        if unknown:
            raise ValueError(
                f"BenchmarkInitializer: unknown target_names {sorted(unknown)}. "
                f"Discovered adversarial targets: {sorted(discovered_names)}."
            )
        return [entry for entry in discovered_entries if entry.name in requested]

    def _build_fanned_specs(self, *, target_entries: list) -> list[AttackTechniqueSpec]:
        """
        Build fanned ``AttackTechniqueSpec``s for every (adversarial-capable technique, target) pair.

        Adversarial-capability is determined by ``_spec_needs_adversarial``
        (re-used from ``scenario_techniques``): a spec needs an adversarial
        chat target when its attack class accepts ``attack_adversarial_config``
        or its ``seed_technique`` has a simulated conversation. Non-adversarial
        techniques (e.g. ``prompt_sending``, ``role_play``) are skipped — the
        benchmark holds the objective target constant and varies the
        adversarial chat helper across runs.

        Args:
            target_entries: The adversarial-tagged registry entries to fan over.

        Returns:
            list[AttackTechniqueSpec]: One fanned spec per (adversarial-capable
            technique, target entry) pair, with the live target bound onto
            ``adversarial_chat`` and benchmark-specific strategy tags appended.
        """
        fanned: list[AttackTechniqueSpec] = []
        for source_spec in SCENARIO_TECHNIQUES:
            if not _spec_needs_adversarial(source_spec):
                continue
            fanned.extend(
                dataclasses.replace(
                    source_spec,
                    name=f"{source_spec.name}__{entry.name}",
                    adversarial_chat=entry.instance,
                    adversarial_chat_key=None,
                    strategy_tags=[*source_spec.strategy_tags, "benchmark_fanout", f"model:{entry.name}"],
                )
                for entry in target_entries
            )
        return fanned
