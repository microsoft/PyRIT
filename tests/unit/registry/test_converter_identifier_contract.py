# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Every converter constructor setting must reach the converter identifier, unless it is declared neutral."""

from __future__ import annotations

import copy
import enum
import inspect
import random
import re
from functools import cache
from itertools import product
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin

import pytest
from PIL import Image

from pyrit.common import REQUIRED_VALUE
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.common.random_context import configure_random_seed, get_configured_random_seed
from pyrit.converter import (
    Base64Converter,
    BinaryConverter,
    CodeAttackConverter,
    IndexSelectionStrategy,
    WordIndexSelectionStrategy,
)
from pyrit.converter.text_selection_strategy import TextSelectionStrategy, WordSelectionStrategy
from pyrit.datasets import TextJailBreak
from pyrit.models import ComponentType, Identifiable, Parameter, SeedPrompt
from pyrit.registry.components import ConverterRegistry
from unit.mocks import MockPromptTarget

if TYPE_CHECKING:
    from collections.abc import Iterator


def _reverse(text: str) -> str:
    return text[::-1]


def _token() -> str:
    return "token"


_SAME_QUALNAME_FUNCTIONS = (
    lambda text: text.upper(),
    lambda text: text.lower(),
)


_RETRIES = "controls how often the request is retried, not what the converter produces"
_AZURE_SPEECH_RESOURCE = (
    "selects which Azure Speech resource serves the request, not how the text or audio is converted"
)
_AZURE_SPEECH_KEY = "a credential, and credentials are never part of an identifier"

NEUTRAL_SETTINGS: dict[tuple[str, str], str] = {
    ("LLMGenericTextConverter", "max_retry_attempts"): _RETRIES,
    ("LLMGenericTextConverter", "retry_wait_max_seconds"): _RETRIES,
    ("LLMGenericTextConverter", "retry_exceptions"): _RETRIES,
    ("TranslationConverter", "max_retries"): _RETRIES,
    ("TranslationConverter", "max_wait_time_in_seconds"): _RETRIES,
    ("MathObfuscationConverter", "rng"): (
        "a random.Random is run-time state, not a setting: its state moves with every draw, so one instance gives "
        "different coefficients on its next call and there is no fixed value to record. The range it draws from, "
        "min_n..max_n, is identified."
    ),
    ("AzureSpeechAudioToTextConverter", "azure_speech_region"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechAudioToTextConverter", "azure_speech_resource_id"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechAudioToTextConverter", "azure_speech_key"): _AZURE_SPEECH_KEY,
    ("AzureSpeechTextToAudioConverter", "azure_speech_region"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechTextToAudioConverter", "azure_speech_resource_id"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechTextToAudioConverter", "azure_speech_key"): _AZURE_SPEECH_KEY,
}

SINGLE_VALUE_SETTINGS: dict[tuple[str, str], str] = {
    (name, "output_format"): "only 'wav' is accepted, so there is nothing to vary"
    for name in (
        "AudioEchoConverter",
        "AudioFrequencyConverter",
        "AudioSpeedConverter",
        "AudioVolumeConverter",
        "AudioWhiteNoiseConverter",
    )
}

KNOWN_GAPS: dict[tuple[str, str], str] = {
    ("CodeChameleonConverter", "encrypt_function"): "custom functions are not identified yet, see #3761",
    ("CodeChameleonConverter", "decrypt_function"): "custom functions are not identified yet, see #3761",
    ("PolicyPuppetryConverter", "prompt_template"): "custom templates are identified by name only, see #3759",
    ("TextJailbreakConverter", "jailbreak_template"): "inline templates share one identifier, see #3760",
}

BASELINE_SETTINGS: dict[str, dict[str, Any]] = {
    "AzureSpeechAudioToTextConverter": {"azure_speech_region": "eastus", "azure_speech_key": "test-key"},
    "AzureSpeechTextToAudioConverter": {"azure_speech_region": "eastus", "azure_speech_key": "test-key"},
}

_CUSTOM_CODECHAMELEON = {"encrypt_type": "custom", "encrypt_function": _reverse, "decrypt_function": _reverse}
_CUSTOM_CODE_ATTACK = {
    "template": Path(CONVERTER_SEED_PROMPT_PATH) / "code_attack_python_list.yaml",
    "encoding": CodeAttackConverter.Encoding.PYTHON_LIST,
}

SETTINGS_THAT_NEED_ANOTHER: dict[tuple[str, str], dict[str, Any]] = {
    ("DecompositionConverter", "codewords"): {"use_word_game": True},
    ("DecompositionConverter", "word_game_prompt"): {"use_word_game": True},
    ("SelectiveTextConverter", "word_separator"): {"selection_strategy": WordIndexSelectionStrategy(indices=[0])},
    ("ImagePromptStyleConverter", "variation"): {"filter_name": "public_space_tv_broadcast"},
    ("CodeChameleonConverter", "encrypt_function"): _CUSTOM_CODECHAMELEON,
    ("CodeChameleonConverter", "decrypt_function"): _CUSTOM_CODECHAMELEON,
    ("CodeAttackConverter", "encoding"): _CUSTOM_CODE_ATTACK,
    ("CodeAttackConverter", "template"): _CUSTOM_CODE_ATTACK,
    **{
        (name, "azure_speech_resource_id"): {"azure_speech_key": _token, "azure_speech_resource_id": "resource"}
        for name in ("AzureSpeechAudioToTextConverter", "AzureSpeechTextToAudioConverter")
    },
}

_MISSING = object()


class _Vocabulary:
    def __init__(self, letters: str) -> None:
        self._tokens = {"".join(token): index for index, token in enumerate(product(letters, repeat=3))}

    def get_vocab(self) -> dict[str, int]:
        return self._tokens


@cache
def _registry() -> ConverterRegistry:
    return ConverterRegistry()


@pytest.fixture(autouse=True)
def fixed_root_seed() -> Iterator[None]:
    previous_seed = get_configured_random_seed()
    configure_random_seed(seed=0)
    try:
        yield
    finally:
        configure_random_seed(seed=previous_seed)


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Any, ...]]:
    folder = tmp_path_factory.mktemp("converter_contract")
    images = []
    for index in range(20):
        png, jpg = folder / f"image_{index}.png", folder / f"image_{index}.jpg"
        Image.new("RGB", (32, 32), (index * 10, 0, 0)).save(png)
        Image.new("RGB", (32, 32), (index * 10, 0, 0)).save(jpg)
        images.append((str(png), str(jpg)))
    documents = []
    for name in ("first.pdf", "second.pdf", "first.docx", "second.docx"):
        (folder / name).write_bytes(b"placeholder")
        documents.append(folder / name)
    pngs, jpgs = [png for png, _ in images], [jpg for _, jpg in images]
    styles = Path(CONVERTER_SEED_PROMPT_PATH) / "image_prompt_style"
    filters = []
    for subfolder, source in (("first", "gritty_documentary.yaml"), ("second", "laundromat_fisheye.yaml")):
        (folder / subfolder).mkdir()
        filters.append(folder / subfolder / "custom.yaml")
        filters[-1].write_bytes((styles / source).read_bytes())
    code_attack = _CUSTOM_CODE_ATTACK["template"].read_text(encoding="utf-8")
    code_attack_templates = (folder / "first_code_attack.yaml", folder / "second_code_attack.yaml")
    code_attack_templates[0].write_text(
        code_attack.replace("{{ wrapped_input }}", "{{ wrapped_input }}  ", 1), encoding="utf-8"
    )
    code_attack_templates[1].write_text(
        code_attack.replace("{{ wrapped_input }}", "{{ wrapped_input }} ", 1), encoding="utf-8"
    )
    return {
        "img_to_add": (pngs[0], pngs[1]),
        "base_image": (pngs[0], pngs[1]),
        "carrier_image_path": (pngs[0], pngs[1]),
        "benign_image_path": (jpgs[0], jpgs[1]),
        "innocuous_images": (pngs[:19], pngs[1:]),
        "payload_background": (pngs[0], pngs[1]),
        "PDFConverter.existing_pdf": (documents[0], documents[1]),
        "WordDocConverter.existing_docx": (documents[2], documents[3]),
        "PDFConverter.prompt_template": (
            SeedPrompt(value="First {{ prompt }}", parameters=["prompt"]),
            SeedPrompt(value="Second {{ prompt }}", parameters=["prompt"]),
        ),
        "WordDocConverter.prompt_template": (
            SeedPrompt(value="First {{ prompt }}", parameters=["prompt"]),
            SeedPrompt(value="Second {{ prompt }}", parameters=["prompt"]),
        ),
        "LLMGenericTextConverter.system_prompt_template": (SeedPrompt(value="First"), SeedPrompt(value="Second")),
        "LLMGenericTextConverter.user_prompt_template_with_objective": (
            SeedPrompt(value="First {{ objective }}", parameters=["objective"]),
            SeedPrompt(value="Second {{ objective }}", parameters=["objective"]),
        ),
        "AddImageTextConverter.bounding_box": ((0, 0, 10, 10), (0, 0, 20, 20)),
        "BinaryConverter.bits_per_char": tuple(BinaryConverter.BitsPerChar),
        "CodeAttackConverter.template": code_attack_templates,
        "CodeAttackConverter.encoding": tuple(CodeAttackConverter.Encoding),
        "ScientificTranslationConverter.mode": ("academic", "technical"),
        "RepeatTokenConverter.token_insert_mode": ("prepend", "append", "repeat"),
        "SATAMaskingConverter.num_masks": (3, 4),
        "SATAMaskingConverter.skip_first": (2, 3),
        "video_path": ("first.mp4", "second.mp4"),
        "wordswap_path": ("filipino.yaml", "indian.yaml"),
        "font_name": (Path("first.ttf"), Path("second.ttf")),
        "filter_path": tuple(filters),
        "injection_items": ([{"text": "first"}], [{"text": "second"}]),
        "encrypt_function": _SAME_QUALNAME_FUNCTIONS,
        "decrypt_function": _SAME_QUALNAME_FUNCTIONS,
        "filter_name": ("laundromat_fisheye", "polaroid_vintage_film"),
        "variation": ("dive_bar_reflection", "airport_lounge"),
        "replace": ("first", "second"),
        "zero_char": ("\u200b", "\u200c"),
        "one_char": ("\u200d", "\u2060"),
        "base_char_utf8": ("a", "b"),
        "custom_substitutions": ({"a": ["4"]}, {"a": ["@"]}),
        "stopwords": (["the"], ["a"]),
        "candidate_words": (["alpha"], ["beta"]),
        "template": ("First {{ prompt }}", "Second {{ prompt }}"),
        "trap_template": ("{prompt} is {wrong_value}", "Is {prompt} {wrong_value}?"),
        "encrypt_type": ("reverse", "binary_tree"),
        "persuasion_technique": ("authority_endorsement", "expert_endorsement"),
        "tokenizer": (_Vocabulary("abcdefgh"), _Vocabulary("ijklmnop")),
        "MathObfuscationConverter.rng": (random.Random(1), random.Random(2)),
        "retry_exceptions": ((ValueError,), (KeyError,)),
        "SearchReplaceConverter.regex_flags": (re.IGNORECASE, re.MULTILINE),
        "jailbreak_template": (
            TextJailBreak(string_template="First {{ prompt }}"),
            TextJailBreak(string_template="Second {{ prompt }}"),
        ),
    }


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
    if (
        isinstance(value, tuple)
        and value
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return tuple((item + 1) % 256 for item in value)
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


def _sample_values(
    owner: str, parameter: Parameter, default: Any, samples: dict[str, tuple[Any, ...]]
) -> tuple[Any, ...] | None:
    for key in (f"{owner}.{parameter.name}", parameter.name):
        if key in samples:
            return samples[key]
    annotation = _unwrap_optional(parameter.param_type)
    allowed = get_args(annotation) if get_origin(annotation) is Literal else None
    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        allowed = tuple(annotation)
    if allowed is not None:
        others = tuple(value for value in allowed if value != default)
        return others or None
    if inspect.isclass(annotation) and issubclass(annotation, WordSelectionStrategy):
        return WordIndexSelectionStrategy(indices=[0]), WordIndexSelectionStrategy(indices=[1])
    if inspect.isclass(annotation) and issubclass(annotation, TextSelectionStrategy):
        return IndexSelectionStrategy(start=0, end=3), IndexSelectionStrategy(start=1, end=4)
    first = _changed(default) if default is not None else _fresh(annotation)
    if first is _MISSING:
        return None
    second = _changed(first)
    if second is _MISSING or second == default:
        return (first, default) if default is not None else None
    return first, second


def _reference(parameter: Parameter, variant: int) -> Any:
    if parameter.reference is None:
        return _MISSING
    if parameter.reference.component_type is ComponentType.TARGET:
        return MockPromptTarget(rpm=10 + variant)
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


def _copied(kwargs: dict[str, Any]) -> dict[str, Any]:
    components = [
        item
        for value in kwargs.values()
        for item in (value if isinstance(value, list) else [value])
        if isinstance(item, Identifiable)
    ]
    return copy.deepcopy(kwargs, {id(component): component for component in components})


def _hash(cls: type, kwargs: dict[str, Any], parameter_name: str | None = None) -> str:
    repeated: set[str] = {
        _build(cls, built, parameter_name).get_identifier().hash for built in (kwargs, _copied(kwargs))
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


def test_exemptions_point_at_registered_settings() -> None:
    registry = _registry()
    assert not NEUTRAL_SETTINGS.keys() & KNOWN_GAPS.keys(), "a setting cannot be both neutral and a known gap"
    assert set(BASELINE_SETTINGS) <= set(registry.get_class_names()), "BASELINE_SETTINGS names an unknown converter"
    exemptions = [*NEUTRAL_SETTINGS, *KNOWN_GAPS, *SETTINGS_THAT_NEED_ANOTHER, *SINGLE_VALUE_SETTINGS]
    for converter_name, parameter_name in exemptions:
        parameters = registry.get_class_metadata(registry.get_class(converter_name)).parameters
        assert parameter_name in {parameter.name for parameter in parameters}, f"{converter_name}.{parameter_name}"
    for converter_name, parameter_name in sorted(SINGLE_VALUE_SETTINGS):
        parameters = registry.get_class_metadata(registry.get_class(converter_name)).parameters
        annotation = _unwrap_optional(next(item for item in parameters if item.name == parameter_name).param_type)
        assert len(get_args(annotation)) == 1, (
            f"{converter_name}.{parameter_name} accepts more values now; remove it from SINGLE_VALUE_SETTINGS"
        )
