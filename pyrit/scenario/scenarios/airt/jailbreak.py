# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path
from typing import Any, ClassVar

from pyrit.common import apply_defaults
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.core.attack_config import AttackAdversarialConfig, AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.role_play import RolePlayAttack, RolePlayPaths
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack
from pyrit.models import Parameter, SeedAttackGroup
from pyrit.prompt_converter import TextJailbreakConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target
from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class _Unset:
    """Sentinel marking an omitted ``num_templates`` argument (distinct from an explicit ``None``)."""


_UNSET = _Unset()


class JailbreakStrategy(ScenarioStrategy):
    """
    Strategy for jailbreak attacks.

    The SIMPLE strategy just sends the jailbroken prompt and records the response. It is meant to
    expose an obvious way of using this scenario without worrying about additional tweaks and changes
    to the prompt.

    COMPLEX strategies use additional techniques to enhance the jailbreak like modifying the
    system prompt or probing the target model for an additional vulnerability (e.g. the SkeletonKeyAttack).
    They are meant to provide a sense of how well a jailbreak generalizes to slight changes in the delivery
    method.
    """

    # Aggregate members (special markers that expand to strategies with matching tags)
    ALL = ("all", {"all"})
    SIMPLE = ("simple", {"simple"})
    COMPLEX = ("complex", {"complex"})

    # Simple strategies
    PromptSending = ("prompt_sending", {"simple"})

    # Complex strategies
    ManyShot = ("many_shot", {"complex"})
    SkeletonKey = ("skeleton", {"complex"})
    RolePlay = ("role_play", {"complex"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            set[str]: Set of tags that are aggregate markers.
        """
        # Include base class aggregates ("all") and add scenario-specific ones
        return super().get_aggregate_tags() | {"simple", "complex"}


class Jailbreak(Scenario):
    """
    Jailbreak scenario implementation for PyRIT.

    This scenario tests how vulnerable models are to jailbreak attacks by applying
    various single-turn jailbreak templates to a set of test prompts. The responses are
    scored to determine if the jailbreak was successful.
    """

    VERSION: int = 2

    #: Number of jailbreak templates sampled by default when neither the constructor argument
    #: nor the ``num_templates`` runtime parameter is supplied. The full catalog ships 162
    #: templates; this is a small, fast-to-run random subset (the team-agreed default for the
    #: quick path). Raise ``--num-templates`` for broader coverage, or pass ``num_templates=None``
    #: to run the full catalog.
    DEFAULT_NUM_TEMPLATES: ClassVar[int] = 10

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_harms"]

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare runtime parameters settable from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run, exposed as ``--num-templates`` and
            ``--num-attempts``.
        """
        return [
            Parameter(
                name="num_templates",
                description=(
                    "Number of jailbreak templates to randomly sample from the full catalog. "
                    "Lower this for a faster run; raise it for broader coverage."
                ),
                param_type=int,
                default=cls.DEFAULT_NUM_TEMPLATES,
            ),
            Parameter(
                name="num_attempts",
                description="Number of times to run each selected jailbreak template.",
                param_type=int,
                default=1,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
        num_templates: "int | None | _Unset" = _UNSET,
        num_attempts: int | None = None,
        jailbreak_names: list[str] | None = None,
    ) -> None:
        """
        Initialize the jailbreak scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for detecting successful jailbreaks
                (non-refusal). If not provided, defaults to an inverted refusal scorer.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
                On resume the template names chosen by the original run are replayed (read from
                ``ScenarioResult.metadata``) so the atomic-attack set stays stable across processes.
            num_templates (int | None): Number of random jailbreak templates to run. When omitted,
                falls back to the ``num_templates`` runtime parameter (default
                ``DEFAULT_NUM_TEMPLATES``). An explicit integer takes precedence over the parameter.
                Pass ``num_templates=None`` to opt out of sampling and run the full catalog. Configure
                the default via the ``num_templates`` runtime parameter (``--num-templates`` / config),
                not ``set_default_value`` — the omitted case is a sentinel, not ``None``.
            num_attempts (int | None): Number of times to try each jailbreak. When omitted, falls back
                to the ``num_attempts`` runtime parameter (default 1).
            jailbreak_names (list[str] | None): List of jailbreak names from the template list under datasets.
                to use.

        Raises:
            ValueError: If both jailbreak_names and num_templates are provided, as random selection
                is incompatible with a predetermined list.
            ValueError: If the jailbreak_names list contains a jailbreak that isn't in the listed
                templates.

        """
        if jailbreak_names is None:
            jailbreak_names = []
        if jailbreak_names and not isinstance(num_templates, _Unset):
            raise ValueError(
                "Please provide only one of `num_templates` (random selection)"
                " or `jailbreak_names` (specific selection)."
            )

        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        # Distinguish an omitted argument (use the runtime default) from an explicit ``None``
        # (opt out of sampling and run the full catalog).
        if isinstance(num_templates, _Unset):
            self._num_templates_unset = True
            self._num_templates: int | None = None
        else:
            self._num_templates_unset = False
            self._num_templates = num_templates
        self._num_attempts = num_attempts
        self._adversarial_target: PromptTarget | None = None

        # Template resolution is split by selection mode:
        # * ``jailbreak_names`` (explicit selection) is validated and resolved eagerly here so an
        #   unknown name fails fast at construction time.
        # * Random ``num_templates`` selection is deferred to ``_get_atomic_attacks_async`` so the
        #   ``num_templates`` runtime parameter (populated into ``self.params`` during
        #   ``initialize_async``) is honored — ``self.params`` does not exist yet in ``__init__``.
        if jailbreak_names:
            all_templates = TextJailBreak.get_jailbreak_templates()
            # Example: if jailbreak_names is {'a', 'b', 'c'}, and all_templates is {'b', 'c', 'd'},
            # then diff = {'a'}, which raises the error as 'a' was not discovered in all_templates.
            diff = set(jailbreak_names) - set(all_templates)
            if diff:
                raise ValueError(f"Error: could not find templates `{diff}`!")
            self._jailbreaks: list[str] = jailbreak_names
            self._jailbreaks_explicit = True
        else:
            self._jailbreaks = []
            self._jailbreaks_explicit = False

        super().__init__(
            version=self.VERSION,
            strategy_class=JailbreakStrategy,
            default_strategy=JailbreakStrategy.SIMPLE,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["airt_harms"], max_dataset_size=4),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @property
    def selected_jailbreak_names(self) -> list[str]:
        """
        Jailbreak template names selected for this run.

        Populated once ``initialize_async`` has resolved the sample (or replayed the persisted set
        on ``--resume``). For the random-sampling path this is empty before initialization. The same
        list is also persisted to ``ScenarioResult.metadata`` and surfaced in the scenario output.

        Returns:
            list[str]: The jailbreak template names this run executes.
        """
        return list(self._jailbreaks)

    def _get_or_create_adversarial_target(self) -> PromptTarget:
        """
        Return the shared adversarial target, creating it on first access.

        Reuses a single PromptTarget instance across all role-play attacks
        to avoid repeated client and TLS setup.

        Returns:
            PromptTarget: The shared adversarial target.
        """
        if self._adversarial_target is None:
            self._adversarial_target = get_default_adversarial_target()
        return self._adversarial_target

    def _load_persisted_jailbreak_names(self) -> list[str] | None:
        """
        Return the template names persisted by a prior run when resuming, otherwise ``None``.

        Template resolution happens inside ``_build_atomic_attacks_async``, which the base class runs
        *before* it applies persisted resume state. Since each template is its own atomic attack,
        the persisted names must be read here (not in ``_apply_persisted_objectives``) so the resumed
        run rebuilds the same atomic attacks instead of drawing a fresh random sample.

        Returns:
            list[str] | None: The persisted template names, or ``None`` when not resuming or when no
            names were persisted.
        """
        if not self._scenario_result_id:
            return None
        stored = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])
        if not stored:
            return None
        names = (stored[0].metadata or {}).get("jailbreak_template_names")
        if not names:
            return None
        return list(names)

    def _resolve_jailbreaks(self) -> list[str]:
        """
        Resolve the jailbreak templates to run.

        Resolution precedence:

        1. On resume, replay the template names persisted by the original run (deterministic resume).
        2. Explicit ``jailbreak_names`` (resolved in ``__init__``).
        3. An explicit constructor ``num_templates`` (an integer wins over the runtime parameter; an
           explicit ``None`` opts out of sampling and runs the full catalog).
        4. The ``num_templates`` runtime parameter, which defaults to ``DEFAULT_NUM_TEMPLATES``.

        Returns:
            list[str]: The jailbreak template file names to run.
        """
        persisted = self._load_persisted_jailbreak_names()
        if persisted is not None:
            return persisted
        if self._jailbreaks_explicit:
            return self._jailbreaks
        num_templates = self.params["num_templates"] if self._num_templates_unset else self._num_templates
        return TextJailBreak.get_jailbreak_templates(num_templates=num_templates)

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Persist the resolved template names so ``--resume`` replays the same sample.

        Extends the base ``objective_hashes`` persistence (preserved via ``super()``) with the
        concrete template names chosen for this run, mirroring that pattern for the template axis.

        Returns:
            dict[str, Any]: Metadata payload for the new ScenarioResult.
        """
        metadata = super()._build_initial_scenario_metadata()
        names = list(self._jailbreaks)
        metadata["jailbreak_template_names"] = names
        summary = metadata.setdefault("summary", {})
        summary["Jailbreak templates"] = ", ".join(names)
        return metadata

    async def _get_atomic_attack_from_strategy_async(
        self, *, strategy: str, jailbreak_template_name: str, seed_groups: list[SeedAttackGroup]
    ) -> AtomicAttack:
        """
        Create an atomic attack for a specific jailbreak template.

        Args:
            strategy (str): JailbreakStrategy to use.
            jailbreak_template_name (str): Name of the jailbreak template file.
            seed_groups (list[SeedAttackGroup]): Seed groups the attack draws from.

        Returns:
            AtomicAttack: An atomic attack using the specified jailbreak template.

        Raises:
            ValueError: If scenario is not properly initialized.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        # Create the jailbreak converter
        jailbreak_converter = TextJailbreakConverter(
            jailbreak_template=TextJailBreak(template_file_name=jailbreak_template_name)
        )

        # Create converter configuration
        converter_config = AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=[jailbreak_converter])
        )

        attack: ManyShotJailbreakAttack | PromptSendingAttack | RolePlayAttack | SkeletonKeyAttack | None = None
        args: dict[str, Any] = {
            "objective_target": self._objective_target,
            "attack_scoring_config": AttackScoringConfig(objective_scorer=self._objective_scorer),
            "attack_converter_config": converter_config,
        }
        match strategy:
            case "many_shot":
                attack = ManyShotJailbreakAttack(**args)
            case "prompt_sending":
                attack = PromptSendingAttack(**args)
            case "skeleton":
                attack = SkeletonKeyAttack(**args)
            case "role_play":
                args["attack_adversarial_config"] = AttackAdversarialConfig(
                    target=self._get_or_create_adversarial_target()
                )
                args["role_play_definition_path"] = RolePlayPaths.PERSUASION_SCRIPT.value
                attack = RolePlayAttack(**args)
            case _:
                raise ValueError(f"Unknown JailbreakStrategy `{strategy}`.")

        if not attack:
            raise ValueError(f"Attack cannot be None!")

        # Extract template name without extension for the atomic attack name
        template_name = Path(jailbreak_template_name).stem

        return AtomicAttack(
            atomic_attack_name=f"jailbreak_{template_name}",
            attack_technique=AttackTechnique(attack=attack),
            seed_groups=seed_groups,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Generate atomic attacks for each jailbreak template.

        This method creates an atomic attack for each retrieved jailbreak template.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: List of atomic attacks to execute, one per jailbreak template.
        """
        atomic_attacks: list[AtomicAttack] = []

        seed_groups = list(context.seed_groups)

        # Resolve templates now that runtime parameters are populated (and replay the persisted
        # sample on --resume). Deferred here rather than in __init__ so self.params exists.
        self._jailbreaks = self._resolve_jailbreaks()
        logger.info(
            "Jailbreak scenario running %d template(s): %s",
            len(self._jailbreaks),
            ", ".join(self._jailbreaks),
        )
        num_attempts = self._num_attempts if self._num_attempts is not None else self.params["num_attempts"]

        strategies = {s.value for s in context.scenario_strategies}

        for strategy in strategies:
            for template_name in self._jailbreaks:
                for _ in range(num_attempts):
                    atomic_attack = await self._get_atomic_attack_from_strategy_async(
                        strategy=strategy, jailbreak_template_name=template_name, seed_groups=seed_groups
                    )
                    atomic_attacks.append(atomic_attack)

        return atomic_attacks
