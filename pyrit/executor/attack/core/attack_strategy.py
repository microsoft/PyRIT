# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import dataclasses
import logging  # noqa: TC003
import time
import traceback
import uuid
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar, overload

from pyrit.common.logger import logger
from pyrit.exceptions.retry_collector import (
    get_retry_collector,
)
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.core import (
    Strategy,
    StrategyContext,
    StrategyEvent,
    StrategyEventData,
    StrategyEventHandler,
)
from pyrit.memory.central_memory import CentralMemory
from pyrit.models import (
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConverterIdentifier,
    Identifiable,
    Message,
    ScorerIdentifier,
    SeedPrompt,
    TargetIdentifier,
)
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackScoringConfig,
    )
    from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
    from pyrit.prompt_target import PromptTarget

AttackStrategyContextT = TypeVar("AttackStrategyContextT", bound="AttackContext[Any]")
AttackStrategyResultT = TypeVar("AttackStrategyResultT", bound="AttackResult")


@dataclass
class AttackContext(StrategyContext, ABC, Generic[AttackParamsT]):
    """
    Base class for all attack contexts.

    This class holds both the immutable attack parameters and the mutable
    execution state. The params field contains caller-provided inputs,
    while other fields track execution progress.

    Attacks that generate certain values internally (e.g., RolePlayAttack generates
    next_message and prepended_conversation) can set the mutable override fields
    (_next_message_override, _prepended_conversation_override) during _setup_async.
    """

    # Immutable parameters from the caller
    params: AttackParamsT

    # Start time of the attack execution
    start_time: float = 0.0

    # Conversations relevant while the attack is running
    related_conversations: set[ConversationReference] = field(default_factory=set)

    # Mutable overrides for attacks that generate these values internally
    _next_message_override: Message | None = None
    _prepended_conversation_override: list[Message] | None = None
    _memory_labels_override: dict[str, str] | None = None

    # Optional attribution from an upstream orchestrator (e.g. Scenario). When
    # set, the persistence path stamps attribution_parent_id + attribution_data
    # onto the resulting AttackResult so it can be located later for hydration
    # and resume. Set by AttackExecutor per-task before scheduling. Stays None
    # for ad-hoc/direct attack execution outside any orchestrator.
    _attribution: AttackResultAttribution | None = None

    # Convenience properties that delegate to params or overrides
    @property
    def objective(self) -> str:
        """Natural-language description of what the attack tries to achieve."""
        return self.params.objective

    @property
    def memory_labels(self) -> dict[str, str]:
        """Additional labels that can be applied to the prompts throughout the attack."""
        # Check override first (for attacks that merge labels)
        if self._memory_labels_override is not None:
            return self._memory_labels_override
        return self.params.memory_labels or {}

    @memory_labels.setter
    def memory_labels(self, value: dict[str, str]) -> None:
        """Set the memory labels (for attacks that merge strategy + context labels)."""
        self._memory_labels_override = value

    @property
    def prepended_conversation(self) -> list[Message]:
        """Conversation that is automatically prepended to the target model."""
        # Check override first (for attacks that generate internally)
        if self._prepended_conversation_override is not None:
            return self._prepended_conversation_override
        # Then check params
        if hasattr(self.params, "prepended_conversation") and self.params.prepended_conversation:
            return self.params.prepended_conversation
        return []

    @prepended_conversation.setter
    def prepended_conversation(self, value: list[Message]) -> None:
        """Set the prepended conversation (for attacks that generate internally)."""
        self._prepended_conversation_override = value

    @property
    def next_message(self) -> Message | None:
        """Optional message to send to the objective target."""
        # Check override first (for attacks that generate internally)
        if self._next_message_override is not None:
            return self._next_message_override
        # Then check params
        if hasattr(self.params, "next_message"):
            return self.params.next_message
        return None

    @next_message.setter
    def next_message(self, value: Message | None) -> None:
        """Set the next message (for attacks that generate internally)."""
        self._next_message_override = value


class _DefaultAttackStrategyEventHandler(StrategyEventHandler[AttackStrategyContextT, AttackStrategyResultT]):
    """
    Default event handler for attack strategies.
    Handles events during the execution of an attack strategy.
    """

    def __init__(self, logger: logging.Logger = logger) -> None:
        """
        Initialize the default event handler with a logger.

        Args:
            logger (logging.Logger): Logger instance for logging events.
        """
        self._logger = logger
        self._events = {
            StrategyEvent.ON_PRE_EXECUTE: self._on_pre_execute_async,
            StrategyEvent.ON_POST_EXECUTE: self._on_post_execute_async,
            StrategyEvent.ON_ERROR: self._on_error_async,
        }
        self._memory = CentralMemory.get_memory_instance()

    async def on_event_async(
        self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]
    ) -> None:
        """
        Handle an event during the attack strategy execution.

        Args:
            event_data (StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]): The event data containing
                context and result.
        """
        if event_data.event in self._events:
            handler = self._events[event_data.event]
            await handler(event_data)
        else:
            await self._on_async(event_data)

    async def _on_async(self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]) -> None:
        """
        Handle specific events during the attack strategy execution.

        Args:
            event_data (StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]): The event data containing
                context and result.
        """
        self._logger.debug(f"Attack is in '{event_data.event.value}' stage for {self.__class__.__name__}")

    async def _on_pre_execute_async(
        self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]
    ) -> None:
        """
        Handle pre-execution logic before the attack strategy runs.

        Sets up execution timing and starts a RetryCollector to capture
        retry events during execution.

        Args:
            event_data (StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]): The event data containing
                context and result.

        Raises:
            ValueError: If the attack context is None.
        """
        if not event_data.context:
            raise ValueError("Attack context is None. Cannot proceed with execution.")

        # Initialize start time for execution
        event_data.context.start_time = time.perf_counter()

        # Log the start of the attack
        self._logger.info(f"Starting attack: {event_data.context.objective}")

    async def _on_post_execute_async(
        self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]
    ) -> None:
        """
        Handle post-execution logic after the attack strategy has run.

        Attaches retry events to the result and persists it to memory.

        Args:
            event_data (StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]): The event data containing
                context and result.

        Raises:
            ValueError: If the attack result is None.
        """
        if not event_data.result:
            raise ValueError("Attack result is None. Cannot log or record the outcome.")

        end_time = time.perf_counter()
        execution_time_ms = int((end_time - event_data.context.start_time) * 1000)
        event_data.result.execution_time_ms = execution_time_ms

        # Attach collected retry events to the result
        collector = get_retry_collector()
        if collector and collector.events:
            event_data.result.retry_events = collector.events
            event_data.result.total_retries = len(collector.events)

        # Stamp attribution onto the result before persistence so the
        # AttackResultEntry row records its lineage. Outside an orchestrator
        # _attribution is None and both attribution fields stay None.
        self._apply_attribution(context=event_data.context, result=event_data.result)
        self._apply_targeted_harm_categories(context=event_data.context, result=event_data.result)

        self._logger.debug(f"Attack execution completed in {execution_time_ms}ms")

        self._log_attack_outcome(event_data.result)
        self._memory.add_attack_results_to_memory(attack_results=[event_data.result])

    @staticmethod
    def _apply_attribution(
        *,
        context: AttackStrategyContextT,
        result: AttackResult,
    ) -> None:
        """
        Copy attribution from the AttackContext onto the AttackResult.

        Reads ``context._attribution`` (an ``AttackResultAttribution`` set by
        the AttackExecutor when an upstream orchestrator supplied a factory).
        When present, writes ``attribution_parent_id`` and a fixed-schema
        ``attribution_data`` dict onto the result so they round-trip into
        ``AttackResultEntry``.

        Args:
            context: The per-task AttackContext.
            result: The AttackResult that is about to be persisted.
        """
        attribution = context._attribution
        if attribution is None:
            return
        result.attribution_parent_id = attribution.parent_id
        attribution_data: dict[str, Any] = {
            "parent_collection": attribution.parent_collection,
        }
        if attribution.parent_eval_hash is not None:
            attribution_data["parent_eval_hash"] = attribution.parent_eval_hash
        result.attribution_data = attribution_data

    @staticmethod
    def _apply_targeted_harm_categories(
        *,
        context: AttackStrategyContextT,
        result: AttackResult,
    ) -> None:
        """
        Copy the attack's targeted harm categories from its parameters onto the result.

        Reads ``context.params.targeted_harm_categories`` (populated in
        ``AttackParameters.from_seed_group_async`` from the SeedGroup's
        deduplicated harm categories) and stamps it onto the result so it
        round-trips into ``AttackResultEntry``. The read is defensive because
        some ``AttackParameters`` subclasses may exclude the field.

        Args:
            context: The per-task AttackContext.
            result: The AttackResult that is about to be persisted.
        """
        params = getattr(context, "params", None)
        harm_categories = getattr(params, "targeted_harm_categories", None)
        if harm_categories:
            result.targeted_harm_categories = list(harm_categories)

    def _log_attack_outcome(self, result: AttackResult) -> None:
        """
        Log the outcome of the attack.

        Args:
            result (AttackResult): The result of the attack containing outcome and reason.
        """
        attack_name = self.__class__.__name__
        reason = f"Reason: {result.outcome_reason or 'Not specified'}"

        if result.outcome == AttackOutcome.SUCCESS:
            message = f"{attack_name} achieved the objective. {reason}"
        elif result.outcome == AttackOutcome.UNDETERMINED:
            message = f"{attack_name} outcome is undetermined. {reason}"
        elif result.outcome == AttackOutcome.ERROR:
            message = f"{attack_name} failed with an error. {reason}"
        else:
            message = f"{attack_name} did not achieve the objective. {reason}"

        self._logger.info(message)

    async def _on_error_async(
        self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]
    ) -> None:
        """
        Handle error during attack execution.

        Creates an error AttackResult with error details and any retry events
        collected during execution, then persists it to memory.

        Args:
            event_data (StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]): The event data containing
                context, result, and error.
        """
        error = event_data.error
        context = event_data.context
        if not error or not context:
            return

        # Collect retry events (visible via inherited ContextVar copy)
        collector = get_retry_collector()
        retry_events = collector.events if collector else []

        # Build a conversation_id — use context's if available, otherwise generate one
        conversation_id = getattr(context, "conversation_id", None) or str(uuid.uuid4())

        error_result = AttackResult(
            conversation_id=conversation_id,
            objective=context.objective,
            outcome=AttackOutcome.ERROR,
            outcome_reason=f"Exception: {type(error).__name__}: {str(error)}",
            labels=context.memory_labels,
            related_conversations=context.related_conversations,
            error_message=str(error),
            error_type=type(error).__name__,
            error_traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)),
            retry_events=retry_events,
            total_retries=len(retry_events),
        )

        end_time = time.perf_counter()
        if context.start_time:
            error_result.execution_time_ms = int((end_time - context.start_time) * 1000)

        # Stamp attribution onto the error result so it is locatable via the
        # attribution_parent_id foreign key on resume.
        self._apply_attribution(context=context, result=error_result)
        self._apply_targeted_harm_categories(context=context, result=error_result)

        self._memory.add_attack_results_to_memory(attack_results=[error_result])

        self._logger.error(f"Attack failed with {type(error).__name__}: {error}")


class AttackStrategy(Strategy[AttackStrategyContextT, AttackStrategyResultT], Identifiable, ABC):
    """
    Abstract base class for attack strategies.
    Defines the interface for executing attacks and handling results.

    Subclasses must use the keyword-only constructor shape
    (``def __init__(self, *, ...)``); the contract is enforced at class
    definition time via ``enforce_keyword_only_init``. See
    ``.github/instructions/attacks.instructions.md`` for the full contract.
    """

    #: Capability requirements placed on ``objective_target``. Subclasses
    #: override to declare what the attack needs. Validated in ``__init__``.
    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Enforce the keyword-only constructor contract on subclasses.

        See ``.github/instructions/attacks.instructions.md`` for the contract.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="AttackStrategy")

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        context_type: type[AttackStrategyContextT],
        params_type: type[AttackParamsT] = AttackParameters,  # type: ignore[ty:invalid-parameter-default]
        logger: logging.Logger = logger,
    ) -> None:
        """
        Initialize the attack strategy with a specific context type and logger.

        Args:
            objective_target (PromptTarget): The target system to attack.
            context_type (type[AttackStrategyContextT]): The type of context this strategy operates on.
            params_type (type[AttackParamsT]): The type of parameters this strategy accepts.
                Defaults to AttackParameters. Use AttackParameters.excluding() to create
                a params type that rejects certain fields.
            logger (logging.Logger): Logger instance for logging events.
        """
        super().__init__(
            context_type=context_type,
            event_handler=_DefaultAttackStrategyEventHandler[AttackStrategyContextT, AttackStrategyResultT](
                logger=logger
            ),
            logger=logger,
        )
        type(self).TARGET_REQUIREMENTS.validate(target=objective_target)
        self._objective_target = objective_target
        self._params_type = params_type
        # Guard so subclasses that set converters before calling super() aren't clobbered
        if not hasattr(self, "_request_converters"):
            self._request_converters: list[Any] = []
        if not hasattr(self, "_response_converters"):
            self._response_converters: list[Any] = []

    def _create_identifier(
        self,
        *,
        params: dict[str, Any] | None = None,
        children: dict[str, ComponentIdentifier | list[ComponentIdentifier]] | None = None,
    ) -> ComponentIdentifier:
        """
        Construct the attack strategy identifier.

        Builds a ComponentIdentifier with the objective target, optional scorer,
        and converter pipeline as children. Subclasses can extend by passing
        additional params or children.

        Args:
            params (dict[str, Any] | None): Additional behavioral parameters from
                the subclass.
            children (dict[str, ComponentIdentifier | list[ComponentIdentifier]] | None):
                Named child component identifiers.

        Returns:
            ComponentIdentifier: The identifier for this attack strategy.
        """
        all_children: dict[str, ComponentIdentifier | list[ComponentIdentifier]] = dict(children) if children else {}
        merged_params: dict[str, Any] = dict(params) if params else {}

        objective_target = TargetIdentifier.from_component_identifier(self.get_objective_target().get_identifier())

        # Add scorer if present
        objective_scorer: ScorerIdentifier | None = None
        scoring_config = self.get_attack_scoring_config()
        if scoring_config and scoring_config.objective_scorer:
            objective_scorer = ScorerIdentifier.from_component_identifier(
                scoring_config.objective_scorer.get_identifier()
            )

        # Add adversarial chat target and its effective prompts if present. The adversarial
        # target becomes a child (filtered to model params by the eval rule), while the
        # effective system/seed prompts land on the attack-strategy node so they are included
        # in both the full component hash and the eval hash. None-valued promoted fields are
        # dropped by ComponentIdentifier.of, so strategies that do not use a given prompt
        # simply omit it.
        adversarial_chat: TargetIdentifier | None = None
        adversarial_system_prompt: str | None = None
        adversarial_seed_prompt: str | None = None
        adversarial_config = self.get_attack_adversarial_config()
        if adversarial_config is not None and getattr(adversarial_config, "target", None) is not None:
            adversarial_chat = TargetIdentifier.from_component_identifier(adversarial_config.target.get_identifier())
            adversarial_system_prompt = self._extract_adversarial_prompt_text(adversarial_config.system_prompt)
            adversarial_seed_prompt = self._extract_adversarial_prompt_text(adversarial_config.first_message)

        # Add request converter identifiers if present
        request_converters: list[ConverterIdentifier] | None = None
        if self._request_converters:
            request_converters = [
                ConverterIdentifier.from_component_identifier(converter.get_identifier())
                for config in self._request_converters
                for converter in config.converters
            ]

        # Add response converter identifiers if present
        response_converters: list[ConverterIdentifier] | None = None
        if self._response_converters:
            response_converters = [
                ConverterIdentifier.from_component_identifier(converter.get_identifier())
                for config in self._response_converters
                for converter in config.converters
            ]

        return AttackIdentifier.of(
            self,
            params=merged_params or None,
            children=all_children or None,
            objective_target=objective_target,
            adversarial_chat=adversarial_chat,
            objective_scorer=objective_scorer,
            request_converters=request_converters,
            response_converters=response_converters,
            adversarial_system_prompt=adversarial_system_prompt,
            adversarial_seed_prompt=adversarial_seed_prompt,
        )

    @staticmethod
    def _extract_adversarial_prompt_text(value: str | SeedPrompt | None) -> str | None:
        """
        Extract a stable text representation of an adversarial prompt for identity.

        Args:
            value: The adversarial system or seed prompt (string, SeedPrompt, or None).

        Returns:
            The prompt text, or None when no prompt is set.
        """
        if value is None:
            return None
        if isinstance(value, SeedPrompt):
            return value.value
        return value

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this attack strategy.

        Subclasses can override this method to call _create_identifier() with
        their specific params and children.

        Returns:
            ComponentIdentifier: The identifier for this attack strategy.
        """
        return self._create_identifier()

    @property
    def params_type(self) -> type[AttackParameters]:
        """
        The parameters type for this attack strategy.

        Returns:
            type[AttackParameters]: The parameters type this strategy accepts.
        """
        return self._params_type

    def get_objective_target(self) -> PromptTarget:
        """
        Get the objective target for this attack strategy.

        Returns:
            PromptTarget: The target system being attacked.
        """
        return self._objective_target

    def get_attack_scoring_config(self) -> AttackScoringConfig | None:
        """
        Get the attack scoring configuration used by this strategy.

        Returns:
            AttackScoringConfig | None: The scoring configuration, or None if not applicable.

        Note:
            Subclasses that use scoring should override this method to return their
            scoring configuration. The default implementation returns None.
        """
        return None

    def get_attack_adversarial_config(self) -> AttackAdversarialConfig | None:
        """
        Get the attack adversarial configuration used by this strategy.

        Returns:
            AttackAdversarialConfig | None: The adversarial configuration, or None if not applicable.

        Note:
            Subclasses that use an adversarial chat target should override this method to return
            the effective adversarial configuration (resolved target plus the system/seed prompts
            actually used), so the adversarial target and prompts are reflected in the attack
            identity. The default implementation returns None.
        """
        return None

    def get_request_converters(self) -> list[Any]:
        """
        Get request converter configurations used by this strategy.

        Returns:
            list[Any]: The list of request ConverterConfiguration objects.
        """
        return self._request_converters

    @overload
    async def execute_async(
        self,
        *,
        objective: str,
        next_message: Message | None = None,
        prepended_conversation: list[Message] | None = None,
        memory_labels: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> AttackStrategyResultT: ...

    @overload
    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AttackStrategyResultT: ...

    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AttackStrategyResultT:
        """
        Execute the attack strategy asynchronously with the provided parameters.

        This method provides a stable contract for all attacks. The signature includes
        all standard parameters (objective, next_message, prepended_conversation, memory_labels).
        Attacks that don't accept certain parameters will raise ValueError if those
        parameters are provided.

        Args:
            objective (str): The objective of the attack.
            next_message (Message | None): Message to send to the target.
            prepended_conversation (list[Message] | None): Conversation to prepend.
            memory_labels (dict[str, str] | None): Memory labels for the attack context.
            **kwargs: Additional context-specific parameters (conversation_id, system_prompt, etc.).

        Returns:
            AttackStrategyResultT: The result of the attack execution.

        Raises:
            ValueError: If required parameters are missing or if unsupported parameters are provided.
        """
        # Get valid field names for params and context
        params_fields = {f.name for f in dataclasses.fields(self._params_type)}
        context_fields = {f.name for f in dataclasses.fields(self._context_type)} - {"params"}

        # Separate kwargs into params kwargs and context kwargs
        params_kwargs = {}
        context_kwargs = {}
        unknown_fields = set()

        for k, v in kwargs.items():
            if v is None:
                continue  # Skip None values
            if k in params_fields:
                params_kwargs[k] = v
            elif k in context_fields:
                context_kwargs[k] = v
            else:
                unknown_fields.add(k)

        # Validate no unknown fields
        if unknown_fields:
            raise ValueError(
                f"{self.__class__.__name__} does not accept parameters: {unknown_fields}. "
                f"Accepted attack parameters: {params_fields}. "
                f"Accepted context parameters: {context_fields}"
            )

        # Validate objective is provided
        if "objective" not in params_kwargs:
            raise ValueError("objective is required")

        # Construct params instance
        params = self._params_type(**params_kwargs)

        # Create context with params and context-specific kwargs
        # Note: We use cast here because the type checker doesn't know that _context_type
        # (which is AttackContext or a subclass) always accepts 'params' as a keyword argument.
        context = self._context_type(params=params, **context_kwargs)

        return await self.execute_with_context_async(context=context)
