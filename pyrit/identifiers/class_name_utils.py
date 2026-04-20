# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Class name conversion utilities for PyRIT identifiers.

This module provides functions for converting between different naming conventions
(e.g., PascalCase class names to snake_case identifiers and vice versa).
"""

import re


def class_name_to_snake_case(class_name: str, *, suffix: str = "") -> str:
    """
    Convert a PascalCase class name to snake_case, optionally stripping a suffix.

    Args:
        class_name: The class name to convert (e.g., "SelfAskRefusalScorer").
        suffix: Optional explicit suffix to strip before conversion (e.g., "Scorer").

    Returns:
        The snake_case name (e.g., "self_ask_refusal" if suffix="Scorer").
    """
    # Strip explicit suffix if provided
    if suffix and class_name.endswith(suffix):
        class_name = class_name[: -len(suffix)]
    # Handle transitions like "XMLParser" -> "XML_Parser"
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", class_name)
    # Handle transitions like "getHTTPResponse" -> "get_HTTP_Response"
    name = re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return name


def snake_case_to_class_name(snake_case_name: str, *, suffix: str = "") -> str:
    """
    Convert a snake_case name to a PascalCase class name.

    Args:
        snake_case_name: The snake_case name to convert (e.g., "my_custom").
        suffix: Optional suffix to append to the class name
            (e.g., "Scenario" would convert "my_custom" to "MyCustomScenario").

    Returns:
        The PascalCase class name (e.g., "MyCustomScenario").
    """
    # Split on underscores and capitalize each part
    parts = snake_case_name.split("_")
    pascal_case = "".join(part.capitalize() for part in parts)

    # Append suffix if provided
    if suffix:
        pascal_case += suffix

    return pascal_case
