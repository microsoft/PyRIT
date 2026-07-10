# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pyrit.executor.core import StrategyConverterConfig
from pyrit.models import JsonSchemaDefinition, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer, TrueFalseScorer

logger = logging.getLogger(__name__)

# Default first message sent to the adversarial chat when there is no objective-target
# response yet (rendered with ``{{ objective }}``).
DEFAULT_ADVERSARIAL_FIRST_MESSAGE = "Generate your first message to achieve: {{ objective }}"

# Default per-turn template handed to the adversarial chat. The manager computes the actual
# feedback text in Python (handling blocked/error/empty responses and optional score feedback)
# and exposes it to the template as ``feedback_text``; the default simply renders it. Custom
# templates may wrap ``feedback_text`` and reference ``objective``, and are rendered strictly, so
# a reference to any other variable raises rather than silently producing empty output.
DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE = "{{ feedback_text }}"


def resolve_adversarial_json_schema(
    *,
    system_prompt: SeedPrompt | None,
    first_message: SeedPrompt | None,
) -> JsonSchemaDefinition | None:
    """
    Resolve the single adversarial-chat response JSON schema from a pair of prompts.

    The schema may be declared on either the adversarial system prompt or the first message
    (via ``response_json_schema`` / ``response_json_schema_name`` in YAML), but not both —
    declaring it twice is ambiguous about which one drives the response shape.

    Args:
        system_prompt: The resolved adversarial system-prompt SeedPrompt, or None.
        first_message: The resolved adversarial first-message SeedPrompt, or None.

    Returns:
        The declared schema, or None when neither prompt declares one.

    Raises:
        ValueError: If both prompts declare a ``response_json_schema``.
    """
    system_schema = system_prompt.response_json_schema if system_prompt is not None else None
    first_message_schema = first_message.response_json_schema if first_message is not None else None
    if system_schema is not None and first_message_schema is not None:
        raise ValueError(
            "Both the adversarial system prompt and first message declare a response_json_schema; "
            "set the schema on only one of them."
        )
    return system_schema or first_message_schema


@dataclass
class AttackAdversarialConfig:
    """
    Adversarial configuration for attacks that involve adversarial chat targets.

    This class defines the configuration for attacks that utilize an adversarial chat target,
    including the target chat model, system prompt, and seed prompt for the attack.
    """

    # Adversarial chat target for the attack
    target: PromptTarget

    # First message sent to the adversarial chat when there is no objective-target response
    # yet (supports the {{ objective }} template variable). May be None for strategies that
    # do not use a first message.
    first_message: str | SeedPrompt | None = DEFAULT_ADVERSARIAL_FIRST_MESSAGE

    # Template rendered each turn to wrap the per-turn feedback text the manager computes from
    # the objective target's latest response. Receives ``feedback_text`` and ``objective``.
    adversarial_prompt_template: str | SeedPrompt | None = DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE

    # System prompt for the adversarial chat target, as an inline Jinja template string or a
    # SeedPrompt.
    system_prompt: str | SeedPrompt | None = None


def resolve_adversarial_system_prompt(
    *,
    config: AttackAdversarialConfig,
    default_system_prompt_path: str | Path,
    required_parameters: list[str],
    error_message: str | None = None,
) -> SeedPrompt:
    """
    Resolve the effective adversarial system-prompt ``SeedPrompt`` for a strategy.

    Resolution order:

    1. ``config.system_prompt`` (inline string or SeedPrompt), if provided.
    2. ``default_system_prompt_path``.

    Inline strings are trusted: they are wrapped in a Jinja ``SeedPrompt`` whose declared
    parameters are set to ``required_parameters``. Explicitly provided ``SeedPrompt`` objects
    and YAML files are validated against ``required_parameters``.

    Args:
        config: The adversarial configuration to resolve the system prompt from.
        default_system_prompt_path: Fallback YAML path when neither inline nor path is set.
        required_parameters: Parameter names the resolved template must support.
        error_message: Optional custom error message for validation failures.

    Returns:
        The resolved adversarial system-prompt SeedPrompt.

    Raises:
        ValueError: If an explicitly provided SeedPrompt is missing required parameters.
    """
    system_prompt = config.system_prompt
    if system_prompt is not None:
        if isinstance(system_prompt, SeedPrompt):
            # Validate only explicitly provided SeedPrompts against the required parameters.
            declared = system_prompt.parameters or []
            missing = [param for param in required_parameters if param not in declared]
            if missing:
                raise ValueError(
                    error_message or f"Adversarial system prompt is missing required parameters: {missing}"
                )
            return system_prompt

        # Inline strings are trusted — declare all required params so Jinja rendering works.
        return SeedPrompt(
            value=system_prompt,
            is_jinja_template=True,
            parameters=list(required_parameters),
        )

    template_path = default_system_prompt_path
    return SeedPrompt.from_yaml_with_required_parameters(
        template_path=template_path,
        required_parameters=required_parameters,
        error_message=error_message,
    )


@dataclass
class AttackScoringConfig:
    """
    Scoring configuration for evaluating attack effectiveness.

    This class defines the scoring components used to evaluate attack effectiveness,
    detect refusals, and perform auxiliary scoring operations.
    """

    # Primary scorer for evaluating attack effectiveness
    objective_scorer: TrueFalseScorer | None = None

    # Refusal scorer for detecting refusals or non-compliance
    refusal_scorer: TrueFalseScorer | None = None

    # Additional scorers for auxiliary metrics or custom evaluations
    auxiliary_scorers: list[Scorer] = field(default_factory=list)

    # Whether to use scoring results as feedback for iterative attacks
    use_score_as_feedback: bool = True

    def __post_init__(self) -> None:
        """
        Validate configuration values.

        Raises:
            ValueError: If the objective or refusal scorers are not of type TrueFalseScorer.
        """
        # Enforce objective scorer type: must be a TrueFalseScorer if provided
        if self.objective_scorer and not isinstance(self.objective_scorer, TrueFalseScorer):
            raise ValueError("Objective scorer must be a TrueFalseScorer")

        # Enforce refusal scorer type: must be a TrueFalseScorer if provided
        if self.refusal_scorer and not isinstance(self.refusal_scorer, TrueFalseScorer):
            raise ValueError("Refusal scorer must be a TrueFalseScorer")


@dataclass
class AttackConverterConfig(StrategyConverterConfig):
    """
    Configuration for converters used in attacks.

    This class defines the converter configurations that transform prompts
    during the attack process, both for requests and responses.
    """
