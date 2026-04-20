# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.identifiers.class_name_utils import class_name_to_snake_case, snake_case_to_class_name

# --- class_name_to_snake_case ---


def test_class_name_to_snake_case_simple():
    assert class_name_to_snake_case("MyClass") == "my_class"


def test_class_name_to_snake_case_single_word():
    assert class_name_to_snake_case("Scorer") == "scorer"


def test_class_name_to_snake_case_multiple_words():
    assert class_name_to_snake_case("SelfAskRefusalScorer") == "self_ask_refusal_scorer"


def test_class_name_to_snake_case_with_suffix_stripped():
    assert class_name_to_snake_case("SelfAskRefusalScorer", suffix="Scorer") == "self_ask_refusal"


def test_class_name_to_snake_case_suffix_not_present():
    assert class_name_to_snake_case("MyClass", suffix="Scorer") == "my_class"


def test_class_name_to_snake_case_with_acronym():
    assert class_name_to_snake_case("XMLParser") == "xml_parser"


def test_class_name_to_snake_case_with_consecutive_uppercase():
    assert class_name_to_snake_case("getHTTPResponse") == "get_http_response"


def test_class_name_to_snake_case_empty_string():
    assert class_name_to_snake_case("") == ""


def test_class_name_to_snake_case_already_lowercase():
    assert class_name_to_snake_case("already") == "already"


def test_class_name_to_snake_case_suffix_equals_class_name():
    assert class_name_to_snake_case("Scorer", suffix="Scorer") == ""


def test_class_name_to_snake_case_with_numbers():
    assert class_name_to_snake_case("Base64Converter") == "base64_converter"


# --- snake_case_to_class_name ---


def test_snake_case_to_class_name_simple():
    assert snake_case_to_class_name("my_class") == "MyClass"


def test_snake_case_to_class_name_single_word():
    assert snake_case_to_class_name("scorer") == "Scorer"


def test_snake_case_to_class_name_with_suffix():
    assert snake_case_to_class_name("my_custom", suffix="Scenario") == "MyCustomScenario"


def test_snake_case_to_class_name_no_suffix():
    assert snake_case_to_class_name("self_ask_refusal") == "SelfAskRefusal"


def test_snake_case_to_class_name_empty_string():
    assert snake_case_to_class_name("") == ""


def test_snake_case_to_class_name_empty_string_with_suffix():
    assert snake_case_to_class_name("", suffix="Scorer") == "Scorer"


def test_snake_case_to_class_name_single_char_parts():
    assert snake_case_to_class_name("a_b_c") == "ABC"


# --- round-trip tests ---


@pytest.mark.parametrize(
    "class_name",
    ["MyClass", "SelfAskRefusal", "Base"],
)
def test_round_trip_snake_to_class(class_name):
    snake = class_name_to_snake_case(class_name)
    result = snake_case_to_class_name(snake)
    assert result == class_name
