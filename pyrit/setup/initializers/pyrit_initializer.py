# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base classes for PyRIT initialization system.

This module provides the abstract base class for all PyRIT initializers,
which are class-based alternatives to initialization scripts.
"""

import sys
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, Optional

from pyrit.common.apply_defaults import get_global_default_values


@dataclass(frozen=True)
class InitializerParameter:
    """
    Describes a parameter that an initializer accepts.

    Each parameter value is a list of strings, which works naturally with
    CLI (comma-separated), YAML (lists), and programmatic APIs.

    Args:
        name: The parameter name (used as key in the params dict).
        description: Human-readable description of the parameter.
        required: Whether the parameter must be provided. Defaults to False.
        default: Default value if not provided. Defaults to None.
    """

    name: str
    description: str
    required: bool = False
    default: Optional[list[str]] = None


class PyRITInitializer(ABC):
    """
    Abstract base class for PyRIT configuration initializers.

    PyRIT initializers provide a structured way to configure default values
    and global settings during PyRIT initialization. They replace the need for
    initialization scripts with type-safe, validated, and discoverable classes.

    Subclasses must implement `initialize_async`. Description is automatically
    derived from the class docstring. Initializers execute in the order they
    are provided — no explicit execution_order is needed.
    """

    def __init__(self) -> None:  # noqa: B027
        """Initialize the PyRIT initializer with no parameters."""
        self.params: dict[str, list[str]] = {}

    def set_params_from_args(self, *, args: dict[str, Any]) -> None:
        """
        Set params from a YAML-style args dictionary.

        Converts each value to a list of strings: lists are stringified element-wise,
        scalars are wrapped in a single-element list.

        Args:
            args: Dictionary of argument names to values (scalars or lists).
        """
        self.params = {k: [str(i) for i in v] if isinstance(v, list) else [str(v)] for k, v in args.items()}

    @property
    def name(self) -> str:
        """
        Deprecated. Use the class docstring for description instead.

        Returns:
            str: The class name.
        """
        from pyrit.common.deprecation import print_deprecation_message

        print_deprecation_message(
            old_item="PyRITInitializer.name",
            new_item="class docstring (used automatically for description)",
            removed_in="0.16.0",
        )
        return type(self).__name__

    @property
    def description(self) -> str:
        """
        Get a description of what this initializer configures.

        By default, extracts the description from the class docstring.
        Falls back to the class name if no docstring is available.

        Returns:
            str: A description of the configuration changes this initializer makes.
        """
        from pyrit.registry.base import ClassRegistryEntry

        return ClassRegistryEntry.description_from_docstring(self.__class__, fallback=type(self).__name__)

    @property
    def required_env_vars(self) -> list[str]:
        """
        Get list of required environment variables for this initializer.

        Override this property to specify which environment variables must be
        set for this initializer to work correctly.

        Returns:
            List[str]: List of required environment variable names. Defaults to empty list.
        """
        return []

    @property
    def execution_order(self) -> int:
        """
        Deprecated. Initializers now execute in the order they are listed.

        Returns:
            int: Always returns 1.
        """
        from pyrit.common.deprecation import print_deprecation_message

        print_deprecation_message(
            old_item="PyRITInitializer.execution_order",
            new_item="list ordering in configuration (initializers execute in listed order)",
            removed_in="0.16.0",
        )
        return 1

    @property
    def supported_parameters(self) -> list[InitializerParameter]:
        """
        Get the list of parameters this initializer accepts.

        Override this property to declare what parameters the initializer
        supports. Parameters are set on self.params before initialize_async() is called.

        Returns:
            list[InitializerParameter]: List of supported parameters. Defaults to empty list.
        """
        return []

    @abstractmethod
    async def initialize_async(self) -> None:
        """
        Execute the initialization logic asynchronously.

        This method should contain all the configuration logic, including
        calls to set_default_value() and set_global_variable() as needed.
        All initializers must implement this as an async method.

        Subclasses that accept parameters should read them from self.params,
        which is populated before this method is called.
        """

    def validate(self) -> None:
        """
        Validate the initializer configuration before execution.

        This method checks that all required environment variables are set
        and validates any configured parameters against supported_parameters.
        Subclasses should not override this method.

        Raises:
            ValueError: If required environment variables are not set or
                if configured parameters are invalid.
        """
        import os

        missing_vars = [var for var in self.required_env_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(
                f"Initializer '{type(self).__name__}' requires the following environment variables to be set: "
                f"{', '.join(missing_vars)}"
            )

        # Validate configured params
        if self.params:
            self._validate_params(params=self.params)

    def _validate_params(self, *, params: dict[str, list[str]]) -> None:
        """
        Validate parameters against supported_parameters.

        Checks that all provided params are declared in supported_parameters
        and that all required params are present.

        Args:
            params: The parameters to validate.

        Raises:
            ValueError: If unknown parameters are provided or required parameters are missing.
        """
        supported = {p.name: p for p in self.supported_parameters}
        supported_names = set(supported.keys())

        # Check for unknown params
        unknown = set(params.keys()) - supported_names
        if unknown:
            raise ValueError(
                f"Initializer '{type(self).__name__}' received unknown parameter(s): {', '.join(sorted(unknown))}. "
                f"Supported parameters: {', '.join(sorted(supported_names)) if supported_names else 'none'}"
            )

        # Check for missing required params
        for param_def in self.supported_parameters:
            if param_def.required and param_def.name not in params:
                raise ValueError(
                    f"Initializer '{type(self).__name__}' requires parameter "
                    f"'{param_def.name}': {param_def.description}"
                )

    async def initialize_with_tracking_async(self) -> None:
        """
        Execute initialization while tracking what changes are made.

        This method runs initialize_async() and captures information about what
        default values and global variables were set. The tracking information
        is not cached - it's captured during the actual initialization run.
        """
        with self._track_initialization_changes():
            await self.initialize_async()

    @contextmanager
    def _track_initialization_changes(self) -> Iterator[dict[str, Any]]:
        """
        Context manager to track what changes during initialization.

        Yields:
            Dict containing tracking info that gets populated during initialization.
        """
        # Capture current state - only track the keys, not the values themselves
        default_values_registry = get_global_default_values()
        current_default_keys = set(default_values_registry._default_values.keys())
        current_main_dict = dict(sys.modules["__main__"].__dict__)

        # Initialize tracking dict
        tracking_info: dict[str, list[str]] = {"default_values": [], "global_variables": []}

        try:
            yield tracking_info
        finally:
            # After initialization, capture what changed
            new_defaults = default_values_registry._default_values
            new_main_dict = sys.modules["__main__"].__dict__

            # Track default values that were added - just collect class.parameter pairs
            for scope in new_defaults:
                if scope not in current_default_keys:
                    class_param = f"{scope.class_type.__name__}.{scope.parameter_name}"
                    if class_param not in tracking_info["default_values"]:
                        tracking_info["default_values"].append(class_param)

            # Track global variables that were added - just collect the variable names
            for name in new_main_dict:
                if name not in current_main_dict and name not in tracking_info["global_variables"]:
                    tracking_info["global_variables"].append(name)

    async def get_dynamic_default_values_info_async(self) -> dict[str, Any]:
        """
        Get information about what default values and global variables this initializer sets.
        This is useful for debugging what default_values are set by an initializer.

        Performs a sandbox run in isolation to discover what would be configured,
        then restores the original state. This works regardless of whether the
        initializer has been run before or which instance is queried.

        Returns:
            Dict[str, Any]: Information about what defaults and globals are set.
        """
        # Check if memory is initialized - required for running initialization in sandbox
        from pyrit.memory import CentralMemory

        try:
            CentralMemory.get_memory_instance()
        except ValueError:
            # Memory is not initialized - return helpful message
            return {
                "default_values": "Call await initialize_pyrit_async() first to see what this initializer configures",
                "global_variables": "Call await initialize_pyrit_async() first to see what this initializer configures",
            }

        # Capture current state for restoration (before try block so finally can access)
        default_values_registry = get_global_default_values()
        original_main_keys = set(sys.modules["__main__"].__dict__.keys())

        # First, clear any existing values that this initializer might have already set
        # This ensures we get accurate tracking even if initialize() was called before
        temp_backup_defaults = {}
        temp_backup_globals = {}

        # Temporarily remove defaults and globals to start fresh for tracking
        for scope_key in list(default_values_registry._default_values.keys()):
            temp_backup_defaults[scope_key] = default_values_registry._default_values[scope_key]
            del default_values_registry._default_values[scope_key]

        for var_name in list(sys.modules["__main__"].__dict__.keys()):
            if not var_name.startswith("_"):  # Keep system variables
                temp_backup_globals[var_name] = sys.modules["__main__"].__dict__[var_name]
                del sys.modules["__main__"].__dict__[var_name]

        try:
            # Run initialization in sandbox with tracking (starting from empty state)
            with self._track_initialization_changes() as tracking_info:
                await self.initialize_async()

            return tracking_info

        except Exception as e:
            return {
                "default_values": f"Error getting defaults info: {str(e)}",
                "global_variables": f"Error getting globals info: {str(e)}",
            }
        finally:
            # Restore original state completely
            # First clear everything that was added
            current_default_keys = set(default_values_registry._default_values.keys())
            for scope_key in current_default_keys:
                if scope_key in default_values_registry._default_values:
                    del default_values_registry._default_values[scope_key]

            current_main_keys = set(sys.modules["__main__"].__dict__.keys())
            for var_name in list(current_main_keys):
                if (
                    (var_name in temp_backup_globals or var_name in original_main_keys)
                    and var_name in sys.modules["__main__"].__dict__
                    and not var_name.startswith("_")
                ):
                    with suppress(KeyError):
                        del sys.modules["__main__"].__dict__[var_name]

            # Then restore what was there originally
            for scope_key, value in temp_backup_defaults.items():
                default_values_registry._default_values[scope_key] = value

            for var_name, value in temp_backup_globals.items():
                sys.modules["__main__"].__dict__[var_name] = value

    @classmethod
    async def get_info_async(cls) -> dict[str, Any]:
        """
        Get information about this initializer class.

        This is a class method so it can be called without instantiating the class:
        await SimpleInitializer.get_info_async() instead of SimpleInitializer().get_info_async()

        Returns:
            Dict[str, Any]: Dictionary containing name, description, class information, and default values.
        """
        # Create a temporary instance to access properties
        instance = cls()

        base_info = {
            "description": instance.description,
            "class": cls.__name__,
        }

        # Add supported parameters if any are declared
        if instance.supported_parameters:
            base_info["supported_parameters"] = [  # type: ignore[ty:invalid-assignment]
                {
                    "name": p.name,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                }
                for p in instance.supported_parameters
            ]

        # Add required environment variables if any are defined
        if instance.required_env_vars:
            base_info["required_env_vars"] = instance.required_env_vars  # type: ignore[ty:invalid-assignment]

        # Add dynamic default values information
        try:
            defaults_info = await instance.get_dynamic_default_values_info_async()
            base_info["default_values"] = defaults_info["default_values"]
            base_info["global_variables"] = defaults_info["global_variables"]
        except Exception as e:
            # If info fails, add error info but don't crash
            base_info["default_values"] = f"Error getting defaults info: {str(e)}"
            base_info["global_variables"] = f"Error getting globals info: {str(e)}"

        return base_info
