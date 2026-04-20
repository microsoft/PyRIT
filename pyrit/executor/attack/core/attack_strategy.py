# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import dataclasses
import logging
import time
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, overload

from pyrit.common.logger import logger
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
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
    AttackOutcome,
    AttackResult,
    ConversationReference,
    Message,
)
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
    _next_message_override: Optional[Message] = None
    _prepended_conversation_override: Optional[List[Message]] = None
    _memory_labels_override: Optional[Dict[str, str]] = None

    # Convenience properties that delegate to params or overrides
    @property
    def objective(self) -> str:
        """Natural-language description of what the attack tries to achieve."""
        return self.params.objective

    @property
    def memory_labels(self) -> Dict[str, str]:
        """Additional labels that can be applied to the prompts throughout the attack."""
        # Check override first (for attacks that merge labels)
        if self._memory_labels_override is not None:
            return self._memory_labels_override
        return self.params.memory_labels or {}

    @memory_labels.setter
    def memory_labels(self, value: Dict[str, str]) -> None:
        """Set the memory labels (for attacks that merge strategy + context labels)."""
        self._memory_labels_override = value

    @property
    def prepended_conversation(self) -> List[Message]:
        """Conversation that is automatically prepended to the target model."""
        # Check override first (for attacks that generate internally)
        if self._prepended_conversation_override is not None:
            return self._prepended_conversation_override
        # Then check params
        if hasattr(self.params, "prepended_conversation") and self.params.prepended_conversation:
            return self.params.prepended_conversation
        return []

    @prepended_conversation.setter
    def prepended_conversation(self, value: List[Message]) -> None:
        """Set the prepended conversation (for attacks that generate internally)."""
        self._prepended_conversation_override = value

    @property
    def next_message(self) -> Optional[Message]:
        """Optional message to send to the objective target."""
        # Check override first (for attacks that generate internally)
        if self._next_message_override is not None:
            return self._next_message_override
        # Then check params
        if hasattr(self.params, "next_message"):
            return self.params.next_message
        return None

    @next_message.setter
    def next_message(self, value: Optional[Message]) -> None:
        """Set the next message (for attacks that generate internally)."""
        self._next_message_override = value


class _DefaultAttackStrategyEventHandler(StrategyEventHandler[AttackStrategyContextT, AttackStrategyResultT]):
    """
    Default event handler for attack strategies.
    Handles events during the execution of an attack strategy.
    """

    def __init__(self, logger: logging.Logger = logger):
        """
        Initialize the default event handler with a logger.

        Args:
            logger (logging.Logger): Logger instance for logging events.
        """
        self._logger = logger
        self._events = {
            StrategyEvent.ON_PRE_EXECUTE: self._on_pre_execute,
            StrategyEvent.ON_POST_EXECUTE: self._on_post_execute,
        }
        self._memory = CentralMemory.get_memory_instance()

    async def on_event(self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]) -> None:
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
            await self._on(event_data)

    async def _on(self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]) -> None:
        """
        Handle specific events during the attack strategy execution.

        Args:
            event_data (StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]): The event data containing
                context and result.
        """
        self._logger.debug(f"Attack is in '{event_data.event.value}' stage for {self.__class__.__name__}")

    async def _on_pre_execute(
        self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]
    ) -> None:
        """
        Handle pre-execution logic before the attack strategy runs.

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

    async def _on_post_execute(
        self, event_data: StrategyEventData[AttackStrategyContextT, AttackStrategyResultT]
    ) -> None:
        """
        Handle post-execution logic after the attack strategy has run.

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

        self._logger.debug(f"Attack execution completed in {execution_time_ms}ms")

        self._log_attack_outcome(event_data.result)
        self._memory.add_attack_results_to_memory(attack_results=[event_data.result])

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
        else:
            message = f"{attack_name} did not achieve the objective. {reason}"

        self._logger.info(message)


class AttackStrategy(Strategy[AttackStrategyContextT, AttackStrategyResultT], ABC):
    """
    Abstract base class for attack strategies.
    Defines the interface for executing attacks and handling results.
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        context_type: type[AttackStrategyContextT],
        params_type: Type[AttackParamsT] = AttackParameters,  # type: ignore[assignment]
        logger: logging.Logger = logger,
    ):
        """
        Initialize the attack strategy with a specific context type and logger.

        Args:
            objective_target (PromptTarget): The target system to attack.
            context_type (type[AttackStrategyContextT]): The type of context this strategy operates on.
            params_type (Type[AttackParamsT]): The type of parameters this strategy accepts.
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
        self._objective_target = objective_target
        self._params_type = params_type

    @property
    def params_type(self) -> Type[AttackParameters]:
        """
        Get the parameters type for this attack strategy.

        Returns:
            Type[AttackParameters]: The parameters type this strategy accepts.
        """
        return self._params_type

    def get_objective_target(self) -> PromptTarget:
        """
        Get the objective target for this attack strategy.

        Returns:
            PromptTarget: The target system being attacked.
        """
        return self._objective_target

    def get_attack_scoring_config(self) -> Optional[AttackScoringConfig]:
        """
        Get the attack scoring configuration used by this strategy.

        Returns:
            Optional[AttackScoringConfig]: The scoring configuration, or None if not applicable.

        Note:
            Subclasses that use scoring should override this method to return their
            scoring configuration. The default implementation returns None.
        """
        return None

    @overload
    async def execute_async(
        self,
        *,
        objective: str,
        next_message: Optional[Message] = None,
        prepended_conversation: Optional[List[Message]] = None,
        memory_labels: Optional[dict[str, str]] = None,
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
            next_message (Optional[Message]): Message to send to the target.
            prepended_conversation (Optional[List[Message]]): Conversation to prepend.
            memory_labels (Optional[Dict[str, str]]): Memory labels for the attack context.
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
