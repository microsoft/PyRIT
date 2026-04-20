# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Context management for enhanced exception and retry logging in PyRIT.

This module provides a contextvar-based system for tracking which component
(objective_target, adversarial_chat, objective_scorer, etc.) is currently
executing, allowing retry decorators and exception handlers to include
meaningful context in their messages.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from pyrit.identifiers.component_identifier import ComponentIdentifier


class ComponentRole(Enum):
    """
    Identifies the role of a component within an attack execution.

    This enum is used to provide meaningful context in error messages and retry logs,
    helping users identify which part of an attack encountered an issue.
    """

    # Core attack components
    OBJECTIVE_TARGET = "objective_target"
    ADVERSARIAL_CHAT = "adversarial_chat"

    # Scoring components
    OBJECTIVE_SCORER = "objective_scorer"
    OBJECTIVE_SCORER_TARGET = "objective_scorer_target"
    REFUSAL_SCORER = "refusal_scorer"
    REFUSAL_SCORER_TARGET = "refusal_scorer_target"
    AUXILIARY_SCORER = "auxiliary_scorer"
    AUXILIARY_SCORER_TARGET = "auxiliary_scorer_target"

    # Conversion components
    CONVERTER = "converter"
    CONVERTER_TARGET = "converter_target"

    # Other components
    UNKNOWN = "unknown"


@dataclass
class ExecutionContext:
    """
    Holds context information about the currently executing component.

    This context is used to enrich error messages and retry logs with
    information about which component failed and its configuration.
    """

    # The role of the component (e.g., objective_scorer, adversarial_chat)
    component_role: ComponentRole = ComponentRole.UNKNOWN

    # The attack strategy class name (e.g., "PromptSendingAttack")
    attack_strategy_name: Optional[str] = None

    # The identifier for the attack strategy
    attack_identifier: Optional[ComponentIdentifier] = None

    # The identifier from the component's get_identifier() (target, scorer, etc.)
    component_identifier: Optional[ComponentIdentifier] = None

    # The objective target conversation ID if available
    objective_target_conversation_id: Optional[str] = None

    # The endpoint/URI if available (extracted from component_identifier for quick access)
    endpoint: Optional[str] = None

    # The component class name (extracted from component_identifier.__type__ for quick access)
    component_name: Optional[str] = None

    # The attack objective if available
    objective: Optional[str] = None

    def get_retry_context_string(self) -> str:
        """
        Generate a concise context string for retry log messages.

        Returns:
            str: A formatted string with component role, component name, and endpoint.

        """
        parts = [self.component_role.value]
        if self.component_name:
            parts.append(f"({self.component_name})")
        if self.endpoint:
            parts.append(f"endpoint: {self.endpoint}")
        return " ".join(parts)

    def get_exception_details(self) -> str:
        """
        Generate detailed exception context for error messages.

        Returns:
            str: A multi-line formatted string with full context details.

        """
        lines = []

        if self.attack_strategy_name:
            lines.append(f"Attack: {self.attack_strategy_name}")

        lines.append(f"Component: {self.component_role.value}")

        if self.objective:
            # Normalize to single line and truncate to 120 characters
            objective_single_line = " ".join(self.objective.split())
            if len(objective_single_line) > 120:
                objective_single_line = objective_single_line[:117] + "..."
            lines.append(f"Objective: {objective_single_line}")

        if self.objective_target_conversation_id:
            lines.append(f"Objective target conversation ID: {self.objective_target_conversation_id}")

        if self.attack_identifier:
            lines.append(f"Attack identifier: {self.attack_identifier}")

        if self.component_identifier:
            lines.append(f"{self.component_role.value} identifier: {self.component_identifier}")
            model_name = self.component_identifier.params.get("model_name")
            endpoint = self.component_identifier.params.get("endpoint")
            if model_name:
                lines.append(f"Model: {model_name}")
            if endpoint:
                lines.append(f"Endpoint: {endpoint}")

        return "\n".join(lines)


# The contextvar that stores the current execution context
_execution_context: ContextVar[Optional[ExecutionContext]] = ContextVar("execution_context", default=None)


def get_execution_context() -> Optional[ExecutionContext]:
    """
    Get the current execution context.

    Returns:
        Optional[ExecutionContext]: The current context, or None if not set.

    """
    return _execution_context.get()


def set_execution_context(context: ExecutionContext) -> None:
    """
    Set the current execution context.

    Args:
        context: The execution context to set.

    """
    _execution_context.set(context)


def clear_execution_context() -> None:
    """Clear the current execution context."""
    _execution_context.set(None)


@dataclass
class ExecutionContextManager:
    """
    A context manager for setting execution context during component operations.

    This class provides a convenient way to set and automatically clear
    execution context when entering and exiting a code block.

    On successful exit, the context is restored to its previous value.
    On exception, the context is preserved so exception handlers can access it.
    """

    context: ExecutionContext
    _token: Any = field(default=None, init=False, repr=False)

    def __enter__(self) -> "ExecutionContextManager":
        """
        Set the execution context on entry.

        Returns:
            ExecutionContextManager: The active context manager instance.

        """
        self._token = _execution_context.set(self.context)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Restore the previous context on exit.

        If an exception occurred, the context is preserved so that exception
        handlers higher in the call stack can access it for enhanced error messages.

        Args:
            exc_type (Any): Exception type if one was raised, otherwise None.
            exc_val (Any): Exception value if one was raised, otherwise None.
            exc_tb (Any): Traceback object if an exception was raised, otherwise None.

        """
        if exc_type is None:
            # No exception - restore previous context
            _execution_context.reset(self._token)
        # On exception, leave context in place for exception handlers to read


def execution_context(
    *,
    component_role: ComponentRole,
    attack_strategy_name: Optional[str] = None,
    attack_identifier: Optional[ComponentIdentifier] = None,
    component_identifier: Optional[ComponentIdentifier] = None,
    objective_target_conversation_id: Optional[str] = None,
    objective: Optional[str] = None,
) -> ExecutionContextManager:
    """
    Create an execution context manager with the specified parameters.

    Args:
        component_role: The role of the component being executed.
        attack_strategy_name: The name of the attack strategy class.
        attack_identifier: The attack identifier.
        component_identifier: The identifier from component.get_identifier().
        objective_target_conversation_id: The objective target conversation ID if available.
        objective: The attack objective if available.

    Returns:
        ExecutionContextManager: A context manager that sets/clears the context.

    """
    # Extract endpoint and component_name from component_identifier if available
    endpoint = None
    component_name = None
    if component_identifier:
        endpoint = component_identifier.params.get("endpoint")
        component_name = component_identifier.class_name

    context = ExecutionContext(
        component_role=component_role,
        attack_strategy_name=attack_strategy_name,
        attack_identifier=attack_identifier,
        component_identifier=component_identifier,
        objective_target_conversation_id=objective_target_conversation_id,
        endpoint=endpoint,
        component_name=component_name,
        objective=objective,
    )
    return ExecutionContextManager(context=context)
