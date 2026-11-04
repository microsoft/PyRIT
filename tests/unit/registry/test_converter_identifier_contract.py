# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Every converter constructor setting must reach the converter identifier, unless it is declared neutral."""

from __future__ import annotations

import copy
import enum
import inspect
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from functools import cache
from types import FunctionType, UnionType
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin
from unittest.mock import patch
from uuid import uuid4

import pytest

from pyrit.common import REQUIRED_VALUE
from pyrit.common.random_context import configure_random_seed, get_configured_random_seed
from pyrit.converter import Base64Converter, CodeAttackConverter, PolicyPuppetryTemplate
from pyrit.converter.text_selection_strategy import (
    IndexSelectionStrategy,
    TextSelectionStrategy,
    WordIndexSelectionStrategy,
    WordSelectionStrategy,
)
from pyrit.datasets import TextJailBreak
from pyrit.memory import CentralMemory
from pyrit.models import ComponentType, Parameter, SeedPrompt
from pyrit.registry.components import ConverterRegistry
from unit.mocks import MockPromptTarget
from unit.registry.converter_identifier_samples import (
    ALLOWED_VALUES_NEED,
    BASELINE_SETTINGS,
    KEYWORD_SETTINGS,
    KNOWN_GAPS,
    NEUTRAL_SETTINGS,
    SELECTION_STRATEGY_SETTINGS,
    SETTINGS_A_MODE_REPLACES,
    SETTINGS_THAT_NEED_ANOTHER,
    SINGLE_VALUE_SETTINGS,
    make_samples,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


_ROOT_SEED = 7
_COLOR_VALUES = 256
_PROCESS_TIMEOUT_SECONDS = 600

_STRATEGY_PARAMETERS = (
    ("SelectiveTextConverter", "selection_strategy", TextSelectionStrategy),
    ("ROT13Converter", "word_selection_strategy", WordSelectionStrategy),
)

_MISSING = object()


@cache
def _registry() -> ConverterRegistry:
    return ConverterRegistry()


@pytest.fixture(autouse=True)
def fixed_root_seed() -> Iterator[None]:
    previous_seed = get_configured_random_seed()
    configure_random_seed(seed=_ROOT_SEED)
    try:
        yield
    finally:
        configure_random_seed(seed=previous_seed)


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Any, ...]]:
    return make_samples(tmp_path_factory.mktemp("converter_contract"))


def _unwrap_optional(annotation: Any) -> Any:
    if get_origin(annotation) not in (Union, UnionType):
        return annotation
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    enums = [arg for arg in args if inspect.isclass(arg) and issubclass(arg, enum.Enum)]
    return (enums or args)[0]


def _changed(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, enum.Enum):
        others = [member for member in type(value) if member != value]
        return others[0] if others else _MISSING
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value / 2 if value else 0.5
    if isinstance(value, str):
        return value + next(char for char in "xyzqj" if char not in value)
    if isinstance(value, SeedPrompt):
        changed = copy.deepcopy(value)
        changed.value = f"{value.value}\nchanged"
        return changed
    if isinstance(value, (list, tuple)) and value:
        last = _changed(value[-1])
        return _MISSING if last is _MISSING else type(value)([*value[:-1], last])
    if isinstance(value, dict) and value:
        key = next(iter(value))
        changed = _changed(value[key])
        return _MISSING if changed is _MISSING else {**value, key: changed}
    return _MISSING


def _fresh(annotation: Any) -> Any:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)[0]
    if origin is tuple and get_args(annotation) and Ellipsis not in get_args(annotation):
        items = tuple(_fresh(arg) for arg in get_args(annotation))
        return _MISSING if _MISSING in items else items
    if origin in (list, tuple) and get_args(annotation):
        item = _fresh(get_args(annotation)[0])
        return _MISSING if item is _MISSING else origin([item])
    if origin is dict and len(get_args(annotation)) == 2:
        value = _fresh(get_args(annotation)[1])
        return _MISSING if value is _MISSING else {"key": value}
    if origin is None and inspect.isclass(annotation):
        if issubclass(annotation, WordSelectionStrategy):
            return WordIndexSelectionStrategy(indices=[0])
        if issubclass(annotation, TextSelectionStrategy):
            return IndexSelectionStrategy(start=0, end=3)
        if issubclass(annotation, enum.Enum):
            return next(iter(annotation))
        for kind, value in ((bool, False), (int, 1), (float, 0.5), (str, "value")):
            if issubclass(annotation, kind):
                return value
    return _MISSING


def _each_position_changed(value: list[Any] | tuple[Any, ...]) -> tuple[Any, ...] | None:
    items = [*value, _changed(value[-1])] if len(value) == 1 else list(value)
    changed = [(item + 1) % _COLOR_VALUES if isinstance(item, int) else _changed(item) for item in items]
    if _MISSING in (*items, *changed):
        return None
    kind = type(value)
    return (
        kind(items),
        *(kind([*items[:index], item, *items[index + 1 :]]) for index, item in enumerate(changed)),
    )


def _allowed_values(parameter: Parameter) -> tuple[Any, ...]:
    annotation = _unwrap_optional(parameter.param_type)
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        return tuple(annotation)
    return ()


def _generated_values(parameter: Parameter, default: Any) -> tuple[Any, ...] | None:
    annotation = _unwrap_optional(parameter.param_type)
    allowed = _allowed_values(parameter)
    if allowed:
        others = tuple(value for value in allowed if value != default)
        return others or None
    if inspect.isclass(annotation) and issubclass(annotation, WordSelectionStrategy):
        return WordIndexSelectionStrategy(indices=[0]), WordIndexSelectionStrategy(indices=[1])
    if inspect.isclass(annotation) and issubclass(annotation, TextSelectionStrategy):
        return IndexSelectionStrategy(start=0, end=3), IndexSelectionStrategy(start=1, end=4)
    start = default if default is not None else _fresh(annotation)
    if start is _MISSING:
        return None
    if isinstance(start, (list, tuple)) and start:
        return _each_position_changed(start)
    first = _changed(start) if default is not None else start
    if first is _MISSING:
        return None
    second = _changed(first)
    if second is _MISSING or second == default:
        return (first, default) if default is not None else None
    if isinstance(first, dict) and first:
        return first, second, {f"other_{key}": item for key, item in first.items()}
    return first, second


def _sample_values(
    owner: str, parameter: Parameter, default: Any, samples: dict[str, tuple[Any, ...]]
) -> tuple[Any, ...] | None:
    for key in (f"{owner}.{parameter.name}", parameter.name):
        if key in samples:
            return samples[key]
    values = _generated_values(parameter, default)
    takes_none = type(None) in get_args(parameter.param_type) and (owner, parameter.name) not in NEUTRAL_SETTINGS
    return (*values, None) if values and default is not None and takes_none else values


def _target(model_name: str) -> MockPromptTarget:
    target = MockPromptTarget()
    target._model_name = model_name
    return target


def _reference(parameter: Parameter, variant: int) -> Any:
    if parameter.reference is None:
        return _MISSING
    if parameter.reference.component_type is ComponentType.TARGET:
        return _target(f"model_{variant}")
    if parameter.reference.component_type is ComponentType.CONVERTER:
        converter = Base64Converter(encoding_func="b32encode") if variant else Base64Converter()
        return [converter] if parameter.is_list else converter
    return _MISSING


def _baseline(cls: type, parameters: list[Parameter], samples: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(BASELINE_SETTINGS.get(cls.__name__, {}))
    for parameter in parameters:
        if parameter.reference is not None:
            value = _reference(parameter, variant=0)
            if value is not _MISSING:
                kwargs[parameter.name] = value
        elif parameter.default is REQUIRED_VALUE:
            values = _sample_values(cls.__name__, parameter, default=None, samples=samples)
            if values is None:
                pytest.fail(f"{cls.__name__}.{parameter.name}: add a sample value")
            kwargs[parameter.name] = values[0]
    return kwargs


def _loaded_template(instance: object, parameter_name: str) -> SeedPrompt | None:
    named = [
        value for key, value in vars(instance).items() if isinstance(value, SeedPrompt) and key.endswith(parameter_name)
    ]
    return named[0] if len(named) == 1 else None


def _cases() -> list[Any]:
    registry = _registry()
    return [
        pytest.param(
            name,
            parameter.name,
            marks=pytest.mark.xfail(reason=KNOWN_GAPS[(name, parameter.name)], raises=AssertionError, strict=True),
        )
        if (name, parameter.name) in KNOWN_GAPS
        else (name, parameter.name)
        for name in registry.get_class_names()
        for parameter in registry.get_class_metadata(registry.get_class(name)).parameters
        if (name, parameter.name) not in NEUTRAL_SETTINGS
    ]


def _setup(
    converter_name: str, parameter_name: str, samples: dict[str, tuple[Any, ...]]
) -> tuple[type, dict[str, Any], tuple[Any, ...]]:
    registry = _registry()
    cls = registry.get_class(converter_name)
    parameters = list(registry.get_class_metadata(cls).parameters)
    parameter = next(item for item in parameters if item.name == parameter_name)
    kwargs = {
        **_baseline(cls, parameters, samples),
        **SETTINGS_THAT_NEED_ANOTHER.get((converter_name, parameter_name), {}),
    }
    if parameter.reference is not None:
        if _MISSING in (kwargs.get(parameter_name, _MISSING), _reference(parameter, 1)):
            pytest.fail(f"{converter_name}.{parameter_name}: add a reference sample")
        return cls, kwargs, (_reference(parameter, 1),)
    default = None if parameter.default is REQUIRED_VALUE else parameter.default
    if default is not None and parameter_name not in kwargs:
        kwargs = {**kwargs, parameter_name: default}
    annotation = _unwrap_optional(parameter.param_type)
    if (converter_name, parameter_name) in SINGLE_VALUE_SETTINGS:
        pytest.skip(SINGLE_VALUE_SETTINGS[(converter_name, parameter_name)])
    if get_origin(annotation) is Literal and len(get_args(annotation)) == 1:
        pytest.fail(f"{converter_name}.{parameter_name}: only one value is allowed; list it in SINGLE_VALUE_SETTINGS")
    found = _sample_values(converter_name, parameter, default=kwargs.get(parameter_name, default), samples=samples)
    if found is None and default is None:
        template = _loaded_template(_build(cls, kwargs), parameter_name)
        found = None if template is None else (_changed(template), _changed(_changed(template)))
    if found is None:
        pytest.fail(f"{converter_name}.{parameter_name}: add a sample value")
    changed = _changed_from(kwargs.get(parameter_name, _MISSING), found)
    if not changed:
        pytest.fail(f"{converter_name}.{parameter_name}: add a sample value that differs from the baseline")
    return cls, kwargs, changed


def _same(first: Any, second: Any) -> bool:
    if first is second:
        return True
    if isinstance(first, TextSelectionStrategy) and type(first) is type(second):
        return bool(vars(first) == vars(second))
    try:
        return bool(first == second)
    except (TypeError, ValueError):
        return False


def _changed_from(baseline: Any, values: tuple[Any, ...]) -> tuple[Any, ...]:
    changed: list[Any] = []
    for value in values:
        if not any(_same(value, seen) for seen in (baseline, *changed)):
            changed.append(value)
    return tuple(changed)


def _build(cls: type, kwargs: dict[str, Any], parameter_name: str | None = None) -> Any:
    try:
        return cls(**kwargs)
    except (OSError, TypeError, ValueError) as error:
        setting = cls.__name__ if parameter_name is None else f"{cls.__name__}.{parameter_name}"
        pytest.fail(f"{setting}: add a sample value the converter accepts ({error})")


def _rebuilt(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return type(value)(_rebuilt(item) for item in value)
    if isinstance(value, MockPromptTarget):
        return _target(value._model_name)
    if isinstance(value, Base64Converter):
        return Base64Converter(encoding_func=value._encoding_func)
    if isinstance(value, FunctionType):
        rebuilt = FunctionType(
            value.__code__.replace(), value.__globals__, value.__name__, value.__defaults__, value.__closure__
        )
        rebuilt.__kwdefaults__ = value.__kwdefaults__
        rebuilt.__annotations__ = value.__annotations__
        return rebuilt
    if isinstance(value, SeedPrompt):
        return value.model_copy(deep=True, update={"id": uuid4(), "date_added": datetime.now(tz=UTC)})
    if isinstance(value, TextJailBreak):
        rebuilt = copy.copy(value)
        rebuilt.template = _rebuilt(value.template)
        return rebuilt
    return copy.deepcopy(value)


def _hash(cls: type, kwargs: dict[str, Any], parameter_name: str | None = None) -> str:
    equal_settings = {name: _rebuilt(value) for name, value in kwargs.items()}
    repeated: set[str] = {
        _build(cls, built, parameter_name).get_identifier().hash for built in (kwargs, equal_settings)
    }
    if len(repeated) != 1:
        setting = cls.__name__ if parameter_name is None else f"{cls.__name__}.{parameter_name}"
        pytest.fail(f"{setting}: the same settings build different identifiers")
    return repeated.pop()


def _hashes(cls: type, kwargs: dict[str, Any], parameter_name: str, values: tuple[Any, ...]) -> list[str]:
    baseline = _hash(cls, kwargs)
    changed = [_hash(cls, {**kwargs, parameter_name: value}, parameter_name) for value in values]
    return [baseline, *changed]


@pytest.mark.filterwarnings("ignore:.*TaskFramingConverter is deprecated:DeprecationWarning")
@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("converter_name", "parameter_name"), _cases())
def test_converter_setting_reaches_identifier(
    converter_name: str, parameter_name: str, samples: dict[str, tuple[Any, ...]]
) -> None:
    cls, kwargs, values = _setup(converter_name, parameter_name, samples)

    hashes = _hashes(cls, kwargs, parameter_name, values)

    picked = "" if parameter_name in kwargs else ", unless a sample is the value it picks when the setting is left out"
    picked = f"{picked}; if it only matters with another setting, list that in SETTINGS_THAT_NEED_ANOTHER"
    assert len(set(hashes)) == len(hashes), (
        f"{converter_name}.{parameter_name} changes the converter but not its identifier{picked}"
    )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("converter_name", "parameter_name"), sorted(NEUTRAL_SETTINGS))
def test_neutral_setting_leaves_identifier_unchanged(
    converter_name: str, parameter_name: str, samples: dict[str, tuple[Any, ...]]
) -> None:
    cls, kwargs, values = _setup(converter_name, parameter_name, samples)
    assert len(values) == 2, (
        f"{converter_name}.{parameter_name}: the samples must differ from each other and from the baseline"
    )

    hashes = _hashes(cls, kwargs, parameter_name, values)

    assert len(set(hashes)) == 1, (
        f"{converter_name}.{parameter_name} is identified now; remove it from NEUTRAL_SETTINGS"
    )


@pytest.mark.usefixtures("patch_central_database")
def test_known_gaps_keep_their_named_templates_apart() -> None:
    registry = _registry()
    jailbreak_converter = registry.get_class("TextJailbreakConverter")
    jailbreaks = {
        _hash(jailbreak_converter, {"jailbreak_template": TextJailBreak(template_file_name=name)})
        for name in ("aim.yaml", "dan_1.yaml")
    }
    assert len(jailbreaks) == 2, "TextJailbreakConverter no longer tells its template files apart"

    puppetry_converter = registry.get_class("PolicyPuppetryConverter")
    puppetry = {
        _hash(puppetry_converter, {"prompt_template": template.to_seed_prompt()}) for template in PolicyPuppetryTemplate
    }
    assert len(puppetry) == len(PolicyPuppetryTemplate), "PolicyPuppetryConverter no longer tells its templates apart"


def _settings_with_allowed_values() -> list[tuple[str, str]]:
    registry = _registry()
    return [
        (name, parameter.name)
        for name in registry.get_class_names()
        for parameter in registry.get_class_metadata(registry.get_class(name)).parameters
        if len(_allowed_values(parameter)) > 1
    ]


def _allowed_value_hashes(
    *, converter_name: str, parameter_name: str, samples: dict[str, tuple[Any, ...]]
) -> list[str]:
    registry = _registry()
    cls = registry.get_class(converter_name)
    parameters = list(registry.get_class_metadata(cls).parameters)
    allowed = _allowed_values(next(item for item in parameters if item.name == parameter_name))
    kwargs = {**_baseline(cls, parameters, samples), **ALLOWED_VALUES_NEED.get((converter_name, parameter_name), {})}

    return [_hash(cls, {**kwargs, parameter_name: value}, parameter_name) for value in allowed]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("converter_name", "parameter_name"), _settings_with_allowed_values())
def test_every_allowed_value_gets_its_own_identifier(
    converter_name: str, parameter_name: str, samples: dict[str, tuple[Any, ...]]
) -> None:
    hashes = _allowed_value_hashes(converter_name=converter_name, parameter_name=parameter_name, samples=samples)

    assert len(set(hashes)) == len(hashes), f"{converter_name}.{parameter_name}: two allowed values share an identifier"


def _pyrit_strategies(base: type = TextSelectionStrategy) -> set[type]:
    found: set[type] = set()
    for subclass in base.__subclasses__():
        if subclass.__module__.startswith("pyrit."):
            found |= _pyrit_strategies(subclass) | (set() if inspect.isabstract(subclass) else {subclass})
    return found


def test_every_selection_strategy_setting_has_a_sample() -> None:
    assert _pyrit_strategies() == set(SELECTION_STRATEGY_SETTINGS), "add the strategy to SELECTION_STRATEGY_SETTINGS"
    for strategy, settings in SELECTION_STRATEGY_SETTINGS.items():
        parameters = inspect.signature(strategy).parameters.values()
        variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        named = {item.name for item in parameters if item.kind not in variadic}
        assert named <= {name for setting in settings for name in setting}, f"{strategy.__name__}: add a sample"


def _strategy_hashes(
    converter_name: str, parameter_name: str, kind: type, samples: dict[str, tuple[Any, ...]]
) -> dict[str, str]:
    registry = _registry()
    cls = registry.get_class(converter_name)
    kwargs = _baseline(cls, list(registry.get_class_metadata(cls).parameters), samples)
    return {
        f"{converter_name} with {strategy.__name__}({setting})": _hash(
            cls, {**kwargs, parameter_name: strategy(**setting)}, parameter_name
        )
        for strategy, settings in SELECTION_STRATEGY_SETTINGS.items()
        if issubclass(strategy, kind)
        for setting in settings
    }


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("converter_name", "parameter_name", "kind"), _STRATEGY_PARAMETERS)
def test_each_selection_strategy_gets_its_own_identifier(
    converter_name: str, parameter_name: str, kind: type, samples: dict[str, tuple[Any, ...]]
) -> None:
    found = _strategy_hashes(converter_name, parameter_name, kind, samples)

    counts = Counter(found.values())
    assert not [built for built, value in found.items() if counts[value] > 1], "these strategies share an identifier"


def _keyword_hashes(converter_name: str, samples: dict[str, tuple[Any, ...]]) -> list[str]:
    registry = _registry()
    cls = registry.get_class(converter_name)
    kwargs = _baseline(cls, list(registry.get_class_metadata(cls).parameters), samples)
    return [_hash(cls, {**kwargs, **extra}) for extra in ({}, *KEYWORD_SETTINGS[converter_name])]


def _takes_keywords(name: str) -> bool:
    parameters = inspect.signature(_registry().get_class(name)).parameters.values()
    return any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("converter_name", [name for name in _registry().get_class_names() if _takes_keywords(name)])
def test_constructor_keywords_reach_identifier(converter_name: str, samples: dict[str, tuple[Any, ...]]) -> None:
    registry = _registry()
    cls = registry.get_class(converter_name)
    kwargs = _baseline(cls, list(registry.get_class_metadata(cls).parameters), samples)
    if converter_name not in KEYWORD_SETTINGS:
        try:
            cls(**kwargs, not_a_setting=1)
        except (TypeError, ValueError):
            return
        pytest.fail(f"{converter_name} keeps extra keywords; list what they do in KEYWORD_SETTINGS")

    hashes = _keyword_hashes(converter_name, samples)

    assert len(set(hashes)) == len(hashes), f"{converter_name}: extra keywords do not reach the identifier"


def _picks_a_mode(parameter: Parameter) -> bool:
    annotation = _unwrap_optional(parameter.param_type)
    return annotation is bool or (inspect.isclass(annotation) and issubclass(annotation, TextSelectionStrategy))


def _modes() -> list[tuple[str, str]]:
    registry = _registry()
    return [
        (name, parameter.name)
        for name in registry.get_class_names()
        for parameter in registry.get_class_metadata(registry.get_class(name)).parameters
        if (name, parameter.name) not in NEUTRAL_SETTINGS and _picks_a_mode(parameter)
    ]


@pytest.mark.filterwarnings("ignore:.*TaskFramingConverter is deprecated:DeprecationWarning")
@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("converter_name", "mode"), _modes())
def test_settings_reach_identifier_in_every_mode(
    converter_name: str, mode: str, samples: dict[str, tuple[Any, ...]]
) -> None:
    registry = _registry()
    cls = registry.get_class(converter_name)
    mode_value = _setup(converter_name, mode, samples)[2][0]
    replaced = {mode, *SETTINGS_A_MODE_REPLACES.get((converter_name, mode), set())}
    for parameter in registry.get_class_metadata(cls).parameters:
        setting = (converter_name, parameter.name)
        if parameter.name in replaced or setting in {*NEUTRAL_SETTINGS, *KNOWN_GAPS, *SINGLE_VALUE_SETTINGS}:
            continue
        _, kwargs, values = _setup(converter_name, parameter.name, samples)
        in_mode = {**kwargs, mode: mode_value, **SETTINGS_THAT_NEED_ANOTHER.get(setting, {})}

        hashes = _hashes(cls, in_mode, parameter.name, values)

        assert len(set(hashes)) == len(hashes), f"{converter_name}.{parameter.name} is lost when {mode} is {mode_value}"


def _setting_hashes(samples: dict[str, tuple[Any, ...]]) -> dict[tuple[str, str], list[str]]:
    registry = _registry()
    found: dict[tuple[str, str], list[str]] = {}
    for name in registry.get_class_names():
        for parameter in registry.get_class_metadata(registry.get_class(name)).parameters:
            if (name, parameter.name) not in SINGLE_VALUE_SETTINGS:
                cls, kwargs, values = _setup(name, parameter.name, samples)
                found[name, parameter.name] = _hashes(cls, kwargs, parameter.name, values)
    return found


@pytest.mark.filterwarnings("ignore:.*TaskFramingConverter is deprecated:DeprecationWarning")
@pytest.mark.usefixtures("patch_central_database")
def test_no_two_settings_share_an_identifier(samples: dict[str, tuple[Any, ...]]) -> None:
    exempt = {*NEUTRAL_SETTINGS, *KNOWN_GAPS}
    changed = [
        (found, setting)
        for setting, hashes in _setting_hashes(samples).items()
        if setting not in exempt
        for found in hashes[1:]
    ]

    counts = Counter(found for found, _ in changed)
    assert not [setting for found, setting in changed if counts[found] > 1], "these settings share an identifier"


def _every_identifier(folder: Path) -> dict[str, Any]:
    samples = make_samples(folder)
    configure_random_seed(seed=_ROOT_SEED)
    with patch.object(CentralMemory, "get_memory_instance"):
        strategies = [_strategy_hashes(*parameters, samples) for parameters in _STRATEGY_PARAMETERS]
        return {
            **{".".join(setting): hashes for setting, hashes in _setting_hashes(samples).items()},
            **{
                f"{name}.{parameter} allowed values": _allowed_value_hashes(
                    converter_name=name, parameter_name=parameter, samples=samples
                )
                for name, parameter in _settings_with_allowed_values()
            },
            **{built: found for sweep in strategies for built, found in sweep.items()},
            **{f"{name} keywords": _keyword_hashes(name, samples) for name in KEYWORD_SETTINGS},
        }


@pytest.mark.filterwarnings("ignore:.*TaskFramingConverter is deprecated:DeprecationWarning")
@pytest.mark.usefixtures("patch_central_database")
def test_process_snapshot_includes_builtin_templates(tmp_path: Path) -> None:
    snapshot = _every_identifier(tmp_path)
    expected = [
        CodeAttackConverter(template=template).get_identifier().hash for template in CodeAttackConverter.Template
    ]

    assert snapshot["CodeAttackConverter.template allowed values"] == expected


@pytest.mark.filterwarnings("ignore:.*TaskFramingConverter is deprecated:DeprecationWarning")
def test_identifiers_stay_the_same_in_another_process(tmp_path: Path) -> None:
    output = tmp_path / "identifiers.json"
    script = (
        f"from json import dumps; from pathlib import Path; from {__name__} import _every_identifier; "
        f"Path({str(output)!r}).write_text(dumps(_every_identifier(Path({str(tmp_path)!r}))))"
    )
    hash_seed = "2" if os.environ.get("PYTHONHASHSEED") == "1" else "1"
    environment = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": os.pathsep.join(sys.path)}

    other = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=_PROCESS_TIMEOUT_SECONDS,
    )

    assert other.returncode == 0, other.stderr[-2000:]
    theirs = json.loads(output.read_text())
    ours = _every_identifier(tmp_path)
    moved = sorted(name for name in {*ours, *theirs} if ours.get(name) != theirs.get(name))
    assert not moved, f"the identifier depends on the process: {moved}"


def test_exemptions_point_at_registered_settings() -> None:
    registry = _registry()
    assert not NEUTRAL_SETTINGS.keys() & KNOWN_GAPS.keys(), "a setting cannot be both neutral and a known gap"
    assert set(BASELINE_SETTINGS) <= set(registry.get_class_names()), "BASELINE_SETTINGS names an unknown converter"
    assert set(KEYWORD_SETTINGS) <= set(registry.get_class_names()), "KEYWORD_SETTINGS names an unknown converter"
    exemptions = [
        *NEUTRAL_SETTINGS,
        *KNOWN_GAPS,
        *SETTINGS_THAT_NEED_ANOTHER,
        *SINGLE_VALUE_SETTINGS,
        *SETTINGS_A_MODE_REPLACES,
        *ALLOWED_VALUES_NEED,
    ]
    for converter_name, parameter_name in exemptions:
        parameters = registry.get_class_metadata(registry.get_class(converter_name)).parameters
        assert parameter_name in {parameter.name for parameter in parameters}, f"{converter_name}.{parameter_name}"
    for converter_name, parameter_name in sorted(SINGLE_VALUE_SETTINGS):
        parameters = registry.get_class_metadata(registry.get_class(converter_name)).parameters
        annotation = _unwrap_optional(next(item for item in parameters if item.name == parameter_name).param_type)
        assert len(get_args(annotation)) == 1, (
            f"{converter_name}.{parameter_name} accepts more values now; remove it from SINGLE_VALUE_SETTINGS"
        )
