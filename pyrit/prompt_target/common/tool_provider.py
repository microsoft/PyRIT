# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import inspect
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, get_type_hints, runtime_checkable

from pydantic import BaseModel, ConfigDict, create_model

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from contextlib import AbstractAsyncContextManager


class Tool(ABC):
    """A tool that a target can advertise and execute."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, object],
        strict: bool = False,
    ) -> None:
        """
        Initialize a tool.

        Args:
            name: The tool name advertised to the model.
            description: A description of what the tool does.
            parameters: The JSON Schema for the tool arguments.
            strict: Whether the model must strictly follow the parameter schema.

        Raises:
            ValueError: If the tool name is invalid.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError(f"Tool name must match [A-Za-z0-9_-]{{1,64}}: {name!r}")

        self.name = name
        self.description = description
        self.parameters = parameters
        self.strict = strict

    @abstractmethod
    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        """
        Execute the tool.

        Args:
            arguments: Arguments supplied by the model.

        Returns:
            The JSON-serializable tool result.
        """


class FunctionTool(Tool):
    """A tool defined by an async Python function."""

    def __init__(
        self,
        *,
        function: Callable[..., Awaitable[object]],
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """
        Create a tool from an async Python function.

        Args:
            function: The function to advertise and execute.
            name: An optional advertised name. Defaults to the function name.
            description: An optional description. Defaults to the function docstring.

        Raises:
            TypeError: If the function is not async or has unsupported or untyped parameters.
        """
        if not inspect.iscoroutinefunction(function):
            raise TypeError("Tool functions must be async")

        function_name = getattr(function, "__name__", None)
        if not isinstance(function_name, str):
            raise TypeError("Tool functions must have a name")

        try:
            type_hints = get_type_hints(function, include_extras=True)
        except NameError as exc:
            raise TypeError(
                "Tool function annotations must be resolvable from the function's module. "
                "Move locally defined annotation types to module scope."
            ) from exc
        fields: dict[str, Any] = {}
        for parameter in inspect.signature(function).parameters.values():
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                raise TypeError(f"Tool function parameter '{parameter.name}' must accept a keyword argument")
            annotation = type_hints.get(parameter.name, parameter.annotation)
            if annotation is inspect.Parameter.empty:
                raise TypeError(f"Tool function parameter '{parameter.name}' must have a type annotation")
            default = ... if parameter.default is inspect.Parameter.empty else parameter.default
            fields[parameter.name] = (annotation, default)

        argument_model = create_model(
            f"{function_name}Arguments",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
        parameters = argument_model.model_json_schema()
        parameters.pop("title", None)

        super().__init__(
            name=name or function_name,
            description=description if description is not None else inspect.cleandoc(function.__doc__ or ""),
            parameters=parameters,
        )
        self._function = function
        self._argument_model: type[BaseModel] = argument_model

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        """
        Validate arguments and call the Python function.

        Args:
            arguments: Arguments supplied by the model.

        Returns:
            The function result.
        """
        validated_arguments = self._argument_model.model_validate(arguments)
        return await self._function(
            **{field_name: getattr(validated_arguments, field_name) for field_name in self._argument_model.model_fields}
        )


def tool(function: Callable[..., Awaitable[object]]) -> FunctionTool:
    """
    Convert an async Python function into an executable tool.

    Args:
        function: The function to advertise and execute.

    Returns:
        A tool whose schema is derived from the function signature.
    """
    return FunctionTool(function=function)


class ToolProvider(Protocol):
    """Discover executable tools for a target."""

    @property
    def identifier(self) -> dict[str, object]:
        """Non-secret provider configuration that affects behavior."""
        ...

    async def get_tools_async(self) -> list[Tool]:
        """Discover executable tools."""
        ...


@runtime_checkable
class _ScopedToolProvider(Protocol):
    """A provider with resources scoped to one target send."""

    def execution_scope_async(self) -> AbstractAsyncContextManager[None]:
        """Open the provider resources used during one target send."""
        ...


async def collect_tools_async(
    *,
    tools: Sequence[Tool] | None = None,
    providers: Sequence[ToolProvider] | None = None,
) -> list[Tool]:
    """
    Combine direct tools with tools discovered sequentially from providers.

    Args:
        tools: Tools available without discovery.
        providers: Providers whose tools should be discovered.

    Returns:
        The combined tools in configuration and discovery order.

    Raises:
        ValueError: If more than one tool has the same name.
    """
    collected_tools: list[Tool] = list(tools) if tools is not None else []
    if providers is not None:
        for provider in providers:
            collected_tools.extend(await provider.get_tools_async())

    names: set[str] = set()
    for collected_tool in collected_tools:
        if collected_tool.name in names:
            raise ValueError(f"Duplicate tool name: {collected_tool.name}")
        names.add(collected_tool.name)

    return collected_tools
