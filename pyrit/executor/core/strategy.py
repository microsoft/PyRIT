# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import ast
import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Dict, Generic, MutableMapping, Optional, TypeVar

from pyrit.common import default_values
from pyrit.common.logger import logger
from pyrit.models import StrategyResultT

StrategyContextT = TypeVar("StrategyContextT", bound="StrategyContext")


@dataclass
class StrategyContext(ABC):
    """Base class for all strategy contexts."""

    def duplicate(self: StrategyContextT) -> StrategyContextT:
        """
        Create a deep copy of the context.

        Returns:
            StrategyContext: A deep copy of the context.
        """
        return deepcopy(self)


class StrategyEvent(Enum):
    """Enumeration of all strategy lifecycle events."""

    # Strategy lifecycle events
    # Validation Events
    ON_PRE_VALIDATE = "on_pre_validate"
    ON_POST_VALIDATE = "on_post_validate"

    # Setup Events
    ON_PRE_SETUP = "on_pre_setup"
    ON_POST_SETUP = "on_post_setup"

    # Execution Events
    ON_PRE_EXECUTE = "on_pre_execute"
    ON_POST_EXECUTE = "on_post_execute"

    # Teardown Events
    ON_PRE_TEARDOWN = "on_pre_teardown"
    ON_POST_TEARDOWN = "on_post_teardown"

    # Error Handling Events
    ON_ERROR = "on_error"


@dataclass
class StrategyEventData(Generic[StrategyContextT, StrategyResultT]):
    """Data passed to event observers."""

    # The event that triggered this data
    event: StrategyEvent

    # Strategy information
    strategy_name: str
    strategy_id: str

    # Context and result of the strategy
    context: StrategyContextT
    result: Optional[StrategyResultT] = None

    # Optional error if the event is related to an error
    error: Optional[Exception] = None


class StrategyEventHandler(ABC, Generic[StrategyContextT, StrategyResultT]):
    """
    Abstract base class for strategy event handlers.

    Generic parameters:
        StrategyContextT: The specific context type (must be a subclass of StrategyContext)
        StrategyResultT: The specific result type (must be a subclass of StrategyResult)
    """

    @abstractmethod
    async def on_event(self, event_data: StrategyEventData[StrategyContextT, StrategyResultT]) -> None:
        """
        Handle a strategy event.

        Args:
            event_data: Data about the event that occurred.
        """
        pass


class StrategyLogAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """
    Custom logger adapter that adds strategy information to log messages.
    """

    _STRATEGY_NAME_KEY = "strategy_name"
    _STRATEGY_ID_KEY = "strategy_id"

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[str, MutableMapping[str, Any]]:
        """
        Add strategy context to each log message.

        Returns:
            tuple: The modified log message and keyword arguments.
        """
        if not self.extra:
            return msg, kwargs

        # Extract strategy information from extra
        strategy_name = self.extra.get(self._STRATEGY_NAME_KEY)
        strategy_id = self.extra.get(self._STRATEGY_ID_KEY)

        if strategy_name and strategy_id:
            strategy_info = f"[{strategy_name} (ID: {str(strategy_id)})]"
            return f"{strategy_info} {msg}", kwargs

        return msg, kwargs


class Strategy(ABC, Generic[StrategyContextT, StrategyResultT]):
    """
    Abstract base class for strategies with enforced lifecycle management.

    Ensures a consistent execution flow: validate -> setup -> execute -> teardown.
    The teardown phase is guaranteed to run even if exceptions occur.

    Subclasses must implement:
    _validate_context(): Validate context
    _setup_async(): Initialize resources
    _perform_async(): Execute the logic
    _teardown_async(): Clean up resources
    """

    def __init__(
        self,
        *,
        context_type: type[StrategyContextT],
        event_handler: Optional[StrategyEventHandler[StrategyContextT, StrategyResultT]] = None,
        logger: logging.Logger = logger,
    ):
        """
        Initialize the strategy with a context type and logger.

        Args:
            context_type (type[StrategyContextT]): The type of context this strategy will use.
            event_handler (Optional[StrategyEventHandler[StrategyContextT, StrategyResultT]]): An optional
                event handler for strategy events.
            logger (logging.Logger): The logger to use for this strategy.
        """
        self._id = uuid.uuid4()
        self._context_type = context_type
        self._event_handlers: Dict[str, StrategyEventHandler[StrategyContextT, StrategyResultT]] = {}

        if event_handler is not None:
            self._register_event_handler(event_handler)

        self._logger = StrategyLogAdapter(
            logger,
            {
                StrategyLogAdapter._STRATEGY_NAME_KEY: self.__class__.__name__,
                StrategyLogAdapter._STRATEGY_ID_KEY: str(self._id)[:8],
            },
        )
        self._memory_labels: Dict[str, str] = ast.literal_eval(
            default_values.get_non_required_value(env_var_name="GLOBAL_MEMORY_LABELS") or "{}"
        )

    def get_identifier(self) -> Dict[str, str]:
        """
        Get a serializable identifier for the strategy instance.

        Returns:
            dict: A dictionary containing the type, module, and unique ID of the strategy.
        """
        return {
            "__type__": self.__class__.__name__,
            "__module__": self.__class__.__module__,
            "id": str(self._id),
        }

    def _register_event_handler(self, event_handler: StrategyEventHandler[StrategyContextT, StrategyResultT]) -> None:
        """
        Register an event handler for strategy events.

        Args:
            event_handler: The event handler to register.
        """
        self._event_handlers[event_handler.__class__.__name__] = event_handler

    @abstractmethod
    def _validate_context(self, *, context: StrategyContextT) -> None:
        """
        Validate the strategy context before execution.
        This method should be implemented by subclasses to validate that the context
        is suitable for the strategy.

        Args:
            context (StrategyContextT): The context to validate.

        Raises:
            Exception: If the context is invalid for this strategy.
        """
        pass

    @abstractmethod
    async def _setup_async(self, *, context: StrategyContextT) -> None:
        """
        Set up the phase before executing the strategy.
        This method should be implemented by subclasses to prepare any necessary state or resources.
        This method is guaranteed to be called before the strategy execution and after context validation.

        Args:
            context (StrategyContextT): The context for the strategy.
        """
        pass

    @abstractmethod
    async def _perform_async(self, *, context: StrategyContextT) -> StrategyResultT:
        """
        Core implementation to be defined by subclasses.
        This contains the actual strategy logic that subclasses must implement.

        Args:
            context (StrategyContextT): The context for the strategy.

        Returns:
            StrategyResultT: The result of the strategy execution.
        """
        pass

    @abstractmethod
    async def _teardown_async(self, *, context: StrategyContextT) -> None:
        """
        Teardown phase after executing the strategy.
        This method should be implemented by subclasses to clean up any resources or state.
        This method is guaranteed to be called even if exceptions occur during execution.

        Args:
            context (StrategyContextT): The context for the strategy.
        """
        pass

    async def _handle_event(
        self,
        *,
        event: StrategyEvent,
        context: StrategyContextT,
        result: Optional[StrategyResultT] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """
        Handle a strategy event by notifying all registered event handlers.

        Args:
            event (StrategyEvent): The event that occurred.
            context (StrategyContextT): The context for the strategy.
            result (Optional[StrategyResultT]): The result of the strategy execution, if applicable.
            error (Optional[Exception]): An error that occurred during execution, if applicable.
        """
        event_data = StrategyEventData(
            event=event,
            strategy_name=self.__class__.__name__,
            strategy_id=str(self._id),
            context=context,
            result=result,
            error=error,
        )

        # Dispatch events to all handlers in parallel
        if self._event_handlers:
            tasks = [asyncio.create_task(handler.on_event(event_data)) for handler in self._event_handlers.values()]
            await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def _execution_context(self, context: StrategyContextT) -> AsyncIterator[None]:
        """
        Manage the complete lifecycle of a strategy execution as an async context manager.

        This method provides a context manager that ensures proper setup and teardown
        of strategy resources, regardless of whether the strategy completes successfully
        or encounters an error.

        Args:
            context (StrategyContextT): The execution context containing configuration and state for the strategy.

        Yields:
            None: Control is yielded back to the caller after setup is complete.
        """
        try:
            # Notify pre-setup event
            await self._handle_event(event=StrategyEvent.ON_PRE_SETUP, context=context)
            await self._setup_async(context=context)
            # Notify post-setup event
            await self._handle_event(event=StrategyEvent.ON_POST_SETUP, context=context)
            yield
        finally:
            # Notify pre-teardown event
            await self._handle_event(event=StrategyEvent.ON_PRE_TEARDOWN, context=context)
            await self._teardown_async(context=context)
            # Notify post-teardown event
            await self._handle_event(event=StrategyEvent.ON_POST_TEARDOWN, context=context)

    async def execute_with_context_async(self, *, context: StrategyContextT) -> StrategyResultT:
        """
        Execute strategy with complete lifecycle management.

        Enforces: validate -> setup -> execute -> teardown.

        Args:
            context (StrategyContextT): The context for the strategy, containing configuration and state.

        Returns:
            StrategyResultT: The result of the strategy execution, including outcome and reason.

        Raises:
            ValueError: If the context validation fails.
            RuntimeError: If the strategy execution fails.
        """
        # Validation phase
        # This is a critical step to ensure the context is suitable for the strategy
        try:
            # Notify pre-validation event
            await self._handle_event(event=StrategyEvent.ON_PRE_VALIDATE, context=context)
            self._validate_context(context=context)
            # Notify post-validation event
            await self._handle_event(event=StrategyEvent.ON_POST_VALIDATE, context=context)
        except Exception as e:
            raise ValueError(f"Strategy context validation failed for {self.__class__.__name__}: {str(e)}") from e

        # Execution with lifecycle management
        # This uses an async context manager to ensure setup and teardown are handled correctly
        try:
            async with self._execution_context(context):
                await self._handle_event(event=StrategyEvent.ON_PRE_EXECUTE, context=context)
                result = await self._perform_async(context=context)
                await self._handle_event(event=StrategyEvent.ON_POST_EXECUTE, context=context, result=result)
                return result
        except Exception as e:
            # Notify error event
            await self._handle_event(event=StrategyEvent.ON_ERROR, context=context, error=e)
            # Raise a specific execution error
            raise RuntimeError(f"Strategy execution failed for {self.__class__.__name__}: {str(e)}") from e

    async def execute_async(self, **kwargs: Any) -> StrategyResultT:
        """
        Execute the strategy asynchronously with the given keyword arguments.

        Returns:
            StrategyResultT: The result of the strategy execution.
        """
        context = self._context_type(**kwargs)
        return await self.execute_with_context_async(context=context)
