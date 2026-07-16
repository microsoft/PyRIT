# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging  # noqa: TC003
from abc import ABC
from dataclasses import dataclass
from typing import TypeVar

from pyrit.common.logger import logger
from pyrit.executor.core.strategy import (
    Strategy,
    StrategyContext,
    StrategyEvent,
    StrategyEventData,
    StrategyEventHandler,
)
from pyrit.models import StrategyResult

WorkflowContextT = TypeVar("WorkflowContextT", bound="WorkflowContext")
WorkflowResultT = TypeVar("WorkflowResultT", bound="WorkflowResult")


@dataclass
class WorkflowContext(StrategyContext, ABC):
    """Base class for all workflow contexts."""


class WorkflowResult(StrategyResult, ABC):
    """Base class for all workflow results."""


class _DefaultWorkflowEventHandler(StrategyEventHandler[WorkflowContextT, WorkflowResultT]):
    """
    Default event handler for workflow strategies.
    Handles events during the execution of a workflow strategy.
    """

    def __init__(self, logger: logging.Logger = logger) -> None:
        """
        Initialize the default event handler with a logger.

        Args:
            logger (logging.Logger): Logger instance for logging events.
        """
        self._logger = logger
        self._events = {
            StrategyEvent.ON_PRE_VALIDATE: self._on_pre_validate_async,
            StrategyEvent.ON_POST_VALIDATE: self._on_post_validate_async,
            StrategyEvent.ON_PRE_SETUP: self._on_pre_setup_async,
            StrategyEvent.ON_POST_SETUP: self._on_post_setup_async,
            StrategyEvent.ON_PRE_EXECUTE: self._on_pre_execute_async,
            StrategyEvent.ON_POST_EXECUTE: self._on_post_execute_async,
            StrategyEvent.ON_PRE_TEARDOWN: self._on_pre_teardown_async,
            StrategyEvent.ON_POST_TEARDOWN: self._on_post_teardown_async,
            StrategyEvent.ON_ERROR: self._on_error_async,
        }

    async def on_event_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        """
        Handle an event during the workflow strategy execution.

        Args:
            event_data: The event data containing context and result.
        """
        if event_data.event in self._events:
            handler = self._events[event_data.event]
            await handler(event_data)

    async def _on_pre_validate_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.debug(f"Starting validation for workflow {event_data.strategy_name}")

    async def _on_post_validate_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.debug(f"Validation completed for workflow {event_data.strategy_name}")

    async def _on_pre_setup_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.debug(f"Starting setup for workflow {event_data.strategy_name}")

    async def _on_post_setup_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.debug(f"Setup completed for workflow {event_data.strategy_name}")

    async def _on_pre_execute_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.info(f"Starting execution of workflow {event_data.strategy_name}")

    async def _on_post_execute_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.info(f"Workflow {event_data.strategy_name} completed.")

    async def _on_pre_teardown_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.debug(f"Starting teardown for workflow {event_data.strategy_name}")

    async def _on_post_teardown_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.debug(f"Teardown completed for workflow {event_data.strategy_name}")

    async def _on_error_async(self, event_data: StrategyEventData[WorkflowContextT, WorkflowResultT]) -> None:
        self._logger.error(
            f"Error in workflow {event_data.strategy_name}: {event_data.error}", exc_info=event_data.error
        )


class WorkflowStrategy(Strategy[WorkflowContextT, WorkflowResultT], ABC):
    """
    Abstract base class for workflow strategies.
    Defines the interface for executing workflows and handling results.
    """

    def __init__(
        self,
        *,
        context_type: type[WorkflowContextT],
        logger: logging.Logger = logger,
        event_handler: StrategyEventHandler[WorkflowContextT, WorkflowResultT] | None = None,
    ) -> None:
        """
        Initialize the workflow strategy with a specific context type and logger.

        Args:
            context_type: The type of context this strategy operates on.
            logger: Logger instance for logging events.
            event_handler: Optional custom event handler for workflow events.
        """
        default_handler = _DefaultWorkflowEventHandler[WorkflowContextT, WorkflowResultT](logger=logger)
        super().__init__(
            context_type=context_type,
            event_handler=event_handler or default_handler,
            logger=logger,
        )
