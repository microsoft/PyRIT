# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pyrit.executor.attack import AttackAdversarialConfig
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.models import SeedPrompt
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class _AttackAdversarialConfigHelper:
    """
    Evaluates and assembles adversarial configuration for AttackTechniqueFactory.

    Encapsulates adversarial target precedence, prompt resolution, conflicting-override
    checks, and construction of AttackAdversarialConfig instances.
    """

    def __init__(
        self,
        *,
        factory_name: str,
        attack_class: type,
        uses_adversarial: bool,
        adversarial_chat: PromptTarget | None = None,
        adversarial_system_prompt: str | SeedPrompt | None = None,
        adversarial_seed_prompt: str | SeedPrompt | None = None,
        adversarial_prompt_template: str | SeedPrompt | None = None,
        adversarial_system_prompt_prefix: str | None = None,
    ) -> None:
        self._factory_name = factory_name
        self._attack_class = attack_class
        self._uses_adversarial = uses_adversarial
        self._adversarial_chat = adversarial_chat
        self._adversarial_system_prompt = adversarial_system_prompt
        self._adversarial_seed_prompt = adversarial_seed_prompt
        self._adversarial_prompt_template = adversarial_prompt_template
        self._adversarial_system_prompt_prefix = adversarial_system_prompt_prefix

    @property
    def has_custom_adversarial_prompt(self) -> bool:
        """Whether this technique has any custom adversarial prompt baked in."""
        return any(
            p is not None
            for p in (
                self._adversarial_system_prompt,
                self._adversarial_seed_prompt,
                self._adversarial_prompt_template,
            )
        )

    def validate_target_conflict(self, *, create_time_target: PromptTarget | None) -> None:
        """
        Verify that create() does not supply an adversarial chat when one is already baked.

        Args:
            create_time_target: Target passed to create().

        Raises:
            ValueError: If an adversarial chat is already baked in and create() also supplies one.
        """
        if create_time_target is not None and self._adversarial_chat is not None:
            raise ValueError(
                f"Factory '{self._factory_name}': an adversarial chat is already baked into this technique, so "
                f"create() cannot supply one. Remove the baked adversarial_chat or the create-time one."
            )

    def validate_prompt_conflict(
        self,
        *,
        create_time_system_prompt: str | SeedPrompt | None = None,
        create_time_seed_prompt: str | SeedPrompt | None = None,
        create_time_prompt_template: str | SeedPrompt | None = None,
    ) -> None:
        """
        Verify that create() does not supply adversarial prompts when custom ones are already baked.

        Args:
            create_time_system_prompt: System prompt passed to create().
            create_time_seed_prompt: Seed prompt passed to create().
            create_time_prompt_template: Prompt template passed to create().

        Raises:
            ValueError: If custom prompts are already baked in and create() also supplies prompts.
        """
        if (
            create_time_system_prompt is not None
            or create_time_seed_prompt is not None
            or create_time_prompt_template is not None
        ) and self.has_custom_adversarial_prompt:
            raise ValueError(
                f"Factory '{self._factory_name}': a custom adversarial prompt is already baked into this technique, "
                f"so create() cannot supply 'adversarial_system_prompt', 'adversarial_seed_prompt', or "
                f"'adversarial_prompt_template'."
            )

    def should_build_adversarial_config(
        self,
        *,
        accepted_params: set[str],
        create_time_target: PromptTarget | None = None,
        create_time_system_prompt: str | SeedPrompt | None = None,
        create_time_seed_prompt: str | SeedPrompt | None = None,
        create_time_prompt_template: str | SeedPrompt | None = None,
    ) -> bool:
        """
        Determine whether AttackAdversarialConfig should be constructed and passed.

        Args:
            accepted_params: Constructor parameters accepted by the attack class.
            create_time_target: Target passed to create().
            create_time_system_prompt: System prompt passed to create().
            create_time_seed_prompt: Seed prompt passed to create().
            create_time_prompt_template: Prompt template passed to create().

        Returns:
            bool: True if the attack accepts attack_adversarial_config and any adversarial
                configuration or flag is active.
        """
        if "attack_adversarial_config" not in accepted_params:
            return False
        return (
            create_time_target is not None
            or create_time_system_prompt is not None
            or create_time_seed_prompt is not None
            or create_time_prompt_template is not None
            or self._adversarial_system_prompt_prefix is not None
            or self._uses_adversarial
        )

    def build_adversarial_config(
        self,
        *,
        create_time_target: PromptTarget | None = None,
        create_time_system_prompt: str | SeedPrompt | None = None,
        create_time_seed_prompt: SeedPrompt | str | None = None,
        create_time_prompt_template: str | SeedPrompt | None = None,
        default_target_resolver: Callable[[], PromptTarget] | None = None,
    ) -> AttackAdversarialConfig:
        """
        Build the adversarial config for a created attack, resolving the target lazily.

        Target precedence: an explicit ``create_time_target`` wins, then the factory's baked
        ``adversarial_chat``, then the lazily-resolved default adversarial target.
        The factory's custom ``adversarial_system_prompt`` / ``adversarial_seed_prompt`` /
        ``adversarial_prompt_template`` take precedence over create-time values, so a technique
        keeps its bespoke persona while a scenario can still supply the target.

        Args:
            create_time_target: An adversarial target supplied at create() time.
            create_time_system_prompt: An adversarial system prompt supplied at create() time.
            create_time_seed_prompt: An adversarial seed prompt supplied at create() time.
            create_time_prompt_template: An adversarial per-turn feedback template supplied
                at create() time.
            default_target_resolver: Optional callable to resolve the default target.
                Defaults to get_default_adversarial_target.

        Returns:
            AttackAdversarialConfig: Config wrapping the resolved adversarial chat target and prompts.
        """
        if create_time_target is not None:
            target: PromptTarget = create_time_target
        elif self._adversarial_chat is not None:
            target = self._adversarial_chat
        else:
            resolver = default_target_resolver or get_default_adversarial_target
            target = resolver()

        system_prompt = self._adversarial_system_prompt or create_time_system_prompt
        seed_prompt = self._adversarial_seed_prompt or create_time_seed_prompt
        prompt_template = (
            self._adversarial_prompt_template
            if self._adversarial_prompt_template is not None
            else create_time_prompt_template
        )

        config_kwargs: dict[str, Any] = {
            "target": target,
            "system_prompt_prefix": self._adversarial_system_prompt_prefix,
        }
        if system_prompt is not None:
            config_kwargs["system_prompt"] = system_prompt
        if seed_prompt is not None:
            config_kwargs["first_message"] = seed_prompt
        if prompt_template is not None:
            config_kwargs["adversarial_prompt_template"] = prompt_template
        return AttackAdversarialConfig(**config_kwargs)
