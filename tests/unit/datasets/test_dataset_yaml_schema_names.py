# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Repository-wide validation for YAML dataset references to JSON schemas.

Every ``response_json_schema_name`` value that appears in a YAML file under
``pyrit/datasets/`` must resolve via ``COMMON_JSON_SCHEMAS``. Without this
check a typo in a freshly-added YAML would only blow up at runtime when the
loader tried to resolve the name — and only on the code path that actually
loaded that YAML.

Mirrors the parametrize-by-file pattern used in
``tests/unit/models/test_harm_definition.py`` for harm-definition YAMLs.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from pyrit.common.path import DATASETS_PATH
from pyrit.models import COMMON_JSON_SCHEMAS

# Files that are intentionally not valid standalone YAML (e.g. jinja-only
# templates whose top-level value is a string). They should never carry a
# ``response_json_schema_name`` reference, so skip them silently rather than
# failing the validation pass.
_SKIP_YAML_PARSE_ERRORS = True


def _collect_schema_name_references() -> list[tuple[Path, list[str], str]]:
    """
    Walk ``DATASETS_PATH`` recursively and collect every ``response_json_schema_name``
    value, regardless of nesting depth.

    Returns:
        list[tuple[Path, list[str], str]]: ``(yaml_path, key_path, schema_name)``
        triples — ``key_path`` is the breadcrumb to the offending key so the
        test failure points at the exact location.
    """
    references: list[tuple[Path, list[str], str]] = []
    for yaml_file in sorted(DATASETS_PATH.rglob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            if _SKIP_YAML_PARSE_ERRORS:
                continue
            raise
        for key_path, value in _walk(data, []):
            if key_path and key_path[-1] == "response_json_schema_name":
                references.append((yaml_file, key_path, value))
    return references


def _walk(node: Any, key_path: list[str]) -> list[tuple[list[str], Any]]:
    """
    Yield every ``(key_path, value)`` pair where ``key_path`` is the chain of
    mapping keys (and list indices, rendered as ``[i]``) from the root to that
    value. Lists and mappings recurse; scalars are leaves.
    """
    if isinstance(node, dict):
        results: list[tuple[list[str], Any]] = []
        for key, value in node.items():
            new_path = [*key_path, str(key)]
            results.append((new_path, value))
            results.extend(_walk(value, new_path))
        return results
    if isinstance(node, list):
        results = []
        for index, value in enumerate(node):
            new_path = [*key_path, f"[{index}]"]
            results.extend(_walk(value, new_path))
        return results
    return []


_SCHEMA_NAME_REFERENCES = _collect_schema_name_references()


def _reference_id(reference: tuple[Path, list[str], str]) -> str:
    yaml_file, key_path, _ = reference
    relative = yaml_file.relative_to(DATASETS_PATH)
    return f"{relative.as_posix()}::{'.'.join(key_path)}"


def test_dataset_yamls_reference_at_least_one_schema_name() -> None:
    """
    Guard against vacuous parametrization: if zero YAMLs are picked up the
    parametrized test below would silently pass without validating anything.
    The bundled refusal scorer YAMLs reference ``true_false_with_rationale``,
    so the collected list should never be empty.
    """
    collected_names = {schema_name for _, _, schema_name in _SCHEMA_NAME_REFERENCES}
    assert "true_false_with_rationale" in collected_names, (
        "Expected at least the refusal scorer YAMLs to reference "
        "'true_false_with_rationale'; the scanner found nothing. "
        "Was the dataset directory layout changed?"
    )


@pytest.mark.parametrize("reference", _SCHEMA_NAME_REFERENCES, ids=_reference_id)
def test_response_json_schema_name_resolves(reference: tuple[Path, list[str], str]) -> None:
    """
    Every ``response_json_schema_name`` in every dataset YAML must resolve via
    ``COMMON_JSON_SCHEMAS``. Catches typos and references to schemas removed
    from the registry, regardless of whether anything currently loads the YAML.
    """
    yaml_file, key_path, schema_name = reference
    assert schema_name in COMMON_JSON_SCHEMAS, (
        f"{yaml_file.relative_to(DATASETS_PATH).as_posix()} references "
        f"response_json_schema_name={schema_name!r} (at {'.'.join(key_path)}) "
        f"which is not registered in COMMON_JSON_SCHEMAS. "
        f"Known names: {sorted(COMMON_JSON_SCHEMAS)}."
    )
