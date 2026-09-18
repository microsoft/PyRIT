# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused registry contracts for enum and word-selection inputs."""

from __future__ import annotations

from enum import Enum
from typing import Any

import pytest

from pyrit.common import apply_defaults, forward_init_parameters
from pyrit.converter import BinaryConverter, CodeAttackConverter, SATAMaskingConverter
from pyrit.converter.text_selection_strategy import WordProportionSelectionStrategy, WordSelectionStrategy
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.registry.components import ConverterRegistry
from pyrit.registry.resolution import derive_parameters, resolve_constructor_args


class _Parent:
    class Mode(Enum):
        FIRST = 1

    @apply_defaults
    def __init__(self, *, mode: Mode = Mode.FIRST, count: int = 1) -> None:
        self.mode = mode


class _Inherited(_Parent):
    pass


class _Forwarded(_Parent):
    @forward_init_parameters
    def __init__(self, *, count: float = 0.5, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class _GenericStructuredInput:
    @classmethod
    def get_registry_input_variants(cls) -> dict[str, type[_GenericStructuredInput]]:
        return {"counted": _CountVariant}


class _CountVariant(_GenericStructuredInput):
    def __init__(self, *, count: int) -> None:
        self.count = count


class _GenericHolder:
    def __init__(self, *, input_value: _GenericStructuredInput) -> None:
        self.input_value = input_value


@pytest.mark.parametrize("cls", [_Parent, _Inherited, _Forwarded])
def test_defining_namespace_wrapping_and_child_precedence(cls: type) -> None:
    parameters = {param.name: param for param in derive_parameters(cls=cls)}
    assert parameters["mode"].param_type is _Parent.Mode
    assert parameters["count"].param_type is (float if cls is _Forwarded else int)
    assert resolve_constructor_args(cls=cls, raw_args={"mode": 1})["mode"] is _Parent.Mode.FIRST


def test_unresolved_annotation_does_not_hide_resolved_parameters() -> None:
    class Partial:
        def __init__(self, *, missing: NotImported = None, count: int = 1) -> None:  # noqa: F821
            pass

    parameters = {param.name: param for param in derive_parameters(cls=Partial)}
    assert parameters["missing"].param_type == "NotImported"
    assert not parameters["missing"].is_string_coercible
    assert parameters["count"].param_type is int


@pytest.mark.parametrize("raw", ["16", "BITS_16", 16, 16.0, BinaryConverter.BitsPerChar.BITS_16])
def test_binary_enum_inputs(raw: Any) -> None:
    args = resolve_constructor_args(cls=BinaryConverter, raw_args={"bits_per_char": raw})
    assert BinaryConverter(**args).bits_per_char is BinaryConverter.BitsPerChar.BITS_16


@pytest.mark.parametrize("raw", ["invalid", 12, None, True, {}, []])
def test_binary_invalid_enum_inputs(raw: Any) -> None:
    with pytest.raises(ValueError, match="bits_per_char"):
        resolve_constructor_args(cls=BinaryConverter, raw_args={"bits_per_char": raw})


@pytest.mark.parametrize("raw", [None, "FIRST", 1, _Parent.Mode.FIRST])
def test_required_nullable_enum_uses_parameter_coercion(raw: Any) -> None:
    class Holder:
        def __init__(self, *, mode: _Parent.Mode | None) -> None:
            self.mode = mode

    parameter = derive_parameters(cls=Holder)[0]
    assert parameter.required
    assert parameter.param_type == _Parent.Mode | None
    parameter.validate()
    resolved = resolve_constructor_args(cls=Holder, raw_args={"mode": raw})
    assert resolved["mode"] is (None if raw is None else _Parent.Mode.FIRST)


def test_code_attack_explicit_null_preserves_default_encoding() -> None:
    args = resolve_constructor_args(cls=CodeAttackConverter, raw_args={"encoding": None})
    assert args == {"encoding": None}
    assert CodeAttackConverter(**args).get_identifier() == CodeAttackConverter().get_identifier()


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, [1]),
        ({"stopwords": None, "candidate_words": None}, [1]),
        ({"stopwords": []}, [0, 1]),
        ({"candidate_words": []}, []),
    ],
)
def test_content_strategy_empty_lists_differ_from_defaults(settings: dict[str, Any], expected: list[int]) -> None:
    args = resolve_constructor_args(
        cls=SATAMaskingConverter,
        raw_args={"selection_strategy": {"type": "content", "parameters": {"skip_first": 0, **settings}}},
    )
    assert args["selection_strategy"].select_words(words=["the", "cat"]) == expected


def test_binary_catalog_default_and_strategy_metadata() -> None:
    registry = ConverterRegistry()
    metadata = registry.get_registered_class_metadata("BinaryConverter")
    assert metadata is not None
    parameters = {param.name: param for param in metadata.parameters}
    bits = parameters["bits_per_char"].model_dump(mode="json")
    assert bits["choices"] == ["8", "16", "32"]
    assert bits["default"] == "16"
    instance = registry.create_instance("BinaryConverter", bits_per_char=bits["default"])
    assert isinstance(instance, BinaryConverter)
    assert instance.bits_per_char.value == 16
    strategies = parameters["word_selection_strategy"].model_dump(mode="json")["variants"]
    assert set(strategies) == {"all", "random", "position", "indices", "keywords", "regex", "content"}
    assert [(param["name"], param["type_name"]) for param in strategies["random"]] == [
        ("proportion", "float"),
        ("seed", "int"),
    ]
    assert strategies["indices"][0]["type_name"] == "list[int]"
    assert strategies["regex"][0]["type_name"] == "str"
    assert [param["type_name"] for param in strategies["content"]][-2:] == ["list[str]", "list[str]"]


def test_structured_variants_are_not_converter_specific() -> None:
    parameter = derive_parameters(cls=_GenericHolder)[0]
    assert parameter.variants is not None
    assert parameter.variants["counted"][0].type_name == "int"
    parameter.validate()

    resolved = resolve_constructor_args(
        cls=_GenericHolder,
        raw_args={"input_value": {"type": "counted", "parameters": {"count": 3}}},
    )

    assert isinstance(resolved["input_value"], _CountVariant)
    assert resolved["input_value"].count == 3


@pytest.mark.parametrize(
    ("kind", "settings", "expected"),
    [
        ("all", {}, [0, 1, 2, 3]),
        ("random", {"proportion": 0.5, "seed": 42}, None),
        ("position", {"start_proportion": 0.25, "end_proportion": 0.75}, [1, 2]),
        ("indices", {"indices": [0, 2]}, [0, 2]),
        ("keywords", {"keywords": ["TWO"], "case_sensitive": False}, [1]),
        ("regex", {"pattern": "^t"}, [1, 2]),
        (
            "content",
            {"max_words": 1, "skip_first": 0, "min_word_length": 2, "stopwords": ["one"], "candidate_words": ["three"]},
            [2],
        ),
    ],
)
def test_word_selection_builds_builtin_behavior(
    kind: str, settings: dict[str, Any], expected: list[int] | None
) -> None:
    raw = {"word_selection_strategy": {"type": kind, "parameters": settings}}
    strategy = resolve_constructor_args(cls=BinaryConverter, raw_args=raw)["word_selection_strategy"]
    assert isinstance(strategy, WordSelectionStrategy)
    selected = strategy.select_words(words=["one", "two", "three", "four"])
    if expected is None:
        assert len(selected) == 2
        other = resolve_constructor_args(cls=BinaryConverter, raw_args=raw)["word_selection_strategy"]
        assert other.select_words(words=["one", "two", "three", "four"]) == selected
    else:
        assert selected == expected


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"kind": "random"},
        {"type": "unknown"},
        {"type": "all", "extra": 1},
        {"type": "all", "parameters": {"seed": 42}},
        {"type": "random"},
        {"type": "random", "parameters": []},
        {"type": "random", "parameters": {"proportion": 1.1}},
        {"type": "random", "parameters": {"proportion": float("nan")}},
        {"type": "random", "parameters": {"proportion": 0.3, "seed": True}},
        {"type": "position", "parameters": {"start_proportion": 0.8, "end_proportion": 0.2}},
        {"type": "indices", "parameters": {"indices": [1.5]}},
        {"type": "indices", "parameters": {"indices": [-1]}},
        {"type": "keywords", "parameters": {"keywords": [None]}},
        {"type": "regex", "parameters": {"pattern": "["}},
        {"type": "regex", "parameters": {"pattern": None}},
        {"type": "content", "parameters": {"max_words": 0}},
    ],
)
def test_invalid_word_selection_has_parameter_context(value: Any) -> None:
    with pytest.raises(ValueError, match="word_selection_strategy"):
        resolve_constructor_args(cls=BinaryConverter, raw_args={"word_selection_strategy": value})


@pytest.mark.parametrize(
    ("cls", "name", "default_name"),
    [
        (BinaryConverter, "word_selection_strategy", "AllWordsSelectionStrategy"),
        (SATAMaskingConverter, "selection_strategy", "ContentWordSelectionStrategy"),
    ],
)
def test_strategy_identity_and_constructor_defaults(cls: type, name: str, default_name: str) -> None:
    strategy = WordProportionSelectionStrategy(proportion=0.3, seed=42)
    assert resolve_constructor_args(cls=cls, raw_args={name: strategy})[name] is strategy
    for raw in ({}, {name: None}):
        instance = cls(**resolve_constructor_args(cls=cls, raw_args=raw))
        assert instance.get_identifier().params[name] == default_name
    instance = cls(**resolve_constructor_args(cls=cls, raw_args={name: {"type": "all"}}))
    assert instance.get_identifier().params[name] == "AllWordsSelectionStrategy"


def test_word_level_and_binary_identifiers_include_strategy_settings() -> None:
    first = BinaryConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=0.3, seed=42))
    second = BinaryConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=0.6, seed=42))
    assert first.get_identifier() != second.get_identifier()
    assert WordLevelConverter._build_identifier(first).params["word_selection_strategy_params"] == {
        "proportion": 0.3,
        "seed": 42,
    }
