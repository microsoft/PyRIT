# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base classes for PyRIT initialization system.

This module provides the abstract base class for all PyRIT initializers,
which are class-based alternatives to initialization scripts.
"""

import sys
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager, suppress
from typing import Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, get_global_default_values
from pyrit.models.parameter import Parameter


class PyRITInitializer(ABC):
    """
    Abstract base class for PyRIT configuration initializers.

    PyRIT initializers provide a structured way to configure default values
    and global settings during PyRIT initialization. They replace the need for
    initialization scripts with type-safe, validated, and discoverable classes.

    Subclasses must implement `initialize_async`. Description is automatically
    derived from the class docstring. Initializers execute in the order they
    are provided — no explicit execution_order is needed.

    Construction prepares defaults without requiring runtime inputs. Configuration
    resolves supplied values once; validation checks the bag without replacing it.
    """

    def __init__(self) -> None:
        """Initialize the PyRIT initializer with no parameters."""
        from pyrit.registry.resolution import resolve_declared_params

        self.params: dict[str, Any] = resolve_declared_params(
            declared=self.supported_parameters(),
            raw_args={},
            owner=f"Initializer '{type(self).__name__}'",
            allow_missing_required=True,
        )

    def set_params_from_args(self, *, args: dict[str, Any]) -> None:
        """
        Resolve typed parameters and defaults from YAML or CLI arguments.

        Args:
            args: Dictionary of argument names to values (scalars or lists).

        Raises:
            ValueError: If a parameter value or declaration is invalid.
        """
        from pyrit.registry.resolution import resolve_declared_params

        declared = self.supported_parameters()
        normalized = dict(args)
        for parameter in declared:
            value = normalized.get(parameter.name)
            if value is None or value is REQUIRED_VALUE:
                continue
            if parameter.is_list and not isinstance(value, list):
                normalized[parameter.name] = [value]
            elif parameter.is_string_coercible and isinstance(value, list):
                if len(value) != 1:
                    raise ValueError(
                        f"Initializer '{type(self).__name__}' parameter '{parameter.name}' expects exactly one value."
                    )
                normalized[parameter.name] = value[0]
        self.params = resolve_declared_params(
            declared=declared, raw_args=normalized, owner=f"Initializer '{type(self).__name__}'"
        )

    @property
    def description(self) -> str:
        """
        A description of what this initializer configures.

        By default, extracts the description from the class docstring.
        Falls back to the class name if no docstring is available.

        Returns:
            str: A description of the configuration changes this initializer makes.
        """
        from pyrit.registry.registry_metadata import RegistryMetadata

        return RegistryMetadata.description_from_docstring(self.__class__, fallback=type(self).__name__)

    @property
    def required_env_vars(self) -> list[str]:
        """
        Required environment variables for this initializer.

        Override this property to specify which environment variables must be
        set for this initializer to work correctly.

        Returns:
            list[str]: List of required environment variable names. Defaults to empty list.
        """
        return []

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare the parameters this initializer accepts.

        Override this classmethod to declare what parameters the initializer
        supports. Parameters are set on self.params before initialize_async() is called.

        Returns:
            list[Parameter]: List of supported parameters. Defaults to empty list.
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

        self.validate_params()

    def validate_params(self) -> None:
        """
        Validate the configured parameters against supported_parameters.

        Checks required inputs, names, and resolved types without changing the bag.
        Unlike ``validate()``, this does not check
        required environment variables, so it can be used to fail fast on
        parameter shape at configuration time — before the environment is set up.

        Raises:
            ValueError: If parameters or their declarations are invalid.
        """
        from pyrit.registry.resolution import validate_declared_params

        validate_declared_params(
            declared=self.supported_parameters(), params=self.params, owner=f"Initializer '{type(self).__name__}'"
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
    def _track_initialization_changes(self) -> Generator[dict[str, Any], None, None]:
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
            dict[str, Any]: Information about what defaults and globals are set.
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
        await TargetInitializer.get_info_async() instead of TargetInitializer().get_info_async()

        Returns:
            dict[str, Any]: Dictionary containing name, description, class information, and default values.
        """
        # Create a temporary instance to access properties
        instance = cls()

        base_info = {
            "description": instance.description,
            "class": cls.__name__,
        }

        # Add supported parameters if any are declared
        if cls.supported_parameters():
            base_info["supported_parameters"] = [
                {
                    "name": p.name,
                    "description": p.description,
                    "default": p.default,
                }
                for p in cls.supported_parameters()
            ]

        # Add required environment variables if any are defined
        if instance.required_env_vars:
            base_info["required_env_vars"] = instance.required_env_vars

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
