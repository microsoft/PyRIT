# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging  # noqa: TC003
from abc import ABC
from dataclasses import dataclass
from typing import Optional, TypeVar

from pyrit.common.logger import logger
from pyrit.executor.core.strategy import (
    Strategy,
    StrategyContext,
    StrategyEventData,
    StrategyEventHandler,
)
from pyrit.models import StrategyResult

PromptGeneratorStrategyContextT = TypeVar("PromptGeneratorStrategyContextT", bound="PromptGeneratorStrategyContext")
PromptGeneratorStrategyResultT = TypeVar("PromptGeneratorStrategyResultT", bound="PromptGeneratorStrategyResult")


@dataclass
class PromptGeneratorStrategyContext(StrategyContext, ABC):
    """Base class for all prompt generator strategy contexts."""


@dataclass
class PromptGeneratorStrategyResult(StrategyResult, ABC):
    """Base class for all prompt generator strategy results."""


class _DefaultPromptGeneratorStrategyEventHandler(
    StrategyEventHandler[PromptGeneratorStrategyContextT, PromptGeneratorStrategyResultT]
):
    """
    Default event handler for prompt generator strategies.
    Handles events during the execution of a prompt generator strategy.
    """

    def __init__(self, logger: logging.Logger = logger):
        """
        Initialize the default event handler with a logger.

        Args:
            logger (logging.Logger): Logger instance for logging events.
        """
        self._logger = logger

    async def on_event(
        self, event_data: StrategyEventData[PromptGeneratorStrategyContextT, PromptGeneratorStrategyResultT]
    ) -> None:
        """
        Handle an event during the execution of a prompt generator strategy.

        Args:
            event_data (StrategyEventData[PromptGeneratorStrategyContextT, PromptGeneratorStrategyResultT]):
                The event data containing context and result.
        """
        self._logger.debug(f"Prompt generator strategy is in '{event_data.event.value}' stage")


class PromptGeneratorStrategy(Strategy[PromptGeneratorStrategyContextT, PromptGeneratorStrategyResultT], ABC):
    """
    Base class for all prompt generator strategies.
    Provides a structure for implementing specific prompt generation strategies.
    """

    def __init__(
        self,
        context_type: type[PromptGeneratorStrategyContextT],
        logger: logging.Logger = logger,
        event_handler: Optional[
            StrategyEventHandler[PromptGeneratorStrategyContextT, PromptGeneratorStrategyResultT]
        ] = None,
    ):
        """
        Initialize the prompt generator strategy.

        Args:
            context_type (type): Type of the context used by the strategy.
            logger (logging.Logger): Logger instance for logging events.
            event_handler (StrategyEventHandler): Event handler for handling strategy events.
        """
        super().__init__(
            logger=logger,
            event_handler=event_handler or _DefaultPromptGeneratorStrategyEventHandler(logger=logger),
            context_type=context_type,
        )
