# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Default value decorator system for PyRIT.

This module provides decorators and utilities for applying default values to class constructors.
It's designed to work with PyRIT's initialization system but is kept in common to avoid circular imports.
"""

import functools
import inspect
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
_APPLY_DEFAULTS_ATTRIBUTE = "__pyrit_apply_defaults__"


class _RequiredValueSentinel:
    """
    Sentinel type to mark parameters as required but eligible for apply_defaults.

    This allows parameters to have no default value in the function signature
    (making them appear required), while still allowing apply_defaults to provide
    a value if one is registered globally.

    Usage:
        @apply_defaults
        def __init__(self, *, objective_target: PromptTarget = REQUIRED_VALUE):
            # If apply_defaults finds a default for objective_target, it will be used.
            # Otherwise, an error will be raised for missing required parameter.
            pass
    """

    def __repr__(self) -> str:
        return "REQUIRED_VALUE"

    def __bool__(self) -> bool:
        # Ensure this evaluates to False in boolean context
        return False


# Global sentinel instance
REQUIRED_VALUE = _RequiredValueSentinel()


@dataclass(frozen=True)
class DefaultValueScope:
    """
    Represents a scope for default values with class type, parameter name, and inheritance rules.

    This class defines the scope where a default value applies, including whether it should
    be inherited by subclasses.
    """

    class_type: type[object]
    parameter_name: str
    include_subclasses: bool = True

    def __hash__(self) -> int:
        """
        Return a hash based on class type, parameter name, and subclass inclusion flag.

        Returns:
            int: Hash value for this scope.
        """
        return hash((self.class_type, self.parameter_name, self.include_subclasses))


class GlobalDefaultValues:
    """
    Global registry for default values that can be applied to class constructors.

    This singleton class maintains a registry of default values that can be automatically
    applied to class parameters when using the @apply_defaults decorator.
    """

    def __init__(self) -> None:
        """Initialize the global default values registry."""
        self._default_values: dict[DefaultValueScope, Any] = {}

    def set_default_value(
        self,
        *,
        class_type: type[object],
        parameter_name: str,
        value: Any,
        include_subclasses: bool = True,
    ) -> None:
        """
        Set a default value for a specific class and parameter.

        Args:
            class_type: The class type for which to set the default.
            parameter_name: The name of the parameter to set the default for.
            value: The default value to set.
            include_subclasses: Whether this default should apply to subclasses as well.
        """
        scope = DefaultValueScope(
            class_type=class_type,
            parameter_name=parameter_name,
            include_subclasses=include_subclasses,
        )
        # A re-registration under the opposite flag replaces the previous one
        # entirely; keeping both would leave the older value reachable (the
        # lookup checks the include_subclasses=True scope first) and subclasses
        # stuck inheriting it.
        opposite_scope = DefaultValueScope(
            class_type=class_type,
            parameter_name=parameter_name,
            include_subclasses=not include_subclasses,
        )
        self._default_values.pop(opposite_scope, None)
        self._default_values[scope] = value
        logger.debug(f"Set default value for {class_type.__name__}.{parameter_name} = {value}")

    def get_default_value(
        self,
        *,
        class_type: type[object],
        parameter_name: str,
    ) -> tuple[bool, Any]:
        """
        Get the default value for a specific class and parameter.

        Args:
            class_type: The class type to get the default for.
            parameter_name: The name of the parameter to get the default for.

        Returns:
            Tuple of (found, value) where found indicates if a default was found.
        """
        # First, try exact match for both registration flags. A default
        # registered with include_subclasses=False must still apply to the
        # registered class itself - the flag only controls whether subclasses
        # inherit the default.
        for include_subclasses in (True, False):
            scope = DefaultValueScope(
                class_type=class_type,
                parameter_name=parameter_name,
                include_subclasses=include_subclasses,
            )
            if scope in self._default_values:
                return True, self._default_values[scope]

        # Then, check parent classes if include_subclasses is True
        for existing_scope, value in self._default_values.items():
            if (
                existing_scope.parameter_name == parameter_name
                and existing_scope.include_subclasses
                and issubclass(class_type, existing_scope.class_type)
            ):
                return True, value

        return False, None

    def reset_defaults(self) -> None:
        """Reset all default values."""
        self._default_values.clear()
        logger.debug("Reset all default values")

    @property
    def all_defaults(self) -> dict[DefaultValueScope, Any]:
        """A copy of all current default values."""
        return self._default_values.copy()


# Global instance
_global_default_values = GlobalDefaultValues()


def get_global_default_values() -> GlobalDefaultValues:
    """
    Get the global default values registry.

    Returns:
        GlobalDefaultValues: The global default values registry instance.
    """
    return _global_default_values


def set_default_value(
    *,
    class_type: type[object],
    parameter_name: str,
    value: Any,
    include_subclasses: bool = True,
) -> None:
    """
    Set a default value for a specific class and parameter.

    This is a convenience function that delegates to the global default values registry.

    Args:
        class_type: The class type for which to set the default.
        parameter_name: The name of the parameter to set the default for.
        value: The default value to set.
        include_subclasses: Whether this default should apply to subclasses as well.
    """
    _global_default_values.set_default_value(
        class_type=class_type,
        parameter_name=parameter_name,
        value=value,
        include_subclasses=include_subclasses,
    )


def reset_default_values() -> None:
    """Reset all default values in the global registry."""
    _global_default_values.reset_defaults()


def set_global_variable(*, name: str, value: Any) -> None:
    """
    Explicitly sets a global variable in the __main__ module namespace.

    This function provides an alternative to relying on naming conventions for variable exposure.
    Instead of using underscore-prefixed variables that may or may not be exposed based on
    the expose_private_vars parameter, this function explicitly sets variables in the global
    namespace, making the intent clear and the behavior predictable.

    Args:
        name (str): The name of the global variable to set.
        value (Any): The value to assign to the global variable.

    Example:
        # Instead of relying on naming conventions:
        # _helper_config = SomeConfig(...)  # May not be exposed
        # global_config = _helper_config    # Exposed globally

        # Use explicit global variable setting:
        helper_config = SomeConfig(...)
        set_global_variable(name="global_config", value=helper_config)

    Note:
        This function directly modifies the __main__ module's namespace, making the
        variable accessible to code that imports or executes after the initialization
        script runs.
    """
    # Set the variable in the __main__ module's global namespace
    sys.modules["__main__"].__dict__[name] = value


def apply_defaults_to_method(method: Callable[..., T]) -> Callable[..., T]:
    """
    Apply default values to a method's parameters.

    This decorator looks up default values for the method's class and applies them
    to parameters that are None or not provided.

    Args:
        method: The method to decorate (typically __init__).

    Returns:
        The decorated method.
    """

    @functools.wraps(method)
    def wrapper(self: object, *args: object, **kwargs: object) -> T:
        # Get the class of the instance
        cls = self.__class__

        # Get method signature
        sig = inspect.signature(method)

        # Bind arguments to get parameter names and values
        bound_args = sig.bind(self, *args, **kwargs)
        bound_args.apply_defaults()

        _apply_default_arguments(class_type=cls, signature=sig, arguments=bound_args.arguments)

        # Call the original method with updated arguments
        return method(*bound_args.args, **bound_args.kwargs)

    setattr(wrapper, _APPLY_DEFAULTS_ATTRIBUTE, True)
    return wrapper


def resolve_constructor_defaults(
    *, class_type: type[object], arguments: dict[str, Any], excluded_parameters: set[str]
) -> dict[str, Any]:
    """
    Resolve effective constructor defaults for deferred validation without constructing an instance.

    Only decorated constructors use global defaults. Excluded execution inputs are
    neither materialized nor looked up. The caller's arguments remain unchanged.

    Args:
        class_type (type[object]): The class whose constructor is validated.
        arguments (dict[str, Any]): Supplied constructor arguments.
        excluded_parameters (set[str]): Execution inputs to leave unresolved.

    Returns:
        dict[str, Any]: Supplied values and effective non-execution defaults.

    Raises:
        ValueError: If a required default placeholder cannot be resolved.
    """
    signature = inspect.signature(class_type.__init__)
    resolved = {
        name: parameter.default
        for name, parameter in signature.parameters.items()
        if name != "self" and name not in excluded_parameters and parameter.default is not inspect.Parameter.empty
    }
    resolved.update({name: value for name, value in arguments.items() if name not in excluded_parameters})
    if getattr(class_type.__init__, _APPLY_DEFAULTS_ATTRIBUTE, False):
        _apply_default_arguments(class_type=class_type, signature=signature, arguments=resolved)
    return resolved


def _apply_default_arguments(
    *, class_type: type[object], signature: inspect.Signature, arguments: dict[str, Any]
) -> None:
    """
    Apply the decorator's global-default rules to an already bound argument bag.

    Args:
        class_type (type[object]): The concrete class used for default lookup.
        signature (inspect.Signature): The decorated method's signature.
        arguments (dict[str, Any]): Bound arguments to update in place.

    Raises:
        ValueError: If a required default placeholder cannot be resolved.
    """
    for name, value in arguments.items():
        if name == "self" or (value is not None and not isinstance(value, _RequiredValueSentinel)):
            continue
        found, default = _global_default_values.get_default_value(class_type=class_type, parameter_name=name)
        if found:
            arguments[name] = default
            logger.debug(f"Applied default value for {class_type.__name__}.{name} = {default}")
        elif isinstance(value, _RequiredValueSentinel):
            raise ValueError(
                f"{name} is required for {class_type.__name__}. "
                "Either pass the parameter explicitly or register a default using set_default_value()."
            )
        elif (parameter := signature.parameters.get(name)) and isinstance(parameter.default, _RequiredValueSentinel):
            raise ValueError(
                f"{name} is required for {class_type.__name__}. "
                "Either pass a valid value or register a default using set_default_value()."
            )


def apply_defaults(method: Callable[..., T]) -> Callable[..., T]:
    """
    Apply default values to a class constructor.

    This is an alias for apply_defaults_to_method for backward compatibility.

    Args:
        method: The method to decorate (typically __init__).

    Returns:
        The decorated method.
    """
    return apply_defaults_to_method(method)
