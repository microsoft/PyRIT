# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared base for scenarios that port promptfoo red-team plugins."""

from __future__ import annotations

import hashlib
import json
from abc import abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common import REQUIRED_VALUE, apply_defaults
from pyrit.common.async_compatibility import run_legacy_sync_async
from pyrit.datasets import PromptfooPlugin, PromptfooPluginProvider
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import BoundedDatasetSize, Parameter
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario.core import get_default_adversarial_target
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetFetchPolicy,
    DatasetSource,
    DatasetSourceKind,
)
from pyrit.scenario.core.matrix_atomic_attack_builder import MatrixAtomicAttackBuilder
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_target_defaults import validate_default_adversarial_target
from pyrit.score import (
    PromptfooRubricScorer,
    SelfAskRefusalScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer


#: promptfoo's ``basic`` strategy: send each generated test unchanged.
PROMPTFOO_BASIC_FACTORY = AttackTechniqueFactory(
    name="basic",
    attack_class=PromptSendingAttack,
    technique_tags=["single_turn"],
)


@AttackTechniqueRegistry.cache_scenario_technique_class
def _build_promptfoo_technique() -> type[ScenarioTechnique]:
    """
    Build the promptfoo technique enum: promptfoo's ``basic`` strategy plus every registered technique.

    Returns:
        type[ScenarioTechnique]: The technique enum, whose default is ``basic``.
    """
    registered = AttackTechniqueRegistry.get_registry_singleton().get_factories()
    factories = {**registered, PROMPTFOO_BASIC_FACTORY.name: PROMPTFOO_BASIC_FACTORY}
    return AttackTechniqueRegistry.build_technique_class_from_factories(
        class_name="PromptfooTechnique",
        factories=list(factories.values()),
        default_names={PROMPTFOO_BASIC_FACTORY.name},
    )


class PromptfooScenario(Scenario):
    """
    Base for scenarios that run promptfoo red-team plugins against an application.

    Each plugin generates tests from the application's ``purpose`` and grades each response with the
    plugin's own rubric through ``PromptfooRubricScorer``. A response that fails the rubric, and is not a
    refusal, is a success. Generated tests are stored in a dataset named for the plugin and a fingerprint
    of the plugin, the purpose, the test count, and the rendered rubric, so a later run with the same
    inputs reuses them and a changed input generates new ones. The default technique, ``basic``, sends
    each test unchanged, like promptfoo's ``basic`` strategy; any registered technique can run too.

    Reference: [@promptfoo2025redteam]
    """

    VERSION: int
    DEFAULT_NUM_TESTS: ClassVar[int] = 5
    _SKIPS_TECHNIQUES_THE_SCORER_CANNOT_JUDGE: ClassVar[bool] = True
    #: ``basic`` already sends each test unchanged, so a baseline would repeat it.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Disabled

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget | None = None,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the scenario.

        Args:
            adversarial_chat (PromptTarget | None): Generates the tests, and drives techniques that use an
                adversarial chat and don't set their own. Defaults to the registered adversarial target.
            objective_scorer (TrueFalseScorer | None): Decides success. Defaults to a composite on the
                scoring target that succeeds when the response fails its plugin's rubric under
                ``PromptfooRubricScorer`` and is not a refusal, like promptfoo, where a refusal or an
                empty response passes.
            scenario_result_id (str | None): Existing result to resume.
        """
        super().__init__(
            version=self.VERSION,
            technique_class=_build_promptfoo_technique(),
            # The datasets are named for the purpose, so they are added once it is known. Declaring none
            # here keeps unregistered names out of the catalog, which preloads every scenario's defaults.
            default_dataset_config=DatasetAttackConfiguration(max_per_dataset="all"),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
            uses_default_adversarial_target=True,
            adversarial_target=adversarial_chat,
        )

    @classmethod
    @abstractmethod
    def get_plugin_ids(cls) -> tuple[str, ...]:
        """
        Return the ids of the promptfoo plugins this scenario runs.

        Returns:
            tuple[str, ...]: The plugin ids, such as ``("hallucination",)``.
        """

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the application's purpose and the number of tests per plugin.

        Returns:
            list[Parameter]: The promptfoo run settings.
        """
        return [
            Parameter(
                name="purpose",
                description=(
                    "What the application under test does, who uses it, and what it can access. "
                    "Plugins generate their tests from it, and their rubrics grade against it."
                ),
                param_type=str,
                default=REQUIRED_VALUE,
                multiline=True,
            ),
            Parameter(
                name="num_tests",
                description="Tests to generate for each plugin.",
                param_type=int,
                default=cls.DEFAULT_NUM_TESTS,
            ),
        ]

    def _get_default_objective_scorer(self) -> TrueFalseScorer:
        """
        Succeed when a response fails its plugin's rubric and is not a refusal, judged on the scoring target.

        promptfoo grades a refusal or an empty response as a pass, so PyRIT's refusal backstop joins the
        inverted rubric verdict.

        Returns:
            TrueFalseScorer: AND of the inverted ``PromptfooRubricScorer`` and the inverted
                ``SelfAskRefusalScorer``.
        """
        chat_target = self._resolve_scoring_target()
        rubric_scorer = PromptfooRubricScorer(chat_target=chat_target)
        refusal_scorer = SelfAskRefusalScorer(chat_target=chat_target)
        for leaf in (rubric_scorer, refusal_scorer):
            leaf.raise_if_scorer_blocks = self.RAISE_IF_DEFAULT_SCORER_BLOCKS
        return TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[TrueFalseInverterScorer(scorer=rubric_scorer), TrueFalseInverterScorer(scorer=refusal_scorer)],
        )

    def _validate_runtime_configuration(self) -> None:
        super()._validate_runtime_configuration()
        purpose = self.params.get("purpose")
        if isinstance(purpose, str) and not purpose.strip():
            raise ValueError("purpose must describe the application under test.")
        if self._get_num_tests() < 1:
            raise ValueError("num_tests must be at least 1.")

    def _get_run_size_budget(self) -> BoundedDatasetSize:
        """
        Size the run by the tests it generates, which may not exist yet.

        Returns:
            BoundedDatasetSize: The generated tests across plugins, capped by ``max_total``.
        """
        generated = self._get_num_tests() * len(self.get_plugin_ids())
        cap = self._dataset_config.max_total
        return BoundedDatasetSize(value=generated if cap == "all" else min(generated, cap))

    def _resolve_default_dataset_config(self) -> DatasetAttackConfiguration:
        """
        Name each plugin's dataset for its generation inputs, so changed inputs generate new tests.

        Returns:
            DatasetAttackConfiguration: One source per plugin, which the configuration's fetch policy
                generates when missing by default.
        """
        purpose = self.params.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            return self._default_dataset_config
        num_tests = self._get_num_tests()
        return self._default_dataset_config.with_overrides(
            sources=[
                DatasetSource(name=self._get_dataset_name(plugin_id=plugin_id, purpose=purpose, num_tests=num_tests))
                for plugin_id in self.get_plugin_ids()
            ]
        )

    async def _prepare_datasets_async(self, *, fetch: DatasetFetchPolicy | None = None) -> None:
        """
        Attach each plugin's generator, on the adversarial target, before preparing datasets.

        Args:
            fetch (DatasetFetchPolicy | None): Optional policy for this operation only.
        """
        config = self._dataset_config
        if config.source_kind is DatasetSourceKind.MEMORY and not isinstance(
            config, CompoundDatasetAttackConfiguration
        ):
            providers = await run_legacy_sync_async(self._build_providers)
            self._dataset_config = self._dataset_config.with_overrides(
                sources=[
                    replace(source, provider=providers[source.name])
                    if source.provider is None and source.name in providers
                    else source
                    for source in self._dataset_config.sources
                ]
            )
        await super()._prepare_datasets_async(fetch=fetch)

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the technique × plugin atomic attacks, grouped by technique.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        selected = {technique.value for technique in context.scenario_techniques}
        factories = {name: factory for name, factory in self._get_readiness_factories().items() if name in selected}
        builder = MatrixAtomicAttackBuilder(
            objective_target=context.objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
            incompatible_technique_policy=context.incompatible_technique_policy,
        )
        return await run_legacy_sync_async(
            builder.build,
            technique_factories=factories,
            dataset_groups=context.seed_groups_by_dataset,
            include_baseline=context.include_baseline,
            technique_converters=context.technique_converters,
        )

    def _build_providers(self) -> dict[str, PromptfooPluginProvider]:
        """
        Build a generator for each plugin's dataset.

        The generator runs on the adversarial target that readiness resolved. An explicit dataset load
        skips readiness, so it resolves the supplied or registered adversarial target here.

        Returns:
            dict[str, PromptfooPluginProvider]: Providers keyed by dataset name.
        """
        target = self._resolved_adversarial_target
        if target is None:
            target = self._supplied_adversarial_target or get_default_adversarial_target()
            validate_default_adversarial_target(target)
        purpose = str(self.params["purpose"])
        num_tests = self._get_num_tests()
        providers: dict[str, PromptfooPluginProvider] = {}
        for plugin_id in self.get_plugin_ids():
            name = self._get_dataset_name(plugin_id=plugin_id, purpose=purpose, num_tests=num_tests)
            providers[name] = PromptfooPluginProvider(
                dataset_name=name,
                plugin=PromptfooPlugin.from_id(plugin_id),
                purpose=purpose,
                count=num_tests,
                target=target,
            )
        return providers

    def _get_num_tests(self) -> int:
        return int(self.params.get("num_tests", self.DEFAULT_NUM_TESTS))

    @staticmethod
    def _get_dataset_name(*, plugin_id: str, purpose: str, num_tests: int) -> str:
        """
        Name a plugin's dataset for the inputs that generate it.

        Returns:
            str: ``promptfoo_<plugin>_<fingerprint>``, where the fingerprint covers the plugin's templates,
                the purpose, the test count, and the rendered rubric each test carries.
        """
        plugin = PromptfooPlugin.from_id(plugin_id)
        inputs: dict[str, Any] = {
            "plugin": plugin.model_dump(mode="json"),
            "purpose": purpose,
            "num_tests": num_tests,
            "rubric": plugin.render_rubric(purpose=purpose),
        }
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        return f"promptfoo_{plugin_id.replace(':', '_')}_{digest}"
