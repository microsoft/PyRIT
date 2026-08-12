# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator

    from pyrit.executor.attack.multi_turn.tree_of_attacks import _TreeOfAttacksNode


class _TreeOfAttacksNodeExecutor:
    """Execute independent tree nodes with bounded concurrency."""

    def __init__(
        self,
        *,
        batch_size: int,
        logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """
        Initialize the node executor.

        Args:
            batch_size (int): Maximum number of nodes to execute concurrently.
            logger (logging.Logger | logging.LoggerAdapter[logging.Logger]): Logger
                used for execution progress.
        """
        self._batch_size = batch_size
        self._logger = logger

    async def execute_nodes_async(
        self,
        *,
        nodes: list[_TreeOfAttacksNode],
        objective: str,
    ) -> AsyncIterator[tuple[int, list[_TreeOfAttacksNode]]]:
        """
        Execute nodes in ordered batches and yield each completed batch.

        Node instances own all branch-specific mutable state. This executor only
        schedules their existing execution protocol, so failures and cancellation
        retain ``asyncio.gather`` semantics.

        Args:
            nodes (list[_TreeOfAttacksNode]): Nodes to execute.
            objective (str): Objective passed to every node.

        Yields:
            tuple[int, list[_TreeOfAttacksNode]]: The batch start offset and nodes
                after every node in that batch has completed.
        """
        for batch_start in range(0, len(nodes), self._batch_size):
            batch_nodes = nodes[batch_start : batch_start + self._batch_size]
            self._log_batch_start(batch_start=batch_start, batch_nodes=batch_nodes, total_nodes=len(nodes))

            await asyncio.gather(*(node.send_prompt_async(objective=objective) for node in batch_nodes))

            yield batch_start, batch_nodes

    def _log_batch_start(
        self,
        *,
        batch_start: int,
        batch_nodes: list[_TreeOfAttacksNode],
        total_nodes: int,
    ) -> None:
        """Log the batch and node dispatch order."""
        batch_end = batch_start + len(batch_nodes)
        self._logger.debug(
            f"Processing batch {batch_start // self._batch_size + 1} "
            f"(nodes {batch_start + 1}-{batch_end} of {total_nodes})"
        )
        for node_index in range(batch_start + 1, batch_end + 1):
            self._logger.debug(f"Preparing prompt for node {node_index}/{total_nodes}")
