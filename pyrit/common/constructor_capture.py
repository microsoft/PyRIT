# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in capture of constructor inputs for component reconstruction."""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec

from pyrit.common.brick_contract import get_constructor_owners

_Params = ParamSpec("_Params")


def copy_constructor_inputs(value: object, *, memo: dict[int, object] | None = None) -> object:
    """
    Copy containers while retaining live component and client references.

    Returns:
        object: An independent container tree with unchanged leaf objects.
    """
    if memo is None:
        memo = {}
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, dict):
        copied_dict: dict[object, object] = {}
        memo[id(value)] = copied_dict
        copied_dict.update({key: copy_constructor_inputs(item, memo=memo) for key, item in value.items()})
        return copied_dict
    if isinstance(value, list):
        copied_list: list[object] = []
        memo[id(value)] = copied_list
        copied_list.extend(copy_constructor_inputs(item, memo=memo) for item in value)
        return copied_list
    if isinstance(value, tuple):
        copied_tuple = tuple(copy_constructor_inputs(item, memo=memo) for item in value)
        return memo.setdefault(id(value), copied_tuple)
    if isinstance(value, set):
        copied_set = {copy_constructor_inputs(item, memo=memo) for item in value}
        memo[id(value)] = copied_set
        return copied_set
    return value


def capture_constructor_parameters(init: Callable[_Params, None]) -> Callable[_Params, None]:
    """
    Retain inputs on the constructed object, not in a global container.

    Returns:
        Callable: A signature-preserving constructor wrapper.
    """
    signature = inspect.signature(init)

    @wraps(init)
    def captured(*args: _Params.args, **kwargs: _Params.kwargs) -> None:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        parameters: dict[str, object] = {}
        for key, value in bound.arguments.items():
            kind = signature.parameters[key].kind
            if kind is inspect.Parameter.VAR_KEYWORD:
                parameters.update({name: copy_constructor_inputs(item) for name, item in value.items()})
            elif key != "self" and kind is not inspect.Parameter.VAR_POSITIONAL:
                parameters[key] = copy_constructor_inputs(value)
        init(*args, **kwargs)
        instance = bound.arguments["self"]
        if type(instance).__init__ is captured:
            resolved = vars(instance).pop("_resolved_constructor_parameters", {})
            resolved_parameters: dict[str, object] = {}
            for owner in reversed(get_constructor_owners(type(instance))):
                constructor = inspect.unwrap(owner.__dict__["__init__"])
                for name, value in resolved.get(constructor, {}).items():
                    if value is not None or name not in resolved_parameters:
                        resolved_parameters[name] = value
            parameters.update(resolved_parameters)
            instance._reconstruction_parameters = parameters

    vars(captured)["__pyrit_capture_constructor__"] = True
    return captured
