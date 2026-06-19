# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
JSON-schema definitions shared across PyRIT components.

This module is the single source of truth for:

* ``JsonSchemaDefinition`` — the type alias used wherever a parsed JSON
  schema travels through PyRIT.
* ``JSON_SCHEMA_METADATA_KEY`` — the ``MessagePiece.prompt_metadata`` key that
  carries a schema from a caller (scorer, attack, converter, …) to a
  schema-aware prompt target.
* ``SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY`` — the ``SeedEntry.prompt_metadata``
  key that ``SeedEntry`` uses to round-trip a ``SeedPrompt.response_json_schema``
  through the database. Reserved: callers must not write this key themselves.
* ``COMMON_JSON_SCHEMAS`` — a registry of reusable named schemas that callers
  (e.g. YAML seed prompts) can reference by name instead of inlining the
  full schema body. Bundled schemas live as YAML files under
  ``pyrit/datasets/json_schemas/`` (filename = registry name); on first
  access the directory is scanned once and the entries are cached. Extensions
  register additional schemas via ``register_common_json_schema`` and
  clean up via ``unregister_common_json_schema`` — both work uniformly
  on top of YAML-discovered entries.

The pieces here are intentionally generic. Scorers, attacks, and converters
all share the same vocabulary without cross-importing each other's modules.
This file's only non-stdlib import outside ``pyrit.models`` is
``pyrit.common.path`` (the bundled YAML directory) — same pattern used by
``pyrit.models.harm_definition`` for its YAML-backed registry. The
``test_import_boundary`` ratchet tracks that exception.
"""

import copy
import logging
from collections.abc import Iterator, Mapping
from typing import Any

import yaml

from pyrit.common.path import JSON_SCHEMAS_PATH

logger = logging.getLogger(__name__)

# A JSON Schema definition, represented as a parsed JSON object.
#
# Python's typing module has no concept of a generic JSON object, so a JSON
# Schema is modeled here as a plain mapping of string keys to arbitrary
# JSON-serializable values (objects, arrays, strings, numbers, booleans, null).
# This alias documents that intent at call sites instead of a misleading
# ``dict[str, str]``.
JsonSchemaDefinition = dict[str, Any]

# Metadata key under which a JSON schema is attached to a
# ``MessagePiece.prompt_metadata``. Targets that natively support
# response-format JSON schemas (e.g. OpenAI chat completions) read this key
# to constrain the response shape; targets that do not are expected to
# strip or ignore it via the normalization pipeline. Centralising the key
# here keeps producers (scorers, attacks, converters) and consumers
# (targets, normalizers) in lock-step.
JSON_SCHEMA_METADATA_KEY = "json_schema"

# Reserved metadata key used by ``SeedEntry`` to round-trip
# ``SeedPrompt.response_json_schema`` through the database.
#
# ``SeedEntry`` stores ``SeedPrompt`` rows with a generic JSON ``prompt_metadata``
# column. Because ``response_json_schema`` is otherwise silently dropped on
# persist, ``SeedEntry`` stashes the resolved schema body under this key on
# save and pops it back out on load. The leading and trailing double underscores
# make the key reserved — application code must not write to it.
SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY = "__response_json_schema__"


# Mutable backing dict that holds the registered schemas.
#
# This dict is populated lazily on first access by
# ``_ensure_discovered``, which scans ``JSON_SCHEMAS_PATH`` for
# ``*.yaml`` files (filename = registry name). Runtime callers and PyRIT
# initializers extend it via ``register_common_json_schema``; tests
# clean up via ``unregister_common_json_schema``. The mutable dict is
# private; external readers go through ``COMMON_JSON_SCHEMAS`` (a read-only
# view) or ``get_common_json_schema`` (deep-copying getter).
_COMMON_JSON_SCHEMAS: dict[str, JsonSchemaDefinition] = {}

# Discovery flag. Flipped to True after the bundled YAML directory has been
# scanned exactly once. All three public functions and every read on
# ``COMMON_JSON_SCHEMAS`` call ``_ensure_discovered()`` first, so the user
# never has to remember to "warm up" the registry.
_DISCOVERED = False


def _load_yaml_schemas() -> dict[str, JsonSchemaDefinition]:
    """
    Scan the bundled ``JSON_SCHEMAS_PATH`` directory for ``*.yaml`` files.

    Each file is expected to contain a JSON Schema as a top-level YAML
    mapping. The file stem becomes the registry name (so
    ``true_false_with_rationale.yaml`` registers as
    ``"true_false_with_rationale"``). Malformed YAML, non-mapping top-level
    values, and parse errors are logged at WARNING and skipped — a bad
    file in a third-party dataset overlay must not break PyRIT startup or
    schema lookups for the well-formed siblings.

    Returns:
        dict[str, JsonSchemaDefinition]: Mapping of file stem to parsed
        schema body. Empty if the directory does not exist.
    """
    loaded: dict[str, JsonSchemaDefinition] = {}

    if not JSON_SCHEMAS_PATH.exists():
        logger.warning(f"JSON schemas directory does not exist: {JSON_SCHEMAS_PATH}")
        return loaded

    for yaml_file in sorted(JSON_SCHEMAS_PATH.glob("*.yaml")):
        name = yaml_file.stem
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logger.warning(f"Failed to load JSON schema file {yaml_file}: {e}")
            continue
        if not isinstance(data, dict):
            logger.warning(
                f"JSON schema file {yaml_file} must contain a top-level YAML mapping; "
                f"got {type(data).__name__}. Skipping."
            )
            continue
        loaded[name] = data

    return loaded


def _ensure_discovered() -> None:
    """
    Trigger one-shot YAML discovery on first access; idempotent thereafter.

    The flag is set BEFORE loading so a directory whose listing fails does
    not cause discovery to loop on every subsequent access; loader errors
    are already logged as warnings inside ``_load_yaml_schemas``.
    """
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    for name, schema in _load_yaml_schemas().items():
        # Runtime registrations done BEFORE discovery shouldn't normally happen
        # (the public register/unregister functions trigger discovery first),
        # but if any did, they win — YAML never silently overwrites them.
        _COMMON_JSON_SCHEMAS.setdefault(name, schema)


def _reset_common_json_schemas_for_tests() -> None:
    """
    Clear the registry cache and the discovered flag.

    Internal helper for tests that monkeypatch ``JSON_SCHEMAS_PATH`` (e.g.
    to point at a fixture directory) and need a clean slate. NOT part of
    the public API — production code must never call this.
    """
    global _DISCOVERED
    _COMMON_JSON_SCHEMAS.clear()
    _DISCOVERED = False


class _LazyJsonSchemaRegistry(Mapping[str, JsonSchemaDefinition]):
    """
    Read-only ``Mapping`` view of ``_COMMON_JSON_SCHEMAS`` that triggers
    YAML discovery on first access.

    Subclassing ``collections.abc.Mapping`` and implementing only the
    three abstract methods gives callers ``keys()``, ``values()``,
    ``items()``, ``get()``, and ``==`` for free — and crucially keeps the
    view read-only (no ``__setitem__`` is defined, so assignment raises
    ``TypeError``). ``__contains__`` is overridden so a membership test
    triggers discovery directly instead of going through ``__getitem__``
    and catching ``KeyError``.
    """

    def __init__(self, backing: dict[str, JsonSchemaDefinition]) -> None:
        self._backing = backing

    def __getitem__(self, key: str) -> JsonSchemaDefinition:
        _ensure_discovered()
        return self._backing[key]

    def __iter__(self) -> Iterator[str]:
        _ensure_discovered()
        return iter(self._backing)

    def __len__(self) -> int:
        _ensure_discovered()
        return len(self._backing)

    def __contains__(self, key: object) -> bool:
        _ensure_discovered()
        return key in self._backing

    def __repr__(self) -> str:
        _ensure_discovered()
        return f"COMMON_JSON_SCHEMAS({self._backing!r})"


COMMON_JSON_SCHEMAS: Mapping[str, JsonSchemaDefinition] = _LazyJsonSchemaRegistry(_COMMON_JSON_SCHEMAS)


def get_common_json_schema(name: str) -> JsonSchemaDefinition:
    """
    Return a deep copy of the named schema from ``COMMON_JSON_SCHEMAS``.

    Triggers a one-shot YAML scan of ``JSON_SCHEMAS_PATH`` on first call.
    A fresh dict is returned so callers may freely mutate, extend, or merge
    the schema without affecting other consumers of the same name.

    Args:
        name (str): Registry key of the desired schema.

    Returns:
        JsonSchemaDefinition: A new dict containing the schema body.

    Raises:
        KeyError: If ``name`` is not registered in ``COMMON_JSON_SCHEMAS``.
    """
    _ensure_discovered()
    if name not in _COMMON_JSON_SCHEMAS:
        known = ", ".join(sorted(_COMMON_JSON_SCHEMAS))
        raise KeyError(f"Unknown JSON schema name {name!r}. Known names: {known}")
    return copy.deepcopy(_COMMON_JSON_SCHEMAS[name])


def register_common_json_schema(
    *,
    name: str,
    schema: JsonSchemaDefinition,
    overwrite: bool = False,
) -> None:
    """
    Register a JSON schema in ``COMMON_JSON_SCHEMAS`` under a stable name.

    YAML seed prompts (and any other ``SeedPrompt`` caller) can then
    reference the schema via the ``response_json_schema_name`` constructor
    kwarg / YAML key instead of inlining the schema body. A deep copy of
    ``schema`` is stored so later mutation of the caller's dict does not
    affect the registry.

    Bundled schemas under ``pyrit/datasets/json_schemas/`` are discovered
    on first access and live in the same registry; this function is the
    runtime path for adding more (e.g. from a ``PyRITInitializer`` body or
    a test fixture).

    Tests that register custom schemas should clean up via
    ``unregister_common_json_schema`` (typically in a fixture's
    teardown) so registrations do not leak between tests.

    Args:
        name (str): Stable registry key. Use ``snake_case``.
        schema (JsonSchemaDefinition): The schema body to register.
        overwrite (bool): If False (default), raises ``ValueError`` when
            ``name`` is already registered. Set True to intentionally
            replace an existing entry.

    Raises:
        TypeError: If ``schema`` is not a ``dict``.
        ValueError: If ``name`` is already registered and ``overwrite``
            is False.
    """
    _ensure_discovered()
    if not isinstance(schema, dict):
        raise TypeError(f"schema must be a dict, got {type(schema).__name__}")
    if not overwrite and name in _COMMON_JSON_SCHEMAS:
        raise ValueError(f"JSON schema {name!r} is already registered. Pass overwrite=True to replace it.")
    _COMMON_JSON_SCHEMAS[name] = copy.deepcopy(schema)


def unregister_common_json_schema(name: str) -> None:
    """
    Remove a previously registered schema from ``COMMON_JSON_SCHEMAS``.

    Works uniformly on YAML-discovered and runtime-registered entries.
    Primarily intended for test teardown so transient registrations do
    not leak across test cases.

    Args:
        name (str): Registry key to remove.

    Raises:
        KeyError: If ``name`` is not currently registered.
    """
    _ensure_discovered()
    if name not in _COMMON_JSON_SCHEMAS:
        raise KeyError(f"JSON schema {name!r} is not registered.")
    del _COMMON_JSON_SCHEMAS[name]
